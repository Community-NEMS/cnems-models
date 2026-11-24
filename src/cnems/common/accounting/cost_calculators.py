"""

Module to contain cost-calculation formulations

Note:  Portions of these cost calculations are borrowed/modified from the Temoa Project
"""

from typing import TYPE_CHECKING

from pyomo.environ import quicksum, value

if TYPE_CHECKING:
    from pyomo.core import Expression, Var
    from pyomo.core.base.component import ComponentData


from logging import getLogger

logger = getLogger(name=__name__)

 # —————————————————————————————————————————————————————————————————— Helper Functions

def annuity_to_pv(rate: float, periods: float) -> float | Expression:
    """
    Multiplication factor to convert an annuity to net present value

    - :math:`i` is the interest/discount rate
    - :math:`N` is the number of periods
    """
    if rate == 0:
        return periods
    return ((1 + rate) ** periods - 1) / (rate * (1 + rate) ** periods)


def pv_to_annuity(rate: float, periods: float) -> float | Expression:
    """
    Multiplication factor to convert net present value to an annuity

    """
    if rate == 0:
        return 1 / periods
    return (rate * (1 + rate) ** periods) / ((1 + rate) ** periods - 1)


def fv_to_pv(rate: float, periods: float) -> float | Expression:
    """
    Multiplication factor to convert a future value to net present value

    """
    if rate == 0:
        return 1
    return 1 / (1 + rate) ** periods

def param_loan_annualize_rule(
    model: TemoaModel, r: Region, t: Technology, v: Vintage
) -> float | Expression:
    """Rule to calculate the annualized loan rate from the loan rate and lifetime."""
    dr = value(model.loan_rate[r, t, v])
    lln = value(model.loan_lifetime_process[r, t, v])
    annualized_rate = pv_to_annuity(dr, lln)
    return annualized_rate



# —————————————————————————————————————————————————————————————————— Accounting


def loan_cost(
    capacity: float | Var | ComponentData,
    capacity_cost: float | Expression,
    loan_annualize: float,
    loan_lifetime: float,
    tech_lifetime: float,
    p_0: int,
    p_e: int,
    global_discount_rate: float,
    vintage: int,
) -> float | Expression:
    """
    Function to calculate the loan cost.  It can be used with fixed values to produce a hard number
    or pyomo variables/params to make a pyomo Expression
    :param capacity: The capacity to use to calculate cost
    :param capacity_cost: the cost/capacity
    :param loan_annualize: parameter
    :param loan_lifetime: lifetime of the loan
    :param P_0: the year to discount the costs back to
    :param P_e: the 'end year' or cutoff year for loan payments
    :param GDR: Global Discount Rate
    :param vintage: the base year of the loan
    :return: fixed number or pyomo expression based on input types
    """
    # calculate the amortised loan repayment (annuity)
    annuity = (
            capacity
            * capacity_cost  # lump investment cost is capacity times cost_invest
            * loan_annualize  # calculate loan annuities for investment cost, if used
    )

    if not global_discount_rate:
        # Undiscounted result
        res = (
                annuity
                * loan_lifetime  # sum of loan payments over loan period
                / tech_lifetime  # redistributed over lifetime of process
                * min(
                tech_lifetime, p_e - vintage
            )  # sum of redistributed costs for life of process (within planning horizon)
        )
    else:
        # Discounted result
        res = (
            annuity
            * annuity_to_pv(
                global_discount_rate, int(loan_lifetime)
            )  # PV of all loan payments, discounted to vintage year using GDR
            * pv_to_annuity(
                global_discount_rate, tech_lifetime
            )  # reamortised over lifetime of process using GDR
            * annuity_to_pv(
                global_discount_rate, min(tech_lifetime, p_e - vintage)
            )  # PV of all reamortised costs (within planning horizon)
            * fv_to_pv(
                global_discount_rate, vintage - p_0
            )  # finally, discounted from vintage year to P_0
        )
    return res


def fixed_or_variable_cost(
    cap_or_flow: float | Var | ComponentData,
    cost_factor: float | Expression,
    cost_years: float | ComponentData,
    global_discount_rate: float | None,
    p_0: float,
    p: int,
) -> float | Expression:
    """
    Extraction of the fixed and var cost formulation.  (It is same for both with either capacity or
    flow as the driving variable.)
    :param cap_or_flow: Capacity if fixed cost / flow out if variable
    :param cost_factor: the cost (either fixed or variable) of the cap/flow variable
    :param cost_years: for how many years is this cost incurred
    :param GDR: discount rate or None
    :param P_0: the period to discount this back to
    :param p: the period under evaluation
    :return:
    """
    annual_cost = cap_or_flow * cost_factor  # annual fixed, variable, or emission cost

    if not global_discount_rate:
        # Undiscounted result
        return annual_cost * cost_years  # annual cost times years paying that cost
    else:
        # Discounted result
        return (
            annual_cost
            * annuity_to_pv(
                global_discount_rate, int(cost_years)
            )  # PV of annual costs over this period, discounted to period p
            * fv_to_pv(global_discount_rate, int(p - p_0))  # discounted from p to p_0
        )


# ============================================================================
# PYOMO EXPRESSION AND OBJECTIVE FUNCTION RULES
# ============================================================================


def period_cost_rule(
    model: TemoaModel, p: int, *, include_hurdle: bool = True
) -> float | Expression:
    """Build a period's cost expression, optionally weighting new investment for choice."""
    p_0 = min(model.time_optimize)
    p_e = model.time_future.last()  # End point of modeled horizon
    global_discount_rate = value(model.global_discount_rate)
    annualize = model.investment_choice_annualize if include_hurdle else model.loan_annualize
    # MPL = M.ModelProcessLife

    if value(model.myopic_discounting_year) != 0:
        p_0 = value(model.myopic_discounting_year)

    loan_costs = quicksum(
        loan_cost(
            model.v_new_capacity[r, S_t, S_v],
            value(model.cost_invest[r, S_t, S_v]),
            value(annualize[r, S_t, S_v]),
            value(model.loan_lifetime_process[r, S_t, S_v]),
            value(model.lifetime_process[r, S_t, S_v]),
            p_0,
            p_e,
            global_discount_rate,
            vintage=S_v,
        )
        for r, S_t, S_v in model.cost_invest.sparse_keys()
        if S_v == p and not model.is_survival_curve_process[r, S_t, S_v]
    )
    loan_costs += quicksum(
        loan_cost_survival_curve(
            model,
            r,
            S_t,
            S_v,
            model.v_new_capacity[r, S_t, S_v],
            value(model.cost_invest[r, S_t, S_v]),
            value(annualize[r, S_t, S_v]),
            value(model.loan_lifetime_process[r, S_t, S_v]),
            p_0,
            p_e,
            global_discount_rate,
        )
        for r, S_t, S_v in model.cost_invest.sparse_keys()
        if S_v == p and model.is_survival_curve_process[r, S_t, S_v]
    )

    fixed_costs = quicksum(
        fixed_or_variable_cost(
            model.v_capacity[r, p, S_t, S_v],
            value(model.cost_fixed[r, p, S_t, S_v]),
            value(model.period_length[p]),
            global_discount_rate,
            p_0,
            p=p,
        )
        for r, S_p, S_t, S_v in model.cost_fixed.sparse_keys()
        if S_p == p
    )

    variable_costs = quicksum(
        fixed_or_variable_cost(
            model.v_flow_out[r, p, s, d, S_i, S_t, S_v, S_o],
            value(model.cost_variable[r, p, S_t, S_v]),
            value(model.period_length[p]),
            global_discount_rate,
            p_0,
            p,
        )
        for r, S_p, S_t, S_v in model.cost_variable.sparse_keys()
        if S_p == p and S_t not in model.tech_annual
        for S_i in model.process_inputs[r, S_p, S_t, S_v]
        for S_o in model.process_outputs_by_input[r, S_p, S_t, S_v, S_i]
        for s in model.time_season
        for d in model.time_of_day
    )

    variable_costs_annual = quicksum(
        fixed_or_variable_cost(
            model.v_flow_out_annual[r, p, S_i, S_t, S_v, S_o],
            value(model.cost_variable[r, p, S_t, S_v]),
            value(model.period_length[p]),
            global_discount_rate,
            p_0,
            p,
        )
        for r, S_p, S_t, S_v in model.cost_variable.sparse_keys()
        if S_p == p and S_t in model.tech_annual
        for S_i in model.process_inputs[r, S_p, S_t, S_v]
        for S_o in model.process_outputs_by_input[r, S_p, S_t, S_v, S_i]
    )

    # The emissions costs occur over the five possible emission sources.
    # to do any/all of them we need 2 baseline sets:  The regular and annual sets
    # of indices that are valid which is basically the filter of:
    #     EmissionActivty by cost_emission
    # and to ensure that the techology is active we need to filter that
    # result with processInput

    # ================= Emissions and Flex and Curtailment =================
    # Flex flows are deducted from v_flow_out, so it is NOT NEEDED to tax them again.  (See
    # commodity balance constr)
    # Curtailment does not draw any inputs, so it seems logical that curtailed flows not be taxed
    # either
    # Earlier versions of this code had accounting for flex & curtailment that have been removed.

    base = [
        (r, p, e, i, t, v, o)
        for (r, e, i, t, v, o) in model.emission_activity.sparse_keys()
        if (r, p, e) in model.cost_emission  # tightest filter first
        and (r, p, t, v) in model.process_inputs
    ]

    # then expand the base for the normal (season/tod) set and annual separately:
    normal = [
        (r, p, e, s, d, i, t, v, o)
        for (r, p, e, i, t, v, o) in base
        for s in model.time_season
        for d in model.time_of_day
        if t not in model.tech_annual
    ]

    annual = [(r, p, e, i, t, v, o) for (r, p, e, i, t, v, o) in base if t in model.tech_annual]

    # 1. variable emissions
    var_emissions = quicksum(
        fixed_or_variable_cost(
            cap_or_flow=model.v_flow_out[r, p, s, d, i, t, v, o]
            * value(model.emission_activity[r, e, i, t, v, o]),
            cost_factor=value(model.cost_emission[r, p, e]),
            cost_years=value(model.period_length[p]),
            global_discount_rate=global_discount_rate,
            p_0=p_0,
            p=p,
        )
        for (r, p, e, s, d, i, t, v, o) in normal
    )

    # 2. flex emissions -- removed (double counting, flex wastes are SUBTRACTIVE from flowout)

    # 3. curtailment emissions -- removed (curtailment is no-flow, for accounting only, so no
    # emissions)

    # 4. annual emissions
    var_annual_emissions = quicksum(
        fixed_or_variable_cost(
            cap_or_flow=model.v_flow_out_annual[r, p, i, t, v, o]
            * value(model.emission_activity[r, e, i, t, v, o]),
            cost_factor=value(model.cost_emission[r, p, e]),
            cost_years=value(model.period_length[p]),
            global_discount_rate=global_discount_rate,
            p_0=p_0,
            p=p,
        )
        for (r, p, e, i, t, v, o) in annual
        if t not in model.tech_flex
    )

    # 5. flex annual emissions -- removed (double counting, flex wastes are SUBTRACTIVE from
    # flowout)

    # 6. embodied - treated as a fixed cost distributed over the deployment period (vintage)
    embodied_emissions = quicksum(
        fixed_or_variable_cost(
            cap_or_flow=model.v_new_capacity[r, t, v]
            * value(model.emission_embodied[r, e, t, v])
            / value(model.period_length[p]),
            cost_factor=value(model.cost_emission[r, p, e]),
            cost_years=value(model.period_length[v]),
            # We assume the embodied emissions are emitted in the same year as the capacity is
            # installed.
            global_discount_rate=global_discount_rate,
            p_0=p_0,
            p=p,
        )
        for (r, e, t, v) in model.emission_embodied.sparse_keys()
        if (r, p, e) in model.cost_emission
        if v == p
    )

    # 6. endoflife - treated as a fixed cost distributed over the retirement period
    endoflife_emissions = quicksum(
        fixed_or_variable_cost(
            cap_or_flow=model.v_annual_retirement[r, p, t, v]
            * value(model.emission_end_of_life[r, e, t, v]),
            cost_factor=value(model.cost_emission[r, p, e]),
            cost_years=value(model.period_length[p]),
            # We assume the embodied emissions are emitted in the same year as the capacity is
            # installed.
            global_discount_rate=global_discount_rate,
            p_0=p_0,
            p=p,
        )
        for (r, e, t, v) in model.emission_end_of_life.sparse_keys()
        if (r, p, e) in model.cost_emission
        if (r, t, v) in model.retirement_periods and p in model.retirement_periods[r, t, v]
    )

    period_emission_cost = (
        var_emissions + var_annual_emissions + embodied_emissions + endoflife_emissions
    )

    period_costs = (
        loan_costs + fixed_costs + variable_costs + variable_costs_annual + period_emission_cost
    )
    return period_costs




def param_investment_choice_annualize_rule(
    model: TemoaModel, r: Region, t: Technology, v: Vintage
) -> float | Expression:
    """Annualize new investment at loan rate plus the additive choice-only hurdle."""
    hurdle = value(model.hurdle_rate[r, t, v])
    if hurdle == 0:
        return value(model.loan_annualize[r, t, v])
    return pv_to_annuity(
        value(model.loan_rate[r, t, v]) + hurdle,
        value(model.loan_lifetime_process[r, t, v]),
    )
