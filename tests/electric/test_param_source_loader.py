"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Tests for src.models.electricity.param_source_loader and its resulting PARAM_SOURCES dict
"""

from definitions import PROJECT_ROOT
from src.models.electricity import data_ingestor
from src.models.electricity.param_source_loader import ParamSource, load_param_sources

PARAM_SOURCES_TOML = PROJECT_ROOT / 'src/models/electricity/param_sources.toml'

# Transcription reference: the original PARAM_SOURCES dict literal, verbatim, before the
# TOML migration. Used only to audit that no data was lost/reordered in the conversion.
_ORIGINAL_PARAM_SOURCES = {
    'battery_efficiency': ('BatteryEfficiency.csv', ('tech',), 'BatteryEfficiency'),
    'cap_cost': ('CapCost.csv', ('region', 'tech', 'step', 'year'), 'CapCost'),
    'cap_cost_initial': ('CapCostInitial.csv', ('region', 'tech', 'step'), 'CapCostInitial'),
    'cap_factor_vre': ('CapFactorVRE.csv', ('region', 'tech', 'step', 'hour'), 'CapFactorVRE'),
    'fom_cost': ('FOMCost.csv', ('region', 'tech', 'step'), 'FOMCost'),
    'h2_price': ('H2Price.csv', ('region', 'tech', 'step', 'year', 'season'), 'H2Price'),
    'hours_to_buy': ('HourstoBuy.csv', ('tech',), 'HourstoBuy'),
    'hydro_cap_factor': ('HydroCapFactor.csv', ('region', 'season'), 'HydroCapFactor'),
    'learning_rate': ('LearningRate.csv', ('tech',), 'LearningRate'),
    'ramp_down_cost': ('RampDownCost.csv', ('tech',), 'RampDownCost'),
    'ramp_rate': ('RampRate.csv', ('tech',), 'RampRate'),
    'ramp_up_cost': ('RampUpCost.csv', ('tech',), 'RampUpCost'),
    'reg_reserves_cost': ('RegReservesCost.csv', ('tech',), 'RegReservesCost'),
    'reserve_margin': ('ReserveMargin.csv', ('region',), 'ReserveMargin'),
    'res_tech_upper_bound': ('ResTechUpperBound.csv', ('restype', 'tech'), 'ResTechUpperBound'),
    'supply_curve': ('SupplyCurve.csv', ('region', 'tech', 'step', 'year'), 'SupplyCurve'),
    'supply_curve_learning': ('SupplyCurveLearning.csv', ('tech',), 'SupplyCurveLearning'),
    'supply_price': (
        'SupplyPrice.csv',
        ('region', 'tech', 'step', 'year', 'season'),
        'SupplyPrice',
    ),
    'tran_cost': ('TranCost.csv', ('source_region', 'destination_region', 'year'), 'TranCost'),
    'tran_cost_int': (
        'TranCostInt.csv',
        ('region', 'region_international', 'step', 'year'),
        'TranCostInt',
    ),
    'tran_limit': (
        'TranLimit.csv',
        ('source_region', 'destination_region', 'season', 'year'),
        'TranLimit',
    ),
    'tran_limit_cap_int': (
        'TranLimitCapInt.csv',
        ('region', 'region_international', 'year', 'season'),
        'TranLimitCapInt',
    ),
    'tran_limit_gen_int': (
        'TranLimitGenInt.csv',
        ('region_international', 'step', 'year', 'season'),
        'TranLimitGenInt',
    ),
}

_REQUIRED_KEYS = {
    'battery_efficiency',
    'hours_to_buy',
    'cap_factor_vre',
    'hydro_cap_factor',
    'supply_price',
    'supply_curve',
    'h2_price',
}


def test_load_param_sources_count_and_keys():
    """all 23 entries load, keyed by the same names as the original dict"""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    assert len(loaded) == len(_ORIGINAL_PARAM_SOURCES) == 23
    assert set(loaded.keys()) == set(_ORIGINAL_PARAM_SOURCES.keys())


def test_load_param_sources_transcription_audit():
    """every filename/index_cols/value_col matches the original tuple exactly"""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    for key, (filename, index_cols, value_col) in _ORIGINAL_PARAM_SOURCES.items():
        source = loaded[key]
        assert source.key == key
        assert source.filename == filename
        assert source.index_cols == index_cols
        assert source.value_col == value_col


def test_load_param_sources_types():
    """every entry is a ParamSource with a tuple index and a bool required flag"""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    for source in loaded.values():
        assert isinstance(source, ParamSource)
        assert isinstance(source.index_cols, tuple)
        assert isinstance(source.required, bool)


def test_required_flags_match_switch_gating_table():
    """required=True set matches the always-used files; everything else is switch-gated"""
    loaded = load_param_sources(PARAM_SOURCES_TOML)

    required = {key for key, source in loaded.items() if source.required}
    assert required == _REQUIRED_KEYS


def test_data_ingestor_param_sources_matches_loader():
    """data_ingestor.PARAM_SOURCES (built at import time) matches a direct load"""
    assert data_ingestor.PARAM_SOURCES == load_param_sources(PARAM_SOURCES_TOML)
