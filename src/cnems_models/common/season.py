"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  10/7/26

Class to hold "Season" time element references that allows sort/hash/equality actions

"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Season:
    """Class to hold a season time element."""

    name: str
    sort_order: int

    def __repr__(self):
        """Represent this season as a string."""
        return f'Season({self.name}, {self.sort_order})'

    def __str__(self):
        """Name of season."""
        return self.name

    def __lt__(self, other):
        """Support sort order."""
        return self.sort_order < other.sort_order

    @classmethod
    def matched_set(cls, first: Collection[Season], other: Collection[Season]) -> bool:
        """Determine whether two groups of seasons are equivalent and free of duplicates."""
        first_set = set(first)
        other_set = set(other)
        combined = set(first) & set(other)
        if not all(isinstance(s, Season) for s in combined):
            return False
        return len({len(first), len(other), len(first_set), len(other_set), len(combined)}) == 1

    @classmethod
    def cycle(cls, seasons: Sequence[Season]) -> list[Season]:
        """Generate a full cycle of seasons in sorted order, looping back to first.

        Returns
        -------
            list[Season]

        Raises
        ------
            ValueError if ``seasons`` is empty or duplicates are present
        """
        if not seasons:
            raise ValueError('Cannot build a season cycle from an empty sequence')
        if len(seasons) != len(set(seasons)):
            raise ValueError('Duplicate seasons')
        sequence = sorted(seasons)
        # add the first back in
        sequence.append(sequence[0])
        return sequence
