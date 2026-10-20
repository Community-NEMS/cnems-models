"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/15/26

A temporary (?) test to lock down the current outputs of a basic no-frills test run

"""

import logging
from pathlib import Path

import pyomo.environ as pyo
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
from src.models.electricity.electricity_model import PowerModel
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
        'storage_efficiency',
        'storage_duration',
        'capacity_factor_vre',
        'hydro_capacity_factor',
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


@pytest.mark.parametrize(
    'bad_value,expected_message',
    [
        (0.0, 'Value not in parameter domain PositiveReals'),
        (-5.0, 'Value not in parameter domain PositiveReals'),
        (float('nan'), 'Value not in parameter domain PositiveReals'),
    ],
    ids=['zero', 'negative', 'nan'],
)
def test_unusable_learning_baseline_is_rejected(monkeypatch, bad_value, expected_message):
    """An unusable ``supply_curve_learning`` is caught at construction, not in the objective.

    The curve divides by this baseline and raises the result to a fractional power, so any of these
    would otherwise surface as a division error, a domain error, or a silent NaN from somewhere
    inside the objective, with nothing to say which technology caused it.

    ``within=PositiveReals`` rejects all three at construction.
    """
    from src.models.electricity import param_data as param_data_module

    original_init = param_data_module.ParamData.__init__
    poisoned = {}

    def poisoned_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        baselines = self.param_dicts['supply_curve_learning']
        key = min(baselines)
        # keys are single element tuples, while the error names the technology itself
        poisoned['tech'] = key[0] if isinstance(key, tuple) else key
        baselines[key] = bad_value

    monkeypatch.setattr(param_data_module.ParamData, '__init__', poisoned_init)

    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    elec_config.capacity_expansion = True
    elec_config.expansion_learning_type = ExpansionLearningType.NONLINEAR

    with pytest.raises(ValueError) as caught:
        ElectricitySequencer().build_model(common_config, elec_config)

    message = str(caught.value)
    assert expected_message in message
    # The offending technology has to be identifiable, which is the point of catching it here.
    assert str(poisoned['tech']) in message


# Builds are in MW against a supply_curve_learning baseline of ~154,000 MW.  1 GW keeps the learning
# discount ~50x above pytest.approx's tolerance, the scale the characterization test uses.
_BUILD_MW = 1000.0


@pytest.fixture
def nonlinear_configs() -> tuple[CommonConfig, ElecConfig]:
    """Basic test configs with expansion, reserve margin and nonlinear learning enabled."""
    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    elec_config.capacity_expansion = True
    elec_config.reserve_margin_required = True
    elec_config.expansion_learning_type = ExpansionLearningType.NONLINEAR
    return common_config, elec_config


@pytest.fixture
def nonlinear_model(nonlinear_configs: tuple[CommonConfig, ElecConfig]) -> PowerModel:
    """Unsolved model built from ``nonlinear_configs``."""
    return ElectricitySequencer().build_model(*nonlinear_configs)


def _expansion_cost_with(model: PowerModel, builds: dict[tuple, float]) -> float:
    """Evaluate ``capacity_expansion_cost`` with only ``builds`` nonzero.

    Parameters
    ----------
    model : PowerModel
        Model built with nonlinear learning.
    builds : dict[tuple, float]
        ``capacity_builds`` key -> MW; every other build is set to 0.

    Returns
    -------
    float
        The evaluated expansion cost.
    """
    for key, var in model.capacity_builds.items():
        var.set_value(builds.get(key, 0.0))
    return value(model.capacity_expansion_cost)


@pytest.mark.parametrize('regions', [None, [str(i) for i in range(1, 26)]], ids=['config', 'all'])
def test_nonlinear_learning_index_domains(
    nonlinear_configs: tuple[CommonConfig, ElecConfig], regions: list[str] | None
) -> None:
    """Nonlinear learning borrows its variable index from the linear cost table.

    ``capacity_builds`` is created from ``capital_cost.keys()``, but the nonlinear objective reads
    ``capital_cost_initial`` and, for the cumulative term, indexes ``capacity_builds`` by every
    ``capital_cost_initial`` key crossed with every earlier year.  Both lookups must be total or the
    expression raises ``KeyError`` during construction.  This needs a build only, so it holds the
    invariant whether or not a nonlinear solver is installed.
    """
    common_config, elec_config = nonlinear_configs
    if regions is not None:
        elec_config.region_filter = regions

    model = ElectricitySequencer().build_model(common_config, elec_config)

    builds = set(model.capacity_builds)
    initial = set(model.capital_cost_initial)

    missing_outer = {(r, tech, step) for (r, tech, step, _y) in builds} - initial
    assert not missing_outer, (
        f'capital_cost_initial missing keys for builds: {sorted(missing_outer)[:5]}'
    )

    techs_built = {tech for (_r, tech, _s, _y) in builds}
    missing_inner = {
        (r, tech, step, y)
        for (r, tech, step) in initial
        for y in model.year
        if tech in techs_built and (r, tech, step, y) not in builds
    }
    assert not missing_inner, f'capacity_builds missing keys: {sorted(missing_inner)[:5]}'


def test_nonlinear_learning_objective_characterization(nonlinear_model: PowerModel) -> None:
    """Record what the nonlinear expansion cost expression evaluates to today.

    This is a **characterization** test, not an acceptance test: it pins current behavior so that
    refactoring the learning multiplier cannot change the formulation silently.  It does not assert
    that these values are correct, the provenance of ``supply_curve_learning`` is unresolved, so
    the numbers are not yet interpretable.

    Evaluates the expression at fixed build levels rather than solving, so it needs no nonlinear
    solver and runs wherever the model can be constructed.
    """
    model = nonlinear_model
    assert len(model.capacity_builds) == 48

    def cost_at(level: float) -> float:
        return _expansion_cost_with(model, dict.fromkeys(model.capacity_builds, level))

    # No builds means no expansion cost, whatever the multiplier does.
    assert cost_at(0.0) == pytest.approx(0.0, abs=1e-9)

    # builds are in MW; 1 and 2.5 GW keep the values captured before the move to MW units
    at_one = cost_at(1000.0)
    at_two_and_a_half = cost_at(2500.0)
    assert at_one == pytest.approx(133777599845.11543)
    assert at_two_and_a_half == pytest.approx(329888419799.9818)

    # Learning makes cost subadditive in cumulative builds: 2.5x the capacity costs less than 2.5x
    # the money.  That is the property the nonlinear mode exists to produce, so it is asserted
    # separately from the pinned values, which are only a snapshot.
    assert at_two_and_a_half < 2.5 * at_one


def test_nonlinear_build_does_not_discount_itself(nonlinear_model: PowerModel) -> None:
    """A build must be discounted by prior years' capacity, not by its own.

    The shared curve takes whatever cumulative quantity it is handed; this checks the objective
    hands it the right one.  First-year builds have no prior experience, so building only in the
    first year must cost exactly the undiscounted price.  Needs a build only, no solver.
    """
    model = nonlinear_model
    first_year = min(model.year)
    keys = [k for k in model.capacity_builds if k[3] == first_year]
    cost = _expansion_cost_with(model, dict.fromkeys(keys, _BUILD_MW))
    undiscounted = sum(_BUILD_MW * value(model.capital_cost_initial[k[:3]]) for k in keys)
    assert cost == pytest.approx(undiscounted)


def test_nonlinear_experience_pools_across_regions(nonlinear_model: PowerModel) -> None:
    """A later build is discounted by earlier builds of the same tech in *other* regions.

    Build in one region in the first year, then price a later-year build of the same technology in
    a different region.  If the cumulative term were region-local the later build would be charged
    the undiscounted price.  The closed form is asserted rather than an inequality, so a wrong
    aggregation cannot pass by discounting some other amount.  Needs a build only, no solver.
    """
    model = nonlinear_model
    tech, step = '1', 3
    source = ('7', tech, step, min(model.year))
    later = ('8', tech, step, max(model.year))
    assert {source, later} <= set(model.capacity_builds)
    source_cost = _BUILD_MW * value(model.capital_cost_initial[source[:3]])
    later_cost = _BUILD_MW * value(model.capital_cost_initial[later[:3]])

    # With no prior experience anywhere, the later build is charged undiscounted.
    assert _expansion_cost_with(model, {later: _BUILD_MW}) == pytest.approx(later_cost)

    pooled = _expansion_cost_with(model, {source: _BUILD_MW, later: _BUILD_MW})
    baseline = value(model.supply_curve_learning[tech])
    multiplier = ((baseline + _BUILD_MW) / baseline) ** -value(model.learning_rate[tech])
    # Guard: the discount must be resolvable at approx's tolerance, or this test proves nothing.
    assert pooled != pytest.approx(source_cost + later_cost)
    assert pooled == pytest.approx(source_cost + later_cost * multiplier)


def test_linear_learning(learning_config_set, caplog: pytest.LogCaptureFixture):
    """Exercise the linear-learning iteration on a single-region micro dataset.

    The dataset lives in tests/electric/test_data_linear_learning_test.  It has a single region
    'CA' and a single tech 'NG_Fired_Plant', with 2,000 MW of existing capacity against a load
    that starts at 4,000 MW and grows 5,000 MW/year, forcing step-3 builds every year.  Asserts on
    the convergence log emitted by ``ElectricitySequencer.solve_model`` each iteration.
    """
    common_config, elec_config = learning_config_set

    sequencer = ElectricitySequencer()
    elec_model = sequencer.build_model(common_config, elec_config)

    # with learning enabled, the learning-gated param_sources.toml entries should be wired in
    for key in ('capital_cost_initial', 'learning_rate', 'supply_curve_learning'):
        assert not PARAM_SOURCES[key].required
        assert hasattr(elec_model, key), f'{key} missing with learning enabled'

    # DEBUG level additionally captures the per-key capital_cost updates for the verbose table.  The
    # iteration log comes from the sequencer and the capital_cost updates from learning.py, so both
    # loggers are raised.
    capture_level = logging.DEBUG if verbose else logging.INFO
    with (
        caplog.at_level(capture_level, logger='src.models.electricity.sequencer'),
        caplog.at_level(capture_level, logger='src.models.electricity.learning'),
    ):
        _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST, f'solve failed with status {status}'

    if verbose:
        # args of the sequencer.update_expansion_cost debug records: (r, tech, step, y, old, new)
        cost_rows = [
            rec.args for rec in caplog.records if rec.msg.startswith('Reduced capital_cost')
        ]
        initial_costs = {
            idx: value(elec_model.capital_cost_initial[idx])
            for idx in elec_model.capital_cost_initial
        }
        print(f'\ncap_cost_initial: {initial_costs}')
        # one record per capital_cost key per iteration; recover the iteration index by
        # chunking
        n_keys = len(elec_model.capital_cost)
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
    # The load forces the same builds whatever the cost, so the second solve repeats the first and
    # the loop stops there.  A change to the stopping rule that moved this count would show here.
    assert len(tolerance_records) == 2

    # the load design must force expansion builds (step 3), otherwise learning has nothing to do
    total_builds = sum(value(elec_model.capacity_builds[idx]) for idx in elec_model.capacity_builds)
    assert total_builds > 0, 'expected capacity builds; dataset failed to force expansion'


def test_linear_learning_prices_builds_on_the_curve(learning_config_set):
    """Pin what linear learning does on the micro dataset, where the curve changes the answer.

    The load forces builds in every year from 2030 to 2035, so every year after the first is priced
    from real prior experience.  Each final ``capital_cost`` is checked against the curve written
    out from the solved builds, and the expansion cost is pinned tightly enough that a change to the
    curve or to the exponent moves it.
    """
    common_config, elec_config = learning_config_set
    sequencer = ElectricitySequencer()
    model = sequencer.build_model(common_config, elec_config)
    assert sequencer.solve_model()[-1] is IterationStatus.BEST

    for r, tech, step, y in model.capital_cost:
        prior = sum(
            value(model.capacity_builds[idx])
            for idx in model.capacity_builds
            if idx[1] == tech and idx[3] < y
        )
        baseline = value(model.supply_curve_learning[tech])
        exponent = value(model.learning_rate[tech])
        multiplier = ((baseline + prior) / baseline) ** (-exponent)
        expected = value(model.capital_cost_initial[r, tech, step]) * multiplier
        assert value(model.capital_cost[r, tech, step, y]) == pytest.approx(expected, rel=1e-9)

    # The expansion cost is about 1/3800 of the objective, so the objective's default tolerance
    # would miss a change of a few dollars in it.  It is pinned on its own, tighter.
    assert value(model.capacity_expansion_cost) == pytest.approx(2525039.4414830687, rel=1e-9)
    assert value(model.total_cost) == pytest.approx(9513531839.477724)
