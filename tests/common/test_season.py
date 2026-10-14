"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  10/7/26

"""

import pytest

from src.common.season import Season


@pytest.fixture
def seasons() -> list[Season]:
    """Group of seasons with 1 duplicate in middle of list."""
    s1 = Season('Summer', 2)
    s1_dupe = Season('Summer', 2)
    s2 = Season('Winter', 3)
    return [s1, s1_dupe, s2]


def test_hash_and_equality(seasons):
    """Test the hash and equality methods."""
    s1, s1_dupe, s2 = seasons
    # check equality
    assert s1 == s1_dupe, 'equality check failed'
    assert s1 != s2, 'equality check failed'

    # check hashing
    assert hash(s1) == hash(s1_dupe), 'hash check failed'
    assert hash(s1) != hash(s2), 'hash check failed'


# entries are positions in the `seasons` fixture: 0 = Summer, 1 = Summer duplicate, 2 = Winter.
# expected=None means cycle() must reject the group.
@pytest.mark.parametrize(
    'group, expected',
    [
        ((0, 2), (0, 2, 0)),
        ((2, 0), (0, 2, 0)),  # unsorted input comes back sorted
        ((0,), (0, 0)),  # a single season cycles back to itself
        ((0, 1), None),  # equal seasons are duplicates
        ((0, 1, 2), None),
    ],
    ids=['sorted', 'unsorted', 'single', 'duplicate_pair', 'duplicate_in_three'],
)
def test_cycle(seasons: list[Season], group: tuple[int, ...], expected: tuple[int, ...] | None):
    """cycle() sorts the seasons and repeats the first at the end, rejecting duplicates."""
    members = [seasons[i] for i in group]
    if expected is None:
        with pytest.raises(ValueError, match='Duplicate seasons'):
            Season.cycle(members)
    else:
        assert Season.cycle(members) == [seasons[i] for i in expected], 'cycle failed'


def test_cycle_empty():
    """cycle() rejects an empty group rather than failing on the wrap-around index."""
    with pytest.raises(ValueError, match='empty'):
        Season.cycle([])


@pytest.mark.parametrize(
    'group1, group2, expected',
    [
        ((0, 2), (2, 0), True),
        ((0, 1), (0, 1), False),
        ((0, 2), (0,), False),
    ],
    ids=['matched', 'internal dupes', 'missing members'],
)
def test_matched_set(seasons, group1, group2, expected):
    """Matched implies same members sans duplicates."""
    group1 = [seasons[i] for i in group1]
    group2 = [seasons[i] for i in group2]
    assert Season.matched_set(group1, group2) == expected, 'matched_set failed'
