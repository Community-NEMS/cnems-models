"""
Created as part of C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/13/26

Module to read structured .csv files and prepare dictionary-based data for model parameter
initialization

"""

import json
import logging
from collections.abc import Iterable, Sequence
from csv import DictReader
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pandas import DataFrame
from pandera.io.pandas_io import from_frictionless_schema
from upath import UPath

from definitions import PROJECT_ROOT
from src.models.electricity.param_source_loader import ParamSource, load_param_sources
from src.models.electricity.property_source_loader import PropertySource, load_property_sources

logger = logging.getLogger(__name__)

# Schema metadata (filename, index columns, value column, required) for each parameter source,
# read from param_sources.toml -- see param_source_loader.py for the ParamSource model.
PARAM_SOURCES: dict[str, ParamSource] = load_param_sources(
    PROJECT_ROOT / 'src/models/electricity/param_sources.toml'
)

# param sources to convert to DF's for further processing.  "Load" handled separately
TIME_BASED_DFS = (
    'cap_cost',
    'cap_factor_vre',
    'h2_price',
    'hydro_cap_factor',
    'supply_curve',
    'supply_price',
    'tran_cost',
    'tran_cost_int',
    'tran_limit',
    'tran_limit_cap_int',
    'tran_limit_gen_int',
)

# Schema metadata (filename, property columns, index columns) for each property source, read
# from property_sources.toml -- see property_source_loader.py for the PropertySource model.
PROPERTY_SOURCES: dict[str, PropertySource] = load_property_sources(
    PROJECT_ROOT / 'src/models/electricity/property_sources.toml'
)

# columns we always want to convert to integer to enable arithmetic on them...
INTEGER_COLS = {'step', 'year', 'hour'}


@dataclass
class FilterPackage:
    """Container to hold the values to use for filtering and the column names they apply to.

    Example:
        region_filter = {'south', 'west'}
        region_cols = ['region', 'region_destination']
        year_filter = {2025, 2042}
        year_col = 'year'

        would allow 'south' and 'west' columns 'region' and 'region_destination' and
        '2025' and '2042' in 'year'
    """

    region_filter: Iterable[str] | Iterable[int] | None = None
    region_cols: Iterable[str] = ('region', 'destination_region', 'source_region')
    year_filter: Iterable[int] | None = None
    year_col: Iterable[str] = ('year',)

    def __post_init__(self):
        """Validate the filter element types and convert the filters to sets for fast lookup."""
        # check for correct types for proper filter functionality
        if self.year_filter and any(isinstance(v, str) for v in self.year_filter):
            raise ValueError('Year filter must be a set of integers')
        # pop items into set for faster lookups
        self.region_filter = set(self.region_filter) if self.region_filter else None
        self.year_filter = set(self.year_filter) if self.year_filter else None


def load_dataframes_w_datapackage(base_path: UPath, filters: FilterPackage | None = None) -> pd.DataFrame:
    """Load dataframes from CSV files and enforce schema.

    This method assumes that ``base_path`` points to a directory (either local or
    remote) that contains a ``datapackage.json`` file and a set of CSVs. It will then
    loop through all of the resources described in the datapackage, check if there
    is a corresponding CSV file for each resource, and read the CSV if it exists.
    While reading CSV files, it will also use pandera to enforce the schema specified
    in the datapackage.

    Optionally, this method can take a ``FilterPackage`` object, which will filter
    the dataframes on a set of region and year columns.
    """
    datapackage = json.loads((base_path / "datapackage.json").read_text())
    dfs = {}
    for resource in datapackage["resources"]:
        # Skip resource if CSV doesn't exist
        csv_path = base_path / resource["path"]
        if not csv_path.exists():
            logger.info(f"{csv_path} does not exist. Skipping!")
            continue

        # Load a pandera schema from the datapackage
        schema = from_frictionless_schema(resource["schema"])
        # Read the CSV and enforce the schema
        df = schema.validate(pd.read_csv(csv_path))

        # Filter on region / year columns
        if filters is not None:
            region_mask = pd.Series(True, index=df.index)
            year_mask = pd.Series(True, index=df.index)
            if filters.region_filter is not None:
                region_cols = list(set(df.columns) & set(filters.region_cols))
                if len(region_cols) > 0:
                    region_mask = df[region_cols].isin(filters.region_filter).all(axis=1)
            if filters.year_filter is not None:
                year_cols = list(set(df.columns) & set(filters.year_col))
                if len(year_cols) > 0:
                    year_mask = df[year_cols].isin(filters.year_filter).all(axis=1)
            df = df[region_mask & year_mask]
        dfs[resource["name"]] = df
    return dfs


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
    df.index = pd.MultiIndex.from_tuples(df.index, names=list(index_names))
    return df.reset_index()


def read_parameter_csv(
    file_path: Path,
    index_cols: Sequence[str],
    value_col: str,
    filter_package: FilterPackage | None = None,
) -> dict[tuple, float]:
    """Read a CSV file into a dictionary mapping index tuples to float values.

    Optional region and year filtering is applied while reading.
    """
    with open(file_path) as f:
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
                        except ValueError as err:
                            logger.error(
                                'Unable to convert %s to int in file %s', row[col_label], file_path
                            )
                            raise ValueError(
                                f'Unable to convert {row[col_label]} to int in file {file_path}'
                            ) from err

            # apply filters, if any
            if filter_package:
                if filter_package.region_filter:
                    discovered_regions = {
                        row.get(col) for col in filter_package.region_cols if row.get(col)
                    }
                    if not discovered_regions.issubset(filter_package.region_filter):
                        continue
                if filter_package.year_filter:
                    discovered_years = {
                        row.get(col) for col in filter_package.year_col if row.get(col)
                    }
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
    logger.info('Read %s entries from %s', len(res), file_path)
    return res


def read_property_csv(
    file_path: Path, param_cols: Iterable[str], index_cols: Iterable[str]
) -> dict[str, list]:
    """Read a 'properties as columns' csv into a dict of indices satisfying each property.

    Assume the first column is the index/reference column and gather those entries in master set.
    For other columns, look for True/true and capture.

    The collected index keys are heterogeneous: a tuple when ``index_cols`` names more than one
    column, otherwise the bare value -- an int for anything in ``INTEGER_COLS``, else a str.
    """
    with open(file_path) as f:
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
                        except ValueError as err:
                            logger.error(
                                'Unable to convert %s to int in file %s', row[col_label], file_path
                            )
                            raise ValueError(
                                f'Unable to convert {row[col_label]} to int in file {file_path}'
                            ) from err

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
                        'Problem converting / comparing value %s of type %s with string '
                        'values in csv file: %s',
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
    """Load every parameter source into a dict of index-tuple -> value maps.

    Sources flagged ``required = false`` in ``param_sources.toml`` are treated as optional:
    if their backing file is absent from ``input_dir`` the source is skipped and mapped to an
    empty dict, letting a reduced input set omit files it does not need. A missing
    ``required = true`` file still raises ``FileNotFoundError``.

    Parameters
    ----------
    input_dir : Path
        Directory holding the parameter CSVs named in ``PARAM_SOURCES``.
    param_filter : FilterPackage, optional
        Region/year filter applied while reading. Defaults to no filtering.

    Returns
    -------
    dict[str, dict[tuple, float]]
        One entry per ``PARAM_SOURCES`` key; empty for absent optional files.
    """
    if not param_filter:
        # make an empty one (no filtering)
        param_filter = FilterPackage()

    res: dict[str, dict[tuple, float]] = {}
    for k, source in PARAM_SOURCES.items():
        file_path = input_dir / source.filename
        if not source.required and not file_path.exists():
            logger.info('Optional param source %s absent (%s); using empty data', k, file_path)
            res[k] = {}
            continue
        res[k] = read_parameter_csv(file_path, source.index_cols, source.value_col, param_filter)
    return res


def load_property_data(input_dir: Path) -> dict[str, dict[str, list[str]]]:
    """Read every source in ``PROPERTY_SOURCES`` from ``input_dir``, keyed by source name.

    Parameters
    ----------
    input_dir : Path
        directory holding the property CSVs named by ``PROPERTY_SOURCES``.

    Returns
    -------
    dict[str, dict[str, list[str]]]
        one entry per property source, mapping each property name to the indices that satisfy it.
    """
    return {
        k: read_property_csv(input_dir / source.filename, source.property_cols, source.index_cols)
        for k, source in PROPERTY_SOURCES.items()
    }


def load_dataframes(
    param_data: dict, names_to_convert: Iterable[str] = TIME_BASED_DFS
) -> dict[str, DataFrame]:
    """Convert param data into Dataframes for the named items listed."""
    res = {}
    for name in names_to_convert:
        if name not in PARAM_SOURCES:
            logger.error(
                'unable to find meta-data for %s in parameter source data.  '
                'Must process independently',
                name,
            )
            raise ValueError(
                f'unable to find meta-data for {name} in parameter source data.  '
                'Must process independently'
            )
        data_dict = param_data.get(name)
        if data_dict is None:
            logger.error('Unable to find data for %s in the parameter data', name)
            raise ValueError(f'Unable to find data for {name}')
        if len(data_dict) == 0:
            logger.warning(
                'No data found for %s in the parameter data.  '
                'May have all been filtered.  Using empty DF',
                name,
            )
            res[name] = pd.DataFrame()
            continue
        res[name] = convert_to_df(
            param_dict=data_dict,
            index_names=PARAM_SOURCES[name].index_cols,
            value_name=PARAM_SOURCES[name].value_col,
        )
    return res


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(message)s')
    logger.debug('Testing the load_param_data and load_property_data functions')
    regions = set('478')
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

    # test new df loader
    regions = {4, 7, 8}
    years = {2025, 2042}
    param_filter = FilterPackage(region_filter=regions, year_filter=years)
    new_dfs = load_dataframes_w_datapackage(
        base_path=UPath("file://input/electricity/cem_inputs"), filters=param_filter
    )
    for name, df in new_dfs.items():
        print(name, '\n', df.head())
