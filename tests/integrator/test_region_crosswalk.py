"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/17/26

Tests for the generic regional crosswalk between models.

"""

import pickle
from pathlib import Path

import pandas as pd
import pytest

from src.common.models_modes import ModelType
from src.integrator.region_crosswalk import (
    REGION_COLUMN,
    REGION_CROSSWALKS,
    VALUE_INDEX,
    WEIGHT_COLUMN,
    CrosswalkSet,
    QuantityKind,
    RegionCrosswalk,
    load_region_weights,
)

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

# every (source, destination) ordering of every registered pair, sorted:  frozenset iteration
# order varies by process, and xdist workers must collect identical test ids
REGISTERED_DIRECTIONS = sorted(
    (
        (source, destination)
        for pair in REGION_CROSSWALKS
        for source in pair
        for destination in pair
        if source is not destination
    ),
    key=lambda direction: (direction[0].value, direction[1].value),
)
ELEC_NG = frozenset({ModelType.ELECTRICITY, ModelType.NATURAL_GAS})


@pytest.fixture(scope='module')
def crosswalk() -> RegionCrosswalk:
    """The registered electricity <-> natural gas crosswalk."""
    return RegionCrosswalk.load(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)


def series(values: dict[str, float], year: int = YEAR, name: str = 'value') -> pd.Series:
    """A one-year ``(region, year)`` series from ``{region: value}``."""
    index = pd.MultiIndex.from_tuples([(r, year) for r in values], names=VALUE_INDEX)
    return pd.Series(list(values.values()), index=index, name=name)


def unit_series(ng_region: str, year: int = YEAR) -> pd.Series:
    """A value of 1.0 in ``ng_region`` and 0.0 in every other natural gas region."""
    return series({r: 1.0 if r == ng_region else 0.0 for r in NG_REGIONS}, year, name='price')


def by_region(result: pd.Series) -> dict[str, float]:
    """Flatten a one-year crosswalk result to ``{region: value}``."""
    return {region: val for (region, _year), val in result.items()}


@pytest.mark.parametrize(
    'pair', REGISTERED_DIRECTIONS, ids=[f'{s.value}->{d.value}' for s, d in REGISTERED_DIRECTIONS]
)
def test_weights_sum_to_one_per_source(pair: tuple[ModelType, ModelType]) -> None:
    """Every registered file allocates each source region fully, with string region ids."""
    source, destination = pair
    weights = load_region_weights(source, destination)
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]

    sums = weights.groupby(src_col)[WEIGHT_COLUMN].sum()
    assert sums.to_numpy() == pytest.approx(1.0, abs=1e-3)
    assert weights[src_col].map(type).eq(str).all()
    assert weights[dst_col].map(type).eq(str).all()


def test_regions_match_both_columns(crosswalk: RegionCrosswalk) -> None:
    """Each model's crosswalk regions are the same read from either direction's file."""
    to_ng = crosswalk.weights_from(ModelType.ELECTRICITY)
    to_elec = crosswalk.weights_from(ModelType.NATURAL_GAS)
    assert crosswalk.regions(ModelType.NATURAL_GAS) == set(NG_REGIONS)
    assert crosswalk.regions(ModelType.NATURAL_GAS) == set(to_ng['ng_region'])
    assert crosswalk.regions(ModelType.ELECTRICITY) == set(to_elec['elec_region'])


@pytest.mark.parametrize('ng_region', ['middle_atlantic', 'new_england', 'mountain'])
def test_unit_price_spreads_by_destination_weights(
    crosswalk: RegionCrosswalk, ng_region: str
) -> None:
    """A unit price in one gas region reaches each electricity region at its elec->ng weight.

    Prices are intensive:  an electricity region wholly inside the gas region sees the full unit
    price, a split region sees its population share of it, and unlinked regions see zero.
    """
    result = crosswalk.apply(
        unit_series(ng_region), ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, NG_REGIONS
    )
    weights = crosswalk.weights_from(ModelType.ELECTRICITY)
    expected = weights[weights['ng_region'] == ng_region].set_index('elec_region')[WEIGHT_COLUMN]

    assert list(result.index.names) == VALUE_INDEX
    got = by_region(result)
    # every electricity region is present:  the zero-priced gas regions still cover them
    assert set(got) == set(weights['elec_region'])
    for elec_region, price in got.items():
        assert price == pytest.approx(expected.get(elec_region, 0.0), abs=1e-9), elec_region
    assert set(expected.index) == {r for r, p in got.items() if p > 0}


def test_unit_price_middle_atlantic_spot_values(crosswalk: RegionCrosswalk) -> None:
    """Pin the values a reader can check against elec_to_ng_crosswalk.csv by eye."""
    result = crosswalk.apply(
        unit_series('middle_atlantic'), ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, NG_REGIONS
    )
    got = by_region(result)
    assert got['8'] == pytest.approx(1.0)  # NYCW, wholly middle_atlantic
    assert got['9'] == pytest.approx(1.0)  # NYUP, wholly middle_atlantic
    assert got['10'] == pytest.approx(0.6767)  # PJME, the rest is south_atlantic
    assert got['11'] == pytest.approx(0.2409)  # PJMW, split four ways
    assert got['7'] == 0.0  # ISNE, new_england only


@pytest.mark.parametrize('ng_region', ['middle_atlantic', 'new_england', 'mountain'])
def test_unit_quantity_conserved(crosswalk: RegionCrosswalk, ng_region: str) -> None:
    """A unit quantity in one gas region is spread over its electricity regions and sums to 1."""
    result = crosswalk.apply(
        unit_series(ng_region), ModelType.NATURAL_GAS, QuantityKind.EXTENSIVE, NG_REGIONS
    )
    weights = crosswalk.weights_from(ModelType.NATURAL_GAS)
    expected = weights[weights['ng_region'] == ng_region].set_index('elec_region')[WEIGHT_COLUMN]

    got = by_region(result)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-3)
    for elec_region, share in got.items():
        assert share == pytest.approx(expected.get(elec_region, 0.0), abs=1e-9), elec_region
    assert set(expected.index) == {r for r, p in got.items() if p > 0}


@pytest.mark.parametrize('kind', list(QuantityKind), ids=lambda k: k.value)
def test_closed_subset_maps(crosswalk: RegionCrosswalk, kind: QuantityKind) -> None:
    """Electricity region 7 and New England cover each other exactly, so a run of them maps."""
    result = crosswalk.apply(series({'7': 5.0}), ModelType.ELECTRICITY, kind, {'7'})
    assert by_region(result) == {'new_england': pytest.approx(5.0)}


@pytest.mark.parametrize(
    'values, model_regions, match',
    [
        pytest.param(
            {'2': 1.0}, {'2'}, 'not closed', id='not_closed'
        ),  # FRCC is all south_atlantic, but south_atlantic also draws on other regions
        pytest.param({'7': 1.0, '8': 1.0}, {'7'}, 'not carried', id='excess_source'),
        pytest.param({'7': 1.0}, {'7', '8'}, 'missing', id='missing_source'),
        pytest.param({'7': 1.0, 'zz': 1.0}, {'7', 'zz'}, 'does not map', id='unknown_region'),
    ],
)
def test_apply_rejects_supplied_regions(
    crosswalk: RegionCrosswalk, values: dict[str, float], model_regions: set[str], match: str
) -> None:
    """Supplied regions must be exactly the model's regions, known, and closed."""
    with pytest.raises(ValueError, match=match):
        crosswalk.apply(
            series(values), ModelType.ELECTRICITY, QuantityKind.EXTENSIVE, model_regions
        )


def test_apply_rejects_incomplete_year_grid(crosswalk: RegionCrosswalk) -> None:
    """Every supplied region needs a value in every supplied year."""
    values = pd.concat([unit_series('pacific', 2025), unit_series('pacific', 2030).iloc[1:]])
    with pytest.raises(ValueError, match='one value per'):
        crosswalk.apply(values, ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, NG_REGIONS)


def test_bad_index_names_raise(crosswalk: RegionCrosswalk) -> None:
    """The series must be indexed by (region, year) so the merge has something to join on."""
    values = unit_series('pacific')
    values.index = values.index.set_names(['r', 'y'])
    with pytest.raises(ValueError, match='indexed by'):
        crosswalk.apply(values, ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, NG_REGIONS)


def test_apply_rejects_model_outside_pair(crosswalk: RegionCrosswalk) -> None:
    """Only the pair's two models can send through it."""
    with pytest.raises(ValueError, match='not part of'):
        crosswalk.apply(unit_series('pacific'), ModelType.MAGIC, QuantityKind.INTENSIVE, NG_REGIONS)


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
        CrosswalkSet().get(*pair)


@pytest.mark.parametrize(
    'models, held',
    [
        pytest.param({ModelType.ELECTRICITY}, set(), id='electricity_only'),
        pytest.param({ModelType.ELECTRICITY, ModelType.MAGIC}, set(), id='no_registered_pair'),
        pytest.param(
            {ModelType.ELECTRICITY, ModelType.NATURAL_GAS, ModelType.MAGIC},
            {ELEC_NG},
            id='elec_ng',
        ),
    ],
)
def test_crosswalk_set_loads_pairs_within_models(
    models: set[ModelType], held: set[frozenset[ModelType]]
) -> None:
    """Only registered pairs whose two models both run are loaded."""
    assert set(CrosswalkSet.for_models(models).crosswalks) == held


def test_crosswalk_set_get_is_order_free_and_raises_when_absent() -> None:
    """Lookup ignores direction; a pair the set does not hold is a KeyError naming what it holds."""
    xwalks = CrosswalkSet.for_models({ModelType.ELECTRICITY, ModelType.NATURAL_GAS})
    forward = xwalks.get(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
    assert forward is xwalks.get(ModelType.NATURAL_GAS, ModelType.ELECTRICITY)
    with pytest.raises(KeyError, match='held pairs'):
        CrosswalkSet().get(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)


def test_crosswalk_set_pickles(crosswalk: RegionCrosswalk) -> None:
    """A crosswalk set survives the trip to a pool worker and maps the same values."""
    xwalks = CrosswalkSet(crosswalks={ELEC_NG: crosswalk})
    clone = pickle.loads(pickle.dumps(xwalks))
    values = unit_series('mountain')
    pd.testing.assert_series_equal(
        clone.get(ModelType.NATURAL_GAS, ModelType.ELECTRICITY).apply(
            values, ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, NG_REGIONS
        ),
        crosswalk.apply(values, ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, NG_REGIONS),
    )


# a small reciprocal elec <-> ng pair:  (elec_region, ng_region, weight) rows per direction
TOY_ELEC_TO_NG = [('1', 'a', 1.0), ('2', 'a', 0.5), ('2', 'b', 0.5)]
TOY_NG_TO_ELEC = [('1', 'a', 0.6), ('2', 'a', 0.4), ('2', 'b', 1.0)]


def write_toy_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    elec_to_ng: list[tuple[str, str, float]],
    ng_to_elec: list[tuple[str, str, float]],
) -> None:
    """Write a toy crosswalk pair to ``tmp_path`` and register it for elec <-> ng."""
    columns = ['elec_region', 'ng_region', WEIGHT_COLUMN]
    files = {
        ModelType.ELECTRICITY: tmp_path / 'elec_to_ng.csv',
        ModelType.NATURAL_GAS: tmp_path / 'ng_to_elec.csv',
    }
    pd.DataFrame(elec_to_ng, columns=columns).to_csv(files[ModelType.ELECTRICITY], index=False)
    pd.DataFrame(ng_to_elec, columns=columns).to_csv(files[ModelType.NATURAL_GAS], index=False)
    monkeypatch.setitem(REGION_CROSSWALKS, ELEC_NG, files)


def test_reciprocal_pair_loads_both_ways(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A consistent pair loads in either direction, returning that direction's weights."""
    write_toy_pair(tmp_path, monkeypatch, TOY_ELEC_TO_NG, TOY_NG_TO_ELEC)
    to_ng = load_region_weights(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
    to_elec = load_region_weights(ModelType.NATURAL_GAS, ModelType.ELECTRICITY)
    assert list(to_ng.columns) == ['elec_region', 'ng_region', WEIGHT_COLUMN]
    assert list(to_elec.columns) == ['ng_region', 'elec_region', WEIGHT_COLUMN]
    assert to_elec.groupby('ng_region')[WEIGHT_COLUMN].sum().to_dict() == {'a': 1.0, 'b': 1.0}


@pytest.mark.parametrize(
    'elec_to_ng, ng_to_elec, match',
    [
        # elec region 3 is only in the elec -> ng file
        (TOY_ELEC_TO_NG + [('3', 'b', 1.0)], TOY_NG_TO_ELEC, r"\['3'\] are in elec_to_ng"),
        # ng region c is only in the ng -> elec file
        (TOY_ELEC_TO_NG, TOY_NG_TO_ELEC + [('2', 'c', 1.0)], r"\['c'\] are in ng_to_elec"),
        # same regions both ways, but (2, a) and (1, b) each appear in one direction only
        (
            TOY_ELEC_TO_NG,
            [('1', 'a', 1.0), ('1', 'b', 0.5), ('2', 'b', 0.5)],
            'links differ',
        ),
        # weights of elec region 2 sum to 1.2
        ([('1', 'a', 1.0), ('2', 'a', 0.7), ('2', 'b', 0.5)], TOY_NG_TO_ELEC, 'sum to 1.0'),
        # a negative weight, though elec region 2 still sums to 1.0
        ([('1', 'a', 1.0), ('2', 'a', 1.5), ('2', 'b', -0.5)], TOY_NG_TO_ELEC, 'finite and >= 0'),
        # a blank weight, which a sum alone would skip
        (TOY_ELEC_TO_NG + [('1', 'b', float('nan'))], TOY_NG_TO_ELEC, 'finite and >= 0'),
    ],
    ids=[
        'isolated_source',
        'isolated_destination',
        'link_mismatch',
        'sum_off',
        'negative_weight',
        'nan_weight',
    ],
)
@pytest.mark.parametrize(
    'direction',
    [
        (ModelType.ELECTRICITY, ModelType.NATURAL_GAS),
        (ModelType.NATURAL_GAS, ModelType.ELECTRICITY),
    ],
    ids=['elec->ng', 'ng->elec'],
)
def test_bad_pair_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    elec_to_ng: list[tuple[str, str, float]],
    ng_to_elec: list[tuple[str, str, float]],
    match: str,
    direction: tuple[ModelType, ModelType],
) -> None:
    """A malformed or non-reciprocal pair fails the load, whichever way is asked."""
    write_toy_pair(tmp_path, monkeypatch, elec_to_ng, ng_to_elec)
    with pytest.raises(ValueError, match=match):
        load_region_weights(*direction)
