"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/31/26

Tests for src.models.electricity.data_validation

The validators report by logging (and, for showstoppers, by raising), so the assertions here
inspect ``caplog`` records rather than return values.  Table data is mocked with small dicts
mirroring the shape of the ingested params (tuple index -> float).
"""

import logging

import pytest

from src.models.electricity.data_validation import (
    validate_domestic_network,
    validate_hourly_coverage,
    validate_seasonal_coverage,
    validate_supply_price_coverage,
)

VALIDATION_LOGGER = 'src.models.electricity.data_validation'

SEASONS = ('winter', 'spring', 'summer', 'fall')
HOURS = (1, 2, 3, 4)


@pytest.fixture
def validation_log(caplog):
    """Capture records from the data_validation logger at DEBUG and above."""
    caplog.set_level(logging.DEBUG, logger=VALIDATION_LOGGER)
    return caplog


def _seasonal_table(base_indices, seasons, season_idx_loc):
    """Build a {index: value} table by splicing `seasons` into `base_indices` at the given spot."""
    return {
        base[:season_idx_loc] + (season,) + base[season_idx_loc:]: 1.0
        for base in base_indices
        for season in seasons
    }


# ---------------------------------------------------------------------------
# validate_seasonal_coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'base_indices, season_idx_loc',
    [
        pytest.param([('CA', 'NG', 1)], 3, id='season-last'),
        pytest.param([('CA', 'NG', 1)], 0, id='season-first'),
        pytest.param([('CA', 'NG', 1)], 2, id='season-middle'),
        pytest.param([()], 0, id='season-only-index'),
        pytest.param([('CA', 'NG', 1), ('CA', 'Coal', 1), ('TX', 'NG', 2)], 3, id='multi-index'),
    ],
)
def test_seasonal_coverage_complete(validation_log, base_indices, season_idx_loc):
    """Full coverage at any season position, for any number of base indices, logs nothing."""
    table = _seasonal_table(base_indices, SEASONS, season_idx_loc)

    assert validate_seasonal_coverage('supply_price', table, season_idx_loc, SEASONS) is True

    assert validation_log.records == []


def test_seasonal_coverage_accepts_unordered_and_repeated_expectation(validation_log):
    """Expected seasons are compared as a set: order and duplicates in the list don't matter."""
    table = _seasonal_table([('CA', 'NG', 1)], SEASONS, 3)

    unordered = ['fall', 'winter', 'summer', 'spring']
    assert validate_seasonal_coverage('supply_price', table, 3, unordered) is True
    assert validate_seasonal_coverage('supply_price', table, 3, list(SEASONS) + ['winter']) is True

    assert validation_log.records == []


def test_seasonal_coverage_empty_table_is_silent(validation_log):
    """An empty table has no base indices to check, so it is vacuously valid."""
    assert validate_seasonal_coverage('supply_price', {}, 3, SEASONS) is True

    assert validation_log.records == []


@pytest.mark.parametrize(
    'table_seasons, expected_seasons, reason',
    [
        pytest.param(('winter', 'spring', 'summer'), SEASONS, 'missing', id='missing-season'),
        pytest.param(('winter',), SEASONS, 'missing', id='single-season-only'),
        pytest.param(SEASONS + ('shoulder',), SEASONS, 'unexpected', id='extra-unexpected-season'),
        pytest.param(('mud', 'road'), SEASONS, 'disjoint', id='wholly-different-seasons'),
    ],
)
def test_seasonal_coverage_incomplete(validation_log, table_seasons, expected_seasons, reason):
    """Any mismatch between found and expected seasons is False and logs one error."""
    table = _seasonal_table([('CA', 'NG', 1)], table_seasons, 3)

    assert validate_seasonal_coverage('supply_price', table, 3, expected_seasons) is False

    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(errors) == 1, f'expected a single error for the {reason} case'
    assert 'supply_price' in errors[0].getMessage()
    assert "('CA', 'NG', 1)" in errors[0].getMessage()


def test_seasonal_coverage_reports_only_the_bad_indices(validation_log):
    """One bad base index among good ones makes the whole table invalid, but only it is logged."""
    table = _seasonal_table([('CA', 'NG', 1), ('TX', 'NG', 1)], SEASONS, 3)
    # strip two seasons from a third index and one from a fourth
    table.update(_seasonal_table([('NY', 'NG', 1)], ('winter', 'spring'), 3))
    table.update(_seasonal_table([('FL', 'NG', 1)], SEASONS[:-1], 3))

    assert validate_seasonal_coverage('supply_price', table, 3, SEASONS) is False

    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(errors) == 2
    messages = ' '.join(r.getMessage() for r in errors)
    assert "('NY', 'NG', 1)" in messages
    assert "('FL', 'NG', 1)" in messages
    assert 'CA' not in messages
    assert 'TX' not in messages


def test_seasonal_coverage_integer_seasons(validation_log):
    """Seasons may be ints as well as strings."""
    complete = _seasonal_table([('CA', 'NG', 1)], (1, 2, 3, 4), 3)
    assert validate_seasonal_coverage('supply_price', complete, 3, (1, 2, 3, 4)) is True
    assert validation_log.records == []

    incomplete = _seasonal_table([('CA', 'NG', 1)], (1, 2, 3), 3)
    assert validate_seasonal_coverage('supply_price', incomplete, 3, (1, 2, 3, 4)) is False
    assert len([r for r in validation_log.records if r.levelno == logging.ERROR]) == 1


# ---------------------------------------------------------------------------
# validate_hourly_coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'base_indices, hour_idx_loc',
    [
        pytest.param([('CA', 'TX', 2030)], 3, id='hour-last'),
        pytest.param([('CA', 'TX', 2030)], 0, id='hour-first'),
        pytest.param([('CA', 'TX', 2030)], 2, id='hour-middle'),
        pytest.param(
            [('CA', 'TX', 2030), ('TX', 'CA', 2030), ('TX', 'NY', 2040)], 3, id='multi-index'
        ),
    ],
)
def test_hourly_coverage_complete(validation_log, base_indices, hour_idx_loc):
    """Full coverage at any hour position, for any number of base indices, logs nothing."""
    table = _seasonal_table(base_indices, HOURS, hour_idx_loc)

    assert validate_hourly_coverage('tran_limit', table, hour_idx_loc, HOURS) is True

    assert validation_log.records == []


def test_hourly_coverage_accepts_range_expectation(validation_log):
    """Expected hours may be any sequence, e.g. the ``range`` held in ``ModelSets.hour``."""
    table = _seasonal_table([('CA', 'TX', 2030)], HOURS, 3)

    assert validate_hourly_coverage('tran_limit', table, 3, range(1, 5)) is True

    assert validation_log.records == []


def test_hourly_coverage_empty_table_is_silent(validation_log):
    """An empty table has no base indices to check, so it is vacuously valid."""
    assert validate_hourly_coverage('tran_limit', {}, 3, HOURS) is True

    assert validation_log.records == []


@pytest.mark.parametrize(
    'table_hours, missing, unexpected',
    [
        pytest.param((1, 2, 3), [4], [], id='missing-hour'),
        pytest.param((1,), [2, 3, 4], [], id='single-hour-only'),
        pytest.param(HOURS + (5,), [], [5], id='extra-unexpected-hour'),
        pytest.param((7, 8), [1, 2, 3, 4], [7, 8], id='wholly-different-hours'),
    ],
)
def test_hourly_coverage_incomplete(validation_log, table_hours, missing, unexpected):
    """Any mismatch is False and logs one error naming the base index and the hour differences."""
    table = _seasonal_table([('CA', 'TX', 2030)], table_hours, 3)

    assert validate_hourly_coverage('tran_limit', table, 3, HOURS) is False

    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert 'tran_limit' in message
    assert "('CA', 'TX', 2030)" in message
    assert f'missing hours {missing}' in message
    assert f'unexpected hours {unexpected}' in message


def test_hourly_coverage_reports_only_the_bad_indices(validation_log):
    """One bad base index among good ones makes the whole table invalid, but only it is logged."""
    table = _seasonal_table([('CA', 'TX', 2030), ('TX', 'CA', 2030)], HOURS, 3)
    table.update(_seasonal_table([('TX', 'NY', 2030)], (1, 2), 3))

    assert validate_hourly_coverage('tran_limit', table, 3, HOURS) is False

    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "('TX', 'NY', 2030)" in errors[0].getMessage()


# ---------------------------------------------------------------------------
# validate_supply_price_coverage
# ---------------------------------------------------------------------------


@pytest.fixture
def capacity_table() -> dict[tuple, float]:
    """A small supply curve keyed (region, tech, step, year)."""
    return {
        ('CA', 'NG', 1, 2030): 100.0,
        ('CA', 'NG', 2, 2030): 50.0,
        ('TX', 'Coal', 1, 2030): 75.0,
    }


def _price_table(indices, seasons=SEASONS) -> dict[tuple, float]:
    """Expand capacity-shaped indices into a price table with a trailing season."""
    return {idx + (season,): 10.0 for idx in indices for season in seasons}


def test_supply_price_coverage_exact_match(validation_log, capacity_table):
    """Matching coverage is valid: nothing raised, nothing logged."""
    price = _price_table(capacity_table)

    assert validate_supply_price_coverage(capacity_table, price) is True

    assert validation_log.records == []


def test_supply_price_coverage_single_season_price(validation_log, capacity_table):
    """Season count in the price table is irrelevant here -- only the trimmed index matters."""
    price = _price_table(capacity_table, seasons=('winter',))

    assert validate_supply_price_coverage(capacity_table, price) is True

    assert validation_log.records == []


def test_supply_price_coverage_both_empty(validation_log):
    """Empty inputs are trivially consistent."""
    assert validate_supply_price_coverage({}, {}) is True

    assert validation_log.records == []


def test_supply_price_extra_price_warns_only(validation_log, capacity_table):
    """Price without capacity is invalid but not a showstopper: False, warning, no raise."""
    price = _price_table(capacity_table)
    price.update(_price_table([('NY', 'NG', 1, 2030)]))

    assert validate_supply_price_coverage(capacity_table, price) is False

    warnings = [r for r in validation_log.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "('NY', 'NG', 1, 2030)" in warnings[0].getMessage()
    assert not [r for r in validation_log.records if r.levelno >= logging.ERROR]


def test_supply_price_extra_price_warns_once_per_omission(validation_log, capacity_table):
    """Each uncovered price index gets its own warning, de-duplicated across seasons."""
    price = _price_table(capacity_table)
    price.update(_price_table([('NY', 'NG', 1, 2030), ('NY', 'NG', 2, 2030)]))

    assert validate_supply_price_coverage(capacity_table, price) is False

    warnings = [r for r in validation_log.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


def test_supply_price_missing_price_raises(validation_log, capacity_table):
    """Capacity without price is fatal: it raises rather than returning a validity flag."""
    price = _price_table([idx for idx in capacity_table if idx[0] != 'TX'])

    with pytest.raises(ValueError, match='Capacity without corresponding price'):
        validate_supply_price_coverage(capacity_table, price)

    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "('TX', 'Coal', 1, 2030)" in errors[0].getMessage()


def test_supply_price_empty_price_raises(validation_log, capacity_table):
    """An entirely missing price table reports every capacity index before raising."""
    with pytest.raises(ValueError, match='Capacity without corresponding price'):
        validate_supply_price_coverage(capacity_table, {})

    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(errors) == len(capacity_table)


def test_supply_price_both_directions_missing(validation_log, capacity_table):
    """Warnings for surplus price are emitted before the missing-price ValueError."""
    price = _price_table([idx for idx in capacity_table if idx[0] != 'TX'])
    price.update(_price_table([('NY', 'NG', 1, 2030)]))

    with pytest.raises(ValueError, match='Capacity without corresponding price'):
        validate_supply_price_coverage(capacity_table, price)

    warnings = [r for r in validation_log.records if r.levelno == logging.WARNING]
    errors = [r for r in validation_log.records if r.levelno == logging.ERROR]
    assert len(warnings) == 1
    assert "('NY', 'NG', 1, 2030)" in warnings[0].getMessage()
    assert len(errors) == 1
    assert "('TX', 'Coal', 1, 2030)" in errors[0].getMessage()


# ---------------------------------------------------------------------------
# validate_domestic_network
# ---------------------------------------------------------------------------

YEARS = (2030, 2040)

# a small symmetric domestic network:  CA <-> TX <-> NY
LINKS = (('CA', 'TX'), ('TX', 'CA'), ('TX', 'NY'), ('NY', 'TX'))


def _network_tables(
    links=LINKS, hours=HOURS, years=YEARS
) -> tuple[dict[tuple, float], dict[tuple, float]]:
    """Build a consistent (tran_limit, tran_cost) pair for the given (destination, source) links.

    ``tran_limit`` is keyed (destination, source, year, hour); ``tran_cost`` drops the hour.
    """
    limit = {
        (dest, src, year, hour): 5.0 for dest, src in links for year in years for hour in hours
    }
    cost = {(dest, src, year): 2.0 for dest, src in links for year in years}
    return limit, cost


def _add_limit_self_loop(limit: dict, cost: dict) -> None:
    """Give CA a transmission limit to itself."""
    limit[('CA', 'CA', YEARS[0], HOURS[0])] = 5.0


def _add_cost_self_loop(limit: dict, cost: dict) -> None:
    """Give CA a transmission cost to itself."""
    cost[('CA', 'CA', YEARS[0])] = 2.0


def _drop_cost_link(limit: dict, cost: dict) -> None:
    """Strip the NY <- TX link out of the cost table, leaving its limits stranded."""
    for idx in [idx for idx in cost if idx[:2] == ('NY', 'TX')]:
        del cost[idx]


def _drop_limit_link(limit: dict, cost: dict) -> None:
    """Strip the NY <- TX link out of the limit table, leaving its costs stranded."""
    for idx in [idx for idx in limit if idx[:2] == ('NY', 'TX')]:
        del limit[idx]


@pytest.mark.parametrize(
    'mutate, expected, expect_level, expect_message',
    [
        pytest.param(None, True, None, None, id='good'),
        pytest.param(
            _add_limit_self_loop,
            False,
            logging.WARNING,
            'Self-loop in Trans Limit',
            id='self-loop-limit',
        ),
        pytest.param(
            _add_cost_self_loop,
            False,
            logging.WARNING,
            'Self-loop in Trans Cost',
            id='self-loop-cost',
        ),
        pytest.param(
            _drop_limit_link,
            False,
            logging.WARNING,
            'Missing limit data',
            id='missing-limit',
        ),
        pytest.param(
            _drop_cost_link,
            ValueError,
            logging.ERROR,
            'Missing cost data',
            id='missing-cost',
        ),
    ],
)
def test_domestic_network(validation_log, mutate, expected, expect_level, expect_message):
    """A matched, loop-free network is silent.

    A self-loop, or a cost stranded without a limit, is reported as a warning and returns False.
    A limit with no corresponding cost is a showstopper: it logs an error and raises.  ``expected``
    is either the expected return value or the exception type the case should raise.
    """
    limit, cost = _network_tables()
    if mutate is not None:
        mutate(limit, cost)

    if isinstance(expected, bool):
        assert validate_domestic_network(limit, cost) is expected
    else:
        with pytest.raises(expected, match=expect_message):
            validate_domestic_network(limit, cost)

    if expect_message is None:
        assert validation_log.records == []
    else:
        hits = [r for r in validation_log.records if r.levelno == expect_level]
        assert hits, f'expected at least one record at level {expect_level}'
        assert expect_message in ' '.join(r.getMessage() for r in hits)
