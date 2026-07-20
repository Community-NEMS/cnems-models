"""This file is the main preprocessor for the electricity model.

It established the parameters and sets that will be used in the model. It contains:
 - A class that contains all sets used in the model
 - A collection of support functions to read in and setup parameter data
 - The preprocessor function, which produces an instance of the Set class and a dict of params
 - A collection of support functions to write out the inputs to the output directory

"""

import logging
import os
from pathlib import Path
from warnings import deprecated

import pandas as pd
from pandas import DataFrame

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.utilities import scale_load, scale_load_with_enduses
from src.models.electricity.data_ingestor import FilterPackage, load_dataframes, load_param_data
from src.models.electricity.elec_config import ElecConfig, LoadScaleMode

###################################################################################################
# Setup


logger = logging.getLogger(__name__)

# switch to load data from csvs(0) or from db(1)
# note: this is a future feature, currently not available
db_switch = 0

# if db_switch == 1:
#     from sqlalchemy import create_engine, MetaData, Table, select
#     from sqlalchemy.ext.automap import automap_base
#     from sqlalchemy.orm import sessionmaker

# Establish paths
data_root = Path(PROJECT_ROOT, 'input', 'electricity')


###################################################################################################


###################################################################################################
# functions to read in and setup parameter data


### Load csvs
@deprecated('CSV files are read in using data_ingestor.py')
def readin_csvs(all_frames):
    """Reads in all of the CSV files from the input dir and returns a dictionary of dataframes,
    where the key is the file name and the value is the table data.

    Parameters
    ----------
    all_frames : dictionary
        empty dictionary to be filled with dataframes

    Returns
    -------
    dictionary
        completed dictionary filled with dataframes from the input directory
    """
    csv_dir = Path(data_root, 'cem_inputs')
    for filename in os.listdir(csv_dir):
        f = Path(csv_dir, filename)
        if os.path.isfile(f):
            all_frames[filename[:-4]] = pd.read_csv(f)
    return all_frames


### Load table from SQLite DB
@deprecated('DB reading is currently not supported.')
def readin_sql(all_frames):
    """Reads in all of the tables from a SQL databased and returns a dictionary of dataframes,
    where the key is the table name and the value is the table data.

    Parameters
    ----------
    all_frames : dictionary
        empty dictionary to be filled with dataframes

    Returns
    -------
    dictionary
        completed dictionary filled with dataframes from the input directory
    """
    db_dir = data_root / 'cem_inputs_database.db'
    engine = create_engine('sqlite:///' + db_dir)
    Session = sessionmaker(bind=engine)
    session = Session()

    Base = automap_base()
    Base.prepare(autoload_with=engine)
    metadata = MetaData()
    metadata.reflect(engine)

    for table in metadata.tables.keys():
        all_frames[table] = load_data(table, metadata, engine)
        all_frames[table] = all_frames[table].drop(columns=['id'])

    session.close()

    return all_frames


@deprecated('DB reading is currently not supported.')
def load_data(tablename, metadata, engine):
    """loads the data from the SQL database; used in readin_sql function.

    Parameters
    ----------
    tablename : string
        table name
    metadata : SQL metadata
        SQL metadata
    engine : SQL engine
        SQL engine

    Returns
    -------
    dataframe
        table from SQL db as a dataframe
    """
    table = Table(tablename, metadata, autoload_with=engine)
    query = select(table.c).where()

    with engine.connect() as connection:
        result = connection.execute(query)
        df = pd.read_sql(query, connection)

    return df


def subset_dfs(all_frames, setin, i):
    """filters dataframes based on the values within the set

    Parameters
    ----------
    all_frames : dictionary
        dictionary of dataframes where the key is the file name and the value is the table data
    setin : ModelSets
        contains an initial batch of sets that are used to solve electricity model
    i : string
        name of the set contained within the sets class that the df will be filtered based on.

    Returns
    -------
    dictionary
        completed dictionary filled with dataframes filtered based on set inputs specified
    """
    for key in all_frames:
        if i in all_frames[key].columns:
            all_frames[key] = all_frames[key].loc[all_frames[key][i].isin(getattr(setin, i))]

    return all_frames


def fill_values(row, subset_list):
    """Function to fill in the subset values, is used to assign all years within the year
    solve range to each year the model will solve for.

    Parameters
    ----------
    row : int
        row number in df
    subset_list : list
        list of values to map

    Returns
    -------
    int
        value from subset_list
    """
    if row in subset_list:
        return row
    for i in range(len(subset_list) - 1):
        if subset_list[i] < row < subset_list[i + 1]:
            return subset_list[i + 1]
    return subset_list[-1]


def avg_by_group(df, set_name, map_frame):
    """takes in a dataframe and groups it by the set specified and then averages the data.

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
    # map_df = map_frame.copy()  # this is a mapping of actual year --> summary year
    # df = df.sort_values(by=list(df[:-1]))
    # print(df.tail())

    # location of y column and list of cols needed for the groupby
    pos = df.columns.get_loc(set_name)
    map_name = 'Map_' + set_name
    groupby_cols = list(df.columns[:-1]) + [map_name]
    groupby_cols.remove(set_name)

    # group df by year map data and update y col
    # df[set_name] = df[set_name].astype(int)
    # ^ should not have to convert the merge column data to ints
    df = pd.merge(df, map_frame, how='left', on=[set_name])
    df = df.groupby(by=groupby_cols, as_index=False).mean()
    df[set_name] = df[map_name].astype(int)
    df = df.drop(columns=[map_name]).reset_index(drop=True)

    # move back to original position
    y_col = df.pop(set_name)
    df.insert(pos, set_name, y_col)

    # used to qa
    df = df.sort_values(by=list(df[:-1])).reset_index(drop=True)
    # print(df.tail())

    return df


# add seasons to data without seasons
def add_season_index(cw_temporal, df, pos):
    """adds a season index to the input dataframe

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
    """create temporal mapping parameters

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
    """builds the capacity credit dataframe

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


def create_hourly_params(all_frames, key, cols):
    """Expands params that are indexed by season to be indexed by hour

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


def create_subsets(df, col, subset):
    """Create subsets off of full sets

    Parameters
    ----------
    df : pd.DataFrame
        data frame of full data
    col : str
        column name
    subset : list[str]
        names of values to subset

    Returns
    -------
    pd.DataFrame
        data frame containing subset of full data
    """
    df = df[df[col].isin(subset)].dropna()
    return df


def create_hourly_sets(all_frames, df):
    """expands sets that are indexed by season to be indexed by hour

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    df : pd.DataFrame
        data frame containing seasonal data

    Returns
    -------
    pd.DataFrame
        data frame containing updated hourly set
    """
    df = pd.merge(df, all_frames['MapHourSeason'].reset_index(), on=['season'], how='left').drop(
        columns=['season']
    )
    return df


def hourly_sc_subset(all_frames, subset):
    """Creates sets/subsets that are related to the supply curve

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    subset : list
        list of technologies to subset

    Returns
    -------
    pd.DataFrame
        data frame containing sets/subsets related to supply curve
    """
    df = create_hourly_sets(
        all_frames, create_subsets(all_frames['SupplyCurve'].reset_index(), 'tech', subset)
    )
    return df


def hr_sub_sc_subset(all_frames, T_subset, hr_subset):
    """creates supply curve subsets by hour

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    T_subset : list
        list of technologies to subset
    hr_subset : list
        list of hours to subset

    Returns
    -------
    pd.DataFrame
        data frame containing supply curve related hourly subset
    """
    il = ['tech', 'year', 'region', 'step', 'hour']
    df_index = create_subsets(hourly_sc_subset(all_frames, T_subset), 'hour', hr_subset).set_index(
        il
    )
    return df_index


def step_sub_sc_subset(all_frames, T_subset, step_subset):
    """creates supply curve subsets by step

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


def create_sc_sets(all_frames, setin):
    """creates supply curve sets

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model

    Returns
    -------
    ModelSets
       updated Set containing all sets related to supply curve
    """
    # sets that are related to the supply curve
    index_list = ['tech', 'year', 'region', 'step', 'hour']

    setin.generation_total_index = hourly_sc_subset(all_frames, setin.T_gen).set_index(index_list)
    setin.Storage_index = hourly_sc_subset(all_frames, setin.T_stor).set_index(index_list)
    setin.H2Gen_index = hourly_sc_subset(all_frames, setin.T_h2).set_index(index_list)
    setin.generation_ramp_index = hourly_sc_subset(all_frames, setin.T_conv).set_index(index_list)
    setin.generation_dispatchable_ub_index = hourly_sc_subset(all_frames, setin.T_disp).set_index(
        index_list
    )

    setin.ramp_most_hours_balance_index = hr_sub_sc_subset(all_frames, setin.T_conv, setin.hour23)
    setin.ramp_first_hour_balance_index = hr_sub_sc_subset(all_frames, setin.T_conv, setin.hour1)
    setin.storage_most_hours_balance_index = hr_sub_sc_subset(
        all_frames, setin.T_stor, setin.hour23
    )
    setin.storage_first_hour_balance_index = hr_sub_sc_subset(all_frames, setin.T_stor, setin.hour1)

    setin.generation_hydro_ub_index = create_hourly_sets(
        all_frames, step_sub_sc_subset(all_frames, setin.T_hydro, [2])
    ).set_index(index_list)

    setin.capacity_hydro_ub_index = (
        step_sub_sc_subset(all_frames, setin.T_hydro, [1])
        .drop(columns=['step'])
        .set_index(['tech', 'year', 'region', 'season'])
    )

    setin.reserves_procurement_index = pd.merge(
        create_hourly_sets(all_frames, all_frames['SupplyCurve'].reset_index()),
        pd.DataFrame({'restypes': setin.restypes}),
        how='cross',
    ).set_index(['restypes'] + index_list)

    return setin


def create_other_sets(all_frames, setin):
    """creates other (non-supply curve) sets

    Parameters
    ----------
    all_frames : dict[str, DataFrame]
        dictionary of dataframes where the key is the file name and the value is the table data
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model

    Returns
    -------
    ModelSets
        updated Sets which has non-supply curve-related sets updated
    """
    # other sets
    setin.Build_index = setin.sw_builds[setin.sw_builds['builds'] == 1].set_index(['tech', 'step'])

    setin.capacity_retirements_index = pd.merge(
        all_frames['SupplyCurve']
        .reset_index()
        .drop(columns=['season', 'SupplyCurve'])
        .drop_duplicates(),
        setin.sw_retires[setin.sw_retires['retires'] == 1],
        on=['tech', 'step'],
        how='right',
    ).set_index(['tech', 'year', 'region', 'step'])

    setin.trade_interational_index = (
        pd.merge(
            all_frames['TranLimitGenInt'].reset_index(),
            all_frames['TranLimitCapInt'].reset_index(),
            how='inner',
        )
        .drop(columns=['TranLimitGenInt'])
        .set_index(['region', 'region1', 'year', 'step', 'hour'])
    )

    setin.trade_interregional_index = create_hourly_sets(
        all_frames, all_frames['TranLimit'].reset_index()
    ).set_index(['region', 'region1', 'year', 'hour'])

    return setin


def inner_join_indices(param_1: dict, param_2: dict) -> tuple[int, int]:
    """
    Computes the indices of the non-overlapping keys after performing an inner join operation
    on the keys of two dictionaries.

    The method identifies the common keys between `param_1` and `param_2`, keeps only those
    keys in the original dictionaries, and calculates how many keys are lost in each dictionary
    after the intersection. The result is returned as a tuple containing the count of missing
    keys in `param_1` and `param_2` respectively.

    Parameters
    ----------
    param_1 : dict
        The first dictionary to join on keys.
    param_2 : dict
        The second dictionary to join on keys.

    Returns
    -------
    tuple of int
        A tuple where the first element is the number of keys that are missing
        from `param_1` after the intersection, and the second element is the
        number of keys that are missing from `param_2` after the intersection.
    """
    intersect_keys = param_1.keys() & param_2.keys()
    param_1 = {k: v for k, v in param_1.items() if k in intersect_keys}
    param_2 = {k: v for k, v in param_2.items() if k in intersect_keys}
    return len(intersect_keys - param_1.keys()), len(intersect_keys - param_2.keys())


###################################################################################################
def preprocessor(
    setin: ModelSets, common_config: CommonConfig, elec_config: ElecConfig
) -> tuple[dict[str, DataFrame], dict[str, dict[tuple, float]], ModelSets]:
    """main preprocessor function that generates the final dataframes and sets sent over to the
    electricity model. This function reads in the input data, modifies it based on the temporal
    and regional mapping specified in the inputs, and gets it into the final formatting needed.
    Also adds some additional regional sets to the set class based on parameter inputs.

    Parameters
    ----------
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model

    Returns
    -------
    all_frames : dictionary
        dictionary of dataframes where the key is the file name and the value is the table data
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model
    """

    # READ IN INPUT DATA
    # TODO:  For now, we will handle some params separately and leave some in "all_frames".  Merge
    #        them later...?

    # read in parameter data from csv's
    param_filter = FilterPackage(
        region_filter=elec_config.region_filter, year_filter=common_config.summary_years
    )
    param_data = load_param_data(input_dir=elec_config.input_path, param_filter=param_filter)
    logger.info('Read in %d parameter elements', len(param_data))

    # load the time-based parameters into data frames for processing
    all_frames: dict[str, DataFrame] = load_dataframes(param_data=param_data)

    # if db_switch == 0:
    #     # add csv input files to all frames
    #     all_frames = readin_csvs(all_frames)
    # elif db_switch == 1:
    #     # add sql db tables to all frames
    #     all_frames = readin_sql(all_frames)

    # read in load data from residential input directory
    res_dir = Path(PROJECT_ROOT, 'input', 'residential')
    if elec_config.load_scale_mode == LoadScaleMode.ANNUAL:
        all_frames['Load'] = scale_load(res_dir).reset_index(drop=True)
    elif elec_config.load_scale_mode == LoadScaleMode.END_USE:
        all_frames['Load'] = scale_load_with_enduses(res_dir).reset_index(drop=True)
    else:
        raise NotImplementedError(f'load_scale_mode {elec_config.load_scale_mode} not implemented')

    # REGIONALIZE DATA

    # dev note:  the set-jockeying below should not be needed.  We have already filtered
    #            the domestic regions
    # international trade sets
    # r_file = all_frames['TranLimitCapInt'][['region', 'region1']].drop_duplicates()
    # r_file = r_file[r_file['region'].isin(setin.region)]
    # setin.region_int_trade = list(r_file['region'].unique())
    # setin.region_int = list(r_file['region1'].unique())
    # setin.region1 = setin.region + setin.region_int

    # dev note:  below is already done during read-in for DOMESTIC regions.  INTL shouldn't matter
    # subset df by region
    # all_frames = subset_dfs(all_frames, setin, 'region')
    # all_frames = subset_dfs(all_frames, setin, 'region1')

    # unk why this would be needed...
    # setin.region_trade = all_frames['TranLimit']['region'].unique()

    # TEMPORALIZE DATA

    # create temporal mapping df
    cw_temporal = setin.cw_temporal

    # year weights
    all_frames['WeightYear'] = pd.DataFrame.from_dict(
        data=setin.year_agg_weights, orient='index'
    )  # setin.WeightYear

    # dev_note:  cap cost is year-filtered upon read-in
    # last year values used
    # filter_list = ['CapCost']
    # for key in filter_list:
    #     all_frames[key] = all_frames[key].loc[
    #         all_frames[key]['year'].isin(getattr(setin, 'years'))
    #     ]

    # average values in years/hours used
    for key in all_frames.keys():
        if 'year' in all_frames[key].columns:
            all_frames[key] = avg_by_group(all_frames[key], 'year', setin.year_map)
        if 'hour' in all_frames[key].columns:
            all_frames[key] = avg_by_group(
                all_frames[key], 'hour', cw_temporal[['hour', 'Map_hour']]
            )

    all_frames['MapDaySeason'] = time_map(cw_temporal, {'Map_day': 'day', 'Map_s': 'season'})
    all_frames['MapHourDay'] = time_map(cw_temporal, {'Map_hour': 'hour', 'Map_day': 'day'})
    all_frames['MapHourSeason'] = time_map(cw_temporal, {'Map_hour': 'hour', 'Map_s': 'season'})
    all_frames['WeightHour'] = time_map(
        cw_temporal, {'Map_hour': 'hour', 'WeightHour': 'WeightHour'}
    )
    all_frames['WeightDay'] = time_map(cw_temporal, {'Map_day': 'day', 'WeightDay': 'WeightDay'})

    # weights per season
    all_frames['WeightSeason'] = cw_temporal[
        ['Map_s', 'Map_hour', 'WeightDay', 'WeightHour']
    ].drop_duplicates()
    all_frames['WeightSeason'].loc[:, 'WeightSeason'] = (
        all_frames['WeightSeason']['WeightDay'] * all_frames['WeightSeason']['WeightHour']
    )
    all_frames['WeightSeason'] = (
        all_frames['WeightSeason']
        .drop(columns=['WeightDay', 'WeightHour', 'Map_hour'])
        .groupby(['Map_s'])
        .agg('sum')
        .reset_index()
        .rename(columns={'Map_s': 'season'})
    )

    # TODO:  come back and look at this.  Shouldn't need to cross years with this unless the data
    #        is different year-to-year, which it *currently* is not
    # using same T_vre capacity factor for all model years and reordering columns
    all_frames['CapFactorVRE'] = pd.merge(
        all_frames['CapFactorVRE'], pd.DataFrame({'year': common_config.summary_years}), how='cross'
    )
    all_frames['CapFactorVRE'] = all_frames['CapFactorVRE'][
        ['tech', 'year', 'region', 'step', 'hour', 'CapFactorVRE']
    ]

    # TODO:  revisit this.  We should be able to use the group-by/average to do this, but sum.
    # Update load to be the total demand in each time segment rather than the average
    all_frames['Load'] = pd.merge(
        all_frames['Load'], all_frames['WeightHour'], how='left', on=['hour']
    )
    all_frames['Load']['Load'] = all_frames['Load']['Load'] * all_frames['Load']['WeightHour']
    all_frames['Load'] = all_frames['Load'].drop(columns=['WeightHour'])

    # TODO:  Revisit this.  Should not need to augment season data here.  Review formulation
    # add seasons to data without seasons
    all_frames['SupplyCurve'] = add_season_index(cw_temporal, all_frames['SupplyCurve'], 1)

    @deprecated('should not be required with proper data')
    def price_MWh_to_GWh(dic, names: list[str]):
        """changing units of prices to all be in $/GWh so obj is $

        Parameters
        ----------
        dic : dict[str, DataFrame]s
            all_frames, main dictionary containing all inputs
        names : list[str]
            names of price tables

        Returns
        -------
        dict[str, DataFrame]s
            the original dict of data frames with updated price units
        """
        for name in names:
            dic[name].loc[:, name] = dic[name][name] * 1000
        return dic

    # TODO:  This should NOT be needed in model.  Needs to be standardized in data.  Flag for
    #        removal.
    all_frames = price_MWh_to_GWh(
        all_frames,
        [
            'SupplyPrice',
            'TranCost',
            'TranCostInt',
            'RegReservesCost',
            'RampUpCost',
            'RampDownCost',
            'CapCost',
            'CapCostInitial',
        ],
    )

    # Recalculate Supply Curve Learning

    # TODO:  Review this.  seems arbitrary and we could pull this data more cleanly?
    # save first year of supply curve summer capacity for learning
    all_frames['SupplyCurveLearning'] = all_frames['SupplyCurve'][
        (all_frames['SupplyCurve']['year'] == setin.start_year)
        & (all_frames['SupplyCurve']['season'] == 2)  # TODO:  remove hard-coded season
    ]

    # set up first year capacity for learning.
    all_frames['SupplyCurveLearning'] = (
        pd.merge(all_frames['SupplyCurveLearning'], all_frames['CapCost'], how='outer')
        .drop(columns=['season', 'year', 'CapCost', 'region', 'step'])
        .rename(columns={'SupplyCurve': 'SupplyCurveLearning'})
        .groupby(['tech'])
        .agg('sum')
        .reset_index()
    )

    # if cap = 0, set to minimum unit size (0.1 for now)
    all_frames['SupplyCurveLearning'].loc[
        all_frames['SupplyCurveLearning']['SupplyCurveLearning'] == 0.0, 'SupplyCurveLearning'
    ] = 0.01

    all_frames['CapacityCredit'] = capacitycredit_df(all_frames, setin)

    # expand a few parameters to be hourly
    TLCI_cols = ['region', 'region1', 'year', 'hour', 'TranLimitCapInt']
    TLGI_cols = ['region1', 'step', 'year', 'hour', 'TranLimitGenInt']
    all_frames['TranLimitCapInt'] = create_hourly_params(all_frames, 'TranLimitCapInt', TLCI_cols)
    all_frames['TranLimitGenInt'] = create_hourly_params(all_frames, 'TranLimitGenInt', TLGI_cols)

    # TODO:  Review this.  Should not need to arbitrarily do this, I don't think.
    # sets the index for all df in dict
    for key in all_frames:
        index = list(all_frames[key].columns[:-1])
        all_frames[key] = all_frames[key].set_index(index)

    # create more indices for the model
    # TODO:  These should not be needed.  We're just creating sets by crossing techs with the
    #        associated params, etc.
    # setin = create_sc_sets(all_frames, setin)
    # setin = create_other_sets(all_frames, setin)

    return all_frames, param_data, setin


###################################################################################################
# Review Inputs


def makedir(dir_out):
    """creates a folder directory based on the path provided

    Parameters
    ----------
    dir_out : str
        path of directory
    """
    if not os.path.exists(dir_out):
        os.makedirs(dir_out)


def output_inputs(OUTPUT_ROOT):
    """function developed initial for QA purposes, writes out to csv all of the dfs and sets passed
    to the electricity model to an output directory.

    Parameters
    ----------
    OUTPUT_ROOT : str
        path of output directory

    Returns
    -------
    all_frames : dictionary
        dictionary of dataframes where the key is the file name and the value is the table data
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model
    """
    output_path = Path(OUTPUT_ROOT, 'electricity_inputs/')
    makedir(output_path)

    years = list(pd.read_csv(PROJECT_ROOT / 'src/integrator/input/sw_year.csv').dropna()['year'])
    regions = list(pd.read_csv(PROJECT_ROOT / 'src/integrator/input/sw_reg.csv').dropna()['region'])

    # Build sets used for model
    all_frames = {}
    setA = ModelSets(years, regions)

    # creates the initial data
    all_frames, setB = preprocessor(setA)
    for key in all_frames:
        # print(key, list(all_frames[key].reset_index().columns))
        fname = key + '.csv'
        all_frames[key].to_csv(output_path / fname)

    return all_frames, setB


def print_sets(setin):
    """function developed initially for QA purposes, prints out all of the sets passed to the
    electricity model.

    Parameters
    ----------
    setin : ModelSets
        an initial batch of sets that are used to solve electricity model
    """
    set_list = dir(setin)
    set_list = sorted([x for x in set_list if '__' not in x])
    for item in set_list:
        if isinstance(getattr(setin, item), pd.DataFrame):
            print(item, ':', getattr(setin, item).reset_index().columns)
        else:
            print(item, ':', getattr(setin, item))


# all_frames, setB = output_inputs(PROJECT_ROOT)
# print_sets(setB)
