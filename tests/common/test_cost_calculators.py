"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/24/26

Tests for the cost-calculation formulations in cnems.common.accounting.cost_calculators.

Expected values are built independently: payments are summed and discounted one at a time,
assuming end-of-year payments discounted to the start of ``p_0``.
"""

import pytest

from cnems.common.accounting.accounting_config import DEFAULT_ACCOUNTING_CONFIG as CFG
from cnems.common.accounting.cost_calculators import (
    annuity_to_pv,
    fixed_or_variable_cost,
    fv_to_pv,
    loan_cost,
    pv_to_annuity,
)

CAPACITY = 10.0
CAPACITY_COST = 1000.0
P_0 = CFG.currency_base_year
VINTAGE = P_0 + 5


@pytest.fixture
def annual_payment() -> float:
    """Level payment on the default loan: A = P r / (1 - (1 + r)^-n)."""
    principal = CAPACITY * CAPACITY_COST
    r, n = CFG.loan_rate, CFG.default_loan_life
    return principal * r / (1 - (1 + r) ** -n)


def discounted_payments(payment: float, first_year: int, count: int) -> float:
    """
    Present value at the start of ``P_0`` of ``count`` end-of-year payments.

    Parameters
    ----------
    payment : float
        Amount paid each year.
    first_year : int
        Year of the first payment.
    count : int
        Number of payments.

    Returns
    -------
    float
        Sum of the individually discounted payments.
    """
    d = CFG.discount_rate
    return sum(payment * (1 + d) ** -(y - P_0 + 1) for y in range(first_year, first_year + count))


@pytest.mark.parametrize('rate', [0.0, 0.03, 0.05, 0.12])
@pytest.mark.parametrize('periods', [1, 5, 20, 40])
def test_annuity_pv_round_trip(rate: float, periods: int) -> None:
    """P/A and A/P are reciprocals, and P/A matches a direct sum of discounted units."""
    assert annuity_to_pv(rate, periods) * pv_to_annuity(rate, periods) == pytest.approx(1.0)
    direct = sum((1 + rate) ** -k for k in range(1, periods + 1))
    assert annuity_to_pv(rate, periods) == pytest.approx(direct)


@pytest.mark.parametrize('rate', [0.0, 0.03, 0.12])
@pytest.mark.parametrize('periods', [0, 1, 10])
def test_fv_to_pv(rate: float, periods: int) -> None:
    """Discounting a future value then compounding it returns the original value."""
    assert fv_to_pv(rate, periods) * (1 + rate) ** periods == pytest.approx(1.0)


def test_loan_cost_whole_loan_in_window(annual_payment: float) -> None:
    """Every payment falls within the horizon, so all are counted."""
    p_e = VINTAGE + CFG.default_loan_life + 10
    result = loan_cost(
        CAPACITY,
        CAPACITY_COST,
        CFG.loan_rate,
        CFG.default_loan_life,
        P_0,
        p_e,
        CFG.discount_rate,
        VINTAGE,
    )
    expected = discounted_payments(annual_payment, VINTAGE, CFG.default_loan_life)
    assert result == pytest.approx(expected)


def test_loan_cost_truncated_by_horizon(annual_payment: float) -> None:
    """The horizon ends after ``min_loan_payments`` payments, so only those are counted."""
    p_e = VINTAGE + CFG.min_loan_payments - 1
    result = loan_cost(
        CAPACITY,
        CAPACITY_COST,
        CFG.loan_rate,
        CFG.default_loan_life,
        P_0,
        p_e,
        CFG.discount_rate,
        VINTAGE,
    )
    expected = discounted_payments(annual_payment, VINTAGE, CFG.min_loan_payments)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ('p_e', 'payments'),
    [
        (VINTAGE + CFG.default_loan_life + 10, CFG.default_loan_life),
        (VINTAGE + CFG.min_loan_payments - 1, CFG.min_loan_payments),
    ],
)
def test_loan_cost_undiscounted(annual_payment: float, p_e: int, payments: int) -> None:
    """Without discounting, the cost is the sum of the payments within the horizon."""
    result = loan_cost(
        CAPACITY,
        CAPACITY_COST,
        CFG.loan_rate,
        CFG.default_loan_life,
        P_0,
        p_e,
        None,
        VINTAGE,
    )
    assert result == pytest.approx(annual_payment * payments)


@pytest.mark.parametrize('preceding_costed_years', [0, 2])
def test_fixed_or_variable_cost(preceding_costed_years: int) -> None:
    """Annual cost is charged in ``p`` and each of the ``preceding_costed_years`` before it."""
    annual, factor, p = 5.0, 100.0, VINTAGE
    result = fixed_or_variable_cost(
        annual, factor, preceding_costed_years, CFG.discount_rate, P_0, p
    )
    first_year = p - preceding_costed_years
    expected = discounted_payments(annual * factor, first_year, preceding_costed_years + 1)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize('preceding_costed_years', [0, 2])
def test_fixed_or_variable_cost_undiscounted(preceding_costed_years: int) -> None:
    """Without discounting, the cost is the annual cost times the years costed."""
    result = fixed_or_variable_cost(5.0, 100.0, preceding_costed_years, None, P_0, VINTAGE)
    assert result == pytest.approx(500.0 * (preceding_costed_years + 1))
