"""
A gathering of utility functions for dealing with model interconnectivity.
"""

import argparse
import logging
import os
from collections.abc import Collection
from datetime import datetime
from logging import getLogger
from pathlib import Path
from warnings import deprecated

import pandas as pd

from src.common.common_config import CommonConfig

# Establish logger
logger = getLogger(__name__)


@deprecated('should be using normal Path functions')
def make_dir(dir_name):
    """Generates an output directory to write model results, output directory is the date/time
    at the time this function executes. It includes subdirs for vars, params, constraints.

    Returns
    -------
    string
        the name of the output directory
    """
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)
    else:
        logger.info('Asked to make dir that already exists:' + str(dir_name))


# Logger Setup
def setup_logger(settings: CommonConfig, **kwargs):
    """Initiates logging, sets up logger in the output directory specified.

    Parameters
    ----------
    output_dir : path
        output directory path
    """
    # set up root logger
    output_dir = settings.output_path / settings.scenario_name / 'electricity'
    log_path = Path(output_dir)
    if not log_path.is_dir():
        log_path.mkdir(parents=True, exist_ok=True)

    # logger level
    if kwargs.get('debug', False):
        loglevel = logging.DEBUG
    else:
        loglevel = logging.INFO

    # logger configs
    logging.basicConfig(
        filename=f'{output_dir}/run.log',
        encoding='utf-8',
        filemode='w',
        # format='[%(asctime)s][%(name)s]' + '[%(funcName)s][%(levelname)s]  :: |%(message)s|',
        format='%(asctime)s | %(name)s | %(levelname)s :: %(message)s',
        datefmt='%d-%b-%y %H:%M:%S',
        level=loglevel,
    )
    logging.getLogger('pyomo').setLevel(logging.WARNING)
    logging.getLogger('pandas').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)


def get_args():
    """Parses args.

    Returns
    -------
    args: Namespace
        Contains arguments pass to main.py executable
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        description='description:\n'
        '\tBuilds and runs models based on user inputs set in src/common/run_config.toml\n'
        '\tMode argument determines which models are run and how they are integrated and solved\n'
        '\tUniversal and module-specific options contained within run_config.toml\n'
        '\tUser can specify regions, time periods, solver options, and mode in run_config\n'
        '\tUsers can also specify the mode via command line argument or run_config.toml',
    )
    parser.add_argument(
        '--mode',
        choices=['unified-combo', 'gs-combo', 'standalone', 'elec', 'h2', 'residential'],
        dest='op_mode',
        help='The mode to run:\n\n'
        'unified-combo:  run unified optimization method, iteratively solves modules turned on in the run_congif file\n'
        'gs-combo:  run gauss-seidel method, iteratively solves modules turned on in the run_congif file\n'
        'standalone: runs in standalone the modules that are turned on in the run_config file\n'
        'elec:  run the electricity module standalone\n'
        'h2:  run the hydrogen module standalone\n'
        'residential: run the residential module standalone, solves updated load based on new given prices\n\n'
        'Mode can be set either via --mode command or in run_config.toml.\n'
        'If no --mode option is provided, default_mode in run_config.toml is used.',
    )
    parser.add_argument('--debug', action='store_true', help='set logging level to DEBUG')

    # parsing arguments
    args = parser.parse_args()

    return args


def scale_load(data_root):
    """Reads in BaseLoad.csv (load for all regions/hours for first year)
    and LoadScalar.csv (a multiplier for all model years). Merges the
    data and multiplies the load by the scalar to generate new load
    estimates for all model years.

    Returns
    -------
    pandas.core.frame.DataFrame
        dataframe that contains load for all regions/years/hours
    """
    # combine first year baseload data with scalar data for all years
    baseload = pd.read_csv(data_root / 'BaseLoad.csv')
    scalar = pd.read_csv(data_root / 'LoadScalar.csv')
    df = pd.merge(scalar, baseload, how='cross')

    # scale load in each year by scalar
    df['Load'] = round(df['Load'] * df['scalar'], 3)
    df = df.drop(columns=['scalar'])

    # reorder columns
    df = df[['region', 'year', 'hour', 'Load']]
    # convert region names to strings
    df['region'] = df['region'].astype(str)
    return df


# TODO:  Simple profiling shows that this function is SUPER slow.  Consider refactor ideas...
# update to above:  pre-filtering regions makes this much more tolerable.  More to do ?
def scale_load_with_enduses(data_root: Path, regions: Collection[str]):
    """Reads in BaseLoad.csv (load for all regions/hours for first year), EnduseBaseShares.csv
    (the shares of demand for each enduse in the base year) and EnduseScalar.csv (a multiplier
    for all model years by enduse category). Merges the data and multiplies the load by the
    adjusted enduse scalar and then sums up to new load estimates for all model years.

    Returns
    -------
    pandas.core.frame.DataFrame
        dataframe that contains load for all regions/years/hours
    """
    tic = datetime.now()

    if len(regions) == 0:
        logger.warning('No regions specified, returning empty Load DataFrame when scaling end uses')
        return pd.DataFrame()
    # share of total base load that is assigned to each enduse cat
    eu = pd.read_csv(data_root / 'EnduseBaseShares.csv')

    # annual incremental growth (percent of eu baseload)
    eus = pd.read_csv(data_root / 'EnduseScalar.csv')

    # converts the annual increment to percent of total baseload
    eu = pd.merge(eu, eus, how='left', on='enduse_cat')
    eu['increment_annual'] = eu['increment_annual'] * eu['base_year_share']
    eu = eu.drop(columns=['base_year_share'])

    # baseload total
    load = pd.read_csv(data_root / 'BaseLoad.csv')
    # convert region names to strings
    load['region'] = load['region'].astype(str)
    # filter to regions of interest
    load = load[load['region'].isin(regions)]

    bla = load.groupby(by=['region'], as_index=False).sum().drop(columns=['hour'])

    # converts the annual increment to mwh
    eu = pd.merge(eu, bla, how='cross')
    eu['increment_annual'] = eu['increment_annual'] * eu['Load']
    eu = eu.drop(columns=['Load'])

    # percent of enduse load for each hour
    euh = pd.read_csv(data_root / 'EnduseShapes.csv')

    # converts the annual increment to an hourly increment
    eu = pd.merge(eu, euh, how='left', on=['enduse_cat'])
    eu['increment'] = eu['increment_annual'] * eu['share']
    eu = eu.drop(columns=['increment_annual', 'share'])
    eu = (
        eu.groupby(by=['year', 'region', 'hour'], as_index=False).sum().drop(columns=['enduse_cat'])
    )

    # creates future load
    load = pd.merge(load, eu, how='left', on=['region', 'hour'])
    load['Load'] = load['Load'] + load['increment']
    load = load[['region', 'year', 'hour', 'Load']]

    toc = datetime.now()
    logger.info(f'Time to scale load: {(toc - tic).total_seconds():0.2f} sec')

    return load
