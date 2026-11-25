"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  11/24/26

Cost-calculation formulations, usable with numbers or pyomo components.

Portions are adapted from the Temoa Project.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyomo.core import Expression, Var

from logging import getLogger

logger = getLogger(name=__name__)

# —————————————————————————————————————————————————————————————————— Helper Functions


def annuity_to_pv(rate: float, periods: float) -> float | Expression:
    """
    Factor converting a uniform annuity to its present value.

    Parameters
    ----------
    rate : float
        Interest/discount rate per period.
    periods : float
        Number of annuity payments.

    Returns
    -------
    float | Expression
        Present value of one unit paid each period.
    """
    if rate == 0:
        return periods
    return ((1 + rate) ** periods - 1) / (rate * (1 + rate) ** periods)


def pv_to_annuity(rate: float, periods: float) -> float | Expression:
    """
    Factor converting a present value to a uniform annuity (capital recovery factor).

    Parameters
    ----------
    rate : float
        Interest/discount rate per period.
    periods : float
        Number of annuity payments.

    Returns
    -------
    float | Expression
        Per-period payment that repays one unit of present value.
    """
    if rate == 0:
        return 1 / periods
    return (rate * (1 + rate) ** periods) / ((1 + rate) ** periods - 1)


def fv_to_pv(rate: float, periods: float) -> float | Expression:
    """
    Factor discounting a future value to its present value.

    Parameters
    ----------
    rate : float
        Discount rate per period.
    periods : float
        Number of periods between the present and the future value.

    Returns
    -------
    float | Expression
        Present value of one unit received `periods` from now.
    """
    if rate == 0:
        return 1
    return 1 / (1 + rate) ** periods


# —————————————————————————————————————————————————————————————————— Accounting


def loan_cost(
    capacity: float | Var,  #| ComponentData,
    capacity_cost: float | Expression,
    loan_rate: float,
    loan_lifetime: int,
    p_0: int,
    p_e: int,
    discount_rate: float,
    vintage: int,
) -> float | Expression:
    """
    Cost of the loan financing a capacity investment, within the planning horizon.

    Parameters
    ----------
    capacity : float | Var
        Capacity built.
    capacity_cost : float | Expression
        Investment cost per unit capacity.
    loan_rate : float
        Interest rate on the loan.
    loan_lifetime : int
        Loan term, in years.
    p_0 : int
        Year costs are discounted to.
    p_e : int
        Last year of the planning horizon; payments past it are excluded.
    discount_rate : float
        Discount rate; falsy gives an undiscounted result.
    vintage : int
        Year the loan begins.

    Returns
    -------
    float | Expression
        A number for numeric inputs, a pyomo Expression for pyomo inputs.
    """
    # calculate the amortised loan repayment (annuity)
    annuity_payment = (
        capacity
        * capacity_cost  # lump investment cost is capacity times cost_invest
        * pv_to_annuity(
            rate=loan_rate, periods=loan_lifetime
        )  # calculate loan annuities for investment cost, if used
    )

    # determine number of loan payments to make based on end condition
    # we have a +1 here because the last year it typically *in* the analysis in CNEMS
    periods = min(p_e - vintage + 1, loan_lifetime)

    if not discount_rate:
        # Undiscounted result: sum of loan payments within the planning horizon
        present_value = annuity_payment * periods
    else:
        # Discounted result

        # calculate PV of all annuity payments
        future_value = annuity_payment * annuity_to_pv(rate=discount_rate, periods=periods)

        # * pv_to_annuity(
        #     discount_rate, tech_lifetime
        # )  # reamortised over lifetime of process using GDR
        # * annuity_to_pv(
        #     discount_rate, min(tech_lifetime, p_e - vintage)
        # )  # PV of all reamortised costs (within planning horizon)

        # bring the future cost to discounted present value
        present_value = future_value * fv_to_pv(discount_rate, vintage - p_0)

    return present_value


def fixed_or_variable_cost(
    annual_cap_or_flow: float | Var,
    cost_factor: float | Expression,
    preceding_costed_years: int,
    discount_rate: float | None,
    p_0: int,
    p: int,
) -> float | Expression:
    """
    Cost of an annual fixed or variable charge, incurred over the years preceding ``p``.

    Note:  When years are aggregated in CNEMS, the trail year drives the boat, so we need to
           know how many years are being aggregated forward, and use the number of years to
           calculate the first costed year, which precedes the evaluation period, ``p``.

    Parameters
    ----------
    annual_cap_or_flow : float | Var
        Capacity (fixed cost) or annual flow (variable cost).
    cost_factor : float | Expression
        Cost per unit of `annual_cap_or_flow`.
    preceding_costed_years : int
        Number of years before `p` that are aggregated into it and also costed.  Without
        aggregation, this should be zero.
    discount_rate : float | None
        Discount rate; falsy gives an undiscounted result.
    p_0 : int
        Year costs are discounted to.
    p : int
        Period under evaluation.

    Returns
    -------
    float | Expression
        A number for numeric inputs, a pyomo Expression for pyomo inputs.
    """
    annual_cost = annual_cap_or_flow * cost_factor  # annual fixed, variable, or emission cost
    costed_years = preceding_costed_years + 1  # the preceding years plus p itself

    if not discount_rate:
        # Undiscounted result
        return annual_cost * costed_years  # annual cost times years paying that cost
    else:
        # Discounted result
        first_cost_year = p - preceding_costed_years
        return (
            annual_cost
            * annuity_to_pv(
                discount_rate, int(costed_years)
            )  # PV of annual costs over this period, discounted
            * fv_to_pv(discount_rate, int(first_cost_year - p_0))  # discounted from p to p_0
        )
