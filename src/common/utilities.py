"""A gathering of utility functions for dealing with model interconnectivity."""

import argparse
from collections.abc import Collection, Sequence
from datetime import datetime
from logging import getLogger
from pathlib import Path

import pandas as pd

# Establish logger
logger = getLogger(__name__)


def get_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments for ``main.py``.

    Parameters
    ----------
    argv : Sequence[str] | None, optional
        Arguments to parse; ``None`` (default) parses ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        ``config_path`` (``Path | None``) and ``debug`` (``bool``).
    """
    parser = argparse.ArgumentParser(
        description="Build and run the models as set in a run config file.  The config's "
        '[common] mode selects standalone or integrated runs, and models_to_run selects the '
        'models run standalone.',
    )
    parser.add_argument(
        'config_path',
        type=Path,
        nargs='?',
        default=None,
        help='path to the run config file (TOML or JSON)',
    )
    parser.add_argument('--debug', action='store_true', help='run in debug mode')
    return parser.parse_args(argv)


def scale_load(data_root):
    """Scales the base-year load out to every model year.

    Reads in BaseLoad.csv (load for all regions/hours for first year) and LoadScalar.csv (a
    multiplier for all model years). Merges the data and multiplies the load by the scalar to
    generate new load estimates for all model years.

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
    """Scales the base-year load out to every model year using per-enduse scalars.

    Reads in BaseLoad.csv (load for all regions/hours for first year), EnduseBaseShares.csv (the
    shares of demand for each enduse in the base year) and EnduseScalar.csv (a multiplier for all
    model years by enduse category). Merges the data and multiplies the load by the adjusted
    enduse scalar and then sums up to new load estimates for all model years.

    Returns
    -------
    pandas.core.frame.DataFrame
        dataframe that contains load for all regions/years/hours
    """
    tic = datetime.now().astimezone()

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

    toc = datetime.now().astimezone()
    logger.info(f'Time to scale load: {(toc - tic).total_seconds():0.2f} sec')

    return load
