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


LOAN_LIFE = CFG.default_loan_life
MIN_PAYMENTS = CFG.min_loan_payments

# (p_e, min_loan_payments, expected payment count)
LOAN_PAYMENT_CASES = [
    pytest.param(VINTAGE + LOAN_LIFE + 10, MIN_PAYMENTS, LOAN_LIFE, id='whole_loan_in_window'),
    pytest.param(VINTAGE + LOAN_LIFE - 1, MIN_PAYMENTS, LOAN_LIFE, id='horizon_ends_with_loan'),
    pytest.param(VINTAGE + 9, MIN_PAYMENTS, 10, id='truncated_by_horizon'),
    pytest.param(VINTAGE + MIN_PAYMENTS - 1, MIN_PAYMENTS, MIN_PAYMENTS, id='horizon_at_minimum'),
    pytest.param(VINTAGE, MIN_PAYMENTS, MIN_PAYMENTS, id='minimum_overrides_horizon'),
    pytest.param(VINTAGE + 2, 1, 3, id='minimum_of_one'),
    pytest.param(VINTAGE, LOAN_LIFE + 5, LOAN_LIFE, id='minimum_capped_by_loan_life'),
]


def _loan_cost(p_e: int, min_loan_payments: int, discount_rate: float | None) -> float:
    """``loan_cost`` on the default loan built at ``VINTAGE``, discounted to ``P_0``."""
    return loan_cost(
        capacity=CAPACITY,
        capacity_cost=CAPACITY_COST,
        loan_rate=CFG.loan_rate,
        loan_lifetime=LOAN_LIFE,
        min_loan_payments=min_loan_payments,
        p_0=P_0,
        p_e=p_e,
        discount_rate=discount_rate,
        vintage=VINTAGE,
    )


@pytest.mark.parametrize(('p_e', 'min_loan_payments', 'payments'), LOAN_PAYMENT_CASES)
def test_loan_cost(annual_payment: float, p_e: int, min_loan_payments: int, payments: int) -> None:
    """Payments run to the horizon, floored at ``min_loan_payments`` and capped at loan life."""
    result = _loan_cost(p_e, min_loan_payments, CFG.discount_rate)
    expected = discounted_payments(annual_payment, VINTAGE, payments)
    assert result == pytest.approx(expected)


@pytest.mark.parametrize(('p_e', 'min_loan_payments', 'payments'), LOAN_PAYMENT_CASES)
def test_loan_cost_undiscounted(
    annual_payment: float, p_e: int, min_loan_payments: int, payments: int
) -> None:
    """Without discounting, the cost is the sum of the payments counted."""
    result = _loan_cost(p_e, min_loan_payments, None)
    assert result == pytest.approx(annual_payment * payments)


@pytest.mark.parametrize('min_loan_payments', [0, -1])
def test_loan_cost_rejects_nonpositive_min_payments(min_loan_payments: int) -> None:
    """``min_loan_payments`` must be at least 1."""
    with pytest.raises(ValueError, match='min_loan_payments'):
        _loan_cost(VINTAGE + LOAN_LIFE, min_loan_payments, CFG.discount_rate)


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
