"""
Created as part of the C-NEMS Project.

Written by:  S. Siddiqui
Contact:  sauleh@american.edu
Created on:  9/2/26

Capacity expansion learning curve for the nonlinear mode.

The NONLINEAR expansion mode puts a one-factor learning curve directly in the objective, where the
quantity is a Pyomo expression in the build variables.  It lives here rather than inline so that it
can be called and tested independently, for instance to generate cost curves.

The LINEAR mode applies the same shape by a different route, iterating externally and recomputing
``cap_cost`` between solves, and keeps its own copy of the formula in
``sequencer.cost_learning_func``.  Consolidating the two is deliberately left for later, so that
reviving the nonlinear path does not change linear results.  The copies differ today: the linear one
carries a calendar-time drift term that this one omits.

The functions are deliberately type-agnostic.  Ordinary numeric inputs give a numeric result, which
is what offline curve plotting and testing need.  If any participating input is
symbolic, the result is a Pyomo expression, which is what the objective needs.  Nothing here coerces
its inputs with ``float()`` or ``value()``, and nothing branches on the value of a quantity, because
either would collapse a symbolic expression to a number at construction time.

Domain contract, which these functions cannot enforce themselves precisely because they must not
branch on symbolic values:

* ``baseline_quantity`` must be **strictly positive**, not merely nonzero.
* ``baseline_quantity + quantity`` must stay positive.

A nonpositive base under a fractional exponent is undefined or complex, and a base approaching zero
destroys the derivatives the nonlinear solver needs.  Callers are responsible for validating both,
in data preparation or model construction.
"""

from __future__ import annotations

from typing import Any


def learning_multiplier(quantity: Any, baseline_quantity: Any, learning_rate: Any) -> Any:
    """Cost multiplier from a one-factor learning curve.

    Implements ``(1 + quantity / baseline_quantity) ** (-learning_rate)``, the standard form
    ``(Q / Q0) ** (-b)`` with ``Q = Q0 + quantity``.

    Parameters
    ----------
    quantity : float or pyomo expression
        Experience accumulated beyond the baseline, in GW.  For the nonlinear objective this is
        cumulative builds in strictly prior years; for the linear path it is the growth estimate.
    baseline_quantity : float or pyomo ParamData
        Capacity the curve is measured from, ``Q0``, in GW.  Must be strictly positive, and
        ``baseline_quantity + quantity`` must stay positive.  Not checked here; see the module
        docstring.
    learning_rate : float or pyomo ParamData
        Curve exponent.  Note this is consumed directly as the exponent ``b``, while the input file
        names the column ``rate``; if those values are learning rates meaning fractional reduction
        per doubling, the exponent would instead be ``-ln(1 - rate) / ln 2``.  That ambiguity is
        unresolved, so no conversion is applied here.

    Returns
    -------
    float or pyomo expression
        Multiplier to apply to an initial capital cost.  Numeric for ordinary numeric inputs, and a
        Pyomo expression when any participating input is symbolic.
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
    """Capital cost of a build after learning, for one region, technology, step and year.

    Thin wrapper over :func:`learning_multiplier`: the discounted unit cost times the amount built.

    Parameters
    ----------
    build_quantity : float or pyomo expression
        Capacity built by this element, in GW.  This is what the cost is charged on.
    cumulative_quantity : float or pyomo expression
        Experience already accumulated, in GW, which sets the discount.  It does **not** include
        ``build_quantity`` itself: in the nonlinear objective the cumulative term is lagged to
        strictly prior years, so a build never discounts its own cost.
    baseline_quantity : float or pyomo ParamData
        Capacity the curve is measured from, ``Q0``, in GW.  Must be strictly positive.
    initial_cost : float or pyomo ParamData
        Undiscounted capital cost per GW.
    learning_rate : float or pyomo ParamData
        Curve exponent.  See :func:`learning_multiplier` on its interpretation.

    Returns
    -------
    float or pyomo expression
        Cost of this build with learning applied.  Keyword-only, because all five arguments are
        numerically interchangeable and a positional swap of build and cumulative quantities would
        otherwise be silent.
    """
    multiplier = learning_multiplier(cumulative_quantity, baseline_quantity, learning_rate)
    return initial_cost * multiplier * build_quantity
