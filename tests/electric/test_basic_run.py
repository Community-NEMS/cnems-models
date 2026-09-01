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
    cost_per_kwh,
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

# ParamSource keys are also the pyomo Param attribute name on PowerModel, so these are checked
# with `hasattr(model, key)` directly.
#
# Always-required sources: declared unconditionally in electricity_model.py, so present for
# every config.
_ALWAYS_REQUIRED = frozenset(
    {
        'battery_efficiency',
        'hours_to_buy',
        'cap_factor_vre',
        'hydro_cap_factor',
        'supply_price',
        'supply_curve',
        'fom_cost',
    }
)

# Switch-gated sources -> the ElecConfig switch gating their declaration in electricity_model.py.
_GATED_BY_SWITCH = {
    'cap_cost': 'capacity_expansion',
    'tran_cost': 'regional_exchange',
    'tran_cost_int': 'regional_exchange',
    'tran_limit': 'regional_exchange',
    'tran_limit_cap_int': 'regional_exchange',
    'tran_limit_gen_int': 'regional_exchange',
    'reserve_margin': 'reserve_margin_required',
    'ramp_up_cost': 'ramping_required',
    'ramp_down_cost': 'ramping_required',
    'ramp_rate': 'ramping_required',
    'reg_reserves_cost': 'spinning_reserve_required',
    'res_tech_upper_bound': 'spinning_reserve_required',
}
# Note: cap_cost_initial/learning_rate/supply_curve_learning are gated by capacity_expansion +
# expansion_learning_type != DISABLED, which none of this file's `configs` cases enable -- those
# three are instead cross-checked in test_linear_learning below.

# TODO:  Add combination with expansion + margin required to test combo
#        constraint near line 1500 in model

# Test configurations with expected outputs:
# (Total Cost is kept current; the variable/constraint counts are the ORIGINAL legacy capture)
# (constraint counts below are pre-VRE_UB-fix; current expectations add +192 constraints)
# (variable counts below are pre-removal of season index from capacity add +450 vars, +450 constr)
# Run Type                                Total Cost    Variables   Constraints
# --------------------------------------  ------------  ----------  -----------
# Basic No-Frills                         3669432143.1       17886        19440
# Exchange Enabled                        2586014294.5       21342        23088
# Expansion (no learning)                 3668698225.7       18060        19566
# Ramping Required                        3739419337.9       32862        41904
# Reserve Margin (mandatory expansion)    5138454401.3       19212        22446
# Agg Years                               ??  Broken.  Suspect it is used in preprocessor

# Note: the six configs below that run with spinning_reserve_required=False had their expected
# total cost re-captured when the `generation_hydro_ub` conditional was re-scoped.  Previously the
# `if spinning_reserve_required else 0` wrapped the whole LHS (generation_total included), so with
# spinning reserves off the constraint collapsed to `0 <= capacity * cf * weight` and hydro
# generation was effectively unbounded.  Scoping the conditional to the reserve sum only -- matching
# generation_dispatchable_ub / generation_vre_ub / storage_outflow_ub -- restores the bound, which
# raises each of these objectives by ~4-13%.  Variable and constraint counts are unchanged: the
# degenerate form still referenced capacity_total (a Var), so the constraint was always constructed.
# Note: all seven expected total costs below were re-captured when the `h2_price` param and its
# objective term were removed.  That term charged an H2 fuel cost on generation from `tech_h2`, so
# dropping it lowers each objective by ~0.0003%.  Variable and constraint counts are unaffected:
# the term only added a Param coefficient onto `generation_total` entries that other constraints
# already referenced.
# Note: all seven expected variable counts below dropped by 6 when the `var_elec_request` Var (and
# its companion `fixed_elec_request` Param) were removed.  That Var held an external electricity
# demand from the deleted hydrogen module; nothing set it and no constraint or objective term
# referenced it, so it contributed |region_analyze| x |year| = 3 x 2 = 6 free variables to every
# config.  Total costs and constraint counts are unaffected.
# Note: the four expected total costs for configs running with capacity_expansion=False (basic,
# exchange, ramping, agg_years) were re-captured when the cost logic was adjusted to apply FOM costs
# universally instead of just to expansion.  The `fom_cost` param and the `fixed_om_cost` objective
# term are now declared unconditionally rather than inside the `if elec_config.capacity_expansion`
# block (where the term was otherwise hard-set to 0.0), so every config charges FOM against
# `capacity_total`.  That adds a constant ~4.49M to each of those objectives (~18.0M for agg_years,
# which weights multiple years into the representative one).  The three capacity_expansion=True
# configs are unchanged -- they already carried the term -- and no variable or constraint count
# moves, since `fixed_om_cost` is an Expression over `capacity_total`, which every config already
# constructed.
configs = [
    ('basic', 3669432143.12, 17430, 19182),
    ('exchange', 2586014294.54, 20886, 22830),
    ('expansion_no_learning', 3668698225.74, 17604, 19308),
    ('ramping', 3739419337.87, 32406, 41646),
    ('reserve_with_expansion_no_learning', 5138454401.3, 18756, 22188),
    # no good starting value,
    # but got 20% reduction after reformulating to honor sparsity from the upper bound table
    (
        'reserve_spinning_with_expansion_no_learning',
        5140330132.57,
        51588,
        56748,
    ),
    ('agg_years', 14203013438.65, 17430, 19182),  # <-- no good starting value
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

        print()
        # drumroll...
        cost_per_kwh(elec_model)

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
    for key in _ALWAYS_REQUIRED:
        assert PARAM_SOURCES[key].required
        assert hasattr(elec_model, key), f'{key} missing for config {config_info!r}'
    for key, switch_name in _GATED_BY_SWITCH.items():
        assert not PARAM_SOURCES[key].required
        expected_present = getattr(elec_config, switch_name)
        assert hasattr(elec_model, key) == expected_present, (
            f'{key} presence mismatch for config {config_info!r}'
        )


def test_linear_learning(learning_config_set, caplog: pytest.LogCaptureFixture):
    """Exercise the linear-learning iteration on a single-region micro dataset.

    The dataset lives in tests/electric/test_data_linear_learning_test.  It has a single region
    'CA' and a single tech 'NG_Fired_Plant', with 2.0 units of existing capacity against a load
    that starts at 4.0 units and grows 5 units/year, forcing step-3 builds every year.  Asserts on
    the convergence log emitted by ``ElectricitySequencer.solve_model`` each iteration.
    """
    common_config, elec_config = learning_config_set

    sequencer = ElectricitySequencer()
    elec_model = sequencer.build_model(common_config, elec_config)

    # with learning enabled, the learning-gated param_sources.toml entries should be wired in
    for key in ('cap_cost_initial', 'learning_rate', 'supply_curve_learning'):
        assert not PARAM_SOURCES[key].required
        assert hasattr(elec_model, key), f'{key} missing with learning enabled'

    # DEBUG level additionally captures the per-key cap_cost updates for the verbose table
    capture_level = logging.DEBUG if verbose else logging.INFO
    with caplog.at_level(capture_level, logger='src.models.electricity.sequencer'):
        status = sequencer.solve_model()
    assert status is IterationStatus.BEST, f'solve failed with status {status}'

    if verbose:
        # args of the sequencer.update_expansion_cost debug records: (r, tech, step, y, old, new)
        cost_rows = [rec.args for rec in caplog.records if rec.msg.startswith('Reduced cap_cost')]
        y0 = value(elec_model.y0_learning)
        initial_costs = {
            idx: value(elec_model.cap_cost_initial[idx]) for idx in elec_model.cap_cost_initial
        }
        print(f'\ny0 for learning: {y0}')
        print(f'cap_cost_initial: {initial_costs}')
        # one record per cap_cost key per iteration; recover the iteration index by
        # chunking
        n_keys = len(elec_model.cap_cost)
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
