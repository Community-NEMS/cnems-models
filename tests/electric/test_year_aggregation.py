"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/17/26
"""

from src.models.electricity.model_sets import ModelSets


def test_year_aggregation():
    """Test the aggregation functions for years."""
    agg_years = [2030, 2025, 2040]
    start_year = 2000

    yr_map = ModelSets._create_year_map(agg_years, start_year)

    assert yr_map[2001] == 2025
    assert yr_map[2040] == 2040, 'last year should capture to last agg year'


def test_year_agg_weights():
    """Test the aggregation weights."""
    agg_years = [2030, 2025, 2040]
    start_year = 2000
    weights = ModelSets._create_year_agg_weights(ModelSets._create_year_map(agg_years, start_year))
    assert len(weights) == 3, '3 agg years provided'
    assert weights[2025] == 26
    assert weights[2030] == 5
    assert weights[2040] == 10
