"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/20/26

Tests for linear capacity-expansion learning.

The helper functions in ``src.models.electricity.learning`` (called by
``ElectricitySequencer.solve_model`` between solves) are tested on a small hand-built pyomo model
that carries only the components they touch.  The iteration itself is tested with full solves on
the single-region micro dataset in ``linear_micro_data/``.
"""

import logging

import pyomo.environ as pyo
import pytest
from pyomo.common.numeric_types import value
from tabulate import tabulate

from src.common.integrated_model_sequencer import IterationStatus
from src.models.electricity.data_ingestor import PARAM_SOURCES
from src.models.electricity.learning import (
    calculate_cap_growth,
    calculate_tolerance,
    cost_learning_func,
    init_old_cap,
    update_expansion_cost,
)
from src.models.electricity.sequencer import _LEARNING_TOLERANCE, ElectricitySequencer
from tests.electric.mini_model import MiniModelData
from tests.electric.test_mini_model import add_solar_expansion

# "verbose" mode prints the per-iteration capital_cost updates from test_linear_learning
verbose = False

# small, easy-to-reason-about dimensions for the mock model
REGIONS = [1, 2]
TECHS = [2, 3]
STEPS = [1]
YEARS = [2030, 2035]
Y0 = 2030


@pytest.fixture
def mock_model() -> pyo.ConcreteModel:
    """A minimal pyomo model with just the components the helpers require.

    ``capacity_builds`` is fixed at 10 (tech 2) and 20 (tech 3) in every year so
    the expected growth/cost values are trivial to compute by hand.
    """
    m = pyo.ConcreteModel()
    m.region = pyo.Set(initialize=REGIONS)
    m.tech = pyo.Set(initialize=TECHS)
    m.step = pyo.Set(initialize=STEPS)
    m.year = pyo.Set(initialize=YEARS)

    m.learning_rate = pyo.Param(m.tech, initialize={2: 0.1, 3: 0.2})
    m.supply_curve_learning = pyo.Param(m.tech, initialize={2: 100.0, 3: 200.0})
    m.capital_cost_initial = pyo.Param(
        m.region,
        m.tech,
        m.step,
        initialize={(r, tech, s): 1000.0 for r in REGIONS for tech in TECHS for s in STEPS},
    )
    m.capital_cost = pyo.Param(
        m.region,
        m.tech,
        m.step,
        m.year,
        initialize={
            (r, tech, s, y): 1000.0 for r in REGIONS for tech in TECHS for s in STEPS for y in YEARS
        },
        mutable=True,
    )

    m.capacity_build_index = pyo.Set(dimen=4, initialize=list(m.capital_cost.keys()))
    m.capacity_builds = pyo.Var(m.capacity_build_index, within=pyo.NonNegativeReals)
    for r, tech, s, y in m.capacity_build_index:
        m.capacity_builds[r, tech, s, y].set_value(10.0 if tech == 2 else 20.0)

    return m


class TestCalculateTolerance:
    """Tests for ``calculate_tolerance``, the linear-learning convergence measure."""

    def test_largest_absolute_difference(self):
        """Tolerance is the largest absolute growth difference over all keys, unweighted."""
        cap_growth = {(2, 2030): 10.0, (2, 2035): 20.0}
        new_cap_growth = {(2, 2030): 12.0, (2, 2035): 15.0}

        # max(|10-12|, |20-15|)
        assert calculate_tolerance(cap_growth, new_cap_growth) == pytest.approx(5.0)

    def test_identical_growth_is_zero(self):
        """Identical growth dicts give a tolerance of exactly zero."""
        cap_growth = {(2, 2030): 10.0, (3, 2030): 5.0}
        assert calculate_tolerance(cap_growth, dict(cap_growth)) == 0.0

    def test_no_keys_is_zero(self):
        """With nothing buildable there is nothing to change, so the loop stops."""
        assert calculate_tolerance({}, {}) == 0.0

    def test_mismatched_keys_raise(self):
        """Growth dicts with different keys are rejected rather than silently compared."""
        with pytest.raises(ValueError):
            calculate_tolerance({(2, 2030): 1.0}, {(3, 2030): 1.0})


def test_init_old_cap(mock_model):
    """0th iteration starts from zero experience in every (tech, year)."""
    result = init_old_cap(mock_model)

    assert set(result.keys()) == {(tech, y) for tech in TECHS for y in YEARS}
    assert all(v == 0 for v in result.values())


def test_calculate_cap_growth(mock_model):
    """Growth in year y is the sum of all builds in years strictly before y."""
    result = calculate_cap_growth(mock_model)

    assert result[(2, 2030)] == pytest.approx(0.0)  # no years before 2030
    assert result[(2, 2035)] == pytest.approx(20.0)  # 2030 build for both regions
    assert result[(3, 2035)] == pytest.approx(40.0)


@pytest.mark.parametrize(
    'missing, expected',
    [
        # region 2 has no 2030 build key:  its absence must not be read
        ((2, 2, 1, 2030), {(2, 2030): 0.0, (2, 2031): 10.0, (2, 2032): 30.0}),
        # region 2 has no 2031 build key:  its 2030 build still counts in 2031 and 2032
        ((2, 2, 1, 2031), {(2, 2030): 0.0, (2, 2031): 20.0, (2, 2032): 30.0}),
    ],
    ids=['earlier-gap', 'later-gap'],
)
def test_calculate_cap_growth_with_gaps(missing, expected):
    """Experience sums the builds that exist, across regions, when some years have no build key."""
    years = [2030, 2031, 2032]
    m = pyo.ConcreteModel()
    m.year = pyo.Set(initialize=years)
    keys = [(r, 2, 1, y) for r in REGIONS for y in years if (r, 2, 1, y) != missing]
    m.capacity_build_index = pyo.Set(dimen=4, initialize=keys)
    m.capacity_builds = pyo.Var(m.capacity_build_index, within=pyo.NonNegativeReals)
    for idx in m.capacity_build_index:
        m.capacity_builds[idx].set_value(10.0)

    assert calculate_cap_growth(m) == pytest.approx(expected)


class TestCostLearningFunc:
    """Tests for ``cost_learning_func``, the capacity-cost learning multiplier."""

    def test_no_new_capacity_in_start_year(self, mock_model):
        """With no growth in the base year the multiplier is 1.0."""
        assert cost_learning_func(mock_model, 2, 0.0) == pytest.approx(1.0)

    def test_multiplier_decreases_with_capacity(self, mock_model):
        """Learning drives the cost multiplier below 1 as capacity grows."""
        low = cost_learning_func(mock_model, 2, 10.0)
        high = cost_learning_func(mock_model, 2, 100.0)

        assert high < low < 1.0

    def test_expected_value(self, mock_model):
        """spot-check against the closed-form expression."""
        expected = ((100.0 + 50.0) / 100.0) ** (-0.1)
        assert cost_learning_func(mock_model, 2, 50.0) == pytest.approx(expected)

    def test_higher_learning_rate_lowers_cost(self, mock_model):
        """Tech 3 has the higher learning rate.

        So a like-for-like fraction of its supply curve yields a bigger cost reduction.
        """
        tech_2 = cost_learning_func(mock_model, 2, 50.0)  # 50% of 100
        tech_3 = cost_learning_func(mock_model, 3, 100.0)  # 50% of 200

        assert tech_3 < tech_2


def test_update_expansion_cost(mock_model):
    """capital_cost is overwritten with capital_cost_initial * learning multiplier."""
    new_cap = {(tech, y): 50.0 for tech in TECHS for y in YEARS}

    update_expansion_cost(mock_model, new_cap)

    for r, tech, step, y in mock_model.capital_cost:
        expected = mock_model.capital_cost_initial[r, tech, step] * cost_learning_func(
            mock_model, tech, new_cap[tech, y]
        )
        assert pyo.value(mock_model.capital_cost[r, tech, step, y]) == pytest.approx(expected)


def test_update_expansion_cost_no_growth_is_unchanged(mock_model):
    """Zero growth in the base year leaves the initial cost in place."""
    new_cap = {(tech, y): 0.0 for tech in TECHS for y in YEARS}

    update_expansion_cost(mock_model, new_cap)

    for r, tech, step in mock_model.capital_cost_initial:
        assert pyo.value(mock_model.capital_cost[r, tech, step, Y0]) == pytest.approx(1000.0)


def test_linear_learning(learning_config_set, caplog: pytest.LogCaptureFixture):
    """Exercise the linear-learning iteration on a single-region micro dataset.

    The dataset lives in tests/electric/learning/linear_micro_data.  It has a single region
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
        # one record per capacity_build_index key per iteration; recover the iteration index by
        # chunking
        n_keys = len(elec_model.capacity_build_index)
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

    for r, tech, step, y in model.capacity_build_index:
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


def add_solar_learning(data: MiniModelData) -> MiniModelData:
    """Make Solar buildable as in ``add_solar_expansion``, with learning data in both regions."""
    add_solar_expansion(data)
    data.add_rows(
        'capital_cost_initial', {(r, 'Solar', 1): 1000.0 for (r,) in data.every('region')}
    )
    data.add_rows('learning_rate', {('Solar',): 0.1})
    data.add_rows('supply_curve_learning', {('Solar',): 100.0})
    return data


@pytest.mark.parametrize(
    'dropped',
    [('R1', 'Solar', 1, 2030), ('R2', 'Solar', 1, 2031)],
    ids=['earlier-gap', 'later-gap'],
)
def test_linear_learning_with_capital_cost_gaps(build_mini, mini_data, dropped):
    """Linear learning solves with a ``capital_cost`` gap and counts every location's builds.

    R2 builds 100 MW of Solar in 2030 either way, so it is all of Solar's 2031 experience.
    The main objective of this test is to ensure that the always-built solar in 2030, R2 is
    credited in 2031 even if R2 has no capital cost in 2031 and is therefor NOT buildable--
    That the summation is region agnostic when looking back
    """
    add_solar_learning(mini_data)
    mini_data.drop_rows('capital_cost', [dropped])
    sequencer, model = build_mini(capacity_expansion=True, expansion_learning_type='linear')
    assert dropped not in model.capacity_build_index

    _, status = sequencer.solve_model()

    assert status is IterationStatus.BEST
    assert value(model.capacity_builds['R2', 'Solar', 1, 2030]) == pytest.approx(100.0)
    assert calculate_cap_growth(model)[('Solar', 2031)] == pytest.approx(100.0)
