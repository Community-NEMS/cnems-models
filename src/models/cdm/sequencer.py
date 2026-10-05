"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Builds, runs and reports C-CDM on its own.

C-CDM is not yet wired into the integrated control loop. ``IntegratedModelSequencer.solve_model``
returns a ``(ModelType, IterationStatus)`` pair and ``ModelType`` has no C-CDM member, so this
sequencer stands alone and the run config lists no models under ``[common] models_to_run``.
``pixi run cdm`` runs it.
"""

import logging
import sys
from pathlib import Path

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IterationStatus
from src.common.log_setup import setup_control_loop_logging
from src.models.cdm.cdm_config import CDMConfig
from src.models.cdm.cdm_model import CDMModel
from src.models.cdm.data import load_all
from src.models.cdm.postprocessor import report

logger = logging.getLogger(__name__)


class CDMSequencer:
    """Builds, runs and reports C-CDM."""

    def __init__(self):
        """Start with no model."""
        self._model: CDMModel | None = None
        self._common_config: CommonConfig | None = None

    @property
    def model(self) -> CDMModel:
        """The built model."""
        if self._model is None:
            raise RuntimeError('C-CDM: build_model() has not been called')
        return self._model

    def build_model(self, common_config: CommonConfig, cdm_config: CDMConfig) -> CDMModel:
        """Load and check the inputs and build the model.

        Parameters
        ----------
        common_config : CommonConfig
            The ``[common]`` settings; C-CDM reads only the output folder.
        cdm_config : CDMConfig
            The ``[cdm]`` settings.

        Returns
        -------
        CDMModel
            The model, ready to run.
        """
        self._common_config = common_config
        self._model = CDMModel(load_all(cdm_config.input_path), cdm_config)
        return self._model

    def solve_model(self) -> IterationStatus:
        """Run the model; C-CDM is a direct calculation, so a finished run is usable."""
        self.model.run()
        return IterationStatus.USABLE

    def full_postprocess(self) -> None:
        """Write the result tables to ``<output_folder>/cdm``."""
        if self._common_config is None or self.model.results is None:
            raise RuntimeError('C-CDM: nothing to report; build and solve the model first')
        report(
            self.model.data,
            self.model.case,
            self.model.results,
            self._common_config.output_folder / 'cdm',
        )


def main(config_path: Path | None = None) -> None:
    """Run C-CDM standalone from a run config (default ``run_configs/basic_cdm_config.toml``)."""
    config_path = config_path or PROJECT_ROOT / 'run_configs/basic_cdm_config.toml'
    common_config, remainder = parse_config_file(config_path)
    common_config.make_scenario_dir()
    setup_control_loop_logging(common_config.output_folder / 'run.log')
    cdm_config = CDMConfig(**remainder.pop('cdm'))
    sequencer = CDMSequencer()
    sequencer.build_model(common_config, cdm_config)
    status = sequencer.solve_model()
    logger.info('C-CDM: finished with status %s', status)
    sequencer.full_postprocess()


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
