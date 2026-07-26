"""A gathering of functions for running models solo."""

# Import packages
from logging import getLogger
from pathlib import Path

from pyomo.environ import value

# Import python modules
from src.common.config_setup import Config_settings
from src.integrator import utilities
from src.models.electricity.sequencer import run_elec_model

# Establish logger
logger = getLogger(__name__)


def run_elec_solo(settings: Config_settings | None = None):
    """
    Runs electricity model by itself as defined in settings.

    Parameters
    ----------
    settings: Config_settings
        Contains configuration settings for which regions, years, and switches to run
    """
    # engage the Electricity Model...
    logger.info('Running Electricity Module')
    instance = run_elec_model(settings)
    print(f'Objective value: {value(instance.total_cost)}')

    # write out prices and plot them
    elec_price = utilities.get_elec_price(instance)
    elec_price.to_csv(Path(settings.OUTPUT_ROOT / 'electricity' / 'prices' / 'elec_price.csv'))
    # plot_price_distro(settings.OUTPUT_ROOT, list(elec_price.price_wt))


def run_h2_solo(settings: Config_settings | None = None):
    """Runs hydrogen model by itself as defined in settings.

    Parameters
    ----------
    settings: Config_settings
        Contains configuration settings for which regions and years to run

    Raises
    ------
    NotImplementedError
        Always; the hydrogen module is not part of this fork.
    """
    raise NotImplementedError(
        'The hydrogen module was removed from this fork; only the electricity model is supported.'
    )


def run_residential_solo(settings: Config_settings | None = None):
    """Runs residential model by itself as defined in settings.

    Parameters
    ----------
    settings: Config_settings
        Contains configuration settings for which regions and years to run

    Raises
    ------
    NotImplementedError
        Always; the residential module is not part of this fork.
    """
    raise NotImplementedError(
        'The residential module was removed from this fork; '
        'only the electricity model is supported.'
    )


def run_standalone(settings: Config_settings):
    """Runs standalone methods based on settings selections; running 1 or more modules.

    Parameters
    ----------
    settings : Config_settings
        Instance of config_settings containing run options, mode and settings
    """
    print('running standalone mode')
    if settings.electricity:
        print('running electricity module')
        run_elec_solo(settings)

    if settings.hydrogen:
        print('running hydrogen module')
        run_h2_solo(settings=settings)

    if settings.residential:
        print('running residential module')
        run_residential_solo(settings)
