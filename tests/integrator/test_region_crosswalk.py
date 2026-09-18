"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/17/26

Tests for the generic regional crosswalk between models.

"""

import logging

import pandas as pd
import pytest

from src.common.models_modes import ModelType
from src.integrator.region_crosswalk import (
    REGION_COLUMN,
    REGION_CROSSWALKS,
    VALUE_INDEX,
    WEIGHT_COLUMN,
    QuantityKind,
    crosswalk_values,
    load_region_weights,
)

LOGGER_NAME = 'src.integrator.region_crosswalk'

# the nine census divisions the natural gas model uses as regions
NG_REGIONS = [
    'new_england',
    'middle_atlantic',
    'east_north_central',
    'west_north_central',
    'south_atlantic',
    'east_south_central',
    'west_south_central',
    'mountain',
    'pacific',
]
YEAR = 2030


def unit_series(ng_region: str, year: int = YEAR) -> pd.Series:
    """A price of 1.0 in ``ng_region`` and 0.0 in every other natural gas region."""
    index = pd.MultiIndex.from_tuples([(r, year) for r in NG_REGIONS], names=VALUE_INDEX)
    return pd.Series(
        [1.0 if r == ng_region else 0.0 for r in NG_REGIONS], index=index, name='price'
    )


def by_elec_region(result: pd.Series) -> dict[str, float]:
    """Flatten a one-year crosswalk result to ``{elec_region: value}``."""
    return {region: val for (region, _year), val in result.items()}


@pytest.mark.parametrize(
    'pair', list(REGION_CROSSWALKS), ids=[f'{s.value}->{d.value}' for s, d in REGION_CROSSWALKS]
)
def test_weights_sum_to_one_per_source(pair: tuple[ModelType, ModelType]) -> None:
    """
    Test of all registered cross-walk files.

    Every registered file allocates each source region fully, with string region ids.
    """
    source, destination = pair
    weights = load_region_weights(source, destination)
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]

    sums = weights.groupby(src_col)[WEIGHT_COLUMN].sum()
    assert sums.to_numpy() == pytest.approx(1.0, abs=1e-3)
    assert weights[src_col].map(type).eq(str).all()
    assert weights[dst_col].map(type).eq(str).all()


@pytest.mark.parametrize('ng_region', ['middle_atlantic', 'new_england', 'mountain'])
def test_unit_price_spreads_by_destination_weights(ng_region: str) -> None:
    """A unit price in one gas region reaches each electricity region at its elec->ng weight.

    Prices are intensive:  an electricity region wholly inside the gas region sees the full unit
    price, a split region sees its population share of it, and unlinked regions see zero.
    """
    # Use a price or rate value in one source region, and spread it INTENSIVE
    result = crosswalk_values(
        unit_series(ng_region), ModelType.NATURAL_GAS, ModelType.ELECTRICITY, QuantityKind.INTENSIVE
    )
    weights = load_region_weights(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
    # locate the mapping for the region selected
    expected = weights[weights['ng_region'] == ng_region].set_index('elec_region')[WEIGHT_COLUMN]

    assert list(result.index.names) == VALUE_INDEX
    got = by_elec_region(result)
    # every electricity region is present:  the zero-priced gas regions still cover them
    assert set(got) == set(weights['elec_region'])

    # walk out the mapping, and ensure we pick up the expected value, or zero if not expected
    # As this is a unit value in 1 region only, we should pick up the weights directly
    for elec_region, price in got.items():
        assert price == pytest.approx(expected.get(elec_region, 0.0), abs=1e-9), elec_region
    assert set(expected.index) == {r for r, p in got.items() if p > 0}


def test_unit_price_middle_atlantic_spot_values() -> None:
    """Pin the values a reader can check against elec_to_ng_crosswalk.csv by eye."""
    result = crosswalk_values(
        unit_series('middle_atlantic'),
        ModelType.NATURAL_GAS,
        ModelType.ELECTRICITY,
        QuantityKind.INTENSIVE,
    )
    got = by_elec_region(result)
    assert got['8'] == pytest.approx(1.0)  # NYCW, wholly middle_atlantic
    assert got['9'] == pytest.approx(1.0)  # NYUP, wholly middle_atlantic
    assert got['10'] == pytest.approx(0.6767)  # PJME, the rest is south_atlantic
    assert got['11'] == pytest.approx(0.2409)  # PJMW, split four ways
    assert got['7'] == 0.0  # ISNE, new_england only


@pytest.mark.parametrize('ng_region', ['middle_atlantic', 'new_england', 'mountain'])
def test_unit_quantity_conserved(ng_region: str) -> None:
    """A unit quantity in one gas region is spread over its electricity regions and sums to 1."""
    result = crosswalk_values(
        unit_series(ng_region), ModelType.NATURAL_GAS, ModelType.ELECTRICITY, QuantityKind.EXTENSIVE
    )
    weights = load_region_weights(ModelType.NATURAL_GAS, ModelType.ELECTRICITY)
    expected = weights[weights['ng_region'] == ng_region].set_index('elec_region')[WEIGHT_COLUMN]

    got = by_elec_region(result)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-3)
    for elec_region, share in got.items():
        assert share == pytest.approx(expected.get(elec_region, 0.0), abs=1e-9), elec_region
    assert set(expected.index) == {r for r, p in got.items() if p > 0}


def test_intensive_renormalises_partial_coverage(caplog: pytest.LogCaptureFixture) -> None:
    """With only some gas regions priced, an average is taken over what is present."""
    index = pd.MultiIndex.from_tuples(
        [('west_south_central', YEAR), ('mountain', YEAR)], names=VALUE_INDEX
    )
    values = pd.Series([2.0, 4.0], index=index, name='price')
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = crosswalk_values(
            values, ModelType.NATURAL_GAS, ModelType.ELECTRICITY, QuantityKind.INTENSIVE
        )

    got = by_elec_region(result)
    assert got['1'] == pytest.approx(2.0)  # TRE, wholly west_south_central
    assert got['20'] == pytest.approx(0.0880 * 2.0 + 0.9120 * 4.0)  # SRSG, fully covered blend
    assert got['3'] == pytest.approx(4.0)  # MISW touches mountain only, at 0.0112:  renormalised
    assert '7' not in got  # ISNE is linked to neither priced region
    assert any('partial coverage' in r.getMessage() for r in caplog.records)


def test_unknown_pair_raises() -> None:
    """A pair with no registered file fails loudly, naming what is registered."""
    with pytest.raises(KeyError, match='registered pairs'):
        load_region_weights(ModelType.MAGIC, ModelType.ELECTRICITY)


@pytest.mark.parametrize(
    'pair',
    [
        (ModelType.ALL, ModelType.ELECTRICITY),
        (ModelType.NATURAL_GAS, ModelType.ALL),
        (ModelType.ELECTRICITY, ModelType.ELECTRICITY),
    ],
    ids=['all_source', 'all_destination', 'same_model'],
)
def test_invalid_endpoints_rejected(pair: tuple[ModelType, ModelType]) -> None:
    """``ModelType.ALL`` is an indicator, not a model, and a model cannot crosswalk to itself."""
    with pytest.raises(ValueError):
        load_region_weights(*pair)
    with pytest.raises(ValueError):
        crosswalk_values(unit_series('pacific'), *pair, QuantityKind.INTENSIVE)


def test_bad_index_names_raise() -> None:
    """The series must be indexed by (region, year) so the merge has something to join on."""
    values = unit_series('pacific')
    values.index = values.index.set_names(['r', 'y'])
    with pytest.raises(ValueError, match='indexed by'):
        crosswalk_values(
            values, ModelType.NATURAL_GAS, ModelType.ELECTRICITY, QuantityKind.INTENSIVE
        )
