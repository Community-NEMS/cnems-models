"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  11/7/26

A class to hold the key years for one iteration of myopic + an iterator/
generator for same
"""

import logging
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MyopicIndex:
    """Time indexing for one myopic iteration."""

    status_year: int | None
    base_year: int
    step_year: int | None
    last_year: int
    included_analysis_years: list[int]

    def __post_init__(self):
        """QA entries."""
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
        if self.step_year and self.step_year not in self.included_analysis_years:
            raise ValueError(f'Step year {self.step_year} not in included analysis years')

    def __repr__(self) -> str:
        """String representation of Myopic Index."""
        return (
            f'MI(status={self.status_year}, base={self.base_year}, step={self.step_year}, '
            f'last={self.last_year}, yrs={self.included_analysis_years})'
        )

    def __str__(self) -> str:
        """String representation of Myopic Index."""
        return self.__repr__()


class MyopicIndexIterator:
    """Iteration tool for generating myopic indexes from analysis years.

    dev note:  maintaining "state" here rather than generating indexes "on the fly" or from
               other indexes to eliminate possible loss of sync from multiple callers
    """

    def __init__(self, analysis_years: list[int], myopic_view: int = 1, myopic_step: int = 1):
        """Initialize Myopic Index Iterator."""
        self._analysis_years = sorted(analysis_years)
        self.generated_mi: deque[MyopicIndex] = deque()

        if myopic_view < 0 or myopic_view < myopic_step:
            raise ValueError(f'invalid myopic view: {myopic_view} with step {myopic_step}')
        if myopic_step < 1:
            raise ValueError(f'invalid myopic step: {myopic_step}')
        self.myopic_view = myopic_view
        self.myopic_step = myopic_step

        # pointers
        self._status_idx: int = -1
        self._base_idx = -1
        self._step_idx: int = -1
        self._last_year_idx: int

        self.num_years = len(self._analysis_years)

        # status
        self._initialized = False

    def next(self) -> MyopicIndex | None:
        """
        Get the next MI from this iterator.

        Returns
        -------
        MyopicIndex | None
        """
        valid = self._advance()
        if not valid:
            return None
        self._initialized = True
        return self._build()

    def revert(self) -> MyopicIndex | None:
        """
        Get a reverted MI from this iterator, expanding the view backwards.

        Returns
        -------
        MyopicIndex | None:  None if we have hit the earliest start year already

        """
        if not self._initialized:
            raise RuntimeError('Tried to revert non-initialized Myopic Index Iterator')
        valid = self._retard()
        if not valid:
            if self._base_idx == 0:
                logger.info('Hit backstop trying to back Myopic Index earlier than base year')
            return None

        logger.info(
            'Reverting Myopic Index back to year %d.  Now covering %d years',
            self._analysis_years[self._base_idx],
            self._last_year_idx - self._analysis_years[self._base_idx] + 1,
        )
        return self._build()

    def _advance(self) -> bool:
        """Advance pointers."""
        if self._step_idx and self._step_idx >= self.num_years:
            return False
        self._base_idx += self.myopic_step if self._initialized else 1
        if self._base_idx > 0:  # ok to advance the status index
            self._status_idx = self._base_idx - 1
        self._step_idx = self._base_idx + self.myopic_step
        self._last_year_idx = min(self._base_idx + self.myopic_view - 1, self.num_years - 1)
        return True

    def _retard(self) -> bool:
        """Retard pointers."""
        # retard leading pointers
        if self._status_idx < 0:
            # cannot back up any further
            return False
        self._base_idx -= 1
        self._status_idx -= 1
        return True

    def _build(self) -> MyopicIndex:
        """Build Myopic Index from pointer values."""
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
