"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Tests for src.models.electricity.param_source_loader and its resulting PARAM_SOURCES dict
"""

from definitions import PROJECT_ROOT
from src.models.electricity import data_ingestor
from src.models.electricity.param_source_loader import ParamSource, load_param_sources

PARAM_SOURCES_TOML = PROJECT_ROOT / 'src/models/electricity/param_sources.toml'

# Expected reference: one entry per C-EMM data-needs sheet row.  Each file is named for its key
# and the value column carries its units (MW, MWh, USD, frac, hr).  Used to audit that the TOML
# matches expectations.
_EXPECTED_PARAM_SOURCES = {
    'storage_efficiency': ('storage_efficiency.csv', ('tech',), 'storage_efficiency_frac'),
    'capital_cost': (
        'capital_cost.csv',
        ('region', 'tech', 'step', 'year'),
        'capital_cost_usd_per_mw',
    ),
    'capital_cost_initial': (
        'capital_cost_initial.csv',
        ('region', 'tech', 'step'),
        'capital_cost_initial_usd_per_mw',
    ),
    'capacity_factor_vre': (
        'capacity_factor_vre.csv',
        ('region', 'tech', 'step', 'hour'),
        'capacity_factor_vre_frac',
    ),
    'fom_cost': ('fom_cost.csv', ('region', 'tech', 'step'), 'fom_cost_usd_per_mw_yr'),
    'storage_duration': ('storage_duration.csv', ('tech',), 'storage_duration_hr'),
    'hydro_capacity_factor': (
        'hydro_capacity_factor.csv',
        ('region', 'season'),
        'hydro_capacity_factor_frac',
    ),
    'learning_rate': ('learning_rate.csv', ('tech',), 'learning_exponent'),
    'ramp_down_cost': ('ramp_down_cost.csv', ('tech',), 'ramp_down_cost_usd_per_mwh'),
    'ramp_rate': ('ramp_rate.csv', ('tech',), 'ramp_rate_frac_per_hr'),
    'ramp_up_cost': ('ramp_up_cost.csv', ('tech',), 'ramp_up_cost_usd_per_mwh'),
    'reserve_cost': ('reserve_cost.csv', ('reserve_type', 'tech'), 'reserve_cost_usd_per_mwh'),
    'planning_reserve_margin': (
        'planning_reserve_margin.csv',
        ('region',),
        'planning_reserve_margin_frac',
    ),
    'reserve_tech_limit': (
        'reserve_tech_limit.csv',
        ('reserve_type', 'tech'),
        'reserve_tech_limit_frac',
    ),
    'available_capacity': (
        'available_capacity.csv',
        ('region', 'tech', 'step', 'year'),
        'available_capacity_mw',
    ),
    'supply_curve_learning': ('supply_curve_learning.csv', ('tech',), 'supply_curve_learning_mw'),
    'generation_cost': (
        'generation_cost.csv',
        ('region', 'tech', 'step', 'year', 'season'),
        'generation_cost_usd_per_mwh',
    ),
    'tran_cost': (
        'tran_cost.csv',
        ('region_dest', 'region_source', 'year'),
        'tran_cost_usd_per_mwh',
    ),
    'tran_cost_intl': (
        'tran_cost_intl.csv',
        ('region', 'region_intl', 'step', 'year'),
        'tran_cost_intl_usd_per_mwh',
    ),
    'tran_limit': (
        'tran_limit.csv',
        ('region_dest', 'region_source', 'year', 'season'),
        'tran_limit_mw',
    ),
    'tran_limit_cap_intl': (
        'tran_limit_cap_intl.csv',
        ('region', 'region_intl', 'year', 'season'),
        'tran_limit_cap_intl_mw',
    ),
    'supply_limit_intl': (
        'supply_limit_intl.csv',
        ('region_intl', 'step', 'year', 'season'),
        'supply_limit_intl_mw',
    ),
}

_REQUIRED_KEYS = {
    'storage_efficiency',
    'storage_duration',
    'capacity_factor_vre',
    'hydro_capacity_factor',
    'generation_cost',
    'available_capacity',
    'fom_cost',
}


def test_load_param_sources_count_and_keys():
    """All 22 entries load, keyed by the expected names."""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    assert len(loaded) == len(_EXPECTED_PARAM_SOURCES) == 22
    assert set(loaded.keys()) == set(_EXPECTED_PARAM_SOURCES.keys())


def test_load_param_sources_transcription_audit():
    """Every filename/index_cols/value_col matches the expected tuple exactly."""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    for key, (filename, index_cols, value_col) in _EXPECTED_PARAM_SOURCES.items():
        source = loaded[key]
        assert source.key == key
        assert source.filename == filename
        assert source.index_cols == index_cols
        assert source.value_col == value_col


def test_load_param_sources_types():
    """Every entry is a ParamSource with a tuple index and a bool required flag."""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    for source in loaded.values():
        assert isinstance(source, ParamSource)
        assert isinstance(source.index_cols, tuple)
        assert isinstance(source.required, bool)


def test_required_flags_match_switch_gating_table():
    """required=True set matches the always-used files; everything else is switch-gated."""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    required = {key for key, source in loaded.items() if source.required}
    assert required == _REQUIRED_KEYS


def test_data_ingestor_param_sources_matches_loader():
    """data_ingestor.PARAM_SOURCES (built at import time) matches a direct load."""
    assert data_ingestor.PARAM_SOURCES == load_param_sources(PARAM_SOURCES_TOML)
