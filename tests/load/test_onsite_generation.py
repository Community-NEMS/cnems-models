"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/7/26

Tests of the on-site generation output of the load model (``onsite_generation_by_year_region.csv``).
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from definitions import PROJECT_ROOT
from src.models.load.calendars import get_calendar
from src.models.load.load_config import LoadConfig
from src.models.load.load_model import NemsLoad, load_tables
from src.models.load.nems_aeo_reader import extract_onsite_generation, open_restart
from src.models.load.run import onsite_by_region, run

TABLES_DIR = PROJECT_ROOT / 'input' / 'load' / 'tables'
DATA = PROJECT_ROOT / 'input' / 'load'
DEMO_CENSUS = 'tests/load/data/demo_census_loads_SYNTHETIC.csv'
SHAPE = (11, 61, 13, 2)  # census, year (1990..2050), technology, slot
YEAR = 2025
RUN_YEAR = 2030  # a year of the synthetic demo loads
NEEDS_2023 = pytest.mark.skipif(
    not (DATA / 'base_load_2023.csv').exists(), reason='input/load/base_load_2023.csv not there'
)


def _array(sold: float, own_use: float, junk: float = 1.0e6) -> np.ndarray:
    """A COGEN array: GWh ``sold`` / ``own_use`` in divisions 1-9, ``junk`` in positions 10-11."""
    a = np.zeros(SHAPE)
    for year in (YEAR, RUN_YEAR):
        a[:9, year - 1990, :, 0] = sold
        a[:9, year - 1990, :, 1] = own_use
        a[9:, year - 1990, :, :] = junk  # position 10 is empty, 11 is the US total in a real file
    return a


@pytest.fixture
def fake_restart(tmp_path) -> Path:
    """A restart file with only the COGEN arrays, distinct values in the two slots."""
    arrays = {
        'COGEN/cgresgen': _array(sold=1.0, own_use=10.0),
        'COGEN/cgcommgen': _array(sold=2.0, own_use=20.0),
        'COGEN/cgindlgen': _array(sold=3.0, own_use=30.0),
        'COGEN/cgrefgen': _array(sold=4.0, own_use=40.0),
    }
    path = tmp_path / 'restart.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **arrays)
    return path


# ---- the reader
# ---------------------------------------------------------------------------------------
def test_slots_and_units(fake_restart):
    """Slot 0 is sold, slot 1 is own use; 13 technologies are summed; GWh become MWh."""
    gen = extract_onsite_generation(fake_restart, YEAR)
    one = gen[gen.census == 1].set_index('category')
    assert one.loc['residential', ['own_use_mwh', 'sold_mwh']].tolist() == [13 * 10e3, 13 * 1e3]
    assert one.loc['commercial', ['own_use_mwh', 'sold_mwh']].tolist() == [13 * 20e3, 13 * 2e3]
    assert one.loc['industrial', ['own_use_mwh', 'sold_mwh']].tolist() == [13 * 30e3, 13 * 3e3]
    assert one.loc['refinery', ['own_use_mwh', 'sold_mwh']].tolist() == [13 * 40e3, 13 * 4e3]
    assert one.loc['transport', ['own_use_mwh', 'sold_mwh']].tolist() == [0.0, 0.0]


def test_only_the_nine_census_divisions_are_used(fake_restart):
    """Positions 10 and 11 (empty, US total) are ignored, so nothing is double counted."""
    gen = extract_onsite_generation(fake_restart, YEAR)
    assert sorted(gen.census.unique()) == list(range(1, 10))
    resid = gen[gen.category == 'residential']
    assert resid.own_use_mwh.sum() == 9 * 13 * 10e3  # not 1e6-sized junk


def test_missing_variable_and_bad_values_are_refused(tmp_path):
    """A missing array, a wrong shape and a negative sum stop with a message naming it."""
    good = {f'COGEN/{n}': _array(1.0, 2.0) for n in ('cgresgen', 'cgcommgen', 'cgindlgen')}
    path = tmp_path / 'missing.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **good)
    with pytest.raises(ValueError, match='cgrefgen'):
        extract_onsite_generation(path, YEAR)

    arrays = {**good, 'COGEN/cgrefgen': np.zeros((11, 61, 13, 3))}
    path = tmp_path / 'shape.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **arrays)
    with pytest.raises(ValueError, match='cgrefgen.*shape'):
        extract_onsite_generation(path, YEAR)

    arrays = {**good, 'COGEN/cgrefgen': -_array(1.0, 2.0)}
    path = tmp_path / 'negative.rlx'
    with open(path, 'wb') as f:
        np.savez(f, **arrays)
    with pytest.raises(ValueError, match='cgrefgen.*not negative'):
        extract_onsite_generation(path, YEAR)


def test_year_outside_the_file_is_refused(fake_restart):
    """A year the file does not cover is an error."""
    with pytest.raises(ValueError, match='2100'):
        extract_onsite_generation(fake_restart, 2100)


# ---- mapping to the EMM regions
# ---------------------------------------------------------------------------------------
@NEEDS_2023
def test_regional_totals_conserve_energy_less_alaska_and_hawaii(fake_restart):
    """The regional columns add up to the census total, less the Alaska/Hawaii share."""
    tables = load_tables(str(TABLES_DIR))
    cal, _ = get_calendar(2023, str(DATA))
    model = NemsLoad(tables, cal)
    gen = extract_onsite_generation(fake_restart, YEAR)
    wide = onsite_by_region(model, gen)

    assert len(wide) == 25
    assert list(wide.columns[:3]) == ['year', 'region_id', 'region']
    assert list(wide.columns[3:5]) == ['residential_own_use_mwh', 'residential_sold_mwh']
    for category in ('residential', 'commercial', 'industrial', 'refinery'):
        for part in ('own_use', 'sold'):
            census_total = gen.loc[gen.category == category, f'{part}_mwh'].sum()
            share = wide[f'{category}_{part}_mwh'].sum() / census_total
            assert 0.99 < share < 1.0
    assert (wide[['transport_own_use_mwh', 'transport_sold_mwh']] == 0).all().all()


# ---- run and config
# ---------------------------------------------------------------------------------------
@NEEDS_2023
def test_a_restart_next_to_a_user_load_file_gives_only_the_generation_file(fake_restart, tmp_path):
    """Loads from the user's file, generation from the restart file, flagged as such."""
    res = run(
        str(TABLES_DIR),
        census_division_loads=str(PROJECT_ROOT / DEMO_CENSUS),
        restart_file=str(fake_restart),
        year=RUN_YEAR,
        base_year=2023,
        load_dir=str(DATA),
        loss_adj=1.0,
        hourly8760=False,
        output_dir=str(tmp_path),
        verbose=False,
    )
    folder = Path(res['output_dir'])
    onsite = pd.read_csv(folder / 'onsite_generation_by_year_region.csv')
    assert set(onsite.annual_loads_source) == {'user file'}
    assert not (folder / 'census_loads_from_NEMS_AEO2026.csv').exists()  # loads are not from NEMS
    assert (folder / 'census_loads_used.csv').exists()


@NEEDS_2023
def test_no_restart_file_means_no_generation_file(tmp_path):
    """With a user load file only, nothing is written and the run still works."""
    res = run(
        str(TABLES_DIR),
        census_division_loads=str(PROJECT_ROOT / DEMO_CENSUS),
        year=RUN_YEAR,
        base_year=2023,
        load_dir=str(DATA),
        loss_adj=1.0,
        output_dir=str(tmp_path),
        verbose=False,
    )
    assert not (Path(res['output_dir']) / 'onsite_generation_by_year_region.csv').exists()


def test_config_allows_a_nems_source_next_to_a_user_file(fake_restart):
    """A restart file may accompany a user load file; two user files or two NEMS sources may not."""
    config = LoadConfig(census_division_loads=DEMO_CENSUS, nems_outputs_dir=DATA, base_year=2023)
    assert config.nems_outputs_dir == DATA and config.census_division_loads is not None
    with pytest.raises(ValidationError, match='exactly one source'):
        LoadConfig(census_division_loads=DEMO_CENSUS, emm_region_loads=DEMO_CENSUS, base_year=2023)
    with pytest.raises(ValidationError, match='exactly one source'):
        LoadConfig(base_year=2023)


# ---- the real file
# ---------------------------------------------------------------------------------------
def _real_restart() -> Path | None:
    """The first cb2026 restart file under input/load, or None if it was not copied in."""
    found = sorted((DATA / 'AEO2026_outputs_by_scenario' / 'cb2026').glob('*/restart.rlx'))
    return found[0] if found else None


@pytest.mark.skipif(_real_restart() is None, reason='no cb2026 restart.rlx under input/load')
def test_real_file_own_use_is_the_larger_slot_and_matches_the_residential_gap():
    """Residential own use in 2025 is the larger slot and about 185 TBtu (the QELRS gap)."""
    restart = _real_restart()
    assert restart is not None
    gen = extract_onsite_generation(restart, YEAR)
    resid = gen[gen.category == 'residential']
    assert resid.own_use_mwh.sum() > resid.sold_mwh.sum()
    assert resid.own_use_mwh.sum() / 292_830 == pytest.approx(185.1, abs=0.5)  # TBtu
    z, names = open_restart(restart)
    us_total = z[names['cgresgen']][10, YEAR - 1990].sum()  # division 11 = US total, GWh
    assert us_total * 1e3 == pytest.approx(resid.own_use_mwh.sum() + resid.sold_mwh.sum())
