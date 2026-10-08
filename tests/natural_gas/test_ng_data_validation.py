"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/6/26

Tests for src.models.natural_gas.data_validation

The validators report by logging and returning a bool, so the assertions inspect both the return
value and the ``caplog`` records.  Curve data is mocked with small dicts mirroring the loaded
shapes.
"""

import copy
import logging
import math

import pytest

from src.models.natural_gas.data_validation import (
    validate_lng_demand_curve,
    validate_lng_export,
    validate_pipeline_arcs,
    validate_qmin_fraction,
    validate_supply_anchors,
    validate_supply_curve_shape,
    validate_tariff_curve_shape,
)

VALIDATION_LOGGER = 'src.models.natural_gas.data_validation'

SUPPLY_SHAPE = {
    'crv_below': [0.30, 0.15, 0.05],
    'crv_above': [0.05, 0.15, 0.30],
    'elas': [0.8, 0.7, 0.5, 0.3, 0.2],
}
TARIFF_SHAPE = {
    'util_break': [0.0, 0.2, 0.6, 0.8, 0.95, 1.0, 1.4],
    'tariff_mult': [0.4, 0.55, 0.75, 0.95, 1.5, 3.0, 3.5],
}
LNG_SHAPE: dict[str, list[float] | float] = {
    'q_frac': [0.0, 0.5, 0.85, 1.0],
    'p_factor': [2.0, 1.5, 1.1, 1.0],
    'world_price': 7.0,
    'max_factor': 2.0,
}


@pytest.fixture
def validation_log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture records from the data_validation logger at DEBUG and above."""
    caplog.set_level(logging.DEBUG, logger=VALIDATION_LOGGER)
    return caplog


def _errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Error records emitted by the validation logger."""
    return [r for r in caplog.records if r.name == VALIDATION_LOGGER and r.levelno == logging.ERROR]


# ---------------------------------------------------------------------------
# validate_supply_curve_shape
# ---------------------------------------------------------------------------


def test_supply_curve_shape_valid(validation_log: pytest.LogCaptureFixture) -> None:
    """A well-formed supply shape passes with no errors."""
    assert validate_supply_curve_shape(SUPPLY_SHAPE)
    assert not _errors(validation_log)


@pytest.mark.parametrize(
    'key,pos,bad,fragment',
    [
        ('crv_below', 0, 0.0, 'crv_below step 1'),
        ('crv_below', 2, 1.0, 'crv_below step 3'),
        ('crv_below', 1, -0.1, 'crv_below step 2'),
        ('crv_above', 1, 0.0, 'crv_above step 2'),
        ('elas', 4, 0.0, 'elas segment 5'),
        ('crv_below', 0, 0.9, 'would be zero or negative'),  # 0.9 / 0.8 >= 1
    ],
    ids=['below-zero', 'below-one', 'below-negative', 'above-zero', 'elas-zero', 'price-negative'],
)
def test_supply_curve_shape_invalid(
    validation_log: pytest.LogCaptureFixture, key: str, pos: int, bad: float, fragment: str
) -> None:
    """Each out-of-range shape value fails and is named in the log."""
    shape = copy.deepcopy(SUPPLY_SHAPE)
    shape[key][pos] = bad
    assert not validate_supply_curve_shape(shape)
    assert any(fragment in r.getMessage() for r in _errors(validation_log))


# ---------------------------------------------------------------------------
# validate_qmin_fraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'qmin_fraction,expected',
    [(0.0, True), (0.20, True), (0.80, True), (0.8075, False), (0.9, False), (-0.1, False)],
)
def test_qmin_fraction(
    validation_log: pytest.LogCaptureFixture, qmin_fraction: float, expected: bool
) -> None:
    """QMIN must sit in [0, QBASE_2/Q0); QBASE_2/Q0 = 0.85 * 0.95 = 0.8075 for the defaults."""
    assert validate_qmin_fraction(qmin_fraction, SUPPLY_SHAPE['crv_below']) is expected
    assert bool(_errors(validation_log)) is not expected


# ---------------------------------------------------------------------------
# validate_supply_anchors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'tiers,anchors',
    [
        ({'a': [(10.0, 2.0), (5.0, 4.0)]}, {}),
        ({'a': [(0.0, 2.0), (0.0, 4.0)]}, {}),
        ({'a': [(10.0, 2.0)]}, {('a', 2030): (0.0, 1.0)}),
    ],
    ids=['positive', 'zero-capacity', 'zero-q0-mult'],
)
def test_supply_anchors_valid(
    validation_log: pytest.LogCaptureFixture,
    tiers: dict[str, list[tuple[float, float]]],
    anchors: dict[tuple[str, int], tuple[float, float]],
) -> None:
    """Positive Q0, or Q0 = 0 for a region with no production, passes with no P0 complaint."""
    assert validate_supply_anchors(tiers, anchors, ['a'], [2025, 2030])
    assert not _errors(validation_log)


@pytest.mark.parametrize(
    'tiers,anchors,fragment',
    [
        ({'a': [(10.0, 2.0), (-5.0, 3.0)]}, {}, 'tier capacities [-5.0]'),
        ({'a': [(math.inf, 2.0)]}, {}, 'tier capacities [inf]'),
        ({'a': [(10.0, 2.0)]}, {('a', 2030): (-1.0, 1.0)}, 'Q0 for (a, 2030)'),
        ({'a': [(10.0, 2.0)]}, {('a', 2030): (math.nan, 1.0)}, 'Q0 for (a, 2030)'),
        ({'a': [(10.0, 0.0)]}, {}, 'P0 for (a, 2025)'),
        ({'a': [(10.0, 2.0)]}, {('a', 2025): (1.0, 0.0)}, 'P0 for (a, 2025)'),
        ({}, {}, 'no supply cost tiers'),
    ],
    ids=[
        'negative-tier',
        'inf-tier',
        'negative-q0-mult',
        'nan-q0-mult',
        'zero-cost',
        'zero-p0-mult',
        'missing-region',
    ],
)
def test_supply_anchors_invalid(
    validation_log: pytest.LogCaptureFixture,
    tiers: dict[str, list[tuple[float, float]]],
    anchors: dict[tuple[str, int], tuple[float, float]],
    fragment: str,
) -> None:
    """Bad capacity, Q0, P0 (where Q0 > 0) or a region with no tiers fails and is logged."""
    assert not validate_supply_anchors(tiers, anchors, ['a'], [2025, 2030])
    assert any(fragment in r.getMessage() for r in _errors(validation_log))


# ---------------------------------------------------------------------------
# validate_tariff_curve_shape / validate_pipeline_arcs
# ---------------------------------------------------------------------------


def test_tariff_curve_shape_valid(validation_log: pytest.LogCaptureFixture) -> None:
    """A well-formed tariff shape passes with no errors."""
    assert validate_tariff_curve_shape(TARIFF_SHAPE)
    assert not _errors(validation_log)


@pytest.mark.parametrize(
    'key,pos,bad,fragment',
    [
        ('util_break', 2, 0.2, 'not strictly increasing'),
        ('util_break', 0, -0.1, 'starts at'),
        ('tariff_mult', 3, 0.5, 'not non-decreasing'),
    ],
    ids=['repeated-break', 'negative-start', 'falling-tariff'],
)
def test_tariff_curve_shape_invalid(
    validation_log: pytest.LogCaptureFixture, key: str, pos: int, bad: float, fragment: str
) -> None:
    """A repeated or negative breakpoint, or a falling tariff, fails."""
    shape = copy.deepcopy(TARIFF_SHAPE)
    shape[key][pos] = bad
    assert not validate_tariff_curve_shape(shape)
    assert any(fragment in r.getMessage() for r in _errors(validation_log))


@pytest.mark.parametrize(
    'capacity,tariff,expected',
    [(100.0, 0.5, True), (100.0, 0.0, True), (0.0, 0.5, False), (100.0, -0.1, False)],
)
def test_pipeline_arcs(
    validation_log: pytest.LogCaptureFixture, capacity: float, tariff: float, expected: bool
) -> None:
    """Arc capacity must be > 0 and tariff >= 0."""
    assert validate_pipeline_arcs([('a', 'b', capacity, tariff)]) is expected
    assert bool(_errors(validation_log)) is not expected


# ---------------------------------------------------------------------------
# validate_lng_demand_curve / validate_lng_export
# ---------------------------------------------------------------------------


def test_lng_demand_curve_valid(validation_log: pytest.LogCaptureFixture) -> None:
    """A well-formed LNG demand curve passes with no errors."""
    assert validate_lng_demand_curve(LNG_SHAPE)
    assert not _errors(validation_log)


@pytest.mark.parametrize(
    'key,pos,bad,fragment',
    [
        ('q_frac', 2, 0.5, 'not strictly increasing'),
        ('q_frac', 0, -0.1, 'starts at'),
        ('p_factor', 2, 1.8, 'not non-increasing'),
        ('p_factor', 3, -0.5, 'negative entry'),
        ('world_price', None, 0.0, 'lng_world_price_per_mmbtu'),
    ],
    ids=['repeated-q', 'negative-q-start', 'rising-price', 'negative-price', 'zero-world-price'],
)
def test_lng_demand_curve_invalid(
    validation_log: pytest.LogCaptureFixture,
    key: str,
    pos: int | None,
    bad: float,
    fragment: str,
) -> None:
    """A repeated or negative q_frac, a rising or negative price, or no world price fails."""
    shape = copy.deepcopy(LNG_SHAPE)
    if pos is None:
        shape[key] = bad
    else:
        values = shape[key]
        assert isinstance(values, list)
        values[pos] = bad
    assert not validate_lng_demand_curve(shape)
    assert any(fragment in r.getMessage() for r in _errors(validation_log))


@pytest.mark.parametrize('bcf,expected', [(100.0, True), (0.0, True), (-1.0, False)])
def test_lng_export(validation_log: pytest.LogCaptureFixture, bcf: float, expected: bool) -> None:
    """Zero LNG export capacity is a valid state; negative is not."""
    assert validate_lng_export({'a': {2025: 50.0, 2030: bcf}}) is expected
    assert bool(_errors(validation_log)) is not expected
