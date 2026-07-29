"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/29/26

A collection of utility functions for manipulating dataframes to support parameter
construction.  Largely drawn from elements of the old "preprocessor.py" file

"""

from warnings import deprecated

import pandas as pd
from pandas import DataFrame


def avg_by_group(df, set_name, map_frame):
    """Takes in a dataframe and groups it by the set specified and then averages the data.

    Parameters
    ----------
    df : dataframe
        parameter data to be modified
    set_name : str
        name of the column/set to average the data by
    map_frame : dataframe
        data that maps the set name to the new grouping for that set

    Returns
    -------
    dataframe
        parameter data that is averaged by specified set mapping
    """
    # location of y column and list of cols needed for the groupby
    pos = df.columns.get_loc(set_name)
    map_name = 'Map_' + set_name
    # check that the "map_frame" is compatible
    if map_name not in map_frame.columns:
        raise ValueError(f'The mapping dataframe does not contain the column: {map_name}')

    groupby_cols = list(df.columns[:-1]) + [map_name]
    groupby_cols.remove(set_name)

    # group df by year map data and update y col

    df = pd.merge(df, map_frame, how='left', on=[set_name])
    df = df.groupby(by=groupby_cols, as_index=False).mean()
    df[set_name] = df[map_name]
    df = df.drop(columns=[map_name]).reset_index(drop=True)

    # move back to original position
    y_col = df.pop(set_name)
    df.insert(pos, set_name, y_col)

    # sort by every index column (i.e. all but the trailing value column)
    df = df.sort_values(by=list(df.columns[:-1])).reset_index(drop=True)

    return df


def add_season_index(cw_temporal, df, pos):
    """Adds a season index to the input dataframe.

    Parameters
    ----------
    cw_temporal : dataframe
        dataframe that includes the season index
    df : dataframe
        parameter data to be modified
    pos : int
        column position for the seasonal set

    Returns
    -------
    dataframe
        modified parameter data now indexed by season
    """
    df_s = cw_temporal[['Map_s']].copy().rename(columns={'Map_s': 'season'}).drop_duplicates()
    df = pd.merge(df, df_s, how='cross')
    s_col = df.pop('season')
    df.insert(pos, 'season', s_col)

    return df


def time_map(cw_temporal, rename_cols):
    """Create temporal mapping parameters.

    Parameters
    ----------
    cw_temporal : pd.DataFrame
        temporal crosswalks
    rename_cols : dict
        columns to rename from/to

    Returns
    -------
    pd.DataFrame
        data frame with temporal mapping parameters
    """
    df = cw_temporal[list(rename_cols.keys())].rename(columns=rename_cols).drop_duplicates()
    return df


def capacitycredit_df(all_frames: dict[str, DataFrame], setin):
    """Builds the capacity credit dataframe.

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model

    Returns
    -------
    pd.DataFrame
        formatted capacity credit data frame
    """
    df = pd.merge(
        all_frames['SupplyCurve'], all_frames['MapHourSeason'], on=['season'], how='left'
    ).drop(columns=['season'])

    # capacity credit is hourly capacity factor for vre technologies
    df = pd.merge(
        df, all_frames['CapFactorVRE'], how='left', on=['tech', 'year', 'region', 'step', 'hour']
    ).rename(columns={'CapFactorVRE': 'CapacityCredit'})

    # capacity credit = 1 for dispatchable technologies
    df['CapacityCredit'] = df['CapacityCredit'].fillna(1)

    # capacity credit is seasonal limit for hydro
    df2 = pd.merge(
        all_frames['HydroCapFactor'],
        all_frames['MapHourSeason'],
        on=['season'],
        how='left',
    ).drop(columns=['season'])
    df2['tech'] = setin.T_hydro[0]
    df = pd.merge(df, df2, how='left', on=['tech', 'region', 'hour'])
    df.loc[df['tech'].isin(setin.T_hydro), 'CapacityCredit'] = df['HydroCapFactor']
    df = df.drop(columns=['SupplyCurve', 'HydroCapFactor'])
    df = df[['tech', 'year', 'region', 'step', 'hour', 'CapacityCredit']]
    return df


@deprecated('Should not be needed.  Future refactoring will remove this function.')
def create_hourly_params(all_frames, key, cols):
    """Expands params that are indexed by season to be indexed by hour.

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    key : str
        name of data frame to access
    cols : list[str]
        column names to keep in data frame

    Returns
    -------
    pd.DataFrame
        data frame with name key with new hourly index
    """
    df = pd.merge(all_frames[key], all_frames['MapHourSeason'], on=['season'], how='left').drop(
        columns=['season']
    )
    df = df[cols]
    return df


def create_subsets(df: DataFrame, col: str, name_filter: list[str]):
    """Create subsets off of full sets.

    Parameters
    ----------
    df : pd.DataFrame
        data frame of full data
    col : str
        column name
    name_filter : list[str]
        names of values accept from the column

    Returns
    -------
    pd.DataFrame
        data frame containing subset of full data
    """
    df = df[df[col].isin(name_filter)].dropna()
    return df


def step_sub_sc_subset(all_frames, T_subset, step_subset):
    """Creates supply curve subsets by step.

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    T_subset : list
       technologies to subset
    step_subset : list
        step numbers to subset

    Returns
    -------
    pd.DataFrame
        data frame containing supply curve subsets by step
    """
    df = create_subsets(
        create_subsets(all_frames['SupplyCurve'].reset_index(), 'tech', T_subset),
        'step',
        step_subset,
    )
    return df
