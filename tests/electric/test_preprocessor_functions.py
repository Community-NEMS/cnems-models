"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/18/26
"""

import logging

import pandas as pd
import pytest

from src.models.electricity.param_utilities import avg_by_group


@pytest.fixture
def year_map():
    """Crosswalk mapping individual years to their representative summary year."""
    df = pd.DataFrame(
        {
            'year': [2000, 2002, 2001, 2008, 2020, 2025, 2029],
            'Map_year': [2010, 2010, 2010, 2010, 2020, 2030, 2030],
        }
    )
    return df


@pytest.fixture
def dummy_df():
    """Small (year, color, value) frame to aggregate over."""
    df = pd.DataFrame(
        {
            'year': [2000, 2002, 2001, 2008, 2020, 2025, 2029],
            'color': ['red', 'red', 'blue', 'blue', 'red', 'green', 'green'],
            'value': [1, 2, 3, 4, 5, 6, 7],
        }
    )
    return df


def test_avg_by_group(dummy_df, year_map, caplog):
    """Test the aggregation function."""
    with caplog.at_level(logging.WARNING, logger='src.models.electricity.param_utilities'):
        grouped_averaged = avg_by_group(
            df=dummy_df, set_name='year', map_frame=year_map, name='dummy'
        )
    assert not caplog.records, 'no gap warning expected when every mapped year is present'

    expected = pd.DataFrame(
        {
            'year': [2010, 2010, 2020, 2030],
            'color': ['blue', 'red', 'red', 'green'],
            'value': [3.5, 1.5, 5.0, 6.5],
        }
    )

    assert grouped_averaged.equals(expected), 'computed aggregation is not as expected'


@pytest.mark.parametrize(
    'dropped_years,expected',
    [
        # a gap inside an aggregated block:  2010/blue is informed by 2008 only
        (
            [2001],
            {
                'year': [2010, 2010, 2020, 2030],
                'color': ['blue', 'red', 'red', 'green'],
                'value': [4.0, 1.5, 5.0, 6.5],
            },
        ),
        # a gap that empties a group:  2020/red disappears
        (
            [2020],
            {
                'year': [2010, 2010, 2030],
                'color': ['blue', 'red', 'green'],
                'value': [3.5, 1.5, 6.5],
            },
        ),
        (
            [2000, 2029],
            {
                'year': [2010, 2010, 2020, 2030],
                'color': ['blue', 'red', 'red', 'green'],
                'value': [3.5, 2.0, 5.0, 6.0],
            },
        ),
    ],
)
def test_avg_by_group_gap_warning(dummy_df, year_map, caplog, dropped_years, expected):
    """Mapped years absent from the source are named in a warning; averaging proceeds."""
    source = dummy_df[~dummy_df['year'].isin(dropped_years)]
    with caplog.at_level(logging.WARNING, logger='src.models.electricity.param_utilities'):
        res = avg_by_group(df=source, set_name='year', map_frame=year_map, name='dummy')

    assert res.equals(pd.DataFrame(expected)), 'averages should use the years present'
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert 'dummy' in record.getMessage()
    assert str(sorted(dropped_years)) in record.getMessage()


def test_avg_by_group_empty_source_no_warning(dummy_df, year_map, caplog):
    """An empty source has no data to aggregate, so it is not reported as a gap."""
    with caplog.at_level(logging.WARNING, logger='src.models.electricity.param_utilities'):
        avg_by_group(df=dummy_df.iloc[0:0], set_name='year', map_frame=year_map, name='dummy')
    assert not caplog.records
