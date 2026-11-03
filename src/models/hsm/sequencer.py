"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

Sequencer for C-HSM: builds the model from the run config, runs it, and writes the results.
Run it standalone with ``pixi run hsm``.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.log_setup import setup_control_loop_logging
from src.common.models_modes import ModelType
from src.common.update_package import ExchangeSpec, UpdatePackage
from src.models.hsm.data import load_henry_hub_path, load_ng_supply_side
from src.models.hsm.hsm_config import HSMConfig
from src.models.hsm.hsm_model import HSMModel
from src.models.hsm.postprocessor import report
from src.models.hsm.update_reader import HSMData, HSMUpdateReader, load_reference_pair
from src.models.hsm.update_writer import HSMUpdateWriter

logger = logging.getLogger(__name__)


class HSMSequencer(IntegratedModelSequencer[HSMModel, HSMConfig, HSMData]):
    """Sequencer for C-HSM."""

    def __init__(self, exchange: ExchangeSpec | None = None) -> None:
        """Initialize the sequencer.

        Parameters
        ----------
        exchange : ExchangeSpec, optional
            The run's exchanges; this model's writer builds its share of them.  Omitted (a
            standalone run), the writer writes nothing.
        """
        exchange = exchange if exchange is not None else ExchangeSpec.empty()
        self._data: HSMData | None = None
        self._model: HSMModel | None = None
        self._hsm_config: HSMConfig | None = None
        self._common_config: CommonConfig | None = None
        self._last_status: IterationStatus | None = None
        self._reader = HSMUpdateReader()
        self._writer = HSMUpdateWriter(exchange.outbound_for(ModelType.HSM), exchange.crosswalks)

    @property
    def model(self) -> HSMModel:
        """The built model.  Raises ``RuntimeError`` if ``build_model()`` has not run yet."""
        if self._model is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return self._model

    @property
    def reader(self) -> HSMUpdateReader:
        """Applies inbound packages to the loaded ``HSMData``."""
        return self._reader

    @property
    def writer(self) -> HSMUpdateWriter:
        """Writes the capacity package for C-NGMM."""
        return self._writer

    @property
    def last_status(self) -> IterationStatus | None:
        """Status of the most recent run, or ``None`` before one."""
        return self._last_status

    @property
    def common_config(self) -> CommonConfig:
        """The common config the model was built from.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model`.
        """
        if self._common_config is None:
            raise RuntimeError('Config is not available; call build_model() first.')
        return self._common_config

    def output_dir(self) -> Path:
        """Where results go: ``<output_folder>/hsm/``.

        Raises
        ------
        RuntimeError
            If :meth:`CommonConfig.make_scenario_dir` has not been called on the config.
        """
        return self.common_config.output_folder / 'hsm'

    def build_model(
        self,
        common_config: CommonConfig,
        model_config: HSMConfig,
        update_packages: Sequence[UpdatePackage] | None = None,
        **kwargs,
    ) -> HSMModel:
        """Load C-HSM's inputs, apply inbound packages to them, and build the model.

        Parameters
        ----------
        common_config : CommonConfig
            The ``[common]`` settings, with :meth:`CommonConfig.make_scenario_dir` already
            called.  C-HSM runs every year from 2023 to 2050 and reports ``summary_years``.
        model_config : HSMConfig
            The ``[hsm]`` settings.
        update_packages : Sequence[UpdatePackage], optional
            Inbound packages, applied to the loaded data by :class:`HSMUpdateReader`.

        Returns
        -------
        HSMModel
            The built model, not yet run. Also kept on the sequencer.
        """
        self._common_config = common_config
        self._hsm_config = model_config
        regions, supply_cost_tiers = load_ng_supply_side(model_config.ng_input_path)
        reference_1987, reference_usd = load_reference_pair(
            model_config.input_path / model_config.coupling_reference_file,
            model_config.coupling_reference_dollar_year,
        )
        self._data = HSMData(
            regions=regions,
            supply_cost_tiers=supply_cost_tiers,
            henry_hub_1987=load_henry_hub_path(
                model_config.input_path / model_config.henry_hub_file
            ),
            reference_1987=reference_1987,
            reference_usd=reference_usd,
            reference_dollar_year=model_config.coupling_reference_dollar_year,
        )
        self._reader.read(update_packages, self._data)
        self._model = self._model_from_data()
        return self._model

    def update_model(self, update_packages: Sequence[UpdatePackage], **kwargs) -> HSMModel:
        """Apply inbound packages to the held data, then rebuild the model from it.

        A rebuild restores the Canada submodule's starting state, which ``reset_canada()`` does
        not.

        Parameters
        ----------
        update_packages : Sequence[UpdatePackage]
            Applied in order by :class:`HSMUpdateReader`.

        Returns
        -------
        HSMModel
            The rebuilt model, not yet run.

        Raises
        ------
        RuntimeError
            If called before :meth:`build_model`.
        """
        if self._data is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        self._reader.read(update_packages, self._data)
        self._model = self._model_from_data()
        return self._model

    def _model_from_data(self) -> HSMModel:
        """Build an ``HSMModel`` from the held config and data."""
        if self._data is None or self._hsm_config is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return HSMModel(
            hsm_config=self._hsm_config,
            regions=self._data.regions,
            supply_cost_tiers=self._data.supply_cost_tiers,
            henry_hub_prices=self._data.henry_hub_1987,
            output_path=self.output_dir(),
            summary_years=self.common_config.summary_years,
        )

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
        """Run every model year.

        There is nothing to optimize, so this cannot fail the way a solver can: any problem in
        the inputs raises an exception instead.  A finished run is ``USABLE``.

        Returns
        -------
        tuple[ModelType, IterationStatus]
            ``ModelType.HSM`` and ``USABLE``.
        """
        self.model.run()
        self._last_status = IterationStatus.USABLE
        return ModelType.HSM, self._last_status

    def get_objective_value(self) -> float | None:
        """C-HSM has no objective; the convergence test leaves it out."""
        return None

    def full_postprocess(self, **kwargs):
        """Write the result CSVs to ``<output_folder>/hsm/``."""
        report(m=self.model, output_dir=self.output_dir())

    def iteration_postprocess(self, **kwargs):
        """Nothing to do between iterations."""


def main() -> int:
    """Build and run C-HSM from ``run_configs/basic_hsm_config.toml``, then write the results.

    Returns
    -------
    int
        ``0`` when the run is usable, ``1`` otherwise.
    """
    config_path = PROJECT_ROOT / 'run_configs/basic_hsm_config.toml'
    common_config, remainder = parse_config_file(config_path)
    common_config.make_scenario_dir()
    setup_control_loop_logging(common_config.output_folder / 'run.log')
    hsm_config = HSMConfig(**remainder.pop('hsm'))
    sequencer = HSMSequencer()
    sequencer.build_model(common_config, hsm_config)
    _, status = sequencer.solve_model()
    if status is IterationStatus.ERROR:
        logger.error('C-HSM: run failed with status %s, no results written', status)
        return 1
    sequencer.full_postprocess()
    logger.info('C-HSM: results in %s', sequencer.output_dir())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
