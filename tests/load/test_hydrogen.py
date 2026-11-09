"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/7/26

Tests of the hydrogen (electrolyzer) treatment of the load model: hydrogen is outside the load
decomposition and is added afterwards as an hourly demand.
"""

import numpy as np
import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.models.load.calendars import get_calendar
from src.models.load.demand_overrides import apply_overrides, make_override_template
from src.models.load.hydrogen import (
    annual_by_region,
    check_hydrogen_rows,
    even_profile,
    even_profiles_from_nems,
)
from src.models.load.load_model import H2_KEY, MWH_PER_TBTU, NemsLoad, load_tables
from src.models.load.nems_aeo_reader import (
    extract_hydrogen_qelhm_hr,
    find_restart,
    open_restart,
)
from src.models.load.run import run

TABLES_DIR = PROJECT_ROOT / 'input' / 'load' / 'tables'
NEMS_OUTPUTS = PROJECT_ROOT / 'input' / 'load' / 'AEO2026_outputs_by_scenario'
DEMO_CENSUS = PROJECT_ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv'
YEARS = (2026, 2030)


@pytest.fixture(scope='module')
def model() -> NemsLoad:
    """A load model on the NEMS 2018 calendar, which needs no data file."""
    cal, _ = get_calendar(2018)
    return NemsLoad(load_tables(str(TABLES_DIR)), cal)


def _qelhm_hr() -> np.ndarray:
    """A qelhm_hr array (region, season, hour of day, year): region 1 has hydrogen, others none."""
    a = np.zeros((28, 4, 24, 61))
    for year in YEARS:
        s, h = np.meshgrid(np.arange(1, 5), np.arange(1, 25), indexing='ij')
        a[0, :, :, year - 1990] = s * h * (year - 2000)  # MWh, distinct per season and hour
        a[25:, :, :, year - 1990] = 1.0e6  # Alaska, Hawaii, US total: must be ignored
    return a


@pytest.fixture
def fake_restart(tmp_path):
    """A restart file with only the hydrogen module array."""
    path = tmp_path / 'restart.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **{'HMMBLK/qelhm_hr': _qelhm_hr()})
    return path


def _loads(model: NemsLoad, rng: np.random.Generator) -> tuple[pd.Series, pd.Series]:
    """Made-up annual loads and base-year loads by (sector, end use) with a hydrogen row of 0."""
    keys = model.end_uses
    base = pd.Series(
        rng.uniform(100, 1000, len(keys)) * 1000, index=pd.MultiIndex.from_tuples(keys)
    )
    base[H2_KEY] = 0.0
    load = base * rng.uniform(0.9, 1.4, len(keys))
    return load, base


# ---- the profile
# ---------------------------------------------------------------------------------------
def test_even_profile_keeps_the_energy_and_repeats_every_day(model, fake_restart):
    """The profile has the annual energy of the file and the same 24 values on every day."""
    profiles = even_profiles_from_nems(model, fake_restart, YEARS)
    long = extract_hydrogen_qelhm_hr(fake_restart, YEARS)
    for year in YEARS:
        total = long[(long.year == year) & (long.region_id == 1)].mwh.sum()
        p = profiles[year][1]
        assert (p * model.weights).sum() == pytest.approx(total, rel=1e-12)
        by_day = p.reshape(model.n_months * model.n_daytypes, model.hours_per_day)
        assert (by_day == by_day[0]).all()  # every month and day type carries the same 24 values
        assert profiles[year][2].sum() == 0.0  # a region without hydrogen
        assert len(profiles[year]) == 25  # Alaska, Hawaii and the US total are not used


def test_hour_of_day_pattern_is_kept(model, fake_restart):
    """Hour h of the day gets (energy of that hour over the four seasons) / days of the year."""
    p = even_profiles_from_nems(model, fake_restart, [2030])[2030][1]
    a = _qelhm_hr()[0, :, :, 2030 - 1990]  # (season, hour of day)
    np.testing.assert_allclose(p[: model.hours_per_day], a.sum(axis=0) / model.n_days)


def test_864_and_8760_forms_are_equal(model, fake_restart):
    """Laid on the 8760 hours, the profile repeats the 24 values every day and keeps the energy."""
    p = even_profiles_from_nems(model, fake_restart, [2030])[2030][1]
    hourly = model.expand_8760({'x': p}, 'x')['x_mw'].to_numpy()
    assert len(hourly) == model.n_hours
    by_day = hourly.reshape(model.n_days, model.hours_per_day)
    assert (by_day == by_day[0]).all()
    assert hourly.sum() == pytest.approx((p * model.weights).sum(), rel=1e-12)
    assert hourly.sum() == pytest.approx(_qelhm_hr()[0, :, :, 2030 - 1990].sum(), rel=1e-12)


def test_even_profile_rejects_bad_input(model):
    """The wrong number of values or a negative one is refused."""
    with pytest.raises(ValueError, match='24 values'):
        even_profile(model, np.ones(23))
    with pytest.raises(ValueError, match='not negative'):
        even_profile(model, -np.ones(24))


# ---- energy: hydrogen is outside the decomposition
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize('base_hydrogen_share', [0.0, 0.3, 2.0])
def test_total_energy_is_the_other_end_uses_plus_hydrogen(model, base_hydrogen_share):
    """Delta and basic: total energy is other end uses plus hydrogen, for any base-year hydrogen."""
    rng = np.random.default_rng(3)
    load, base = _loads(model, rng)
    h2 = rng.uniform(0, 50, model.n_load_points)
    energy = float((h2 * model.weights).sum())
    load_h, base_h = load.copy(), base.copy()
    load_h[H2_KEY] = energy
    base_h[H2_KEY] = base_hydrogen_share * energy
    total = load.sum() + energy
    for p in (
        model.basic_profile(1, load_h, h2_hourly_mw=h2),
        model.delta_profile(1, load_h, base_h, h2_hourly_mw=h2),
    ):
        assert p['annual_sales_mwh'] == pytest.approx(total, rel=1e-12)
        assert p['system_load'] == pytest.approx(total, rel=1e-12)
        assert p['hydrogen_mwh'] == pytest.approx(energy, rel=1e-12)
        assert p['system_load_ex_h2_mwh'] == pytest.approx(load.sum(), rel=1e-12)


def test_slr_does_not_contain_hydrogen(model):
    """The slr is the ratio of the other end uses; base-year hydrogen does not change it."""
    rng = np.random.default_rng(4)
    load, base = _loads(model, rng)
    with_h2, base_h = load.copy(), base.copy()
    with_h2[H2_KEY], base_h[H2_KEY] = 5.0e6, 7.0e6
    a = model.delta_profile(1, load, base)
    b = model.delta_profile(1, with_h2, base_h)
    assert b['slr'] == pytest.approx(a['slr'], rel=1e-14)
    assert b['slr'] == pytest.approx(load.sum() / base.sum(), rel=1e-14)


def test_hydrogen_goes_through_the_td_factor_and_the_industrial_curve(model):
    """The load is sales with hydrogen times td; the industrial curve has hydrogen, no losses."""
    rng = np.random.default_rng(5)
    load, base = _loads(model, rng)
    h2 = rng.uniform(1, 50, model.n_load_points)
    plain = model.delta_profile(1, load, base, loss_adj=0.8)
    with_h2 = model.delta_profile(1, load, base, loss_adj=0.8, h2_hourly_mw=h2)
    np.testing.assert_allclose(with_h2['sales'], plain['sales'] + h2)
    np.testing.assert_allclose(with_h2['load'], with_h2['sales'] * with_h2['td'])
    np.testing.assert_allclose(
        with_h2['sector_sales'][3], plain['sector_sales'][3] + h2
    )  # no losses in the sector curve


def test_hydrogen_row_without_a_profile_is_spread_evenly(model):
    """The annual row alone gives a flat profile with the same energy."""
    rng = np.random.default_rng(6)
    load, _ = _loads(model, rng)
    load[H2_KEY] = 8.76e6
    p = model.basic_profile(1, load)
    assert p['hydrogen_mwh'] == pytest.approx(8.76e6, rel=1e-12)
    flat = p['sales'] - model.basic_profile(1, load.drop(H2_KEY))['sales']
    np.testing.assert_allclose(flat, 8.76e6 / model.n_hours)


def test_without_hydrogen_nothing_changes(model):
    """A zero hydrogen row and no hydrogen row at all give identical profiles."""
    rng = np.random.default_rng(7)
    load, base = _loads(model, rng)
    pairs = [
        (model.basic_profile(1, load), model.basic_profile(1, load.drop(H2_KEY))),
        (
            model.delta_profile(1, load, base),
            model.delta_profile(1, load.drop(H2_KEY), base.drop(H2_KEY)),
        ),
    ]
    for zero, none in pairs:
        np.testing.assert_array_equal(zero['sales'], none['sales'])
        np.testing.assert_array_equal(zero['load'], none['load'])
        assert zero['hydrogen_mwh'] == 0.0


# ---- the rows a load file must carry
# ---------------------------------------------------------------------------------------
def _census_rows(years=(2030,), value=0.0) -> pd.DataFrame:
    return pd.DataFrame(
        [(y, 3, 4, c, value) for y in years for c in range(1, 10)],
        columns=['year', 'sector', 'euse', 'census', 'mwh'],
    )


def test_hydrogen_rows_are_required(tmp_path):
    """Missing rows, empty values and negative values are errors; zeros are fine."""
    check_hydrogen_rows(_census_rows(), [2030], 'census', range(1, 10), 'f.csv')
    with pytest.raises(ValueError, match='hydrogen rows are missing'):
        check_hydrogen_rows(_census_rows().iloc[:-1], [2030], 'census', range(1, 10), 'f.csv')
    with pytest.raises(ValueError, match='f.csv'):
        check_hydrogen_rows(_census_rows(), [2030, 2035], 'census', range(1, 10), 'f.csv')
    with pytest.raises(ValueError, match='not negative'):
        check_hydrogen_rows(_census_rows(value=-1.0), [2030], 'census', range(1, 10), 'f.csv')
    no_rows = _census_rows().assign(euse=1)  # only the other end uses
    with pytest.raises(ValueError, match='never filled in silently'):
        check_hydrogen_rows(no_rows, [2030], 'census', range(1, 10), 'f.csv')


def test_run_refuses_a_load_file_without_hydrogen_rows(tmp_path):
    """A user file without industrial end use 4 stops the run; it is not read as zero."""
    census = pd.read_csv(DEMO_CENSUS)
    census = census[~((census.sector == 3) & (census.euse == 4))]
    path = tmp_path / 'no_hydrogen.csv'
    census.to_csv(path, index=False)
    with pytest.raises(ValueError, match='hydrogen rows are missing'):
        run(
            str(TABLES_DIR),
            census_division_loads=str(path),
            year=2030,
            base_year=2023,
            calendar_year=2018,
            loss_adj=1.0,
            output_dir=str(tmp_path / 'out'),
            verbose=False,
        )


def test_industrial_override_needs_the_hydrogen_end_use(tmp_path):
    """An industrial replacement without end use 4 rows is rejected."""
    rows = [(y, 3, e, c, 1.0 + e) for y in (2023, 2030) for e in range(1, 5) for c in range(1, 10)]
    nems = pd.DataFrame(rows, columns=['year', 'sector', 'euse', 'census', 'mwh'])
    f = tmp_path / 'industry.csv'
    template = make_override_template(nems, 'industrial', [2023, 2030], str(f))
    assert (template.euse == 4).any()  # the template asks for hydrogen
    template.assign(mwh=1.0).to_csv(f, index=False)
    apply_overrides(nems, {'industrial': str(f)}, [2023, 2030])  # complete: accepted
    template.assign(mwh=1.0)[template.euse != 4].to_csv(f, index=False)
    with pytest.raises(ValueError, match='missing'):
        apply_overrides(nems, {'industrial': str(f)}, [2023, 2030])


def test_run_adds_the_hydrogen_rows_to_the_hourly_load(model, tmp_path):
    """The run keeps the energy: hourly sales = other end uses + the mapped hydrogen rows."""
    res = run(
        str(TABLES_DIR),
        census_division_loads=str(DEMO_CENSUS),
        year=2030,
        base_year=2023,
        calendar_year=2018,
        loss_adj=1.0,
        output_dir=str(tmp_path),
        verbose=False,
    )
    census = pd.read_csv(DEMO_CENSUS)
    hydrogen = model.census_to_region(census[census.year == 2030])
    hydrogen = hydrogen[(hydrogen.sector == 3) & (hydrogen.euse == 4)].set_index('region').mwh
    assert hydrogen.sum() > 0  # the demo has hydrogen in 2030
    profs = res['profiles'][2030]['delta']
    for r, p in profs.items():
        assert p['hydrogen_mwh'] == pytest.approx(hydrogen[r], rel=1e-12, abs=1e-6)
        assert p['annual_sales_mwh'] == pytest.approx(p['system_load'], rel=1e-10)
    summary = pd.read_csv(tmp_path / res['scenario'] / 'summary_by_year_region.csv')
    assert 'hydrogen_mwh' not in summary.columns  # hydrogen is part of industrial load
    hourly = pd.read_csv(tmp_path / res['scenario'] / 'hourly_load_mwh.csv')
    assert list(hourly.columns) == ['region_id', 'region', 'year', 'hour', 'sales_mwh', 'load_mwh']
    by_region = hourly.groupby('region_id').sales_mwh.sum()
    for r, p in profs.items():
        assert by_region[r] == pytest.approx(p['annual_sales_mwh'], rel=1e-9)


# ---- reading the NEMS hydrogen module
# ---------------------------------------------------------------------------------------
def test_reader_returns_long_form_for_the_25_regions(fake_restart):
    """Columns, sizes and the use of positions 0-24 of the region axis."""
    long = extract_hydrogen_qelhm_hr(fake_restart, 2030)
    assert list(long.columns) == ['year', 'region_id', 'season', 'hour_of_day', 'mwh']
    assert len(long) == 25 * 4 * 24
    assert sorted(long.region_id.unique()) == list(range(1, 26))
    assert long.mwh.max() < 1.0e6  # the junk of positions 25-27 is not read


def test_reader_refuses_missing_variable_bad_shape_negative_and_year(tmp_path, fake_restart):
    """Each failure names the variable or the year."""
    path = tmp_path / 'none.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **{'HMMBLK/other': np.zeros(500)})
    with pytest.raises(ValueError, match='qelhm_hr'):
        extract_hydrogen_qelhm_hr(path, 2030)
    path = tmp_path / 'shape.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **{'HMMBLK/qelhm_hr': np.zeros((28, 3, 24, 61))})
    with pytest.raises(ValueError, match='expected shape'):
        extract_hydrogen_qelhm_hr(path, 2030)
    path = tmp_path / 'negative.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **{'HMMBLK/qelhm_hr': -_qelhm_hr()})
    with pytest.raises(ValueError, match='not negative'):
        extract_hydrogen_qelhm_hr(path, 2030)
    with pytest.raises(ValueError, match='2100'):
        extract_hydrogen_qelhm_hr(fake_restart, 2100)


def test_annual_by_region_adds_up_the_profiles(model, fake_restart):
    """The annual table is the energy of each profile."""
    profiles = even_profiles_from_nems(model, fake_restart, YEARS)
    table = annual_by_region(profiles, model)
    long = extract_hydrogen_qelhm_hr(fake_restart, YEARS)
    want = long.groupby(['year', 'region_id']).mwh.sum().reset_index()
    got = table.sort_values(['year', 'region']).mwh.to_numpy()
    np.testing.assert_allclose(got, want.sort_values(['year', 'region_id']).mwh.to_numpy())


# ---- the real NEMS files (skipped if absent)
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize('scenario', ['higheldmd', 'cb2026'])
def test_hydrogen_module_matches_the_annual_hydrogen_load(scenario):
    """The module total equals qelinh2e x 292,830 MWh per TBtu within 1% in 2026 and 2030."""
    try:
        restart = find_restart(NEMS_OUTPUTS, scenario)
    except FileNotFoundError:
        pytest.skip(f'no restart file for {scenario}')
    z, names = open_restart(restart)
    annual = np.asarray(z[names['qelinh2e']], dtype=float)
    for year in (2026, 2030):
        module = extract_hydrogen_qelhm_hr(restart, year).mwh.sum()
        census = annual[:9, year - 1990].sum() * MWH_PER_TBTU
        assert module > 0
        assert module == pytest.approx(census, rel=0.01)
