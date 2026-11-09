"""Consistency tests of the load-profile algorithm on made-up annual loads.

Energy conservation of the basic and delta approaches (with and without hydrogen), the T&D
factor, the base-year freeze rule, the 864 -> 8760 expansion and the renormalised shapes.  The
shape tables (INPUT 1) are in input/load/tables; the 2023 runs need input/load/base_load_2023.csv.
"""

import numpy as np
import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.models.load.calendars import build_calendar, get_calendar
from src.models.load.load_model import H2_KEY, NemsLoad, load_tables

TABLES_DIR = PROJECT_ROOT / 'input' / 'load' / 'tables'
DATA = PROJECT_ROOT / 'input' / 'load'
REGION = 1


@pytest.fixture(scope='module')
def tables() -> dict[str, pd.DataFrame]:
    """The shape tables of the load model; skipped if the folder is missing."""
    if not TABLES_DIR.is_dir():
        pytest.skip(f'shape tables not found: {TABLES_DIR}')
    return load_tables(str(TABLES_DIR))


@pytest.fixture(scope='module')
def nems_model(tables) -> NemsLoad:
    """The model on the 2018 calendar, which has the NEMS day counts (no renormalisation)."""
    cal, _ = get_calendar(2018)
    return NemsLoad(tables, cal, renormalize=False)


@pytest.fixture(scope='module')
def calendar_model(tables) -> NemsLoad:
    """The model on the 2023 federal-holiday calendar, whose day counts differ from NEMS."""
    return NemsLoad(tables, build_calendar(2023, 'federal', 'nearest15'))


@pytest.fixture(scope='module')
def loads(nems_model) -> tuple[pd.Series, pd.Series]:
    """Made-up base-year and projection-year loads by (sector, end use), hydrogen zero."""
    rng = np.random.default_rng(1)
    keys = nems_model.end_uses
    base = pd.Series(
        rng.uniform(100, 1000, len(keys)) * 1000, index=pd.MultiIndex.from_tuples(keys)
    )
    base[H2_KEY] = 0.0
    load = base * rng.uniform(0.9, 1.4, len(keys))
    load[H2_KEY] = 0.0
    return base, load


@pytest.fixture(scope='module')
def hydrogen(nems_model) -> tuple[np.ndarray, float]:
    """A made-up hydrogen demand (MW at each of the 864 points) and its annual energy (MWh)."""
    h2 = np.random.default_rng(2).uniform(0, 50, nems_model.n_load_points)
    return h2, float((h2 * nems_model.weights).sum())


def _rel(value: float, reference: float) -> float:
    """Relative difference of ``value`` from ``reference``."""
    return abs(value - reference) / reference


def _all_regions(model: NemsLoad, load: pd.Series, year: int = 2035) -> pd.DataFrame:
    """The load of one year, the same in every region, in the layout ``build_all_regions`` reads."""
    return pd.concat(
        [
            load.rename('mwh')
            .reset_index()
            .rename(columns={'level_0': 'sector', 'level_1': 'euse'})
            .assign(year=year, region=r)
            for r in model.regions
        ]
    )


# ---- tables and calendar
# -------------------------------------------------------------------------------
def test_every_end_use_of_every_region_resolves_to_a_shape(nems_model):
    """Every non-hydrogen end use has a shape in every region."""
    missing = [
        (r, k)
        for r in nems_model.regions
        for k in nems_model.end_uses
        if k != H2_KEY and nems_model.shape_name(r, *k) not in nems_model.dist_eu
    ]
    assert not missing


def test_census_division_maps_fully_to_the_regions(nems_model):
    """Residential census division 1 is shared out completely over the EMM regions."""
    assert sum(nems_model.map_c2n[(r, 1, 1)] for r in nems_model.regions) == pytest.approx(1)


def test_2018_calendar_reproduces_the_nems_weights(nems_model, tables):
    """The 2018 day counts are the V_SYS_LOAD_WEIGHTS and the NEMS shapes sum to 1 with them."""
    weights = tables['V_SYS_LOAD_WEIGHTS']
    want = {
        (int(a), int(b)): int(c)
        for a, b, c in weights[['Month', 'DAY', 'WEIGHTS']].itertuples(index=False)
    }
    assert nems_model.day_weights == want
    assert len(nems_model.calendar) == nems_model.n_days
    sums = nems_model.shape_sum.items()
    assert max(abs(v - 1) for k, v in sums if k[0] == 'system') < 1e-5
    assert max(abs(v - 1) for k, v in sums if k[0] == 'end use') < 2e-3


def test_other_year_is_renormalised_so_shapes_sum_to_one(calendar_model, nems_model):
    """Another year has other day counts than NEMS; every shape then sums to 1 exactly."""
    assert calendar_model.renormalize
    assert (pd.Series(calendar_model.day_weights) != pd.Series(nems_model.day_weights)).any()
    shapes = list(calendar_model.dist_sys.values()) + list(calendar_model.dist_eu.values())
    assert max(abs((a * calendar_model.weights).sum() - 1) for a in shapes) < 1e-12


# ---- energy conservation and delta terms
# -----------------------------------------------------------
def test_basic_profile(nems_model, loads):
    """BASIC: the profile has the energy of the loads, load = sales x td, sectors add up."""
    _, load = loads
    p = nems_model.basic_profile(REGION, load)
    assert _rel(p['annual_sales_mwh'], load.sum()) < 2e-3
    assert abs(p['load'] / p['sales'] - p['td']).max() < 1e-12
    np.testing.assert_allclose(sum(p['sector_sales'].values()), p['sales'])


def test_delta_profile(nems_model, loads):
    """DELTA: the profile has the energy of the loads; equal growth gives the system shape."""
    base, load = loads
    p = nems_model.delta_profile(REGION, load, base)
    assert _rel(p['annual_sales_mwh'], load.sum()) < 2e-3
    assert abs(p['load'] / p['sales'] - p['td']).max() < 1e-12
    same = nems_model.delta_profile(REGION, base * 1.3, base)
    np.testing.assert_allclose(same['sales'], nems_model.dist_sys[REGION] * (base * 1.3).sum())


def test_hydrogen_adds_its_own_energy_in_both_approaches(nems_model, loads, hydrogen):
    """The total is the other end uses plus the hydrogen energy, whatever the base hydrogen is."""
    base, load = loads
    h2, h2_energy = hydrogen
    load_h2 = load.copy()
    load_h2[H2_KEY] = h2_energy
    base_h2 = base.copy()
    base_h2[H2_KEY] = 0.3 * h2_energy  # non-zero base-year hydrogen
    total = load.sum() + h2_energy
    basic = nems_model.basic_profile(REGION, load_h2, h2_hourly_mw=h2)
    assert _rel(basic['annual_sales_mwh'], total) < 2e-3
    delta = nems_model.delta_profile(REGION, load_h2, base_h2, h2_hourly_mw=h2)
    assert _rel(delta['annual_sales_mwh'], total) < 2e-3
    assert _rel(delta['system_load'], total) < 1e-12


def test_hydrogen_row_without_a_profile_is_spread_evenly(nems_model, loads, hydrogen):
    """An annual hydrogen row with no profile carries its whole energy."""
    _, load = loads
    _, h2_energy = hydrogen
    load_h2 = load.copy()
    load_h2[H2_KEY] = h2_energy
    flat = nems_model.basic_profile(REGION, load_h2)
    assert flat['hydrogen_mwh'] == pytest.approx(h2_energy, rel=1e-9)


# ---- T&D losses and the base year
# ----------------------------------------------------------------
def test_ulossadj_table_uses_the_value_of_the_run_year(nems_model, loads):
    """A ULOSSADJ table gives the same result as the number for that year."""
    _, load = loads
    all_regions = _all_regions(nems_model, load)
    by_table = nems_model.build_all_regions(
        all_regions, 2035, 'basic', loss_adj=pd.Series({2034: 1.0, 2035: 0.8})
    )[REGION]
    by_number = nems_model.build_all_regions(all_regions, 2035, 'basic', loss_adj=0.8)[REGION]
    np.testing.assert_allclose(by_table['load'], by_number['load'])
    assert by_table['td'] == pytest.approx(1 + nems_model.tdloss[REGION] * 0.8, abs=1e-12)


def test_ulossadj_table_without_the_run_year_stops(nems_model, loads):
    """A ULOSSADJ table that lacks the run year is an error."""
    _, load = loads
    with pytest.raises(KeyError):
        nems_model.build_all_regions(
            _all_regions(nems_model, load), 2035, 'basic', loss_adj=pd.Series({2034: 1.0})
        )


def test_base_year_freeze(nems_model, loads):
    """Up to the freeze year each year is its own base; later years use the freeze year."""
    _, load = loads
    all_regions = _all_regions(nems_model, load)
    two = pd.concat([all_regions, all_regions.assign(year=2040, mwh=all_regions.mwh * 1.2)])
    early = nems_model.build_all_regions(two, 2035, 'delta', base_freeze_year=2036)[REGION]
    np.testing.assert_allclose(early['sales'], nems_model.dist_sys[REGION] * load.sum())
    late = nems_model.build_all_regions(two, 2040, 'delta', base_freeze_year=2035)[REGION]
    fixed = nems_model.build_all_regions(two, 2040, 'delta', base_year=2035)[REGION]
    np.testing.assert_allclose(late['sales'], fixed['sales'])
    assert late['slr'] == pytest.approx(1.2, abs=1e-9)


# ---- 864 -> 8760 expansion
# ---------------------------------------------------------------------------
def test_expansion_has_the_calendar_hours_and_the_profile_energy(nems_model, loads):
    """The expanded profile has one row per hour and sums to the weighted point sum."""
    base, load = loads
    p = nems_model.delta_profile(REGION, load, base)
    e = nems_model.expand_8760(p, 'sales')
    assert len(e) == nems_model.n_hours
    assert abs(e['sales_mw'].sum() - p['annual_sales_mwh']) < 1e-6 * p['annual_sales_mwh']


def test_calendar_weights_conserve_energy_exactly(calendar_model, loads):
    """With the calendar's own weights the profiles and their expansion carry the load exactly."""
    base, load = loads
    basic = calendar_model.basic_profile(REGION, load)
    assert _rel(basic['annual_sales_mwh'], load.sum()) < 1e-12
    delta = calendar_model.delta_profile(REGION, load, base)
    assert _rel(delta['annual_sales_mwh'], load.sum()) < 1e-12
    expanded = calendar_model.expand_8760(delta, 'sales')
    assert len(expanded) == calendar_model.n_hours
    assert _rel(expanded['sales_mw'].sum(), load.sum()) < 1e-12
