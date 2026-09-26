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
def complete_df():
    """(year, color, value) frame where every group carries every year its block maps."""
    df = pd.DataFrame(
        {
            'year': [2000, 2001, 2002, 2008, 2000, 2001, 2002, 2008, 2020, 2025, 2029],
            'color': ['red'] * 4 + ['blue'] * 4 + ['red', 'green', 'green'],
            'value': [1, 3, 2, 6, 10, 22, 30, 20, 5, 6, 8],
        }
    )
    return df


def test_avg_by_group(complete_df, year_map, caplog):
    """Complete data averages over each block, with nothing logged."""
    with caplog.at_level(logging.WARNING, logger='src.models.electricity.param_utilities'):
        res = avg_by_group(df=complete_df, set_name='year', map_frame=year_map, name='dummy')

    expected = pd.DataFrame(
        {
            'year': [2010, 2010, 2020, 2030],
            'color': ['blue', 'red', 'red', 'green'],
            'value': [20.5, 3.0, 5.0, 7.0],
        }
    )
    assert res.equals(expected), 'computed aggregation is not as expected'
    assert not caplog.records


@pytest.mark.parametrize(
    'dropped,fragment',
    [
        # one group short a year other groups carry:  2010/blue has 3 of its 4 years
        ([(2001, 'blue')], '1 of 4 aggregate group(s)'),
        # a year absent from the whole source is named as such
        ([(2001, 'blue'), (2001, 'red')], '[2001]'),
        # a gap that empties a group:  2020/red has no rows at all
        ([(2020, 'red')], '[2020]'),
    ],
    ids=['group_gap', 'table_gap', 'emptied_group'],
)
def test_avg_by_group_gap_raises(complete_df, year_map, caplog, dropped, fragment):
    """Each gap is logged as a single error naming the source and the gap, then raised."""
    drop = pd.MultiIndex.from_tuples(dropped)
    source = complete_df[~complete_df.set_index(['year', 'color']).index.isin(drop)]
    with (
        caplog.at_level(logging.WARNING, logger='src.models.electricity.param_utilities'),
        pytest.raises(ValueError, match='dummy') as exc_info,
    ):
        avg_by_group(df=source, set_name='year', map_frame=year_map, name='dummy')

    assert fragment in str(exc_info.value)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == str(exc_info.value)


def test_avg_by_group_empty_source_passes(complete_df, year_map, caplog):
    """An empty source has no data to aggregate, so it is not reported as a gap."""
    with caplog.at_level(logging.WARNING, logger='src.models.electricity.param_utilities'):
        res = avg_by_group(
            df=complete_df.iloc[0:0], set_name='year', map_frame=year_map, name='dummy'
        )
    assert res.empty
    assert not caplog.records
