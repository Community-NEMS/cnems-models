"""
Created as part of the C-NEMS Project.

Written by:  S. Siddiqui
Contact:  sauleh@american.edu
Created on:  9/2/26

Capacity expansion learning: the one-factor learning curve, and the helpers that drive the linear
mode's iteration.

The curve, ``learning_multiplier`` and ``learning_cost``, is shared by both learning modes.  The
nonlinear objective calls ``learning_cost`` symbolically.  The linear mode calls
``learning_multiplier`` through ``cost_learning_func`` between solves, which still adds a
calendar-time drift term to the experience that the nonlinear objective does not.

The two curve functions are type-agnostic: numeric inputs give a number, symbolic inputs give a
Pyomo expression.  Neither calls ``float()`` or ``value()`` or branches on a quantity, since either
would collapse a symbolic expression at construction time.

Callers must ensure ``baseline_quantity`` is strictly positive and that
``baseline_quantity + quantity`` stays positive.  A nonpositive base under a fractional exponent is
undefined, and a base near zero destroys the derivatives the solver needs.  The curve functions
cannot check it themselves, for the reason above.

The linear-mode helpers, ``init_old_cap``, ``calculate_cap_growth``, ``cost_learning_func``,
``update_expansion_cost`` and ``calculate_tolerance``, are numeric.  They read solved values off an
instance and rewrite its mutable ``cap_cost`` between the solves of
``ElectricitySequencer.solve_model``.
"""

from collections import defaultdict
from logging import getLogger
from typing import TYPE_CHECKING, Any

from pyomo.common.numeric_types import value

if TYPE_CHECKING:
    from src.models.electricity.electricity_model import PowerModel

logger = getLogger(__name__)


def learning_multiplier(quantity: Any, baseline_quantity: Any, learning_rate: Any) -> Any:
    """Cost multiplier from a one-factor learning curve.

    Implements ``(Q / Q0) ** (-b)`` with ``Q = Q0 + quantity``.

    Parameters
    ----------
    quantity : float or pyomo expression
        Experience beyond the baseline, in GW.  Cumulative builds in strictly prior years for the
        nonlinear objective.
    baseline_quantity : float or pyomo ParamData
        Capacity the curve is measured from, ``Q0``, in GW.  Must be strictly positive.
    learning_rate : float or pyomo ParamData
        Curve exponent ``b``, consumed directly.  The input file names this column ``rate``; if the
        values are learning rates meaning fractional reduction per doubling, the exponent would be
        ``-ln(1 - rate) / ln 2``.  That ambiguity is unresolved, so no conversion is applied.

    Returns
    -------
    float or pyomo expression
        Multiplier to apply to an initial capital cost.
    """
    return ((baseline_quantity + quantity) / baseline_quantity) ** (-1.0 * learning_rate)


def learning_cost(
    *,
    build_quantity: Any,
    cumulative_quantity: Any,
    baseline_quantity: Any,
    initial_cost: Any,
    learning_rate: Any,
) -> Any:
    """Capital cost of one build after learning, for a region, technology, step and year.

    The discounted unit cost times the amount built.  Keyword-only, because all five arguments are
    numerically interchangeable and swapping build for cumulative would otherwise be silent.

    Parameters
    ----------
    build_quantity : float or pyomo expression
        Capacity built by this element, in GW.  What the cost is charged on.
    cumulative_quantity : float or pyomo expression
        Experience already accumulated, in GW, which sets the discount.  Excludes
        ``build_quantity``, so a build never discounts its own cost.
    baseline_quantity : float or pyomo ParamData
        Capacity the curve is measured from, ``Q0``, in GW.  Must be strictly positive.
    initial_cost : float or pyomo ParamData
        Undiscounted capital cost per GW.
    learning_rate : float or pyomo ParamData
        Curve exponent.  See :func:`learning_multiplier`.

    Returns
    -------
    float or pyomo expression
        Cost of this build with learning applied.
    """
    multiplier = learning_multiplier(cumulative_quantity, baseline_quantity, learning_rate)
    return initial_cost * multiplier * build_quantity


def init_old_cap(instance: PowerModel) -> dict[tuple, float]:
    """Initialize capacity growth for 0th iteration.

    Parameters
    ----------
    instance : PowerModel
        unsolved electricity model
    """
    initial_growth = {}
    # instance.cap_set = []
    # instance.old_cap_wt = {}

    # pyrefly: ignore[not-iterable]  - pyomo's IndexedComponent.__iter__ is untyped
    for _r, tech, _step, y in instance.cap_cost:
        if (tech, y) not in initial_growth:
            # each tech will increase cap by 1 GW per year. reasonable starting point.
            # TODO:  come back to this assumption after better understanding of process
            initial_growth[tech, y] = (y - instance.y0_learning) * 1
            # instance.old_cap_wt[(tech, y)] = instance.weight_year[y] * instance.old_cap[(tech, y)]
    return initial_growth


def calculate_cap_growth(instance: PowerModel) -> dict[tuple, float]:
    """Calculate the current capacity of all buildable tech by year."""
    result = defaultdict(float)
    # pyrefly: ignore[not-iterable]  - pyomo's IndexedComponent.__iter__ is untyped
    for r, tech, step, y in instance.cap_cost:
        # pyrefly: ignore[no-matching-overload]  - pyomo's value() is typed as returning None too
        result[(tech, y)] += sum(
            value(instance.capacity_builds[r, tech, step, year])
            for year in instance.year
            if year < y
        )
    return result


def cost_learning_func(instance: PowerModel, tech: Any, y: int, new_cap: float) -> float:
    """Learning multiplier on capital cost for one technology and year, for the linear mode.

    Calls :func:`learning_multiplier` with ``new_cap`` plus a calendar-time drift of 0.0001 GW per
    year since ``y0_learning`` as the experience.  The nonlinear objective has no drift term.

    Parameters
    ----------
    instance : PowerModel
        Electricity model built with learning enabled.
    tech : str or int
        Technology.
    y : int
        Year.
    new_cap : float
        Cumulative builds of ``tech`` before ``y``, in GW.

    Returns
    -------
    float
        Multiplier to apply to ``cap_cost_initial``.
    """
    # pyrefly: ignore[unsupported-operation]  - pyomo ParamData arithmetic is untyped
    drift = 0.0001 * (y - instance.y0_learning)
    return learning_multiplier(
        drift + new_cap,
        instance.supply_curve_learning[tech],
        instance.learning_rate[tech],
    )


def update_expansion_cost(instance, new_cap: dict[tuple, float]):
    """Update capital cost based on new capacity learning."""
    new_multiplier = {}
    for key in new_cap:
        tech, y = key
        new_multiplier[tech, y] = cost_learning_func(instance, tech, y, new_cap[tech, y])

    # Assign new cost
    for r, tech, step, y in instance.cap_cost:
        new_cost = instance.cap_cost_initial[r, tech, step] * new_multiplier[tech, y]
        old_value = value(instance.cap_cost[r, tech, step, y])
        instance.cap_cost[r, tech, step, y] = new_cost
        logger.debug(
            'Reduced cap_cost[%s, %s, %s, %s] from %0.2f to %0.2f',
            r,
            tech,
            step,
            y,
            old_value,
            new_cost,
        )


def calculate_tolerance(
    cap_growth: dict[tuple, float], new_cap_growth: dict[tuple, float]
) -> float:
    """Largest change in cumulative builds between two linear-learning iterations.

    Unweighted, so the stopping rule does not depend on year aggregation or on how many
    technology-year keys there are.

    Parameters
    ----------
    cap_growth : dict[tuple, float]
        Cumulative builds before each year, by ``(tech, year)``, from the previous iteration, in GW.
    new_cap_growth : dict[tuple, float]
        The same from the current iteration.

    Returns
    -------
    float
        Largest absolute difference over all keys, in GW, or 0.0 if there are none.

    Raises
    ------
    ValueError
        If the two dicts do not have the same keys.
    """
    if not set(cap_growth.keys()) == set(new_cap_growth.keys()):
        raise ValueError('cap_growth and new_cap_growth must have the same keys')

    return max((abs(cap_growth[key] - new_cap_growth[key]) for key in cap_growth), default=0.0)
