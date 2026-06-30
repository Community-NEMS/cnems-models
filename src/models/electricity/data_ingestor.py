"""
Created as part of C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/13/26

Module to read structured .csv files and prepare dictionary-based data for model parameter
initialization

"""

import logging
from collections.abc import Iterable, Sequence
from csv import DictReader
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from definitions import PROJECT_ROOT

logger = logging.getLogger(__name__)

# TODO:  There is some inconsistency in the column ordering here, but it is captured as-is for now
#        to align with the model.  This code will work fine if the columns are re-sequenced here and
#        it will render the index in the order given here
PARAM_SOURCES = {
    # key: (Filename, columns to index, value)
    'battery_efficiency': ('BatteryEfficiency.csv', ('tech',), 'BatteryEfficiency'),
    'cap_cost': ('CapCost.csv', ('region', 'tech', 'year', 'step'), 'CapCost'),
    'cap_cost_initial': ('CapCostInitial.csv', ('region', 'tech', 'step'), 'CapCostInitial'),
    'cap_factor_vre': ('CapFactorVRE.csv', ('tech', 'region', 'step', 'hour'), 'CapFactorVRE'),
    'fom_cost': ('FOMCost.csv', ('region', 'tech', 'step'), 'FOMCost'),
    'h2_price': ('H2Price.csv', ('region', 'season', 'tech', 'step', 'year'), 'H2Price'),
    'hours_to_buy': ('HourstoBuy.csv', ('tech',), 'HourstoBuy'),
    'hydro_cap_factor': ('HydroCapFactor.csv', ('region', 'season'), 'HydroCapFactor'),
    'learning_rate': ('LearningRate.csv', ('tech',), 'LearningRate'),
    'ramp_down_cost': ('RampDownCost.csv', ('tech',), 'RampDownCost'),
    'ramp_rate': ('RampRate.csv', ('tech',), 'RampRate'),
    'ramp_up_cost': ('RampUpCost.csv', ('tech',), 'RampUpCost'),
    'reg_reserves_cost': ('RegReservesCost.csv', ('tech',), 'RegReservesCost'),
    'reserve_margin': ('ReserveMargin.csv', ('region',), 'ReserveMargin'),
    'res_tech_upper_bound': ('ResTechUpperBound.csv', ('restype', 'tech'), 'ResTechUpperBound'),
    'supply_curve': ('SupplyCurve.csv', ('region', 'tech', 'step', 'year'), 'SupplyCurve'),
    'supply_curve_learning': ('SupplyCurveLearning.csv', ('tech',), 'SupplyCurveLearning'),
    'supply_price': (
        'SupplyPrice.csv',
        ('region', 'season', 'tech', 'step', 'year'),
        'SupplyPrice',
    ),
    'tran_cost': ('TranCost.csv', ('source_region', 'destination_region', 'year'), 'TranCost'),
    'tran_cost_int': (
        'TranCostInt.csv',
        ('region', 'region_international', 'step', 'year'),
        'TranCostInt',
    ),
    'tran_limit': (
        'TranLimit.csv',
        ('source_region', 'destination_region', 'season', 'year'),
        'TranLimit',
    ),
    'tran_limit_cap_int': (
        'TranLimitCapInt.csv',
        ('region', 'region_international', 'year', 'season'),
        'TranLimitCapInt',
    ),
    'tran_limit_gen_int': (
        'TranLimitGenInt.csv',
        ('region_international', 'step', 'year', 'season'),
        'TranLimitGenInt',
    ),
}

# param sources to convert to DF's for further processing.  "Load" handled separately
TIME_BASED_DFS = (
    'cap_cost',
    'cap_factor_vre',
    'h2_price',
    'supply_curve',
    'supply_price',
    'tran_cost',
    'tran_cost_int',
    'tran_limit',
    'tran_limit_cap_int',
    'tran_limit_gen_int',
)

PROPERTY_SOURCES = {
    # Filename, columns to property-ize, index/basis cols
    'tech_data': (
        'tech_data.csv',
        'tech,T_conv,T_re,T_hydro,T_stor,T_vre,T_wind,T_solar,T_h2,T_disp,T_gen'.split(','),
        ('tech',),
    ),
    'buildable_techs': ('build_data.csv', ['builds'], ('tech', 'step')),
    'retireable_techs': ('retire_data.csv', ['retires'], ('tech', 'step')),
    'region_data': ('region_data.csv', ['region', 'domestic', 'international'], ('region',)),
}

# columns we always want to convert to integer to enable arithmetic on them...
INTEGER_COLS = {'step', 'year', 'hour'}


@dataclass
class FilterPackage:
    """Container to hold the values to use for filtering and the column names they apply to
    Example:
        region_filter = {'south', 'west'}
        region_cols = ['region', 'region_destination']
        year_filter = {2025, 2042}
        year_col = 'year'

        would allow 'south' and 'west' columns 'region' and 'region_destination' and
        '2025' and '2042' in 'year'
    """

    region_filter: Iterable[str] | None = None
    region_cols: Iterable[str] = ('region', 'destination_region', 'source_region')
    year_filter: Iterable[int] | None = None
    year_col: Iterable[str] = ('year',)

    def __post_init__(self):
        # check for correct types for proper filter functionality
        if any(isinstance(v, str) for v in self.year_filter):
            raise ValueError('Year filter must be a set of integers')
        if any(isinstance(v, int) for v in self.region_filter):
            raise ValueError('Region filter must be a set of strings')
        # pop items into set for faster lookups
        self.region_filter = set(self.region_filter) if self.region_filter else None
        self.year_filter = set(self.year_filter) if self.year_filter else None


def convert_to_df(param_dict: dict, index_names: Sequence[str], value_name: str) -> DataFrame:
    """
    Convert a nested dictionary into a Pandas DataFrame with a multi-level index.

    This function takes a dictionary with tuple keys, converts it into a DataFrame,
    and appropriately flattens the tuple keys into a MultiIndex. The resulting
    DataFrame also contains a specified column for the values from the input
    dictionary and resets its index for a flat structure.

    Parameters
    ----------
    param_dict : dict
        A dictionary where keys are tuples representing multi-level index values,
        and the values are the corresponding data that will populate the DataFrame.
    index_names : Sequence[str]
        A sequence of strings representing the names of the index levels in the
        resulting DataFrame.
    value_name : str
        A string representing the name of the column that will store the data
        values from the input dictionary.

    Returns
    -------
    DataFrame
        A Pandas DataFrame where the tuple keys of the input dictionary have been
        transformed into a MultiIndex, and the values have been added as a single
        column identified by `value_name`. The DataFrame index is reset, resulting
        in an integer-based default index.
    """
    df = pd.DataFrame.from_dict(param_dict, orient='index', columns=[value_name])

    # Convert tuple index to MultiIndex to flatten tuples and then reset & return
    df.index = pd.MultiIndex.from_tuples(df.index, names=index_names)
    return df.reset_index()


def read_parameter_csv(
    file_path: Path,
    index_cols: Sequence[str],
    value_col: str,
    filter_package: FilterPackage | None = None,
) -> dict[tuple, float]:
    """
    Reads a CSV file and processes its content into a dictionary mapping index tuples
    to float values while applying optional region and year filtering.

    """
    with open(file_path, 'r') as f:
        res = {}
        reader = DictReader(f)
        flag_floats = False
        for row in reader:
            # convert integer columns to int
            for col_label in INTEGER_COLS:
                if col_label in row:
                    try:
                        row[col_label] = int(row[col_label])
                    except ValueError:
                        try:
                            row[col_label] = int(float(row[col_label]))
                            flag_floats = True
                        except ValueError:
                            logger.error(
                                'Unable to convert %s to int in file %s', row[col_label], file_path
                            )
                            raise (
                                ValueError(
                                    f'Unable to convert {row[col_label]} to int in file {file_path}'
                                )
                            )

            # apply filters, if any
            if filter_package:
                if filter_package.region_filter:
                    discovered_regions = set(
                        row.get(col) for col in filter_package.region_cols if row.get(col)
                    )
                    if not discovered_regions.issubset(filter_package.region_filter):
                        continue
                if filter_package.year_filter:
                    discovered_years = set(
                        row.get(col) for col in filter_package.year_col if row.get(col)
                    )
                    if not discovered_years.issubset(filter_package.year_filter):
                        continue

            # make an index tuple
            try:
                idx = tuple(row[col] for col in index_cols)
            except KeyError:
                logger.error('Expecting index columns %s in csv file: %s', index_cols, file_path)
                raise

            # capture the value
            try:
                value = float(row[value_col])
            except ValueError:
                logger.error(
                    'Expecting float value in column %s in csv file: %s', value_col, file_path
                )
                raise
            res[idx] = value
    if flag_floats:
        logger.warning('Converted floats to ints in %s', file_path)
    return res


def read_property_csv(
    file_path: Path, param_cols: Iterable[str], index_cols: Iterable[str]
) -> dict[str, list[str]]:
    """read csv file that has 'properties as columns' and some index columns and convert
    to dictionary of indices that satisfy each property (pivot)


    Assume the first column is the index/reference column and gather those entries in master set.
    For other columns, look for True/true and capture
    """

    with open(file_path, 'r') as f:
        reader = DictReader(f)
        flag_floats = False
        # make set of dictionaries to catch the "True" vals
        res = {label: [] for label in param_cols}
        for row in reader:
            # convert integer columns to int
            for col_label in INTEGER_COLS:
                if col_label in row:
                    try:
                        row[col_label] = int(row[col_label])
                    except ValueError:
                        try:
                            row[col_label] = int(float(row[col_label]))
                            flag_floats = True
                        except ValueError:
                            logger.error(
                                'Unable to convert %s to int in file %s', row[col_label], file_path
                            )
                            raise (
                                ValueError(
                                    f'Unable to convert {row[col_label]} to int in file {file_path}'
                                )
                            )

            # grab index
            try:
                idx = tuple(row[col] for col in index_cols)
                # de-tuple any singletons
                if len(idx) == 1:
                    idx = idx[0]
            except KeyError:
                logger.error('Expecting index columns %s in csv file: %s', index_cols, file_path)
                raise
            for col_label in param_cols:
                # look for "true" value in each property column, if found, add to that collection
                # also capture if the column name is in the index set to create master set(s)
                try:
                    token = row[col_label].lower()
                    if token == 'true' or col_label in index_cols:
                        res[col_label].append(idx)
                except KeyError:
                    logger.error(
                        'Expecting parameter column %s in csv file: %s', col_label, file_path
                    )
                    raise
                except AttributeError:
                    logger.error(
                        'Problem converting / comparing value %s of type %s with string values in csv file: %s',
                        row[col_label],
                        type(row[col_label]),
                        file_path,
                    )
                    raise
    if flag_floats:
        logger.warning('Converted floats to ints in %s', file_path)
    return res


def load_param_data(
    input_dir: Path,
    param_filter: FilterPackage | None = None,
) -> dict[str, dict[tuple, float]]:
    if not param_filter:
        # make an empty one (no filtering)
        param_filter = FilterPackage()

    return dict(
        (k, read_parameter_csv(input_dir / file_path, index_cols, value_col, param_filter))
        for k, (file_path, index_cols, value_col) in PARAM_SOURCES.items()
    )


def load_property_data(input_dir: Path) -> dict[str, dict[str, list[str]]]:
    return dict(
        (k, read_property_csv(input_dir / filename, prop_cols, idx_cols))
        for k, (filename, prop_cols, idx_cols) in PROPERTY_SOURCES.items()
    )


def load_dataframes(
    param_data: dict, names_to_convert: Iterable[str] = TIME_BASED_DFS
) -> dict[str, DataFrame]:
    """Convert param data into Dataframes for the named items listed"""
    res = {}
    for name in names_to_convert:
        if name not in PARAM_SOURCES:
            logger.error(
                'unable to find meta-data for %s in parameter source data.  Must process independently',
                name,
            )
            raise ValueError(
                f'unable to find meta-data for {name} in parameter source data.  Must process independently'
            )
        data_dict = param_data.get(name)
        if data_dict is None:
            logger.error('Unable to find data for %s in the parameter data', name)
            raise ValueError(f'Unable to find data for {name}')
        if len(data_dict) == 0:
            logger.warning(
                'No data found for %s in the parameter data.  May have all been filtered.  Using empty DF',
                name,
            )
            res[name] = pd.DataFrame()
            continue
        res[name] = convert_to_df(
            param_dict=data_dict,
            index_names=PARAM_SOURCES[name][1],
            value_name=PARAM_SOURCES[name][2],
        )
    return res


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')
    logger.debug('Testing the load_param_data and load_property_data functions')
    regions = set(list('478'))
    years = {2025, 2042}
    param_filter = FilterPackage(region_filter=regions, year_filter=years)
    source_dir = PROJECT_ROOT / 'input/electricity/cem_inputs'
    param_data = load_param_data(source_dir, param_filter)
    for k, v in param_data.items():
        print(k, len(v))
        print(v)
    source_dir = PROJECT_ROOT / 'input/electricity'
    print('\n*** property data ***\n')
    data = load_property_data(source_dir)
    for k, v in data.items():
        print(k)
        for k2, v2 in v.items():
            print('  ', k2, len(v2))

    tran_cost = param_data['tran_cost']

    # make df conversions
    dfs = load_dataframes(param_data)
    for name, df in dfs.items():
        print(name, '\n', df.head())
