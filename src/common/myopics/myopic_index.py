"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  11/7/26

"""

import logging
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MyopicIndex:
    """Time indexing for one myopic iteration"""

    status_year: int | None
    last_year: int
    base_year: int
    step_year: int
    last_year: int
    included_analysis_years: list[int]

    def __post_init__(self):
        if self.included_analysis_years != sorted(self.included_analysis_years):
            raise ValueError(
                f'Included analysis years are not sorted: {self.included_analysis_years}'
            )
        if self.base_year != self.included_analysis_years[0]:
            raise ValueError(
                f'Base year {self.base_year} != first analysis year{self.included_analysis_years[0]}'
            )
        if self.status_year and self.status_year >= self.base_year:
            raise ValueError(f'Status year {self.status_year} >= base year {self.base_year}')
        if self.last_year != self.included_analysis_years[-1]:
            raise ValueError(
                'Last year {self.last_year} != last analysis year{self.included_analysis_years[-1]}'
            )
        if self.step_year not in self.included_analysis_years:
            raise ValueError(f'Step year {self.step_year} not in included analysis years')


class MyopicIndexIterator:
    """Iteration tool for generating myopic indexes from analysis years

    dev note:  maintaining "state" here rather than generating indexes "on the fly" or from
               other indexes to eliminate possible loss of sync from multiple callers
    """

    def __init__(self, analysis_years: list[int], myopic_view: int = 1, myopic_step: int = 1):
        self._analysis_years = sorted(analysis_years)
        self.generated_mi: deque[MyopicIndex] = deque()

        self.myopic_view = myopic_view
        self.myopic_step = myopic_step

        # pointers
        self._status_idx: int | None = None
        self._base_idx = -1
        self._step_idx: int
        self._last_year_idx: int

        self.num_years = len(self._analysis_years)

    def _advance(self) -> bool:
        """Advance pointers"""
        self._base_idx += 1
        if self._base_idx >= len(self._analysis_years):
            return False
        if self._base_idx == 0:  # first one
            self._status_idx = None
        else:
            # default is the last year *before* this start
            self._status_idx = self._base_idx - 1
        self._step_idx = min(self._base_idx + self.myopic_step, self.num_years - 1)
        self._last_year_idx = min(self._base_idx + self.myopic_step, self.num_years - 1)
        return True

    def next(self) -> MyopicIndex | None:
        valid = self._advance()
        if not valid:
            return None
        year_slice = self._analysis_years[self._base_idx : self._last_year_idx]

        mi = MyopicIndex(
            status_year=self._analysis_years[self._status_idx] if self._status_idx else None,
            base_year=self._analysis_years[self._base_idx],
            step_year=self._analysis_years[self._step_idx],
            last_year=self._analysis_years[self._last_year_idx],
            included_analysis_years=self._analysis_years[self._base_idx : self._last_year_idx],
        )
        self.generated_mi.append(mi)
        return mi

    def revert(self) -> MyopicIndex | None:
        if self._base_idx == 0:
            logger.info('Hit backstop trying to back Myopic Index earlier than base year')

        self._base_idx -= 1
        logger.info(
            'Reverting Myopic Index back to year %d.  Now covering %d years',
            self._analysis_years[self._base_idx],
            self._last_year_idx - self._base_idx + 1,
        )

    def _slice(self, first_idx: int, count: int) -> list[int]:
        """
        Construct a slice of the analysis years from the first index forward

        :param first_idx: the index of the first analysis year
        :param count: the number of analysis years
        :return: a slice of the analysis years

        """
        end_idx = min(first_idx + count, len(self._analysis_years[first_idx:]))
        return self._analysis_years[first_idx:end_idx]


if __name__ == '__main__':
    myopic_index = MyopicIndex(
        status_year=2020,
        base_year=2025,
        step_year=2030,
        last_year=2050,
        included_analysis_years=[2020, 20252030, 3050],
    )
