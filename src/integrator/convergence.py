"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Convergence tests for the iterators:  relative change of each model's objective and of selected
exchanged values, scale-free.
"""

import math
from collections.abc import Collection, Iterable

import pandas as pd

from src.common.integrated_model_sequencer import (
    ALLOW_OUTBOUND_UPDATES,
    ALLOW_TERMINATION,
    IterationResult,
)
from src.common.models_modes import ModelType


def relative_change(previous: float, current: float) -> float:
    """The magnitude of the change from ``previous`` to ``current``, relative to ``previous``.

    Parameters
    ----------
    previous : float
        The earlier objective value; the change is scaled by its magnitude.
    current : float
        The later objective value.

    Returns
    -------
    float
        ``|current - previous| / |previous|``.  When ``previous`` is zero:  0.0 if ``current`` is
        also zero, otherwise ``inf``.
    """
    if previous == 0:
        return 0.0 if current == 0 else math.inf
    return abs(current - previous) / abs(previous)


class ConvergenceTracker:
    """Tracks each model's relative objective change across iterations.

    A model is stable once its objective has changed by less than ``epsilon`` (relative to the
    previous iteration's) for ``required_iterations`` consecutive iterations.  The run has
    converged when every model reporting an objective is stable.

    - A model whose status is outside ``ALLOW_OUTBOUND_UPDATES`` is not stable that iteration:
      its streak resets, and its last good objective stays the baseline for the next change.
    - A model whose status allows updates but not termination (``PENALTY``:  accurate, but
      leaning on a soft limit) blocks convergence and resets its streak.  Its objective is
      accurate, so its change is still recorded and it becomes the next baseline.
    - A model that solves with no objective (e.g. MAGIC) is left out of the test.
    - A model's first objective has no previous value to change from, so it starts no streak.
    - With no objective-bearing model in the iteration, the run never converges.

    Parameters
    ----------
    epsilon : float
        The relative-change threshold, in (0, 1].
    required_iterations : int
        How many consecutive iterations a model must stay under ``epsilon``.
    """

    def __init__(self, epsilon: float, required_iterations: int) -> None:
        self.epsilon = epsilon
        self.required_iterations = required_iterations
        self._previous: dict[ModelType, float] = {}
        self._streak: dict[ModelType, int] = {}
        # each model's most recent relative change; None until it has two good objectives
        self.changes: dict[ModelType, float | None] = {}

    def update(self, results: Iterable[IterationResult]) -> bool:
        """Record one iteration's results and report whether the run has converged.

        Parameters
        ----------
        results : iterable of IterationResult
            One iteration's results, one per model.

        Returns
        -------
        bool
            True if every objective-bearing model has now been stable for
            ``required_iterations`` consecutive iterations.
        """
        converged = True
        any_objective = False
        for result in results:
            model = result.model_type
            if result.status not in ALLOW_OUTBOUND_UPDATES:
                self._streak[model] = 0
                self.changes[model] = None
                converged = False
                continue
            may_stop = result.status in ALLOW_TERMINATION
            if not may_stop:
                converged = False
            objective = result.objective_value
            if objective is None:
                continue
            any_objective = True
            previous = self._previous.get(model)
            if previous is None:
                change = None
                self._streak[model] = 0
            else:
                change = relative_change(previous, objective)
                stable = may_stop and change < self.epsilon
                self._streak[model] = self._streak.get(model, 0) + 1 if stable else 0
            self.changes[model] = change
            self._previous[model] = objective
            if self._streak[model] < self.required_iterations:
                converged = False
        return converged and any_objective


def largest_relative_change(previous: pd.Series, current: pd.Series) -> float:
    """The largest per-entry :func:`relative_change` between two indexed series.

    Parameters
    ----------
    previous : pd.Series
        The earlier values.
    current : pd.Series
        The later values.

    Returns
    -------
    float
        The largest ``|current - previous| / |previous|`` over the entries; ``inf`` if the two
        series do not hold the same entries.
    """
    if not previous.index.equals(current.index):
        if set(previous.index) != set(current.index):
            return math.inf
        current = current.reindex(previous.index)
    return max(
        (relative_change(float(p), float(c)) for p, c in zip(previous, current, strict=True)),
        default=0.0,
    )


class ExchangeTracker:
    """Tracks the largest relative change of selected exchanged values across iterations.

    :class:`ConvergenceTracker` does not see a model without an objective, such as C-HSM.  This
    tracker requires the one-column ``elements`` of each tracked package type to change by less
    than ``epsilon`` for ``required_iterations`` consecutive iterations.

    - Pass only types the run exchanges (``ExchangeSpec.expected``); a passed type the run never
      sends blocks the stop.
    - A type's first values start no streak.
    - An iteration with no package of a tracked type from a result in ``ALLOW_OUTBOUND_UPDATES``
      resets that type's streak.

    Parameters
    ----------
    epsilon : float
        The relative-change threshold, in (0, 1].
    required_iterations : int
        How many consecutive iterations each tracked type must stay under ``epsilon``.
    package_names : Collection of str
        The package class names to track.
    """

    def __init__(
        self, epsilon: float, required_iterations: int, package_names: Collection[str]
    ) -> None:
        self.epsilon = epsilon
        self.required_iterations = required_iterations
        self.package_names = frozenset(package_names)
        self._previous: dict[str, pd.Series] = {}
        self._streak: dict[str, int] = dict.fromkeys(self.package_names, 0)
        # each tracked type's latest largest relative change; None until it has two values
        self.changes: dict[str, float | None] = dict.fromkeys(self.package_names)

    def update(self, results: Iterable[IterationResult]) -> bool:
        """Record one iteration's packages and report whether every tracked type is stable.

        Parameters
        ----------
        results : iterable of IterationResult
            One iteration's results, one per model.

        Returns
        -------
        bool
            True if each tracked type has been stable for ``required_iterations`` consecutive
            iterations, or nothing is tracked.
        """
        missing = set(self.package_names)
        for result in results:
            if result.status not in ALLOW_OUTBOUND_UPDATES:
                continue
            for package in result.update_packages:
                name = type(package).__name__
                if name not in self.package_names:
                    continue
                missing.discard(name)
                elements = getattr(package, 'elements', None)
                if not isinstance(elements, pd.DataFrame) or elements.shape[1] != 1:
                    raise TypeError(f'{name} carries no one-column elements frame to track')
                values = elements.iloc[:, 0].astype(float)
                previous = self._previous.get(name)
                if previous is None:
                    change = None
                    self._streak[name] = 0
                else:
                    change = largest_relative_change(previous, values)
                    stable = change < self.epsilon
                    self._streak[name] = self._streak[name] + 1 if stable else 0
                self.changes[name] = change
                self._previous[name] = values
        for name in missing:
            self._streak[name] = 0
            self.changes[name] = None
        return all(streak >= self.required_iterations for streak in self._streak.values())
