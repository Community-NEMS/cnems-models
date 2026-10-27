"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/15/26

A temporary (?) test to lock down the current outputs of a basic no-frills test run

"""

from pathlib import Path

import pyomo.environ as pyo
import pytest
from pyomo.common.numeric_types import value

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
from src.models.electricity.data_ingestor import PARAM_SOURCES
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.sequencer import run_elec_model

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
        'storage_efficiency',
        'storage_duration',
        'capacity_factor_hourly',
        'capacity_factor_seasonal',
        'generation_cost',
        'available_capacity',
        'fom_cost',
    }
)

# Switch-gated sources -> the ElecConfig switch gating their declaration in electricity_model.py.
_GATED_BY_SWITCH = {
    'capital_cost': 'capacity_expansion',
    'tran_cost': 'regional_exchange',
    'tran_cost_intl': 'regional_exchange',
    'tran_limit': 'regional_exchange',
    'tran_limit_cap_intl': 'regional_exchange',
    'supply_limit_intl': 'regional_exchange',
    'planning_reserve_margin': 'reserve_margin_required',
    'ramp_up_cost': 'ramping_required',
    'ramp_down_cost': 'ramping_required',
    'ramp_rate': 'ramping_required',
    'reserve_cost': 'spinning_reserve_required',
    'reserve_tech_limit': 'spinning_reserve_required',
}
# Note: capital_cost_initial/learning_rate/supply_curve_learning are gated by capacity_expansion +
# expansion_learning_type != DISABLED, so they are cross-checked in
# tests/electric/learning/test_learning_linear.py::test_linear_learning instead.

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
# Note: the agg_years expected total cost was re-captured (14203013438.65 -> 14564553802.5, ~+2.5%)
# when the parameter read's year filter was widened from `summary_years` to every year in
# `year_map`.  Previously the non-summary years were filtered out before aggregation, so the
# year-indexed tables were "averaged" over their representative year alone; they are now averaged
# over every year each summary year represents.  Only input data changed -- not the formulation --
# and the variable and constraint counts are unchanged.  The other configs do not aggregate years.
# Note: all eight expected total costs below were re-captured when the model moved from GW/GWh to
# MW/MWh internally.  Capacity, transmission and load inputs were scaled x1000 in the data, the
# x1000 cost adjustment in ParamData was removed, and the unit-bearing constants were rescaled.
# With fom_cost temporarily divided by 1000, every LP config reproduced its prior objective (term by
# term, to ~1e-14 relative) with unchanged variable and constraint counts, so the unit change
# itself is neutral.  The moves come from FOMCost, which was always in $/MW-yr but was charged
# against GW capacity and so was 1000x understated.  The dispatch-only configs rise by exactly 999x
# their prior fixed_om_cost; the expansion configs re-optimize (retiring capacity), rising 1.5-1.8x.
# Note: the expansion_no_learning and ramping expected total costs were re-captured when
# UNMET_LOAD_PRICE was raised from 500 to 50,000 $/MWh.  These are the two configs that solve with
# some unmet load (PENALTY status), so the higher price raises their objectives by ~0.04%; the
# other configs serve all load and are unchanged.  Variable and constraint counts do not move.
# Note: the reserve_spinning_with_expansion_no_learning expected total cost was re-captured
# (8079497724.77 -> 8081343408.89, ~+0.02%) when the wind and solar members of the regulation and
# flex requirements were taken from the VRE generation index instead of the reserve suppliers.
# Wind and solar supply no reserves, so the old source left both sets empty: regulation was 1% of
# load and the flex requirement was zero.  Only this config turns on operating reserves, and its
# variable and constraint counts do not move, since the fix adds terms to existing constraints.
configs = [
    ('basic', 8152448639.12, 17430, 19182),
    ('exchange', 7069030790.54, 20886, 22830),
    ('expansion_no_learning', 6474740524.53, 17604, 19308),
    ('ramping', 8225257879.01, 32406, 41646),
    ('reserve_with_expansion_no_learning', 8070923124.59, 18756, 22188),
    # no good starting value,
    # but got 20% reduction after reformulating to honor sparsity from the upper bound table
    (
        'reserve_spinning_with_expansion_no_learning',
        8081343408.89,
        51588,
        56748,
    ),
    ('agg_years', 32382704815.50, 17430, 19182),  # <-- no good starting value
    # Nonlinear learning is paired with the reserve margin deliberately.  Without it the optimum
    # builds nothing, the learning term multiplies zero, and the case would pin solver tolerance
    # noise rather than model behavior.  Counts match the reserve/expansion case above because
    # only the objective expression differs between learning modes.
    ('nonlinear_learning_with_reserve', 8168017354.43, 18756, 22188),
]

# Nonlinear learning solves through IPOPT, whose termination tolerance is much looser than the LP
# cases and varies with solver build, so the captured objective is compared at a matching
# tolerance rather than pytest.approx's 1e-6 relative default.
_NONLINEAR_REL_TOL = 1e-4


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
        'Nonlinear Learning with Reserve',
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
    # TODO:  Bring the test data into the test folder when data format changes stabilize
    #        This currently relies on data outside the test environment
    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)

    # introduce the ElecConfig
    elec_config = ElecConfig(**remainder.pop('elec_config'))

    # make adjustments based on the config_info
    if config_info == 'agg_years':
        # re-validate rather than assign, so the year-aggregation validator runs
        common_config = CommonConfig.model_validate(
            common_config.model_dump() | {'aggregate_years': True}
        )
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
        common_config.strict_validation = False
    elif config_info == 'nonlinear_learning_with_reserve':
        if not pyo.SolverFactory('ipopt').available(exception_flag=False):
            pytest.skip('nonlinear learning requires ipopt, which is not in this environment')
        elec_config.capacity_expansion = True
        elec_config.reserve_margin_required = True
        elec_config.expansion_learning_type = ExpansionLearningType.NONLINEAR

    # run_elec_model postprocesses into the config's output folder
    common_config.make_scenario_dir()
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
        elec_model.fixed_om_cost.pprint()
        # print(f'terms in om cost: {elec_model.fixed_om_cost.linear_vars}')
        if elec_config.capacity_expansion:
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

    if elec_config.expansion_learning_type is ExpansionLearningType.NONLINEAR:
        approx_cost = pytest.approx(expected_total_cost, rel=_NONLINEAR_REL_TOL)
    else:
        approx_cost = pytest.approx(expected_total_cost)
    assert value(elec_model.total_cost) == approx_cost, (
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
