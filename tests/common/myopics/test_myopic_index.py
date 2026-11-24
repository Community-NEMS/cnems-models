"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/12/26

Tests for MyopicIndex and MyopicIndexIterator: forward stepping, reversion, and end points.

Expected indexes are written as (status, base, step, last) tuples; included years are checked
separately where they matter.  Scenarios follow the examples in src/cnems/common/myopics/notes.md.
"""

import itertools

import pytest

from cnems.common.myopics.myopic_index import MyopicIndex, MyopicIndexIterator

# notes.md, example 1
YEARS_EX1 = [2030, 2031, 2032, 2036, 2038]
# notes.md, example 2
YEARS_EX2 = [2030, 2031, 2032, 2033, 2035, 2036, 2038]

Key = tuple[int | None, int, int | None, int]


def key(mi: MyopicIndex) -> Key:
    """Reduce an MI to (status, base, step, last) for compact comparison."""
    return mi.status_year, mi.base_year, mi.step_year, mi.last_year


def run_forward(mii: MyopicIndexIterator) -> list[MyopicIndex]:
    """Call next() until the iterator is exhausted, with a guard against runaway loops."""
    out: list[MyopicIndex] = []
    for _ in range(100):
        mi = mii.next()
        if mi is None:
            return out
        out.append(mi)
    raise AssertionError('iterator did not terminate')


@pytest.mark.parametrize(
    'years, view, step, expected',
    [
        pytest.param(
            YEARS_EX1,
            3,
            1,
            [
                (None, 2030, 2031, 2032),
                (2030, 2031, 2032, 2036),
                (2031, 2032, 2036, 2038),
                (2032, 2036, 2038, 2038),
                (2036, 2038, None, 2038),
            ],
            id='notes_ex1_view3_step1',
        ),
        pytest.param(
            YEARS_EX2,
            3,
            3,
            [
                (None, 2030, 2033, 2032),
                (2032, 2033, 2038, 2036),
                (2036, 2038, None, 2038),
            ],
            id='notes_ex2_view3_step3',
        ),
        pytest.param(
            YEARS_EX1,
            1,
            1,
            [
                (None, 2030, 2031, 2030),
                (2030, 2031, 2032, 2031),
                (2031, 2032, 2036, 2032),
                (2032, 2036, 2038, 2036),
                (2036, 2038, None, 2038),
            ],
            id='view1_step1',
        ),
        pytest.param(
            YEARS_EX1,
            2,
            2,
            [
                (None, 2030, 2032, 2031),
                (2031, 2032, 2038, 2036),
                (2036, 2038, None, 2038),
            ],
            id='view2_step2_tiles',
        ),
        pytest.param(
            YEARS_EX1,
            10,
            10,
            [(None, 2030, None, 2038)],
            id='view_exceeds_horizon',
        ),
        pytest.param([2030], 3, 1, [(None, 2030, None, 2030)], id='single_year'),
    ],
)
def test_forward_walk(years: list[int], view: int, step: int, expected: list[Key]):
    """Walk forward to exhaustion and check every index, then confirm it stays exhausted."""
    mii = MyopicIndexIterator(years, myopic_view=view, myopic_step=step)
    mis = run_forward(mii)
    assert [key(mi) for mi in mis] == expected
    # included years are exactly the analysis-year slice [base, last]
    for mi in mis:
        assert mi.included_analysis_years == [y for y in years if mi.base_year <= y <= mi.last_year]
    # every analysis year is covered, and repeated next() past the end keeps returning None
    assert set().union(*(mi.included_analysis_years for mi in mis)) == set(years)
    assert mii.next() is None
    assert list(mii.generated_mi) == mis


@pytest.mark.parametrize('view', [1, 2, 3, 4, 7, 9])
@pytest.mark.parametrize('step_frac', [0.0, 0.5, 1.0])
def test_forward_invariants(view: int, step_frac: float):
    """For any view/step, each base is the prior step year and the view never exceeds `view`."""
    step = max(1, round(view * step_frac))
    mii = MyopicIndexIterator(YEARS_EX2, myopic_view=view, myopic_step=step)
    mis = run_forward(mii)

    assert mis[0].status_year is None
    assert mis[0].base_year == YEARS_EX2[0]
    assert mis[-1].last_year == YEARS_EX2[-1]
    assert mis[-1].step_year is None
    for prev, cur in itertools.pairwise(mis):
        assert cur.base_year == prev.step_year
        assert cur.status_year == YEARS_EX2[YEARS_EX2.index(cur.base_year) - 1]
    for mi in mis:
        assert len(mi.included_analysis_years) <= view


@pytest.mark.parametrize(
    'years, view, step, ops',
    [
        pytest.param(
            YEARS_EX2,
            3,
            3,
            [
                ('next', (None, 2030, 2033, 2032)),
                ('next', (2032, 2033, 2038, 2036)),
                ('revert', (2031, 2032, 2038, 2036)),  # notes.md MI[2]
                ('next', (2036, 2038, None, 2038)),  # resumes at prior step year
                ('next', None),
            ],
            id='notes_ex2_single_revert',
        ),
        pytest.param(
            YEARS_EX2,
            3,
            3,
            [
                ('next', (None, 2030, 2033, 2032)),
                ('next', (2032, 2033, 2038, 2036)),
                ('revert', (2031, 2032, 2038, 2036)),
                ('revert', (2030, 2031, 2038, 2036)),
                ('revert', (None, 2030, 2038, 2036)),
                ('revert', None),  # backstop at the first analysis year expires the iterator
                ('revert', None),
                ('next', None),
            ],
            id='revert_to_backstop_expires',
        ),
        pytest.param(
            YEARS_EX1,
            3,
            1,
            [
                ('next', (None, 2030, 2031, 2032)),
                ('next', (2030, 2031, 2032, 2036)),
                ('next', (2031, 2032, 2036, 2038)),
                ('revert', (2030, 2031, 2036, 2038)),
                ('next', (2032, 2036, 2038, 2038)),
                ('next', (2036, 2038, None, 2038)),
                ('revert', (2032, 2036, None, 2038)),  # revert on the final MI
                ('next', None),  # exhausted -> expired
                ('revert', None),
                ('next', None),
            ],
            id='revert_final_mi_then_expire',
        ),
        pytest.param(
            YEARS_EX1,
            1,
            1,
            [
                ('next', (None, 2030, 2031, 2030)),
                ('next', (2030, 2031, 2032, 2031)),
                ('revert', (None, 2030, 2032, 2031)),
                ('revert', None),
                ('next', None),
            ],
            id='view1_revert_widens_window',
        ),
    ],
)
def test_advance_revert_sequence(
    years: list[int], view: int, step: int, ops: list[tuple[str, Key | None]]
):
    """Drive a scripted next/revert sequence and check each returned index."""
    mii = MyopicIndexIterator(years, myopic_view=view, myopic_step=step)
    for i, (op, expected) in enumerate(ops):
        mi = mii.next() if op == 'next' else mii.revert()
        got = None if mi is None else key(mi)
        assert got == expected, f'op {i} ({op}): {got} != {expected}'


def test_revert_keeps_last_year_and_extends_included_years():
    """Reversion only moves status/base back; last/step and the tail of the window are kept."""
    mii = MyopicIndexIterator(YEARS_EX2, myopic_view=3, myopic_step=3)
    mii.next()
    fwd = mii.next()
    assert fwd is not None
    rev = mii.revert()
    assert rev is not None
    assert rev.last_year == fwd.last_year
    assert rev.step_year == fwd.step_year
    assert rev.included_analysis_years == [2032] + fwd.included_analysis_years


@pytest.mark.parametrize('view, step', [(1, 1), (3, 1), (3, 3)])
def test_revert_on_first_mi_expires(view: int, step: int):
    """Reverting the first MI hits the backstop, which expires the iterator for good."""
    mii = MyopicIndexIterator(YEARS_EX1, myopic_view=view, myopic_step=step)
    assert mii.next() is not None  # the initial MI, which cannot be "reverted"
    assert mii.revert() is None
    for _ in range(3):
        assert mii.next() is None
        assert mii.revert() is None
    assert len(mii.generated_mi) == 1


def test_revert_before_next_raises():
    """Reverting an iterator that has not produced an index is an error."""
    mii = MyopicIndexIterator(YEARS_EX1)
    with pytest.raises(RuntimeError):
        mii.revert()


@pytest.mark.parametrize(
    'view, step',
    [(2, 3), (0, 1), (1, 0), (3, -1)],
    ids=['step_gt_view', 'view_zero', 'step_zero', 'step_negative'],
)
def test_bad_view_step_raises(view: int, step: int):
    """View must be >= step and step must be >= 1."""
    with pytest.raises(ValueError):
        MyopicIndexIterator(YEARS_EX1, myopic_view=view, myopic_step=step)


@pytest.mark.parametrize(
    'kwargs',
    [
        {
            'status_year': None,
            'base_year': 2030,
            'step_year': None,
            'last_year': 2031,
            'included_analysis_years': [2031, 2030],
        },
        {
            'status_year': None,
            'base_year': 2031,
            'step_year': None,
            'last_year': 2032,
            'included_analysis_years': [2030, 2032],
        },
        {
            'status_year': 2030,
            'base_year': 2030,
            'step_year': None,
            'last_year': 2031,
            'included_analysis_years': [2030, 2031],
        },
        {
            'status_year': None,
            'base_year': 2030,
            'step_year': None,
            'last_year': 2031,
            'included_analysis_years': [2030, 2032],
        },
    ],
    ids=['unsorted', 'base_mismatch', 'status_not_before_base', 'last_mismatch'],
)
def test_myopic_index_validation(kwargs: dict):
    """MyopicIndex rejects inconsistent year assignments."""
    with pytest.raises(ValueError):
        MyopicIndex(**kwargs)
