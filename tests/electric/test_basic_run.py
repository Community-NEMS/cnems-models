"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/15/26

A temporary (?) test to lock down the current outputs of a basic no-frills test run

"""

from pathlib import Path

import pyomo.environ as pyo
import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.data_ingestor import PARAM_SOURCES
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.sequencer import run_elec_model, solve_elec_model
from analysis_tools.model_diagnostics import (
    breakdown_obj_elements,
    capacity_inspector,
    gather_constraint_data,
    gather_param_data,
    gather_set_data,
    gather_var_data,
    load_inspector,
)

verbose = False

# Always-required ParamSource keys -> the pyomo Param attribute they end up as on PowerModel.
# Declared unconditionally in electricity_model.py, so should exist for every config.
_ALWAYS_REQUIRED_ATTRS = {
    'battery_efficiency': 'BatteryEfficiency',
    'hours_to_buy': 'HourstoBuy',
    'cap_factor_vre': 'CapFactorVRE',
    'hydro_cap_factor': 'HydroCapFactor',
    'supply_price': 'SupplyPrice',
    'supply_curve': 'SupplyCurve',
    'h2_price': 'H2Price',
}

# Switch-gated ParamSource keys -> the pyomo Param attribute they end up as, plus which
# ElecConfig switch gates their declaration in electricity_model.py.
_GATED_ATTRS = {
    'fom_cost': ('FOMCost', 'capacity_expansion'),
    'cap_cost': ('CapCostLearning', 'capacity_expansion'),
    'tran_cost': ('TranCost', 'regional_exchange'),
    'tran_cost_int': ('TranCostInt', 'regional_exchange'),
    'tran_limit': ('TranLimit', 'regional_exchange'),
    'tran_limit_cap_int': ('TranLimitCapInt', 'regional_exchange'),
    'tran_limit_gen_int': ('TranLimitGenInt', 'regional_exchange'),
    'reserve_margin': ('ReserveMargin', 'reserve_margin_required'),
    'ramp_up_cost': ('RampUpCost', 'ramping_required'),
    'ramp_down_cost': ('RampDownCost', 'ramping_required'),
    'ramp_rate': ('RampRate', 'ramping_required'),
    'reg_reserves_cost': ('RegReservesCost', 'spinning_reserve_required'),
    'res_tech_upper_bound': ('ResTechUpperBound', 'spinning_reserve_required'),
}
# Note: cap_cost_initial/learning_rate/supply_curve_learning are gated by capacity_expansion +
# expansion_learning_type != DISABLED, which none of this file's `configs` cases enable -- those
# three are instead cross-checked in test_linear_learning below.

# TODO:  Add combination with expansion + margin required to test combo constraint near line 1500 in model

# Test configurations with expected ORIGINAL outputs:
# Run Type                                  Total Cost         Variables    Constraints      Notes for new
# ----------------------------------------  -----------------  -----------  -----------     ---------------
# Basic No-Frills                           3452103301.9            17886        19440      constr = 19632 VRE_UB fix (+192)
# Exchange Enabled                          2278237043.0            21342        23088      constr = 23280 (+192 constr, from above)
# Expansion (no learning)                   3455793875.5            18060        19566      same... +192
# Ramping Required                          3522284566.9            32862        41904      same... +192
# Reserve Margin (mandatory expansion)      4925573167.9            19212        22446      same... +192
# Agg Years                                 ??  Broken.  Suspect it is used in preprocessor

configs = [
    ('basic', 3452103301.9, 17886, 19632),
    ('exchange', 2278237043.0, 21342, 23280),
    ('expansion_no_learning', 3455793875.5, 18060, 19758),
    ('ramping', 3522284566.9, 32862, 42096),
    ('reserve_with_expansion_no_learning', 4925573167.9, 19212, 22638),
    (
        'reserve_spinning_with_expansion_no_learning',
        5138465483.62,
        62412,
        67566,
    ),  # <-- no good starting value
    ('agg_years', 13363835326.77, 17886, 19632),  # <-- no good starting value
]


@pytest.mark.parametrize(
    'config_info,expected_total_cost,expected_nvariables,expected_nconstraints',
    configs,
    ids=[
        'Basic No-Frills',
        'Exchange Enabled',
        'Expansion (no learning)',
        'Ramping Required',
        'Reserve with Expansion (no learning)',
        'Reserve (with spinning) with Expansion (no learning)',
        'Agg Years',
    ],
)
def test_basic_run(config_info, expected_total_cost, expected_nvariables, expected_nconstraints):
    """
    Perform a couple of basic runs (with some features in isolation) and compare results to captured values

    dev notes:
    1.  basic config file turns OFF many features that may need separate verification
    2.  the values captured here for test were generated from run of legacy code and are *assumed*
        good for this test and dataset
    """
    # config_path = Path(PROJECT_ROOT, 'tests/electric/meta_config.toml')
    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)

    # introduce the ElecConfig
    elec_config = ElecConfig(**remainder.pop('elec_config'))

    # make adjustments based on the config_info
    if config_info == 'agg_years':
        common_config.aggregate_years = True
    elif config_info == 'ramping':
        elec_config.ramping_required = True
    elif config_info == 'reserve_with_expansion_no_learning':
        elec_config.capacity_expansion = True
        elec_config.reserve_margin_required = True
        elec_config.expansion_learning_type = ExpansionLearningType.DISABLED
    elif config_info == 'reserve_spinning_with_expansion_no_learning':
        elec_config.capacity_expansion = True
        elec_config.spinning_reserve_required = True
        elec_config.reserve_margin_required = True
        elec_config.expansion_learning_type = ExpansionLearningType.DISABLED
    elif config_info == 'expansion_no_learning':
        elec_config.expansion_learning_type = ExpansionLearningType.DISABLED
        elec_config.capacity_expansion = True
    elif config_info == 'exchange':
        elec_config.regional_exchange = True

    elec_model = run_elec_model(common_config, elec_config, solve=True)
    if verbose:
        print('\n~~ Sets ~~')
        gather_set_data(elec_model)
        print('\n~~ Variables ~~')
        gather_var_data(elec_model)
        print('\n~~ Parameters ~~')
        gather_param_data(elec_model)
        print('\n~~ Constraints ~~')
        gather_constraint_data(elec_model)
        print()
        breakdown_obj_elements(elec_model)
        capacity_inspector(elec_model, region='7', year=2025)
        load_inspector(elec_model, region='7')
        if hasattr(elec_model, 'fixed_om_cost'):
            elec_model.fixed_om_cost.pprint()
            # print(f'terms in om cost: {elec_model.fixed_om_cost.linear_vars}')
            print(
                f'sum of capacity: {sum(value(elec_model.capacity_total[i]) for i in elec_model.capacity_total)}'
            )
            print(
                f'sum of expansion: {sum(value(elec_model.capacity_builds[i]) for i in elec_model.capacity_builds)}'
            )
            print(
                f'sum of retirements: {sum(value(elec_model.capacity_retirements[i]) for i in elec_model.capacity_retirements)}'
            )
    # for test development/capture:
    print(value(elec_model.total_cost), elec_model.nvariables(), elec_model.nconstraints())

    assert value(elec_model.total_cost) == pytest.approx(expected_total_cost), (
        f'found {value(elec_model.total_cost)} total cost'
    )
    assert elec_model.nvariables() == expected_nvariables, (
        f'found {elec_model.nvariables()} variables'
    )
    assert elec_model.nconstraints() == expected_nconstraints, (
        f'found {elec_model.nconstraints()} constraints'
    )

    # Empirically verify param_sources.toml's `required` flags against actual model structure:
    # always-required sources must be present regardless of config, switch-gated sources must
    # be present only when their gating switch is active for this config.
    for key, attr in _ALWAYS_REQUIRED_ATTRS.items():
        assert PARAM_SOURCES[key].required
        assert hasattr(elec_model, attr), f'{attr} missing for config {config_info!r}'
    for key, (attr, switch_name) in _GATED_ATTRS.items():
        assert not PARAM_SOURCES[key].required
        expected_present = getattr(elec_config, switch_name)
        assert hasattr(elec_model, attr) == expected_present, (
            f'{attr} presence mismatch for config {config_info!r}'
        )


def test_linear_learning(config_set):
    """fundamental test to exercise linear learning capability"""

    # TODO:  This needs development.  RN, it just ensures it runs with at least 1 iteration

    common_config, elec_config = config_set
    # override settings to enable expansion w/ learning
    elec_config.capacity_expansion = True
    elec_config.expansion_learning_type = ExpansionLearningType.LINEAR
    elec_config.region_filter = list('78913462')

    elec_model = run_elec_model(common_config, elec_config, solve=False)

    # with learning enabled, the learning-gated param_sources.toml entries should be wired in
    for key, attr in {
        'cap_cost_initial': 'CapCostInitial',
        'learning_rate': 'LearningRate',
        'supply_curve_learning': 'SupplyCurveLearning',
    }.items():
        assert not PARAM_SOURCES[key].required
        assert hasattr(elec_model, attr), f'{attr} missing with learning enabled'

    # the basic model above requires no capital expansion to meet load => no learning
    # as a TEMP coaxing, we'll increase the load

    # Capture current Load values
    load_data = {idx: value(elec_model.Load[idx]) * 2 for idx in elec_model.Load}
    # Delete the immutable parameter
    elec_model.del_component(elec_model.Load)
    # Re-initialize with increased values
    elec_model.Load = pyo.Param(load_data.keys(), initialize=load_data, mutable=False)

    solve_elec_model(elec_model, elec_config=elec_config)
