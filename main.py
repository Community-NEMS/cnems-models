"""main.py for Bluesky Prototype."""

# Import packages
import logging
import types
from pathlib import Path
from typing import Any
from warnings import deprecated

from pydantic import ValidationError

# Import python modules
from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, ModelConfig, parse_config_file
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.log_setup import setup_control_loop_logging
from src.common.models_modes import ModelType, RunMode
from src.common.utilities import get_args
from src.integrator import combine
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.magic.magic_model import MagicConfig, MagicSequencer
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.sequencer import NGSequencer

logger = logging.getLogger(__name__)


@deprecated('needs reconfig if preserved')
def app_main(selected_mode):
    """Main run through the bsky gui app.

    Parameters
    ----------
    selected_mode : str
        selected mode to run model
    """
    app_args = types.SimpleNamespace()
    app_args.op_mode = selected_mode
    app_args.debug = False

    # main(app_settings)


def main(config_path: Path, debug: bool = False) -> None:
    """Run the models as set in the config file, dispatching on its run mode.

    Parameters
    ----------
    config_path : Path
        The run config file (TOML or JSON) with a ``[common]`` section plus model sections.
    debug : bool, optional
        Debug mode; currently only sets the standalone run log to DEBUG.  False by default.

    Raises
    ------
    NotImplementedError
        If the config's mode is ``RunMode.INTEGRATED_GS``.
    """
    common_config, remainder = parse_config_file(config_path)

    match common_config.mode:
        case RunMode.STANDALONE:
            # Claim a fresh scenario output dir, then establish the logger in it.  Standalone runs
            # solve in this process with no per-model scenario log, so the run log is the
            # complete record -- solver output included -- while the console stays project-only
            common_config.make_scenario_dir()
            log_file = common_config.output_folder / 'run.log'
            setup_control_loop_logging(log_file, level=logging.DEBUG if debug else logging.INFO)

            logger.info('Starting Logging')
            logger.info(f'Model running in: {common_config.mode} mode')
            logger.info(
                f'Years: {common_config.summary_years}'
                + (
                    f' aggregated with start year {common_config.aggregate_start_year}'
                    if common_config.aggregate_years
                    else ''
                )
            )
            _run_standalone(common_config, remainder)
        case RunMode.INTEGRATED_JACOBI:
            # TODO:  combine.main() reads its own config and runs a fixed set of models; wire it
            #        to this config and models_to_run.  future:  enable model selection...
            logger.info('Running integrated Jacobi mode; models_to_run is ignored for now')
            combine.main()
        case RunMode.INTEGRATED_GS:
            raise NotImplementedError('Integrated Gauss-Seidel mode is not implemented')

    logger.info('Finished.')
    print('Finished.')


def _run_standalone(common_config: CommonConfig, remainder: dict) -> None:
    """Build, solve, and postprocess each model in ``models_to_run``, one after another.

    A model whose config section is missing or invalid is logged as an error and skipped; the
    remaining models still run.

    Parameters
    ----------
    common_config : CommonConfig
        Common run configuration, with its scenario dir already claimed.
    remainder : dict
        The config file's sections other than ``[common]``.
    """
    models = common_config.models_to_run
    if ModelType.ALL in models:
        models = [model for model in ModelType if model is not ModelType.ALL]

    for model_type in models:
        sequencer: IntegratedModelSequencer[Any, Any, Any]
        try:
            match model_type:
                case ModelType.ELECTRICITY:
                    model_config: ModelConfig = ElecConfig(**remainder['elec_config'])
                    sequencer = ElectricitySequencer()
                case ModelType.NATURAL_GAS:
                    model_config = NGConfig(**remainder['natural_gas'])
                    sequencer = NGSequencer()
                case ModelType.MAGIC:
                    model_config = MagicConfig(**remainder.get('magic_config', {}))
                    sequencer = MagicSequencer()
                case _:
                    logger.error('No standalone run available for %s; skipping', model_type)
                    continue
        except KeyError as e:
            logger.error('No config section %s found for %s; skipping it', e, model_type)
            continue
        except ValidationError as e:
            logger.error('Invalid config for %s; skipping it:\n%s', model_type, e)
            continue

        logger.info('Running %s standalone', model_type)
        sequencer.build_model(common_config, model_config)
        _, status = sequencer.solve_model()
        if status is IterationStatus.ERROR:
            logger.error('%s: solve failed, no results written', model_type)
            continue
        logger.info('%s solved with status %s', model_type, status)
        if (obj_value := sequencer.get_objective_value()) is not None:
            logger.info('%s objective value: %0.2f', model_type, obj_value)
        sequencer.full_postprocess()


if __name__ == '__main__':
    args = get_args()
    if args.config_path:
        main(config_path=args.config_path, debug=args.debug)
    else:
        # no config given:  run a default and default + exchange model to enable viewing of
        # results, plus the reduced string-named input set (input/electricity_light) with exchange
        # and a natural gas standalone run
        for name in (
            'basic_elec_config',
            'exchange_elec_config',
            'reduced_elec_config',
            'basic_ng_config',
        ):
            main(config_path=PROJECT_ROOT / 'run_configs' / f'{name}.toml', debug=args.debug)
