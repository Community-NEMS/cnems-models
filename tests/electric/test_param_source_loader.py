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

# Expected reference: filename/index_cols transcribed verbatim from the original (pre-TOML)
# PARAM_SOURCES dict literal; value_col reflects the semantic rename of the CSV value columns
# (per input/electricity/cem_inputs/data_fixers/value_rename_map.txt), which replaced the
# original filename-stem column names. Used to audit that the TOML matches expectations.
_EXPECTED_PARAM_SOURCES = {
    'battery_efficiency': ('BatteryEfficiency.csv', ('tech',), 'efficiency'),
    'cap_cost': ('CapCost.csv', ('region', 'tech', 'step', 'year'), 'cost'),
    'cap_cost_initial': ('CapCostInitial.csv', ('region', 'tech', 'step'), 'cost'),
    'cap_factor_vre': ('CapFactorVRE.csv', ('region', 'tech', 'step', 'hour'), 'value'),
    'fom_cost': ('FOMCost.csv', ('region', 'tech', 'step'), 'cost'),
    'hours_to_buy': ('HourstoBuy.csv', ('tech',), 'hours'),
    'hydro_cap_factor': ('HydroCapFactor.csv', ('region', 'season'), 'value'),
    'learning_rate': ('LearningRate.csv', ('tech',), 'rate'),
    'ramp_down_cost': ('RampDownCost.csv', ('tech',), 'cost'),
    'ramp_rate': ('RampRate.csv', ('tech',), 'rate'),
    'ramp_up_cost': ('RampUpCost.csv', ('tech',), 'cost'),
    'reg_reserves_cost': ('RegReservesCost.csv', ('tech',), 'cost'),
    'reserve_margin': ('ReserveMargin.csv', ('region',), 'margin'),
    'res_tech_upper_bound': ('ResTechUpperBound.csv', ('restype', 'tech'), 'value'),
    'supply_curve': ('SupplyCurve.csv', ('region', 'tech', 'step', 'year'), 'capacity'),
    'supply_curve_learning': ('SupplyCurveLearning.csv', ('tech',), 'capacity'),
    'supply_price': (
        'SupplyPrice.csv',
        ('region', 'tech', 'step', 'year', 'season'),
        'cost',
    ),
    'tran_cost': ('TranCost.csv', ('destination_region', 'source_region', 'year'), 'cost'),
    'tran_cost_int': (
        'TranCostInt.csv',
        ('region', 'region_international', 'step', 'year'),
        'cost',
    ),
    'tran_limit': (
        'TranLimit.csv',
        ('destination_region', 'source_region', 'season', 'year'),
        'value',
    ),
    'tran_limit_cap_int': (
        'TranLimitCapInt.csv',
        ('region', 'region_international', 'year', 'season'),
        'capacity',
    ),
    'tran_limit_gen_int': (
        'TranLimitGenInt.csv',
        ('region_international', 'step', 'year', 'season'),
        'generation',
    ),
}

_REQUIRED_KEYS = {
    'battery_efficiency',
    'hours_to_buy',
    'cap_factor_vre',
    'hydro_cap_factor',
    'supply_price',
    'supply_curve',
}


def test_load_param_sources_count_and_keys():
    """All 22 entries load, keyed by the same names as the original dict."""
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
