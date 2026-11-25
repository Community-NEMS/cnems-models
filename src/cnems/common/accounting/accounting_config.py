"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  11/24/26

Accounting elements needed for improved cost calculations

"""

from pydantic import BaseModel, field_validator


class AccountingConfig(BaseModel):
    """A container for accounting settings."""

    currency_base_year: int
    default_loan_life: int
    default_tech_life: int
    loan_rate: float
    min_loan_payments: int
    discount_rate: float

    @field_validator('currency_base_year')
    @classmethod
    def currency_base_year_validator(cls, v):
        """Validate that a currency base year is reasonable."""
        if v < 1970:
            raise ValueError('currency_base_year must be greater than 1970.')
        return v

    @field_validator('default_loan_life')
    @classmethod
    def default_loan_life_validator(cls, v):
        """Validate loan life is positive integer."""
        if v <= 0:
            raise ValueError('default_loan_life must be greater than 0.')
        return v

    @field_validator('default_tech_life')
    @classmethod
    def default_tech_life_validator(cls, v):
        """Validate that a technical life is positive integer."""
        if v <= 0:
            raise ValueError('default_tech_life must be greater than 0.')
        return v

    @field_validator('loan_rate')
    @classmethod
    def loan_rate_validator(cls, v):
        """Validate that a loan rate is reasonable."""
        if v <= 0:
            raise ValueError('loan_rate must be greater than 0.')
        if v > 0.5:
            raise ValueError(
                f'Huge loan rate detected: {v}.  loan_rates are expressed as '
                'decimals, not percentage.'
            )
        return v

    @field_validator('discount_rate')
    @classmethod
    def discount_rate_validator(cls, v):
        """Validate that a discount rate is reasonable."""
        if v <= 0:
            raise ValueError('discount_rate must be greater than 0.')
        if v > 0.5:
            raise ValueError(
                f'Huge discount rate detected: {v}.  discount_rates are expressed as decimals, '
                'not percentage.'
            )
        return v

    @field_validator('min_loan_payments')
    @classmethod
    def min_loan_payments_validator(cls, v):
        """Validate at least 1 loan payment is made."""
        if v < 1:
            raise ValueError('min_loan_payments must be greater than 0.')
        return v


DEFAULT_ACCOUNTING_CONFIG = AccountingConfig(
    currency_base_year=2025,
    default_loan_life=20,
    default_tech_life=30,
    loan_rate=0.05,
    min_loan_payments=5,
    discount_rate=0.03,
)
"""A usable set of accounting defaults for dev/testing"""
