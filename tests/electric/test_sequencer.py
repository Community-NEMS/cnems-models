"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/20/26

Tests for the standalone helper functions in
``src.models.electricity.sequencer`` (everything outside of
``ElectricitySequencer`` that does not build/solve a real model).

These use a small hand-built pyomo model that carries only the components the
helpers touch, rather than a full ``PowerModel``.
"""

import pyomo.environ as pyo
import pytest

from src.models.electricity.sequencer import (
    calculate_cap_growth,
    calculate_tolerance,
    cost_learning_func,
    init_old_cap,
    set_new_cap,
    update_expansion_cost,
)

# small, easy-to-reason-about dimensions for the mock model
REGIONS = [1]
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

    m.y0_learning = pyo.Param(initialize=Y0)
    m.WeightYear = pyo.Param(m.year, initialize={2030: 5, 2035: 3})

    m.LearningRate = pyo.Param(m.tech, initialize={2: 0.1, 3: 0.2})
    m.SupplyCurveLearning = pyo.Param(m.tech, initialize={2: 100.0, 3: 200.0})
    m.CapCostInitial = pyo.Param(
        m.region,
        m.tech,
        m.step,
        initialize={(r, tech, s): 1000.0 for r in REGIONS for tech in TECHS for s in STEPS},
    )
    m.CapCostLearning = pyo.Param(
        m.region,
        m.tech,
        m.step,
        m.year,
        initialize={
            (r, tech, s, y): 1000.0 for r in REGIONS for tech in TECHS for s in STEPS for y in YEARS
        },
        mutable=True,
    )

    m.capacity_builds = pyo.Var(list(m.CapCostLearning.keys()), within=pyo.NonNegativeReals)
    for r, tech, s, y in m.CapCostLearning:
        m.capacity_builds[r, tech, s, y].set_value(10.0 if tech == 2 else 20.0)

    return m


class TestCalculateTolerance:
    def test_weighted_absolute_difference(self):
        """tolerance is the year-weighted sum of absolute growth differences."""
        cap_growth = {(2, 2030): 10.0, (2, 2035): 20.0}
        new_cap_growth = {(2, 2030): 12.0, (2, 2035): 15.0}
        year_weights = {2030: 2, 2035: 3}

        # |10-12| * 2 + |20-15| * 3
        assert calculate_tolerance(cap_growth, new_cap_growth, year_weights) == pytest.approx(19.0)

    def test_identical_growth_is_zero(self):
        cap_growth = {(2, 2030): 10.0, (3, 2030): 5.0}
        assert calculate_tolerance(cap_growth, dict(cap_growth), {2030: 4}) == 0.0

    def test_mismatched_keys_raise(self):
        with pytest.raises(ValueError):
            calculate_tolerance({(2, 2030): 1.0}, {(3, 2030): 1.0}, {2030: 1})


def test_init_old_cap(mock_model):
    """0th iteration assumes 1 GW/yr of growth since the learning start year."""
    result = init_old_cap(mock_model)

    assert set(result.keys()) == {(tech, y) for tech in TECHS for y in YEARS}
    assert result[(2, 2030)] == 0
    assert result[(2, 2035)] == 5
    assert result[(3, 2035)] == 5


def test_calculate_cap_growth(mock_model):
    """growth in year y is the sum of all builds in years strictly before y."""
    result = calculate_cap_growth(mock_model)

    assert result[(2, 2030)] == pytest.approx(0.0)  # no years before 2030
    assert result[(2, 2035)] == pytest.approx(10.0)  # 2030 build only
    assert result[(3, 2035)] == pytest.approx(20.0)


def test_set_new_cap(mock_model):
    """new_cap matches cap growth; new_cap_wt applies the year weight."""
    set_new_cap(mock_model)

    assert mock_model.new_cap[(2, 2030)] == pytest.approx(0.0)
    assert mock_model.new_cap[(2, 2035)] == pytest.approx(10.0)
    assert mock_model.new_cap[(3, 2035)] == pytest.approx(20.0)

    assert mock_model.new_cap_wt[(2, 2035)] == pytest.approx(10.0 * 3)
    assert mock_model.new_cap_wt[(3, 2030)] == pytest.approx(0.0)


def test_set_new_cap_matches_calculate_cap_growth(mock_model):
    """the two routines should agree on capacity by (tech, year)."""
    growth = calculate_cap_growth(mock_model)
    set_new_cap(mock_model)

    assert mock_model.new_cap == pytest.approx(dict(growth))


class TestCostLearningFunc:
    def test_no_new_capacity_in_start_year(self, mock_model):
        """with no growth in the base year the multiplier is 1.0."""
        assert cost_learning_func(mock_model, 2, Y0, 0.0) == pytest.approx(1.0)

    def test_multiplier_decreases_with_capacity(self, mock_model):
        """learning drives the cost multiplier below 1 as capacity grows."""
        low = cost_learning_func(mock_model, 2, Y0, 10.0)
        high = cost_learning_func(mock_model, 2, Y0, 100.0)

        assert high < low < 1.0

    def test_expected_value(self, mock_model):
        """spot-check against the closed-form expression."""
        expected = ((100.0 + 0.0001 * (2035 - Y0) + 50.0) / 100.0) ** (-0.1)
        assert cost_learning_func(mock_model, 2, 2035, 50.0) == pytest.approx(expected)

    def test_higher_learning_rate_lowers_cost(self, mock_model):
        """tech 3 has the higher learning rate, so a like-for-like fraction of
        its supply curve yields a bigger cost reduction."""
        tech_2 = cost_learning_func(mock_model, 2, Y0, 50.0)  # 50% of 100
        tech_3 = cost_learning_func(mock_model, 3, Y0, 100.0)  # 50% of 200

        assert tech_3 < tech_2


def test_update_expansion_cost(mock_model):
    """CapCostLearning is overwritten with CapCostInitial * learning multiplier."""
    new_cap = {(tech, y): 50.0 for tech in TECHS for y in YEARS}

    update_expansion_cost(mock_model, new_cap)

    for r, tech, step, y in mock_model.CapCostLearning:
        expected = mock_model.CapCostInitial[r, tech, step] * cost_learning_func(
            mock_model, tech, y, new_cap[tech, y]
        )
        assert pyo.value(mock_model.CapCostLearning[r, tech, step, y]) == pytest.approx(expected)


def test_update_expansion_cost_no_growth_is_unchanged(mock_model):
    """zero growth in the base year leaves the initial cost in place."""
    new_cap = {(tech, y): 0.0 for tech in TECHS for y in YEARS}

    update_expansion_cost(mock_model, new_cap)

    for r, tech, step in mock_model.CapCostInitial:
        assert pyo.value(mock_model.CapCostLearning[r, tech, step, Y0]) == pytest.approx(1000.0)
