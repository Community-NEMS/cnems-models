"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/7/26

A class to hold the key years for one iteration of myopic + an iterator/
generator for same
"""

import logging
from collections import deque
from collections.abc import Collection
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MyopicIndex:
    """
    Time indexing for one myopic iteration.

    Attributes
    ----------
    status_year : int | None
        Analysis year just before ``base_year``; ``None`` when ``base_year`` is the first
        analysis year.
    base_year : int
        First analysis year optimized in this iteration.
    step_year : int | None
        Analysis year the next iteration starts at; ``None`` when there is no further step.
    last_year : int
        Last analysis year optimized in this iteration.
    included_analysis_years : list[int]
        Sorted analysis years from ``base_year`` through ``last_year``, inclusive.
    """

    status_year: int | None
    base_year: int
    step_year: int | None
    last_year: int
    included_analysis_years: list[int]

    def __post_init__(self) -> None:
        """
        Check the years for consistency.

        Raises
        ------
        ValueError
            If ``included_analysis_years`` is unsorted or does not start at ``base_year`` and
            end at ``last_year``, or if ``status_year`` is not before ``base_year``.
        """
        if self.included_analysis_years != sorted(self.included_analysis_years):
            raise ValueError(
                f'Included analysis years are not sorted: {self.included_analysis_years}'
            )
        if self.base_year != self.included_analysis_years[0]:
            raise ValueError(
                f'Base year {self.base_year} != first analysis year '
                f'{self.included_analysis_years[0]}'
            )
        if self.status_year and self.status_year >= self.base_year:
            raise ValueError(f'Status year {self.status_year} >= base year {self.base_year}')
        if self.last_year != self.included_analysis_years[-1]:
            raise ValueError(
                f'Last year {self.last_year} != last analysis year '
                f'{self.included_analysis_years[-1]}'
            )

    def __repr__(self) -> str:
        """Compact representation of the ``MyopicIndex``."""
        return (
            f'MI(status={self.status_year}, base={self.base_year}, step={self.step_year}, '
            f'last={self.last_year}, yrs={self.included_analysis_years})'
        )

    def __str__(self) -> str:
        """Same as ``__repr__``."""
        return self.__repr__()


class MyopicIndexIterator:
    """
    Iteration tool for generating ``MyopicIndex`` objects from analysis years.

    Once ``next`` or ``revert`` returns ``None``, the iterator is expired and both return
    ``None`` from then on.

    Notes
    -----
    State is held here, rather than generating indexes on the fly or from other indexes, so
    that multiple callers cannot fall out of sync.
    """

    def __init__(
        self, analysis_years: Collection[int], myopic_view: int = 1, myopic_step: int = 1
    ) -> None:
        """
        Initialize the ``MyopicIndexIterator``.

        Parameters
        ----------
        analysis_years : Collection[int]
            The years to iterate over; sorted on intake.
        myopic_view : int, default 1
            Number of analysis years optimized in each iteration, including ``base_year``.
            Must be ``>= myopic_step``.
        myopic_step : int, default 1
            Number of analysis years to advance between iterations. Must be ``>= 1``.

        Raises
        ------
        ValueError
            If ``myopic_step < 1`` or ``myopic_view < myopic_step``.
        """
        self._analysis_years: list[int] = sorted(analysis_years)
        self.generated_mi: deque[MyopicIndex] = deque()

        if myopic_view < 0 or myopic_view < myopic_step:
            raise ValueError(f'invalid myopic view: {myopic_view} with step {myopic_step}')
        if myopic_step < 1:
            raise ValueError(f'invalid myopic step: {myopic_step}')
        self.myopic_view: int = myopic_view
        self.myopic_step: int = myopic_step

        # pointers
        self._status_idx: int = -1
        self._base_idx: int = -1
        self._step_idx: int = -1
        self._last_year_idx: int = -1

        self.num_years: int = len(self._analysis_years)

        # status
        self._initialized: bool = False
        self._expired: bool = False

    def next(self) -> MyopicIndex | None:
        """
        Advance to the next ``MyopicIndex``.

        After a revert, the new ``base_year`` is the ``step_year`` of the reverted index.

        Returns
        -------
        MyopicIndex | None
            The next index, or ``None`` if the analysis years are exhausted or the iterator
            has already expired.
        """
        if self._expired:
            return None
        valid = self._advance()
        if not valid:
            self._expire('analysis years exhausted')
            return None
        self._initialized = True
        return self._build()

    def revert(self) -> MyopicIndex | None:
        """
        Expand the current ``MyopicIndex`` back by one analysis year.

        ``status_year`` and ``base_year`` move back; ``step_year`` and ``last_year`` are kept.

        Returns
        -------
        MyopicIndex | None
            The reverted index, or ``None`` if ``base_year`` is already the first analysis
            year or the iterator has already expired.

        Raises
        ------
        RuntimeError
            If called before the first ``next``.
        """
        if self._expired:
            return None
        if not self._initialized:
            raise RuntimeError('Tried to revert non-initialized Myopic Index Iterator')
        valid = self._retard()
        if not valid:
            if self._base_idx == 0:
                logger.info('Hit backstop trying to back Myopic Index earlier than base year')
            self._expire('revert backstop reached')
            return None

        logger.info(
            'Reverting Myopic Index back to year %d.  Now covering %d years',
            self._analysis_years[self._base_idx],
            self._last_year_idx - self._base_idx + 1,
        )
        return self._build()

    def _expire(self, reason: str) -> None:
        """
        Mark the iterator expired so ``next`` and ``revert`` return ``None`` from now on.

        Parameters
        ----------
        reason : str
            Logged explanation.
        """
        self._expired = True
        logger.info('Myopic Index Iterator expired: %s', reason)

    def _advance(self) -> bool:
        """
        Move the pointers forward one step.

        Returns
        -------
        bool
            ``False`` if ``_step_idx`` is already past the last analysis year.
        """
        if self._step_idx and self._step_idx >= self.num_years:
            return False
        self._base_idx = self._step_idx if self._initialized else 0
        if self._base_idx > 0:  # ok to advance the status index
            self._status_idx = self._base_idx - 1
        self._step_idx = self._base_idx + self.myopic_step
        self._last_year_idx = min(self._base_idx + self.myopic_view - 1, self.num_years - 1)
        return True

    def _retard(self) -> bool:
        """
        Move ``_base_idx`` and ``_status_idx`` back one analysis year.

        Returns
        -------
        bool
            ``False`` if ``_status_idx`` is already before the first analysis year.
        """
        if self._status_idx < 0:
            # cannot back up any further
            return False
        self._base_idx -= 1
        self._status_idx -= 1
        return True

    def _build(self) -> MyopicIndex:
        """
        Build a ``MyopicIndex`` from the current pointers and record it in ``generated_mi``.

        Returns
        -------
        MyopicIndex
        """
        mi = MyopicIndex(
            status_year=self._analysis_years[self._status_idx] if self._status_idx >= 0 else None,
            base_year=self._analysis_years[self._base_idx],
            step_year=self._analysis_years[self._step_idx]
            if self._step_idx < len(self._analysis_years)
            else None,
            last_year=self._analysis_years[self._last_year_idx],
            included_analysis_years=self._analysis_years[self._base_idx : self._last_year_idx + 1],
        )
        self.generated_mi.append(mi)
        return mi


if __name__ == '__main__':
    mii = MyopicIndexIterator(
        analysis_years=[2020, 2021, 2022, 2023, 2024, 2025, 2026],
        myopic_view=4,
        myopic_step=3,
    )
    print(mii.next())
    print(mii.next())
    print(mii.revert())
    print(mii.revert())
    print(mii.next())
    for i in range(20):
        print(mii.next())
    print(mii.revert())
