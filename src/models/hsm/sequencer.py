"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

Sequencer for C-HSM: builds the model from the run config, runs it, and writes the results.
Run it standalone with ``pixi run hsm``.
"""

import logging
from pathlib import Path

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.utilities import setup_logger
from src.models.hsm.data import load_henry_hub_path, load_ng_supply_side
from src.models.hsm.hsm_config import HSMConfig
from src.models.hsm.hsm_model import HSMModel
from src.models.hsm.postprocessor import report

logger = logging.getLogger(__name__)


class HSMSequencer(IntegratedModelSequencer[HSMModel, HSMConfig]):
    """Sequencer for the C-HSM hydrocarbon supply model."""

    def __init__(self):
        """Initialize the sequencer."""
        self._model: HSMModel | None = None
        self._hsm_config: HSMConfig | None = None
        self._common_config: CommonConfig | None = None

    @property
    def model(self) -> HSMModel:
        """The built model.  Raises ``RuntimeError`` if ``build_model()`` has not run yet."""
        if self._model is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return self._model

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
        self, common_config: CommonConfig, model_config: HSMConfig, **kwargs
    ) -> HSMModel:
        """Build C-HSM from its inputs.

        Parameters
        ----------
        common_config : CommonConfig
            The ``[common]`` settings, with :meth:`CommonConfig.make_scenario_dir` already
            called: the optional debug files are written under its ``output_folder``. C-HSM
            always runs every year from 2023 to 2050, whatever ``summary_years`` says.
        model_config : HSMConfig
            The ``[hsm]`` settings.

        Returns
        -------
        HSMModel
            The built model, not yet run. Also kept on the sequencer.
        """
        self._common_config = common_config
        self._hsm_config = model_config
        regions, supply_cost_tiers = load_ng_supply_side(model_config.ng_input_path)
        henry_hub = load_henry_hub_path(model_config.input_path / model_config.henry_hub_file)
        self._model = HSMModel(
            hsm_config=model_config,
            regions=regions,
            supply_cost_tiers=supply_cost_tiers,
            henry_hub_prices=henry_hub,
            output_path=self.output_dir(),
        )
        return self._model

    def update_model(self, **kwargs) -> HSMModel:
        """Not implemented; C-HSM is not yet wired into the iterative integrator."""
        raise NotImplementedError

    def solve_model(self, **kwargs) -> IterationStatus:
        """Run every model year.

        There is nothing to optimize, so this cannot fail the way a solver can: any problem in
        the inputs raises an exception instead. A finished run is reported as ``USABLE``, the
        same status C-NGMM uses for a good solve.

        Returns
        -------
        IterationStatus
            ``USABLE``.
        """
        self.model.run()
        return IterationStatus.USABLE

    def full_postprocess(self, **kwargs):
        """Write the result CSVs to ``<output_folder>/hsm/``."""
        report(m=self.model, output_dir=self.output_dir())

    def iteration_postprocess(self, **kwargs):
        """Not implemented; C-HSM is not yet wired into the iterative integrator."""


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
    setup_logger(common_config)
    hsm_config = HSMConfig(**remainder.pop('hsm'))
    sequencer = HSMSequencer()
    sequencer.build_model(common_config, hsm_config)
    status = sequencer.solve_model()
    if status is IterationStatus.ERROR:
        logger.error('C-HSM: run failed with status %s, no results written', status)
        return 1
    sequencer.full_postprocess()
    logger.info('C-HSM: results in %s', sequencer.output_dir())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
