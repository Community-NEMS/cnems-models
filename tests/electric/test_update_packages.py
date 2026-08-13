"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/11/26

Tests for detecting index mismatches in updates to param_data dataframes

"""

import logging

import pandas as pd
import pytest

from src.models.electricity.param_data import ParamData

LOGGER_NAME = 'src.models.electricity.param_data'


def make_index(entries: list[tuple], names: tuple[str, ...] = ('region', 'year')) -> pd.MultiIndex:
    """Build a small MultiIndex for the gap-report tests."""
    return pd.MultiIndex.from_tuples(entries, names=names)


@pytest.mark.parametrize(
    'old_entries, new_entries, expected_missing',
    [
        pytest.param(
            [('7', 2025), ('7', 2030)],
            [('7', 2025), ('7', 2030)],
            [],
            id='full_coverage',
        ),
        pytest.param(
            [('7', 2025), ('7', 2030)],
            [('7', 2025)],
            [('7', 2030)],
            id='partial_coverage',
        ),
        pytest.param(
            [('7', 2025)],
            [('7', 2025), ('8', 2025), ('8', 2030)],
            [],
            id='overage_ignored',
        ),
        pytest.param(
            [('7', 2025), ('8', 2025)],
            [('9', 2030)],
            [('7', 2025), ('8', 2025)],
            id='no_overlap',
        ),
    ],
)
def test_report_index_gaps(
    old_entries: list[tuple], new_entries: list[tuple], expected_missing: list[tuple], caplog
) -> None:
    """Missing held entries are returned and warned about; overages are not."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        missing = ParamData._report_index_gaps(
            make_index(old_entries), make_index(new_entries), name='test_frame'
        )

    assert list(missing) == expected_missing
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert bool(warnings) == bool(expected_missing)
    if expected_missing:
        assert 'does not cover' in warnings[0].getMessage()


def test_report_index_gaps_level_mismatch_raises() -> None:
    """An index with a different number of levels cannot be aligned."""
    old = make_index([('7', 2025)])
    new = make_index([('7', 2025, 'x')], names=('region', 'year', 'extra'))
    with pytest.raises(ValueError, match='level'):
        ParamData._report_index_gaps(old, new, name='test_frame')


def test_report_index_gaps_name_mismatch_warns_and_compares(caplog) -> None:
    """Differing level names warn but are still compared by position."""
    old = make_index([('7', 2025), ('7', 2030)])
    new = make_index([('7', 2025)], names=('destination', 'yr'))
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        missing = ParamData._report_index_gaps(old, new, name='test_frame')

    assert list(missing) == [('7', 2030)]
    assert any('level names' in r.getMessage() for r in caplog.records)
