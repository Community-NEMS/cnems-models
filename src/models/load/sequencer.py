"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Sequencer for the hourly load model.

``LoadSequencer`` implements the ``IntegratedModelSequencer`` interface so the load model is run
like the other models.  Nothing is optimized:  "solving" builds the hourly load profiles of the 25
EMM regions for the run's ``summary_years`` (see ``run.py``) and writes them, together with the
audit files, into ``<output_path>/load/<scenario_name>`` (``load_output_folder``).  The hourly
file ``hourly_load_mwh.csv`` is the electricity model's load, in MWh, by region id, year and hour
of the year.
"""

import csv
import shutil
from collections.abc import Sequence
from logging import getLogger
from pathlib import Path
from typing import Any

from src.common.common_config import CommonConfig
from src.common.integrated_model import IntegratedModel
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import ExchangeSpec, UpdatePackage
from src.models.load.calendars import display_path
from src.models.load.load_config import LoadConfig, nems_scenario_for
from src.models.load.load_model import N_REGIONS
from src.models.load.nems_aeo_reader import find_restart
from src.models.load.run import run
from src.models.load.update_reader import LoadUpdateReader
from src.models.load.update_writer import LoadUpdateWriter

logger = getLogger(__name__)

SCENARIO_USED_FILE = 'scenario_used.csv'
LOAD_FOLDER = 'load'
RUN_LOG = 'run.log'


def load_output_folder(common_config: CommonConfig) -> Path:
    """Return the fixed results folder of the load model, ``<output_path>/load/<scenario_name>``.

    Unlike the other models' run folders it is never suffixed (``_1``, ...): the load is built
    once per scenario and read by other models, so a rerun replaces the earlier results.

    Parameters
    ----------
    common_config : CommonConfig
        Common run configuration; supplies ``output_path`` and ``scenario_name``.

    Returns
    -------
    Path
        The folder (not necessarily existing yet).
    """
    return common_config.output_path / LOAD_FOLDER / common_config.scenario_name


def clear_previous_results(folder: Path) -> None:
    """Empty a load results folder (created if missing), keeping the run log.

    A rerun must not leave files of an earlier run behind, e.g. an old ``hourly_load_mwh.csv``
    after a run that stopped with an error.

    Parameters
    ----------
    folder : Path
        The load model's own folder, see :func:`load_output_folder`.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for item in folder.iterdir():
        if item.name == RUN_LOG:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


class LoadProfileModel(IntegratedModel):
    """The hourly load model:  annual loads by end use in, hourly load by EMM region out.

    Attributes
    ----------
    result : dict or None
        What :func:`src.models.load.run.run` returned, or ``None`` before :meth:`solve`.
    """

    @property
    def label(self) -> str:
        """Display name."""
        return 'Load'

    @property
    def regions(self) -> frozenset[str]:
        """The 25 EMM regions, by id ('1' .. '25') as the electricity model names them."""
        return frozenset(str(i) for i in range(1, N_REGIONS + 1))

    def __init__(
        self,
        common_config: CommonConfig,
        load_config: LoadConfig,
        nems_scenario: str | None,
        restart_file: Path | None,
    ) -> None:
        """Hold the settings of one run.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; supplies the years and the output folder.
        load_config : LoadConfig
            Load model configuration.
        nems_scenario : str or None
            The NEMS scenario folder the loads come from, if they come from the NEMS outputs.
        restart_file : Path or None
            The NEMS restart file read, if the loads come from one.
        """
        self.common_config = common_config
        self.load_config = load_config
        self.nems_scenario = nems_scenario
        self.restart_file = restart_file
        self.result: dict[str, Any] | None = None

    @property
    def output_folder(self) -> Path:
        """The fixed results folder, ``<output_path>/load/<scenario_name>``."""
        return load_output_folder(self.common_config)

    def solve(self) -> IterationStatus:
        """Build the load profiles and write them to the load results folder.

        Results of an earlier run of the same scenario are removed first.

        Returns
        -------
        IterationStatus
            ``BEST``.  Bad input stops the run with an error naming the problem; no gaps are
            filled silently.
        """
        cfg = self.load_config
        clear_previous_results(self.output_folder)
        self.result = run(
            str(cfg.tables_dir),
            emm_region_loads=_text(cfg.emm_region_loads),
            year=sorted(self.common_config.summary_years),
            census_division_loads=_text(cfg.census_division_loads),
            restart_file=_text(self.restart_file),
            restart_values=cfg.restart_values,
            scenario=self.nems_scenario or self.common_config.scenario_name,
            demand_overrides={k: str(v) for k, v in cfg.demand_overrides.items()} or None,
            method=cfg.method,
            base_year=cfg.base_year,
            base_freeze_year=cfg.base_freeze_year,
            ulossadj_file=_text(cfg.ulossadj_file),
            loss_adj=cfg.loss_adj,
            output_folder=str(self.output_folder),
            hourly8760=cfg.hourly8760,
            save_profiles_864=cfg.save_profiles_864,
            validate=cfg.validate_inputs,
            renormalize=cfg.renormalize,
            calendar_year=cfg.calendar_year,
            calendar_csv=_text(cfg.calendar_csv),
            load_dir=_text(cfg.load_dir),
            holidays=cfg.holidays,
            peak_days_csv=_text(cfg.peak_days_csv),
        )
        return IterationStatus.BEST


def _text(path: Path | None) -> str | None:
    """Return ``path`` as ``str``, passing ``None`` through (``run`` takes plain strings)."""
    return None if path is None else str(path)


class LoadSequencer(IntegratedModelSequencer[LoadProfileModel, LoadConfig, None]):
    """Build/solve orchestration for the hourly load model."""

    def __init__(self, exchange: ExchangeSpec | None = None):
        """Initialize the sequencer.

        Parameters
        ----------
        exchange : ExchangeSpec, optional
            The run's exchanges; this model's writer builds its share of them.  Omitted (a
            standalone run), the writer writes nothing.
        """
        super().__init__()
        self._model: LoadProfileModel | None = None
        self._reader = LoadUpdateReader()
        exchange = exchange if exchange is not None else ExchangeSpec.empty()
        self._writer = LoadUpdateWriter(exchange.outbound_for(ModelType.LOAD), exchange.crosswalks)
        self._last_status: IterationStatus | None = None

    @property
    def model(self) -> LoadProfileModel:
        """The built load model.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model`.
        """
        if self._model is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return self._model

    @property
    def reader(self) -> LoadUpdateReader:
        """The no-handler reader shell."""
        return self._reader

    @property
    def writer(self) -> LoadUpdateWriter:
        """The writer shell; it sends no package yet."""
        return self._writer

    @property
    def last_status(self) -> IterationStatus | None:
        """Status of the most recent solve, or ``None`` before one."""
        return self._last_status

    def build_model(
        self,
        common_config: CommonConfig,
        model_config: LoadConfig,
        update_packages: Sequence[UpdatePackage] | None = None,
        **kwargs,
    ) -> LoadProfileModel:
        """Find the source of the annual loads and set up the model; nothing is read or computed.

        With ``nems_outputs_dir`` the project's ``scenario_name`` is looked up in the scenario map
        to find the NEMS scenario folder; the other sources need no lookup.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : LoadConfig
            Load model configuration.
        update_packages : Sequence[UpdatePackage], optional
            Updates to read-in data, applied by :class:`LoadUpdateReader`.

        Returns
        -------
        LoadProfileModel
            The built model (also retained as :attr:`model`).

        Raises
        ------
        ValueError
            If the run's scenario is not in the scenario map.
        FileNotFoundError
            If the NEMS scenario folder or its restart file is missing.
        """
        self._reader.read(update_packages, None)
        nems_scenario = None
        restart_file = None
        if model_config.nems_outputs_dir is not None:
            nems_scenario = nems_scenario_for(
                common_config.scenario_name, model_config.scenario_map
            )
            restart_file = find_restart(model_config.nems_outputs_dir, nems_scenario)
            logger.info(
                'Scenario %s: NEMS scenario %s, restart file %s',
                common_config.scenario_name,
                nems_scenario,
                display_path(restart_file),
            )
        self._model = LoadProfileModel(common_config, model_config, nems_scenario, restart_file)
        return self._model

    def update_model(self, update_packages: Sequence[UpdatePackage], **kwargs) -> LoadProfileModel:
        """Not implemented; the load model takes no inbound updates yet.

        Raises
        ------
        NotImplementedError
            Always.
        """
        raise NotImplementedError()

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
        """Build the load profiles and write them.

        Returns
        -------
        tuple[ModelType, IterationStatus]
            :attr:`ModelType.LOAD` paired with the status, always ``IterationStatus.BEST``.
        """
        self._last_status = self.model.solve()
        return ModelType.LOAD, self._last_status

    def full_postprocess(self, **kwargs) -> None:
        """Record which NEMS scenario and file the loads came from, next to the results.

        Writes ``scenario_used.csv`` with the project scenario, the NEMS scenario and the name of
        the source file (file name only, no local path, so the folder can be shared).
        """
        model = self.model
        cfg = model.load_config
        source = next(
            (
                Path(path).name
                for path in (cfg.census_division_loads, cfg.emm_region_loads, model.restart_file)
                if path is not None
            ),
            None,
        )
        out = model.output_folder / SCENARIO_USED_FILE
        with open(out, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['scenario_name', 'nems_scenario', 'source_file'])
            writer.writerow(
                [model.common_config.scenario_name, model.nems_scenario or '', source or '']
            )
        logger.info('Load results are in %s', display_path(model.output_folder))

    def iteration_postprocess(self, **kwargs) -> None:
        """No-op; the load model has nothing to do between iterations."""
        logger.info('iterative postprocessing load model...done')

    def get_objective_value(self) -> float | None:
        """Get ``None``; the load model has no objective."""
        return None
