"""
Created as part of the C-NEMS Project.

Written by:  S. Siddiqui
Contact:  sauleh@american.edu
Created on:  9/2/26

One-factor capacity expansion learning curve, used by the nonlinear expansion mode.

It lives here rather than inline so it can be called and tested on its own, for instance to
generate cost curves.  The linear mode keeps its own copy of the formula in
``sequencer.cost_learning_func``, which still carries a calendar-time drift term that this one
omits.  Consolidating the two is left for later so that reviving the nonlinear path does not move
linear results.

Both functions are type-agnostic: numeric inputs give a number, symbolic inputs give a Pyomo
expression.  Nothing here calls ``float()`` or ``value()`` or branches on a quantity, since either
would collapse a symbolic expression at construction time.

Callers must ensure ``baseline_quantity`` is strictly positive and that
``baseline_quantity + quantity`` stays positive.  A nonpositive base under a fractional exponent is
undefined, and a base near zero destroys the derivatives the solver needs.  These functions cannot
check it themselves, for the reason above.
"""

from typing import Any


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
