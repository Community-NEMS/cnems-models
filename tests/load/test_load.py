"""Tests of the load-profile algorithm.

The shape tables (INPUT 1) are in input/load/tables; the 2023 tests need
input/load/base_load_2023.csv.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.models.load.calendars import (
    build_calendar,
    get_calendar,
    resolve_calendar,
    to_model_calendar,
)
from src.models.load.data_validation import (
    check_energy_balance,
    check_no_negative_load,
    validate_calendar,
)
from src.models.load.load_model import NemsLoad, load_tables
from src.models.load.run import run

ROOT = PROJECT_ROOT
TABLES_DIR = ROOT / 'input' / 'load' / 'tables'
DATA = ROOT / 'input' / 'load'
DEMO_CENSUS = ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv'
PEAKS_2026 = {
    1: 14,
    2: 11,
    3: 11,
    4: 15,
    5: 13,
    6: 10,
    7: 15,
    8: 12,
    9: 16,
    10: 14,
    11: 11,
    12: 16,
}  # all Wednesdays


@pytest.fixture(scope='session')
def tables():
    """The shape tables of the load model; the test is skipped if the folder is missing."""
    folder = TABLES_DIR
    if not folder.is_dir():
        pytest.skip(f'shape tables not found: {folder}')
    return load_tables(str(folder))


needs_2023 = pytest.mark.skipif(
    not (DATA / 'base_load_2023.csv').exists(), reason='input/load/base_load_2023.csv not there'
)


# ---- calendars (no tables needed)
# ---------------------------------------------------------------------
def test_holidays_and_peaks_classify_every_other_day_by_weekday():
    """Holidays and peaks classify every other day by weekday."""
    cal, _ = resolve_calendar(
        2026, holidays=[(1, 1), (7, 3), (12, 25)], peak_days=PEAKS_2026
    )  # unnamed holidays
    n = cal.groupby('day_type').size().to_dict()
    assert n == {'peak': 12, 'weekend': 104 + 3, 'weekday': 365 - 12 - 107}


def test_holiday_on_a_weekend_is_not_counted_twice():
    """Holiday on a weekend is not counted twice."""
    cal, _ = resolve_calendar(
        2026, holidays=[(1, 3), (1, 1)], peak_days=PEAKS_2026
    )  # Jan 3 2026 is a Saturday
    assert cal.groupby('day_type').size()['weekend'] == 104 + 1


def test_peak_day_must_be_a_weekday():
    """Peak day must be a weekday."""
    with pytest.raises(ValueError, match='non-holiday weekday'):
        build_calendar(2026, [(1, 1)], {**PEAKS_2026, 3: 14})  # Mar 14 2026 is a Saturday


def test_other_year_needs_holidays_and_peaks():
    """Other year needs holidays and peaks."""
    with pytest.raises(ValueError, match='holidays are needed'):
        get_calendar(2026)
    with pytest.raises(ValueError, match='peak days are needed'):
        get_calendar(2026, holidays='federal')


def test_default_falls_back_to_2018_with_a_note_but_a_named_year_does_not():
    """Default falls back to 2018 with a note but a named year does not."""
    cal, notes = get_calendar(None, load_csv=str(ROOT / 'no_such_folder'))
    assert pd.to_datetime(cal.date).dt.year.iloc[0] == 2018 and any('NOTE' in n for n in notes)
    with pytest.raises(FileNotFoundError):
        get_calendar(2023, load_csv=str(ROOT / 'no_such_folder'))


def test_day_type_words_are_converted_and_unknown_words_become_nan():
    """Day type words are converted and unknown words become nan."""
    c = pd.DataFrame(
        {
            'date': pd.to_datetime(['2023-01-01'] * 3),
            'month': 1,
            'day_type': ['Weekday ', 'weekend', 'peak day'],
        }
    )
    assert to_model_calendar(c).day_type.isna().tolist() == [False, False, True]


@needs_2023
def test_2023_calendar_built_from_the_load_file():
    """2023 calendar built from the load file."""
    cal, _ = get_calendar(load_csv=str(DATA))
    c = cal.groupby('day_type').size().to_dict()
    assert (
        pd.to_datetime(cal.date).dt.year.iloc[0] == 2023 and c[3] == 12 and sum(c.values()) == 365
    )
    peaks = pd.to_datetime(cal[cal.day_type == 3].date).dt.strftime('%m-%d').tolist()
    assert (
        '02-03' in peaks and '10-02' in peaks and '02-04' not in peaks
    )  # weekday peaks, not the Sat / Sun maxima


# ---- with the tables
# ------------------------------------------------------------------------------------
def test_2018_calendar_reproduces_the_nems_weights(tables):
    """2018 calendar reproduces the nems weights."""
    cal, _ = get_calendar(2018)
    m = NemsLoad(tables, cal, renormalize=False)
    w = tables['V_SYS_LOAD_WEIGHTS']
    assert m.day_weights == {
        (int(a), int(b)): int(c)
        for a, b, c in w[['Month', 'DAY', 'WEIGHTS']].itertuples(index=False)
    }


def test_validate_calendar_catches_bad_calendars(tables):
    """Validate calendar catches bad calendars."""
    cal, _ = get_calendar(2018)
    assert validate_calendar(cal, tables) == []
    assert any('expected 365' in i[2] for i in validate_calendar(cal.iloc[1:], tables))
    bad = cal.copy()
    bad.loc[0, 'month'] = 2
    assert any('does not match' in i[2] for i in validate_calendar(bad, tables))
    two = cal.copy()
    two.loc[5, 'date'] = pd.Timestamp('2019-01-06')
    assert any('several years' in i[2] for i in validate_calendar(two, tables))


def test_shapes_sum_to_one_after_renormalisation(tables):
    """Shapes sum to one after renormalisation."""
    cal23 = build_calendar(2023, 'federal', 'nearest15')
    m = NemsLoad(tables, cal23)
    for a in list(m.dist_sys.values()) + list(m.dist_eu.values()):
        assert abs((a * m.weights).sum() - 1) < 1e-12


@needs_2023
def test_run_end_to_end(tables, tmp_path):
    """Run end to end."""
    loads = ROOT / 'tests' / 'load' / 'data' / 'demo_annual_loads_SYNTHETIC.csv'
    out = run(
        str(TABLES_DIR),
        str(loads),
        2035,
        base_year=2024,
        method='both',
        hourly8760=True,
        load_dir=str(DATA),
        output_dir=str(tmp_path),
        verbose=False,
    )
    base = tmp_path / out['scenario']  # results go to <output_dir>/<scenario>/
    for meth in ('basic', 'delta'):
        h = pd.read_csv(base / meth / 'hourly_load_mwh.csv')
        all_rows = pd.read_csv(base / 'summary_by_year_region.csv')
        summary_raw = all_rows[all_rows.method == meth]
        summary = summary_raw.set_index('region_id')
        assert list(h.columns) == ['region_id', 'region', 'year', 'hour', 'sales_mwh', 'load_mwh']
        assert list(summary_raw.columns[:4]) == [
            'year',
            'region_id',
            'region',
            'method',
        ]  # same names as the hourly file
        assert (
            len(h) == 25 * 8760
            and h.region_id.nunique() == 25
            and set(h.region_id) == set(range(1, 26))
        )
        assert (
            h.hour.min() == 1
            and h.hour.max() == 8760
            and (h.groupby('region_id').hour.nunique() == 8760).all()
        )
        assert h.equals(h.sort_values(['year', 'region_id', 'hour']))  # sorted ascending
        np.testing.assert_allclose(
            h.groupby('region_id').load_mwh.sum(), summary.annual_load_mwh.sort_index(), rtol=1e-10
        )
        np.testing.assert_allclose(
            h.groupby('region_id').sales_mwh.sum(),
            summary.annual_sales_mwh.sort_index(),
            rtol=1e-10,
        )
        np.testing.assert_allclose(h.load_mwh, h.sales_mwh * h.region_id.map(summary.td), rtol=1e-9)
        assert not list((base / meth).glob('profile864_*'))  # profiles are off by default
    assert (base / 'calendar_used_2023.csv').exists()
    xw = pd.read_csv(base / 'crosswalk_864_to_8760.csv')
    assert (
        xw.hour_index.iloc[0] == 1
        and xw.hour_index.iloc[-1] == 8760
        and 'hour_of_day' in xw.columns
    )


@needs_2023
def test_census_loads_are_mapped_to_regions_and_conserve_energy(tables, tmp_path):
    """Census loads are mapped to regions and conserve energy."""
    folder = str(TABLES_DIR)
    census = pd.read_csv(ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv')
    out = run(
        folder,
        census_division_loads=str(
            ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv'
        ),
        year=[2023, 2035],
        base_year=2023,
        load_dir=str(DATA),
        loss_adj=1.0,
        output_dir=str(tmp_path),
        verbose=False,
    )
    s = pd.read_csv(tmp_path / out['scenario'] / 'summary_by_year_region.csv')
    for y, g in s.groupby('year'):
        entered = census[census.year == y].mwh.sum()  # MWh
        assert (
            0.99 < g.system_load_mwh.sum() / entered < 1.0
        )  # Alaska and Hawaii are dropped, nothing else
        np.testing.assert_allclose(
            g.annual_sales_mwh, g.system_load_mwh, rtol=1e-10
        )  # energy conserved by the profiles
    assert set(s.year) == {2023, 2035}


def _map_census_to_regions(census: pd.DataFrame, tables: dict) -> tuple[pd.Series, float]:
    """Map census loads to the EMM regions straight from the map table.

    This is written independently of ``NemsLoad.census_to_region``: each census division's
    sector load is shared out by ``MappCtoN`` divided by the national-total row (EMM_REG 28).

    Parameters
    ----------
    census : pd.DataFrame
        Columns ``year, sector, euse, census, mwh`` of one year.
    tables : dict
        The shape tables.

    Returns
    -------
    regions : pd.Series
        MWh by EMM region 1-25.
    alaska_hawaii : float
        MWh that the map sends to Alaska and Hawaii (rows 26 and 27).
    """
    mp = tables['V_EMM_LDSMSTR_RGN_CEN']
    total = mp[mp.EMM_REG == 28].set_index(['SECTOR', 'CENSUS']).MappCtoN
    mp = mp[(mp.CENSUS <= 9) & (mp.EMM_REG < 28)].copy()
    denom = total.reindex(list(zip(mp.SECTOR, mp.CENSUS, strict=True))).to_numpy()
    mp['frac'] = np.nan_to_num(mp.MappCtoN.to_numpy() / np.where(denom > 0, denom, np.nan))
    load = census.groupby(['sector', 'census']).mwh.sum().rename('mwh').reset_index()
    joined = mp.merge(load, left_on=['SECTOR', 'CENSUS'], right_on=['sector', 'census'])
    joined['part'] = joined.mwh * joined.frac
    regions = joined[joined.EMM_REG <= 25].groupby('EMM_REG').part.sum()
    return regions.reindex(range(1, 26)).fillna(0.0), float(joined[joined.EMM_REG > 25].part.sum())


@pytest.mark.parametrize('method', ['basic', 'delta'])
def test_hourly_regional_totals_equal_the_independently_mapped_census_loads(
    tables, tmp_path, method
):
    """Hourly sales of each region add up to its census loads mapped by hand, hydrogen included."""
    census = pd.read_csv(DEMO_CENSUS)
    year = 2030
    mapped, alaska_hawaii = _map_census_to_regions(census[census.year == year], tables)
    # the whole census input is in the 25 regions or in Alaska and Hawaii, nothing else
    assert mapped.sum() + alaska_hawaii == pytest.approx(
        census[census.year == year].mwh.sum(), rel=1e-12
    )
    assert alaska_hawaii > 0
    res = run(
        str(TABLES_DIR),
        census_division_loads=str(DEMO_CENSUS),
        year=year,
        base_year=2023,
        method=method,
        calendar_year=2018,
        loss_adj=1.0,
        hourly8760=True,
        output_dir=str(tmp_path),
        verbose=False,
    )
    hourly = pd.read_csv(Path(res['output_dir']) / 'hourly_load_mwh.csv')
    assert len(hourly) == 25 * 8760
    got = hourly.groupby('region_id').sales_mwh.sum().sort_index()
    np.testing.assert_allclose(got.to_numpy(), mapped.sort_index().to_numpy(), rtol=1e-9)
    used = pd.read_csv(Path(res['output_dir']) / 'region_loads_used.csv')
    assert list(used.columns) == ['year', 'region', 'sector', 'euse', 'mwh']
    by_region = used[used.year == year].groupby('region').mwh.sum().sort_index()
    np.testing.assert_allclose(by_region.to_numpy(), mapped.sort_index().to_numpy(), rtol=1e-9)
    assert got.sum() < census[census.year == year].mwh.sum()  # Alaska and Hawaii are not modelled
    assert got.sum() + alaska_hawaii == pytest.approx(
        census[census.year == year].mwh.sum(), rel=1e-9
    )


def test_energy_balance_check_stops_a_run_that_loses_energy():
    """A profile that does not add up to its annual load, in 864 points or in hours, is an error."""
    model = {1: 'A', 2: 'B'}
    good = {1: {'system_load': 100.0, 'annual_sales_mwh': 100.0}}
    hours = [pd.DataFrame({'region_id': 1, 'sales_mwh': [60.0, 40.0]})]
    check_energy_balance(good, model, 2030, 'basic', hours)  # adds up: no error
    lost = {1: {'system_load': 100.0, 'annual_sales_mwh': 99.0}}
    with pytest.raises(ValueError, match=r'A \(864 points'):
        check_energy_balance(lost, model, 2030, 'basic')
    short_hours = [pd.DataFrame({'region_id': 1, 'sales_mwh': [60.0, 39.0]})]
    with pytest.raises(ValueError, match=r'A \(hourly'):
        check_energy_balance(good, model, 2030, 'delta', short_hours)


# ---- replacing a sector with the user's own forecast (demand_overrides.py)
# ---------------------------------------
def _demand_table():
    import pandas as pd

    rows = [
        (y, s, e, c, 1.0 + 0.1 * e + 0.01 * c + 0.5 * (y - 2023))
        for y in (2023, 2030)
        for s, n in ((1, 3), (2, 5))
        for e in range(1, n + 1)
        for c in range(1, 10)
    ]
    return pd.DataFrame(rows, columns=['year', 'sector', 'euse', 'census', 'mwh'])


def test_override_replaces_only_that_sector(tmp_path):
    """Override replaces only that sector."""
    from src.models.load.demand_overrides import apply_overrides, make_override_template

    nems = _demand_table()
    f = tmp_path / 'c.csv'
    t = make_override_template(nems, 'commercial', [2023, 2030], str(f))
    t['mwh'] = t.nems_reference_mwh * 2
    t.to_csv(f, index=False)
    out, sources, _ = apply_overrides(nems, {'commercial': str(f)}, [2023, 2030])
    a = out.set_index(['year', 'sector', 'euse', 'census']).mwh
    b = nems.set_index(['year', 'sector', 'euse', 'census']).mwh
    assert (a.xs(1, level='sector') == b.xs(1, level='sector')).all()  # residential untouched
    assert abs((a.xs(2, level='sector') / b.xs(2, level='sector')).mean() - 2) < 1e-12
    assert list(sources.source.str.startswith('REPLACED'))[:2] == [False, True]


def test_override_must_be_complete_and_valid(tmp_path):
    """Override must be complete and valid."""
    import pandas as pd
    import pytest

    from src.models.load.demand_overrides import apply_overrides, make_override_template

    nems = _demand_table()
    f = tmp_path / 'c.csv'
    t = make_override_template(nems, 'commercial', [2023, 2030], str(f))
    t['mwh'] = 1.0
    bad = t.drop(index=3)  # a missing row
    bad.to_csv(f, index=False)
    with pytest.raises(ValueError, match='missing'):
        apply_overrides(nems, {'commercial': str(f)}, [2023, 2030])
    t.assign(euse=t.euse + 50).to_csv(f, index=False)  # end uses that do not exist
    with pytest.raises(ValueError, match='do not exist'):
        apply_overrides(nems, {'commercial': str(f)}, [2023, 2030])
    t.assign(sector=1).to_csv(f, index=False)  # file of another sector
    with pytest.raises(ValueError, match='other sectors'):
        apply_overrides(nems, {'commercial': str(f)}, [2023, 2030])
    pd.concat([t, t.iloc[:1]]).to_csv(f, index=False)  # duplicate row
    with pytest.raises(ValueError, match='duplicated'):
        apply_overrides(nems, {'commercial': str(f)}, [2023, 2030])
    with pytest.raises(ValueError, match='unknown sector'):
        apply_overrides(nems, {'offices': str(f)}, [2023, 2030])


def test_system_level_and_region_level_load_files_give_the_same_system_load(tmp_path):
    """System level and region level load files give the same system load."""
    from src.models.load.calendars import read_system_load

    rng = np.random.default_rng(0)
    n = 8760
    a, b = rng.uniform(10, 20, n), rng.uniform(5, 9, n)
    pd.DataFrame(
        {
            'region': [1] * n + [2] * n,
            'hour': list(range(1, n + 1)) * 2,
            'base_load_mwh': np.r_[a, b],
        }
    ).to_csv(tmp_path / 'base_load_2023.csv', index=False)
    sys_dir = tmp_path / 'system'
    sys_dir.mkdir()
    pd.DataFrame({'hour': range(1, n + 1), 'base_load_mwh': a + b}).to_csv(
        sys_dir / 'base_load_2023.csv', index=False
    )
    r = read_system_load(str(tmp_path), 2023)
    s = read_system_load(str(sys_dir), 2023)
    assert np.allclose(r.values, s.values) and r.index.equals(s.index)
    pd.DataFrame({'hour': [1, 1], 'base_load_mwh': [1.0, 2.0]}).to_csv(
        sys_dir / 'base_load_2023.csv', index=False
    )
    with pytest.raises(ValueError):  # repeated hours without a region column
        read_system_load(str(sys_dir), 2023)


def test_find_restart_takes_the_rlx_file_and_flags_lfs_pointers(tmp_path):
    """Find restart takes the rlx file and flags lfs pointers."""
    from src.models.load.nems_aeo_reader import find_restart

    (tmp_path / 's1' / 'd1').mkdir(parents=True)
    (tmp_path / 's1' / 'd1' / 'restart.rlx').write_bytes(b'x' * 2000)
    assert find_restart(tmp_path, 's1').name == 'restart.rlx'
    (tmp_path / 's3' / 'd1').mkdir(parents=True)
    (tmp_path / 's3' / 'd1' / 'restart.rlx').write_text('pointer')
    with pytest.raises(FileNotFoundError, match='LFS'):
        find_restart(tmp_path, 's3')
    (tmp_path / 's4' / 'd1').mkdir(parents=True)
    (tmp_path / 's4' / 'd1' / 'restart.npz').write_bytes(b'x' * 2000)
    with pytest.raises(FileNotFoundError, match='no restart file'):
        find_restart(tmp_path, 's4')  # restart.npz is not looked up


def test_negative_hourly_load_stops_the_run_and_lists_the_rows(tmp_path):
    """A negative hourly load raises, names the rows and saves them; zero is allowed."""
    long = pd.DataFrame(
        {
            'region_id': [1, 1, 2, 2],
            'region': ['A', 'A', 'B', 'B'],
            'year': [2030, 2030, 2030, 2031],
            'hour': [1, 2, 5, 6],
            'load_mwh': [10.0, 0.0, -3.5, -1.0],
        }
    )
    with pytest.raises(ValueError, match='2 hourly loads are negative') as err:
        check_no_negative_load(long, tmp_path, 'delta')
    assert '-3.5' in str(err.value)
    bad = pd.read_csv(tmp_path / 'negative_load_rows.csv')
    assert bad.load_mwh.tolist() == [-3.5, -1.0]  # most negative first
    ok = tmp_path / 'ok'
    check_no_negative_load(long[long.load_mwh >= 0], ok, 'delta')
    assert not ok.exists()


@needs_2023
def test_864_profiles_are_written_only_when_asked(tables, tmp_path):
    """The 864-point profile files appear with save_profiles_864; the summary is always written."""
    kw = {
        'census_division_loads': str(
            ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv'
        ),
        'year': 2030,
        'base_year': 2023,
        'load_dir': str(DATA),
        'loss_adj': 1.0,
        'verbose': False,
    }
    off = run(str(TABLES_DIR), output_dir=str(tmp_path / 'off'), **kw)
    on = run(str(TABLES_DIR), output_dir=str(tmp_path / 'on'), save_profiles_864=True, **kw)
    folder_off, folder_on = Path(off['output_dir']), Path(on['output_dir'])
    assert not list(folder_off.rglob('profile864_*.csv'))
    assert len(list(folder_on.rglob('profile864_*.csv'))) == 25
    for folder in (folder_off, folder_on):
        assert len(pd.read_csv(folder / 'summary_by_year_region.csv')) == 25
        assert not (folder / 'summary.csv').exists()


@needs_2023
def test_hourly_file_is_a_csv_in_calendar_order(tables, tmp_path):
    """The hourly file is a csv with hours 1..8760 per region and year, in ascending order."""
    res = run(
        str(TABLES_DIR),
        output_dir=str(tmp_path),
        census_division_loads=str(
            ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv'
        ),
        year=2030,
        base_year=2023,
        load_dir=str(DATA),
        loss_adj=1.0,
        hourly8760=True,
        verbose=False,
    )
    df = pd.read_csv(Path(res['output_dir']) / 'hourly_load_mwh.csv')
    assert list(df.columns) == ['region_id', 'region', 'year', 'hour', 'sales_mwh', 'load_mwh']
    for _, g in df.groupby(['region_id', 'year']):
        assert g.hour.tolist() == list(range(1, 8761))
    xw = pd.read_csv(
        Path(res['output_dir']) / 'crosswalk_864_to_8760.csv', parse_dates=['datetime']
    )
    assert xw.hour_index.tolist() == list(range(1, 8761))
    assert xw.datetime.is_monotonic_increasing
    assert xw.datetime.iloc[0] == pd.Timestamp('2023-01-01 00:00')
    assert xw.datetime.iloc[-1] == pd.Timestamp('2023-12-31 23:00')


@needs_2023
def test_hourly_output_refuses_a_leap_year_calendar(tables, tmp_path):
    """Hourly output refuses a leap year calendar."""
    folder = str(TABLES_DIR)
    peaks = tmp_path / 'peaks2024.csv'  # a Tuesday in every month of 2024
    pd.DataFrame(
        {'month': range(1, 13), 'day': [16, 13, 12, 16, 14, 11, 16, 13, 17, 15, 12, 17]}
    ).to_csv(peaks, index=False)
    with pytest.raises(ValueError, match='365 days'):
        run(
            folder,
            census_division_loads=str(
                ROOT / 'tests' / 'load' / 'data' / 'demo_census_loads_SYNTHETIC.csv'
            ),
            year=2030,
            base_year=2023,
            calendar_year=2024,
            holidays='federal',
            peak_days_csv=str(peaks),
            load_dir=None,
            loss_adj=1.0,
            hourly8760=True,
            output_dir=str(tmp_path),
            verbose=False,
        )


def test_files_in_gwh_or_tbtu_get_a_clear_message(tmp_path):
    """Files in GWh or TBtu instead of MWh get a clear message."""
    from src.models.load.data_validation import validate_census_loads, validate_loads

    old_census = pd.DataFrame(
        {'year': [2030], 'sector': [1], 'euse': [1], 'census': [1], 'tbtu': [1.0]}
    )
    old_loads = pd.DataFrame(
        {'year': [2030], 'region': [1], 'sector': [1], 'euse': [1], 'gwh': [1.0]}
    )
    assert (
        'MWh' in validate_census_loads(old_census)[0][2]
        and '292,830' in validate_census_loads(old_census)[0][2]
    )
    assert 'MWh' in validate_loads(old_loads)[0][2]


def test_census_loads_are_already_in_mwh_and_nems_values_are_converted_once():
    """Census loads are already in mwh and nems values are converted once."""
    from src.models.load.load_model import MWH_PER_TBTU

    assert MWH_PER_TBTU == 292_830.0  # NEMS: 292.83 GWh per TBtu


def test_log_paths_do_not_reveal_the_folders_of_the_machine(tmp_path, monkeypatch):
    """Paths in the log are relative to the working folder, or just the name outside it."""
    from src.models.load.calendars import display_path

    monkeypatch.chdir(tmp_path)
    assert display_path(tmp_path / 'output' / 'load' / 'x.csv') == 'output/load/x.csv'
    assert display_path('output/load') == 'output/load'
    outside = tmp_path.parent / 'elsewhere' / 'y.csv'
    assert display_path(outside) == 'y.csv'
