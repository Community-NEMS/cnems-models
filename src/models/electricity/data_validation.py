"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/31/26

A set of independent functions that can be used to validate raw data used in the electricity
model.  Validations can be self-contained to a data table or may compare 2 tables for
coverage gaps, etc.  Design is to look at the raw, ingested data before some of it is
expanded to multiple years, etc. to catch inconsistencies early.

Validations should be able to run independently or in concert and report warnings to log
file and/or halt on showstoppers

"""

import logging
from collections import defaultdict, namedtuple
from collections.abc import Iterable, Sequence
from typing import Literal

from pandas import DataFrame

from src.common.exceptions import DataValidationError
from src.common.season import Season
from src.models.electricity.elec_config import ExpansionLearningType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData

logger = logging.getLogger(__name__)

# ParamFrames keys indexed by season, and those ParamData has expanded from season to hour
SeasonalParam = Literal['generation_cost', 'hydro_capacity_factor']
HourlyParam = Literal['tran_limit', 'tran_limit_cap_intl', 'supply_limit_intl']


def validate_all(model_sets: ModelSets, param_data: ParamData, strict: bool = True) -> None:
    """Run all validations on the parameter data.

    Every validation is run (fatal ``ValueError``s are caught and logged) so that a single pass
    reports all problems before halting.  Transmission tables are only validated when
    ``regional_exchange`` is enabled.

    Parameters
    ----------
    model_sets : ModelSets
        Built model sets; supplies the expected seasons and hours.
    param_data : ParamData
        Fully constructed parameter data to validate.
    strict : bool
        True => raise on validation failure; False => log it and continue

    Raises
    ------
    DataValidationError
        If any validation failed and ``strict`` is set.
    """
    all_valid = True
    frames = param_data.param_frames
    exchange = param_data.elec_config.regional_exchange

    # region coverage
    all_valid &= validate_region_sets(
        model_sets.region,
        domestic=model_sets.region_domestic,
        international=model_sets.region_international,
    )

    # seasonal coverage in tables with season reference
    seasonal_params: list[SeasonalParam] = ['generation_cost', 'hydro_capacity_factor']
    for name in seasonal_params:
        df = frames[name]
        if df.empty:
            continue
        all_valid &= validate_seasonal_coverage(
            name, _frame_to_dict(df), df.index.names.index('season'), model_sets.season
        )

    # hourly coverage for the params ParamData expanded from season to hour
    if exchange:
        hourly_params: list[HourlyParam] = [
            'tran_limit',
            'tran_limit_cap_intl',
            'supply_limit_intl',
        ]
        for name in hourly_params:
            df = frames[name]
            if df.empty:
                continue
            all_valid &= validate_hourly_coverage(
                name, _frame_to_dict(df), df.index.names.index('hour'), model_sets.hour
            )

    # available capacity vs. generation cost to ensure the two tables cover each other
    try:
        all_valid &= validate_generation_cost_coverage(
            _frame_to_dict(frames['available_capacity']), _frame_to_dict(frames['generation_cost'])
        )
    except ValueError as e:
        logger.error(e)
        all_valid = False

    # domestic transmission network to exclude self loops and ensure compatible cost-limit
    # coverage
    if exchange:
        try:
            all_valid &= validate_domestic_network(
                _frame_to_dict(frames['tran_limit']), _frame_to_dict(frames['tran_cost'])
            )
        except ValueError as e:
            logger.error(e)
            all_valid = False

    # build costs must cover everything buildable in every analyzed region (and year)
    if param_data.elec_config.capacity_expansion:
        all_valid &= validate_build_cost_coverage(
            'capital_cost',
            _frame_to_dict(frames['capital_cost']),
            model_sets.region_analyze,
            model_sets.tech_builds,
            set(model_sets.year_map.values()),
        )
        if param_data.elec_config.expansion_learning_type is not ExpansionLearningType.DISABLED:
            all_valid &= validate_build_cost_coverage(
                'capital_cost_initial',
                param_data.param_dicts['capital_cost_initial'],
                model_sets.region_analyze,
                model_sets.tech_builds,
            )

    if not all_valid:
        message = 'Data validation failed.  See log for details.'
        if strict:
            raise DataValidationError(message)
        logger.error('%s  Continuing, since strict validation is off.', message)


def _frame_to_dict(df: DataFrame) -> dict[tuple, float]:
    """Dump a single-value-column param frame to a tuple-keyed dict (empty frame -> {})."""
    if df.empty:
        return {}
    return {k if isinstance(k, tuple) else (k,): v for k, v in df.iloc[:, 0].items()}


def validate_region_sets(
    all_regions: Iterable[str], domestic: Iterable[str], international: Iterable[str]
) -> bool:
    """Validate the region sets non-overlap and coverage."""
    all_regions = set(all_regions)
    domestic = set(domestic)
    international = set(international)
    dual_marked = domestic & international
    if dual_marked:
        logger.error('Regions marked as both domestic and international: %s', dual_marked)
        return False
    non_declared = all_regions - domestic - international
    if non_declared:
        logger.error('Regions not marked as either domestic or international: %s', non_declared)
        return False
    return True


def validate_seasonal_coverage(
    element_name: str, table: dict[tuple, float], season_idx_loc: int, seasons: Sequence[Season]
) -> bool:
    """Validate full seasonal coverage for table data.

    Group the table index by everything *except* the season position and ensure that each
    resulting base index carries exactly the expected set of seasons -- no gaps, no strays.

    Parameters
    ----------
    element_name : str
        Name of the data element, used for logging only.
    table : dict[tuple, float]
        Raw ingested data keyed by tuple index.
    season_idx_loc : int
        Position of the season within the index tuple.
    seasons : Sequence[int | str]
        The expected seasons.

    Returns
    -------
    bool
        True if every base index covers exactly the expected seasons, False otherwise.  Failures
        are logged as errors, one per offending base index.
    """
    # We'll group by all indices except season and count the season entries for each
    counter_dict = defaultdict(list)
    for idx in table:
        reduced_idx = idx[0:season_idx_loc] + idx[season_idx_loc + 1 :]
        season = idx[season_idx_loc]
        counter_dict[reduced_idx].append(season)

    # now review the entries for consistent "season count"
    expected_seasons = set(seasons)
    valid = True
    for idx, found_seasons in counter_dict.items():
        # ensure all seasons are represented
        if set(found_seasons) != expected_seasons:
            valid = False
            logger.error(
                'Seasonal entries in %s for base index %s do not cover expected season list %s',
                element_name,
                idx,
                seasons,
            )

    return valid


def validate_hourly_coverage(
    element_name: str, table: dict[tuple, float], hour_idx_loc: int, hours: Sequence[int]
) -> bool:
    """Validate full hourly coverage for table data.

    Hourly counterpart of :func:`validate_seasonal_coverage`, for params that ``ParamData`` has
    expanded from season to hour.  Group the table index by everything *except* the hour position
    and ensure that each resulting base index carries exactly the expected set of hours.  Errors
    list the missing and unexpected hours rather than the (long) full expected set.

    Parameters
    ----------
    element_name : str
        Name of the data element, used for logging only.
    table : dict[tuple, float]
        Data keyed by tuple index.
    hour_idx_loc : int
        Position of the hour within the index tuple.
    hours : Sequence[int]
        The expected hours, e.g. ``ModelSets.hour``.

    Returns
    -------
    bool
        True if every base index covers exactly the expected hours, False otherwise.  Failures
        are logged as errors, one per offending base index.
    """
    # group by all indices except hour and collect the hours found for each
    found_hours: defaultdict[tuple, set[int]] = defaultdict(set)
    for idx in table:
        found_hours[idx[:hour_idx_loc] + idx[hour_idx_loc + 1 :]].add(idx[hour_idx_loc])

    expected_hours = set(hours)
    valid = True
    for idx, found in found_hours.items():
        if found != expected_hours:
            valid = False
            logger.error(
                'Hourly entries in %s for base index %s: missing hours %s, unexpected hours %s',
                element_name,
                idx,
                sorted(expected_hours - found),
                sorted(found - expected_hours),
            )

    return valid


def validate_generation_cost_coverage(
    capacity: dict[tuple, float], price: dict[tuple, float]
) -> bool:
    """Compare the ``available_capacity`` coverage to the ``generation_cost`` coverage.

    This test omits the coverage of seasons within the cost table, which is inspected separately.
    The season is assumed to occupy the last position of the cost index.

    Parameters
    ----------
    capacity : dict[tuple, float]
        ``available_capacity`` data keyed by tuple index.
    price : dict[tuple, float]
        ``generation_cost`` data keyed by the same index plus a trailing season.

    Returns
    -------
    bool
        True if the two tables cover each other exactly, False if cost entries exist without a
        matching capacity entry (logged as warnings; not fatal).

    Raises
    ------
    ValueError
        If any capacity index has no corresponding cost entry.
    """
    valid = True
    price_coverage = {idx[:-1] for idx in price}  # trim "season" off
    capacity_omissions = price_coverage - capacity.keys()
    if capacity_omissions:
        valid = False
        for omission in capacity_omissions:
            logger.warning('Price exists for %s without corresponding entry in supply:', omission)

    price_omissions = capacity.keys() - price_coverage
    if price_omissions:
        for omission in price_omissions:
            logger.error(
                'Capacity exists for %s without corresponding entry in price data:', omission
            )
        raise ValueError('Capacity without corresponding price.  See log for details.')

    return valid


def validate_build_cost_coverage(
    element_name: str,
    costs: dict[tuple, float],
    regions: Iterable,
    buildable: Iterable,
    years: Iterable[int] | None = None,
) -> bool:
    """Validate that a build-cost table covers every buildable (tech, step) in every region.

    The expected keys are ``regions x buildable`` (``x years`` when given).  Extra cost entries
    (e.g. for non-buildable techs) are allowed; missing ones are not, because ``capacity_builds``
    is indexed from the costs while the capacity constraints reach it through ``tech_buildable``.

    Parameters
    ----------
    element_name : str
        Name of the cost table, used for logging only.
    costs : dict[tuple, float]
        Cost data keyed (region, tech, step[, year]).
    regions : Iterable
        The analyzed regions, e.g. ``ModelSets.region_analyze``.
    buildable : Iterable
        Buildable (tech, step) pairs, e.g. ``ModelSets.tech_builds``.
    years : Iterable[int] | None
        Model years when the table is year-indexed (``capital_cost``); None for
        ``capital_cost_initial``.

    Returns
    -------
    bool
        True if every expected key is present, False otherwise.  Each missing (region, tech, step)
        is logged as an error, with the missing years where the table is year-indexed.
    """
    base = {(r, tech, step) for r in regions for tech, step in buildable}
    if years is None:  # screening for initial costs
        missing = sorted(base - costs.keys())
        for idx in missing:
            logger.error('Missing %s for buildable index %s', element_name, idx)
        expected = len(base)
    else:  # screening for yearly costs
        year_set = set(years)
        found_years: defaultdict[tuple, set[int]] = defaultdict(set)
        for idx in costs:
            found_years[idx[:3]].add(idx[3])
        missing = sorted(idx for idx in base if not year_set <= found_years[idx])
        for idx in missing:
            logger.error(
                'Missing %s for buildable index %s in years %s',
                element_name,
                idx,
                sorted(year_set - found_years[idx]),
            )
        expected = len(base) * len(year_set)

    # with full coverage, anything beyond the expected count is an (allowed) extra
    extra = len(costs) - expected
    if not missing and extra > 0:
        logger.debug('%s has %d entries beyond the buildable set (ignored)', element_name, extra)
    return not missing


def validate_domestic_network(
    tran_limit: dict[tuple, float], tran_cost: dict[tuple, float]
) -> bool:
    """Validate the domestic network (tran_limit, tran_cost).

    Two checks are made:  neither table may contain a self-loop (a region trading with itself),
    and the two tables must cover the same set of links.  Coverage is compared on
    ``(destination, source, year)`` -- the hour carried by ``tran_limit`` is not part of the
    comparison because ``tran_cost`` is not time-sliced.  Self-loops are structural, so they
    short-circuit the coverage comparison rather than cascading into it.

    Parameters
    ----------
    tran_limit : dict[tuple, float]
        Transmission limits keyed (destination, source, year, hour), as expanded by ``ParamData``.
    tran_cost : dict[tuple, float]
        Transmission costs keyed (destination, source, year).

    Returns
    -------
    bool
        True if the network is loop-free and the two tables cover each other exactly, False if a
        self-loop or a Trans Cost without a matching Trans Limit was found (logged as warnings;
        not fatal).

    Raises
    ------
    ValueError
        If any (destination, source, year) carries a Trans Limit with no corresponding Trans
        Cost.  Every offending index is logged as an error before the raise.
    """
    TLI = namedtuple('TranLimitIndex', ('destination', 'source', 'year', 'hour'))
    TCI = namedtuple('TranCostIndex', ('destination', 'source', 'year'))
    limit_indices = {TLI(*idx) for idx in tran_limit}
    cost_indices = {TCI(*idx) for idx in tran_cost}

    # self-loops, reported once per offending region rather than once per year/hour
    limit_loops = {idx.source for idx in limit_indices if idx.source == idx.destination}
    for region in limit_loops:
        logger.warning('Self-loop in Trans Limit data for region %s in some years', region)
    cost_loops = {idx.source for idx in cost_indices if idx.source == idx.destination}
    for region in cost_loops:
        logger.warning('Self-loop in Trans Cost data for region %s in some years', region)
    if limit_loops or cost_loops:
        return False

    # check for unequal coverage, comparing on (destination, source, year)
    valid = True
    limit_coverage = {TCI(idx.destination, idx.source, idx.year) for idx in limit_indices}
    missing_cost = limit_coverage - cost_indices
    missing_limit = cost_indices - limit_coverage
    for idx in missing_limit:
        valid = False
        logger.warning('Missing limit data for index: %s which has a Trans Cost', tuple(idx))
    if missing_cost:
        for idx in missing_cost:
            logger.error('Missing cost data for index: %s which has a Trans Limit', tuple(idx))
        raise ValueError(
            'Missing cost data for indices which have a Trans Limit.  See log for details.'
        )

    return valid
