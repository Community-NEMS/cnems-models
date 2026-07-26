"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/18/26
"""

import pandas as pd
import pytest

from src.models.electricity.preprocessor import avg_by_group


@pytest.fixture
def year_map():
    df = pd.DataFrame(
        {
            'year': [2000, 2002, 2001, 2008, 2020, 2025, 2029],
            'Map_year': [2010, 2010, 2010, 2010, 2020, 2030, 2030],
        }
    )
    return df


@pytest.fixture
def dummy_df():
    df = pd.DataFrame(
        {
            'year': [2000, 2002, 2001, 2008, 2020, 2025, 2029],
            'color': ['red', 'red', 'blue', 'blue', 'red', 'green', 'green'],
            'value': [1, 2, 3, 4, 5, 6, 7],
        }
    )
    return df


def test_avg_by_group(dummy_df, year_map):
    """Test the aggregation function."""
    grouped_averaged = avg_by_group(df=dummy_df, set_name='year', map_frame=year_map)

    expected = pd.DataFrame(
        {
            'year': [2010, 2010, 2020, 2030],
            'color': ['blue', 'red', 'red', 'green'],
            'value': [3.5, 1.5, 5.0, 6.5],
        }
    )
    # print()
    # print(grouped_averaged)
    # print(expected)
    #
    assert grouped_averaged.equals(expected), 'computed aggregation is not as expected'
