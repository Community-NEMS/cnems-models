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
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# collect all
ALL_VALIDATIONS = []


def validate_seasonal_coverage(
    element_name: str, table: dict[tuple, float], season_idx_loc: int, seasons: Sequence[int | str]
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


def validate_supply_price_coverage(capacity: dict[tuple, float], price: dict[tuple, float]) -> bool:
    """Compare the supply curve (capacity) to the price coverage.

    This test omits the coverage of seasons within the price table, which is inspected separately.
    The season is assumed to occupy the last position of the price index.

    Parameters
    ----------
    capacity : dict[tuple, float]
        Supply curve data keyed by tuple index.
    price : dict[tuple, float]
        Supply price data keyed by the same index plus a trailing season.

    Returns
    -------
    bool
        True if the two tables cover each other exactly, False if price entries exist without a
        matching supply entry (logged as warnings; not fatal).

    Raises
    ------
    ValueError
        If any capacity index has no corresponding price entry.
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


def validate_domestic_network(
    tran_limit: dict[tuple, float], tran_cost: dict[tuple, float]
) -> bool:
    """Validate the domestic network (tran_limit, tran_cost).

    Two checks are made:  neither table may contain a self-loop (a region trading with itself),
    and the two tables must cover the same set of links.  Coverage is compared on
    ``(destination, source, year)`` -- the season carried by ``tran_limit`` is not part of the
    comparison because ``tran_cost`` is not seasonal.  Self-loops are structural, so they
    short-circuit the coverage comparison rather than cascading into it.

    Parameters
    ----------
    tran_limit : dict[tuple, float]
        Transmission limits keyed (destination, source, season, year).
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
    TLI = namedtuple('TranLimitIndex', ('destination', 'source', 'season', 'year'))
    TCI = namedtuple('TranCostIndex', ('destination', 'source', 'year'))
    limit_indices = {TLI(*idx) for idx in tran_limit}
    cost_indices = {TCI(*idx) for idx in tran_cost}

    # self-loops, reported once per offending region rather than once per year/season
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
