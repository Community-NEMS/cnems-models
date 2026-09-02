"""Tests for the shared capacity-learning curve in ``src/models/electricity/learning.py``.

The curve takes two kinds of input.  Offline use, such as generating a cost curve, passes floats and
wants a number back.  The nonlinear objective passes Pyomo expressions and needs an expression back.
A function that evaluated its inputs would keep passing the numeric tests while putting a constant
into the objective, so the symbolic behavior is tested rather than assumed.
"""

import pyomo.environ as pyo
import pytest
from pyomo.core.expr.visitor import identify_variables

from src.models.electricity.learning import learning_cost, learning_multiplier

Q0 = 100.0
RATE = 0.1


class TestLearningMultiplier:
    """Numeric behavior of the multiplier, on plain floats."""

    def test_no_experience_gives_unity(self):
        """With nothing accumulated the curve has not started, so cost is undiscounted."""
        assert learning_multiplier(0.0, Q0, RATE) == pytest.approx(1.0)

    def test_zero_rate_gives_unity(self):
        """A zero exponent disables learning however much has been built."""
        assert learning_multiplier(500.0, Q0, 0.0) == pytest.approx(1.0)

    def test_decreases_with_experience(self):
        """More accumulated capacity means a cheaper build."""
        low = learning_multiplier(10.0, Q0, RATE)
        high = learning_multiplier(100.0, Q0, RATE)

        assert high < low < 1.0

    def test_matches_closed_form(self):
        """Spot check against the curve written out longhand."""
        expected = ((Q0 + 50.0) / Q0) ** (-RATE)

        assert learning_multiplier(50.0, Q0, RATE) == pytest.approx(expected)

    def test_higher_exponent_discounts_more(self):
        """A larger exponent produces a bigger reduction for the same relative experience."""
        shallow = learning_multiplier(Q0, Q0, 0.1)
        steep = learning_multiplier(Q0, Q0, 0.2)

        assert steep < shallow


class TestLearningCost:
    """The cost wrapper, which charges the discounted unit cost on the amount built."""

    def test_cost_is_multiplier_times_build_times_initial(self):
        """The wrapper is the curve applied to initial cost and quantity.

        Written out longhand rather than calling ``learning_multiplier``, so a defect in the
        multiplier cannot cancel itself out of both sides of the comparison.
        """
        expected = 1000.0 * (((Q0 + 50.0) / Q0) ** (-RATE)) * 3.0

        assert learning_cost(
            build_quantity=3.0,
            cumulative_quantity=50.0,
            baseline_quantity=Q0,
            initial_cost=1000.0,
            learning_rate=RATE,
        ) == pytest.approx(expected)

    def test_no_build_costs_nothing(self):
        """Charging on zero capacity costs nothing regardless of accumulated experience."""
        assert learning_cost(
            build_quantity=0.0,
            cumulative_quantity=500.0,
            baseline_quantity=Q0,
            initial_cost=1000.0,
            learning_rate=RATE,
        ) == pytest.approx(0.0)

    def test_build_does_not_discount_itself(self):
        """Cost depends on prior experience only, so the build quantity is not in the multiplier.

        Doubling the build doubles the cost exactly.  If the build leaked into the cumulative term
        the relationship would be subadditive instead.
        """
        kwargs = {
            'cumulative_quantity': 50.0,
            'baseline_quantity': Q0,
            'initial_cost': 1000.0,
            'learning_rate': RATE,
        }
        single = learning_cost(build_quantity=1.0, **kwargs)
        double = learning_cost(build_quantity=2.0, **kwargs)

        assert double == pytest.approx(2.0 * single)


class TestSymbolicBehavior:
    """The curve must stay symbolic when handed Pyomo objects, not collapse to a number."""

    @pytest.fixture
    def model(self) -> pyo.ConcreteModel:
        """A bare model carrying one build variable."""
        m = pyo.ConcreteModel()
        m.build = pyo.Var(within=pyo.NonNegativeReals, initialize=0.0)
        return m

    def test_result_actually_contains_the_variable(self, model):
        """The returned expression must genuinely depend on the variable it was given.

        ``hasattr(result, 'is_expression_type')`` would pass for an expression that had already
        been collapsed to a constant, so membership is checked directly.
        """
        result = learning_multiplier(model.build, Q0, RATE)

        assert result.is_potentially_variable()
        assert list(identify_variables(result)) == [model.build]

    def test_tracks_changes_to_the_variable(self, model):
        """Changing the variable must change the evaluated result.

        This is the property that proves the expression stayed symbolic.  A function that coerced
        its input with ``value()`` would bake in the value at construction and return the same
        number here both times, while still passing every numeric test above.
        """
        expression = learning_multiplier(model.build, Q0, RATE)

        model.build.set_value(0.0)
        at_zero = pyo.value(expression)
        model.build.set_value(100.0)
        at_hundred = pyo.value(expression)

        assert at_zero == pytest.approx(1.0)
        assert at_hundred < at_zero

    def test_cost_expression_is_nonlinear_in_the_variable(self, model):
        """Cost built on a symbolic cumulative quantity is not polynomial.

        ``polynomial_degree()`` returning None establishes non-polynomiality, which is why this
        formulation needs a nonlinear solver rather than the LP path.
        """
        expression = learning_cost(
            build_quantity=1.0,
            cumulative_quantity=model.build,
            baseline_quantity=Q0,
            initial_cost=1000.0,
            learning_rate=RATE,
        )

        assert expression.polynomial_degree() is None

    def test_both_quantities_stay_symbolic_and_independent(self, model):
        """A symbolic build and a symbolic cumulative quantity must both survive into the cost.

        Testing only the cumulative argument would leave a coerced ``build_quantity`` undetected,
        since the earlier tests all pass it as a float.
        """
        model.other = pyo.Var(within=pyo.NonNegativeReals, initialize=1.0)
        expression = learning_cost(
            build_quantity=model.build,
            cumulative_quantity=model.other,
            baseline_quantity=Q0,
            initial_cost=1000.0,
            learning_rate=RATE,
        )

        # Pyomo Vars are unhashable, so compare by identity rather than by set membership.
        found = {id(var) for var in identify_variables(expression)}
        assert found == {id(model.build), id(model.other)}

        model.build.set_value(1.0)
        model.other.set_value(0.0)
        baseline = pyo.value(expression)
        model.build.set_value(2.0)
        assert pyo.value(expression) == pytest.approx(2.0 * baseline)
        model.build.set_value(1.0)
        model.other.set_value(500.0)
        assert pyo.value(expression) < baseline

    def test_pyomo_params_are_accepted(self, model):
        """The docstring promises ParamData support, including mutable params.

        A mutable Param is the sharpest check for accidental coercion: if the function evaluated
        its inputs at construction, changing the param afterwards would not move the result.
        """
        model.baseline = pyo.Param(initialize=Q0, mutable=True)
        model.rate = pyo.Param(initialize=RATE, mutable=True)
        expression = learning_multiplier(50.0, model.baseline, model.rate)

        before = pyo.value(expression)
        model.rate.set_value(0.0)

        assert before < 1.0
        assert pyo.value(expression) == pytest.approx(1.0)
