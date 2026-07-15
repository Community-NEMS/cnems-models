"""main.py for Bluesky Prototype"""

# Import packages
import logging
import os
import types
from pathlib import Path
from warnings import deprecated

import tomllib

# Import python modules
from definitions import PROJECT_ROOT
from src.common.common_config import parse_config_file
from src.common.config_setup import Config_settings
from src.common.models_modes import ModelType, RunMode
from src.common.utilities import get_args, setup_logger
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.runner import run_elec_model

# Specify config path
default_config_path = Path(PROJECT_ROOT, 'run_configs/basic_elec_config.toml')


@deprecated('needs reconfig if preserved')
def app_main(selected_mode):
    """main run through the bsky gui app

    Parameters
    ----------
    selected_mode : str
        selected mode to run model
    """
    app_args = types.SimpleNamespace()
    app_args.op_mode = selected_mode
    app_args.debug = False

    app_settings, config_data = Config_settings(config_path=default_config_path, args=app_args)
    main(app_settings)


def main(
    common_config_path: Path = default_config_path, elec_config_path: Path | None = None, **kwargs
):
    """
    Runs model as defined in settings

    Parameters
    -------
    args: settings
        Contains configuration settings for which models and solvers to run
    """
    # Parse the args to get selected mode if one is provided
    # TODO:  Come back to this and review the arg_parser
    args = get_args()

    # Build the common config
    common_config, remainder = parse_config_file(common_config_path)
    if not elec_config_path:
        # expect the elec_config to be in the remainder of a "unified" config file
        elec_config = ElecConfig(**remainder.pop('elec_config'))
    else:
        # expect the elec_config to be in a separate file
        elec_config = ElecConfig.from_toml(elec_config_path)

    # inspect kwargs for overrides
    if run_mode := kwargs.get('run_mode'):
        common_config.mode = run_mode

    # Establish the logger
    setup_logger(common_config, **args.__dict__)
    logger = logging.getLogger(__name__)

    # Log settings
    logger.info('Starting Logging')
    logger.info(f'Model running in: {common_config.mode} mode')
    logger.info('Config settings:')
    # logger.info(f'Regions: {settings.regions}')  # Not common amongst models
    logger.info(
        f'Years: {common_config.summary_years}{"aggregated with start year " + str(common_config.aggregate_start_year) if common_config.aggregate_years else ""}'
    )
    # with open(default_config_path, 'rb') as f:
    #     data = tomllib.load(f)
    # config_list = []
    # for key, value in data.items():
    #     config_list.append(f'{key}: {value}')
    # logger.info(config_list)
    # logger.debug(f'Config settings: these settings dont have checks: {settings.missing_checks}')
    # settings.cw_temporal.to_csv(Path(settings.OUTPUT_ROOT / 'cw_temporal.csv'), index=False)

    # Run the cases you want to run based on the mode and settings you pass
    # TODO:  Remove access to globals() which is retrieving a function based on a string??
    if common_config.mode == RunMode.STANDALONE and common_config.models_to_run == [
        ModelType.ELECTRICITY
    ]:
        run_elec_model(common_config, elec_config, solve=True)
    else:
        logger.error('No valid run mode selected.  Exiting.')
        raise NotImplementedError('No valid run mode selected')
    # runner = globals()[settings.run_method]
    # runner(settings)

    # print the output directory once run is finished
    # path_parts = os.path.normpath(settings.OUTPUT_ROOT).split(os.sep)
    # output_name = os.path.join(path_parts[-2], path_parts[-1])
    # print(f'Results located in: {output_name}')
    logger.info('Finished.')
    print('Finished.')


if __name__ == '__main__':
    # run a default and default + exchange model to enable viewing of results
    main(common_config_path=Path(PROJECT_ROOT, 'run_configs/basic_elec_config.toml'))
    main(common_config_path=Path(PROJECT_ROOT, 'run_configs/exchange_elec_config.toml'))
