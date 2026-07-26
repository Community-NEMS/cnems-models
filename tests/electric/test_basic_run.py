"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/15/26

A temporary (?) test to lock down the current outputs of a basic no-frills test run

"""

import logging
from pathlib import Path

import pytest
from pyomo.common.numeric_types import value
from tabulate import tabulate

from analysis_tools.model_diagnostics import (
    breakdown_obj_elements,
    capacity_inspector,
    gather_constraint_data,
    gather_param_data,
    gather_set_data,
    gather_var_data,
    load_inspector,
)
from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.models.electricity.data_ingestor import PARAM_SOURCES
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.sequencer import (
    _LEARNING_TOLERANCE,
    ElectricitySequencer,
    run_elec_model,
)

# "verbose" mode is supplied for these basic tests to screen output key data to aid in
# development
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

# TODO:  Add combination with expansion + margin required to test combo
#        constraint near line 1500 in model

# Test configurations with expected ORIGINAL outputs:
# (constraint counts below are pre-VRE_UB-fix; current expectations add +192 constraints)
# (variable counts below are pre-removal of season index from capacity add +450 vars, +450 constr)
# Run Type                                Total Cost    Variables   Constraints
# --------------------------------------  ------------  ----------  -----------
# Basic No-Frills                         3452103301.9       17886        19440
# Exchange Enabled                        2278237043.0       21342        23088
# Expansion (no learning)                 3455793875.5       18060        19566
# Ramping Required                        3522284566.9       32862        41904
# Reserve Margin (mandatory expansion)    4925573167.9       19212        22446
# Agg Years                               ??  Broken.  Suspect it is used in preprocessor

configs = [
    ('basic', 3452103301.9, 17436, 19182),
    ('exchange', 2278237043.0, 20892, 22830),
    ('expansion_no_learning', 3455793875.5, 17610, 19308),
    ('ramping', 3522284566.9, 32412, 41646),
    ('reserve_with_expansion_no_learning', 4925573167.9, 18762, 22188),
    (
        'reserve_spinning_with_expansion_no_learning',
        5138465483.62,
        61962,
        67116,
    ),  # <-- no good starting value
    ('agg_years', 13363835326.77, 17436, 19182),  # <-- no good starting value
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
    """Perform a couple of basic runs (with some features in isolation).

    Results are compared to captured values.

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
            total_cap = sum(value(elec_model.capacity_total[i]) for i in elec_model.capacity_total)
            print(f'sum of capacity: {total_cap}')
            builds = sum(value(elec_model.capacity_builds[i]) for i in elec_model.capacity_builds)
            print(f'sum of expansion: {builds}')
            retirements = sum(
                value(elec_model.capacity_retirements[i]) for i in elec_model.capacity_retirements
            )
            print(f'sum of retirements: {retirements}')
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


def test_linear_learning(learning_config_set, caplog: pytest.LogCaptureFixture):
    """Exercise the linear-learning iteration on the micro dataset in tests/electric/test_data_linear_learning_test.

    The dataset (single region 'CA', single tech 'NG_Fired_Plant') has 2.0 units of existing
    capacity against a load that starts at 4.0 units and grows 5 units/year, forcing step-3
    builds every year.  Asserts on the convergence log emitted by
    ``ElectricitySequencer.solve_model`` each iteration.
    """
    common_config, elec_config = learning_config_set

    sequencer = ElectricitySequencer()
    elec_model = sequencer.build_model(common_config, elec_config)

    # with learning enabled, the learning-gated param_sources.toml entries should be wired in
    for key, attr in {
        'cap_cost_initial': 'CapCostInitial',
        'learning_rate': 'LearningRate',
        'supply_curve_learning': 'SupplyCurveLearning',
    }.items():
        assert not PARAM_SOURCES[key].required
        assert hasattr(elec_model, attr), f'{attr} missing with learning enabled'

    # DEBUG level additionally captures the per-key CapCostLearning updates for the verbose table
    capture_level = logging.DEBUG if verbose else logging.INFO
    with caplog.at_level(capture_level, logger='src.models.electricity.sequencer'):
        status = sequencer.solve_model()
    assert status is IterationStatus.BEST, f'solve failed with status {status}'

    if verbose:
        # args of the sequencer.update_expansion_cost debug records: (r, tech, step, y, old, new)
        cost_rows = [
            rec.args for rec in caplog.records if rec.msg.startswith('Reduced CapCostLearning')
        ]
        y0 = value(elec_model.y0_learning)
        initial_costs = {
            idx: value(elec_model.CapCostInitial[idx]) for idx in elec_model.CapCostInitial
        }
        print(f'\ny0 for learning: {y0}')
        print(f'CapCostInitial: {initial_costs}')
        # one record per CapCostLearning key per iteration; recover the iteration index by chunking
        n_keys = len(elec_model.CapCostLearning)
        table = [
            (i // n_keys, y, old, new)
            for i, (_r, _tech, _step, y, old, new) in enumerate(cost_rows)
        ]
        headers = ['iteration', 'year', 'old cost', 'new cost']
        print(tabulate(table, headers=headers, floatfmt='.2f'))

    # gather the per-iteration convergence records: args are (iteration, eps)
    tolerance_records = [
        rec.args
        for rec in caplog.records
        if rec.msg.startswith('Tolerance in linear learning iteration')
    ]
    assert len(tolerance_records) >= 1, 'no learning iterations were logged'
    _, final_eps = tolerance_records[-1]
    assert final_eps < _LEARNING_TOLERANCE, (
        f'learning did not converge: final eps {final_eps} >= tolerance {_LEARNING_TOLERANCE}'
    )

    # the load design must force expansion builds (step 3), otherwise learning has nothing to do
    total_builds = sum(value(elec_model.capacity_builds[idx]) for idx in elec_model.capacity_builds)
    assert total_builds > 0, 'expected capacity builds; dataset failed to force expansion'
