"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Tests for the Jacobi iterator's relative-change convergence test and its config.

"""

import math

import pytest
from pydantic import ValidationError

from src.common.integrated_model_sequencer import (
    ALLOW_OUTBOUND_UPDATES,
    ALLOW_TERMINATION,
    IterationResult,
    IterationStatus,
)
from src.common.models_modes import ModelType
from src.integrator.convergence import ConvergenceTracker, relative_change
from src.integrator.iteration_monitor import DeltaMode
from src.integrator.jacobi.jacobi_config import JacobiConfig

ELEC, NG, MAGIC = ModelType.ELECTRICITY, ModelType.NATURAL_GAS, ModelType.MAGIC


def result(
    model: ModelType, objective: float | None, status: IterationStatus = IterationStatus.BEST
) -> IterationResult:
    """A result for ``model`` with no packages."""
    return IterationResult(
        model_type=model, status=status, objective_value=objective, update_packages=[]
    )


@pytest.mark.parametrize(
    ('previous', 'current', 'expected'),
    [
        (100.0, 101.0, 0.01),
        (100.0, 99.0, 0.01),
        (-200.0, -198.0, 0.01),  # scaled by magnitude, so negative objectives work alike
        (0.0, 0.0, 0.0),
        (0.0, 1e-9, math.inf),
    ],
)
def test_relative_change(previous: float, current: float, expected: float) -> None:
    """The change is scaled by the previous value's magnitude, with the zero cases defined."""
    assert relative_change(previous, current) == pytest.approx(expected)


def test_converges_after_required_stable_iterations() -> None:
    """Every objective-bearing model must stay under epsilon for the required run of iterations.

    Iteration 1 only sets baselines, so with 2 required iterations the earliest convergence is
    iteration 3.  Models on very different scales converge on the same relative footing.
    """
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=2)
    assert not tracker.update([result(ELEC, 1.0e9), result(NG, -5.0e11)])
    assert not tracker.update([result(ELEC, 1.001e9), result(NG, -5.001e11)])
    assert tracker.update([result(ELEC, 1.002e9), result(NG, -5.002e11)])


def test_a_large_change_restarts_the_streak() -> None:
    """A change at or over epsilon resets the model's count of stable iterations."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=2)
    tracker.update([result(ELEC, 100.0)])
    tracker.update([result(ELEC, 100.5)])  # stable 1
    assert not tracker.update([result(ELEC, 110.0)])  # jump: reset
    assert not tracker.update([result(ELEC, 110.1)])  # stable 1
    assert tracker.update([result(ELEC, 110.2)])  # stable 2
    assert tracker.changes[ELEC] == pytest.approx(0.1 / 110.1)


def test_every_model_must_be_stable() -> None:
    """One stable model is not enough while another is still moving."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    tracker.update([result(ELEC, 100.0), result(NG, 100.0)])
    assert not tracker.update([result(ELEC, 100.0), result(NG, 150.0)])
    assert tracker.update([result(ELEC, 100.0), result(NG, 150.0)])


def test_models_without_objectives_are_left_out() -> None:
    """MAGIC reports no objective and neither blocks nor counts toward convergence."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    tracker.update([result(ELEC, 100.0), result(MAGIC, None)])
    assert tracker.update([result(ELEC, 100.0), result(MAGIC, None)])
    assert MAGIC not in tracker.changes


def test_no_objectives_never_converge() -> None:
    """With nothing to measure, the run cannot be declared converged."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    for _ in range(3):
        assert not tracker.update([result(MAGIC, None)])


def test_failed_solve_resets_and_keeps_the_baseline() -> None:
    """A rejected status blocks convergence, resets the streak, and keeps the last good value.

    The next good objective is measured against the pre-failure objective, not the failed one.
    """
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    tracker.update([result(ELEC, 100.0)])
    assert not tracker.update([result(ELEC, 999.0, IterationStatus.ERROR)])
    assert tracker.changes[ELEC] is None
    assert tracker.update([result(ELEC, 100.5)])
    assert tracker.changes[ELEC] == pytest.approx(0.005)


@pytest.mark.parametrize(
    ('status', 'sends', 'may_stop'),
    [
        (IterationStatus.BEST, True, True),
        (IterationStatus.USABLE, True, True),
        (IterationStatus.PENALTY, True, False),
        (IterationStatus.ERROR, False, False),
    ],
)
def test_status_sets(status: IterationStatus, sends: bool, may_stop: bool) -> None:
    """PENALTY results are sent onward but are no place to stop."""
    assert (status in ALLOW_OUTBOUND_UPDATES) is sends
    assert (status in ALLOW_TERMINATION) is may_stop


def test_penalty_blocks_convergence_but_keeps_its_objective() -> None:
    """A stable PENALTY model holds the run open; its accurate objective is the next baseline."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    tracker.update([result(ELEC, 100.0)])
    assert not tracker.update([result(ELEC, 150.0, IterationStatus.PENALTY)])
    assert tracker.changes[ELEC] == pytest.approx(0.5)
    # measured against the penalty iteration's 150, not the earlier 100
    assert tracker.update([result(ELEC, 150.5)])


def test_penalty_resets_the_streak() -> None:
    """Stable iterations before a PENALTY do not carry past it."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=2)
    tracker.update([result(ELEC, 100.0)])
    tracker.update([result(ELEC, 100.1)])  # stable 1
    assert not tracker.update([result(ELEC, 100.2, IterationStatus.PENALTY)])  # reset
    assert not tracker.update([result(ELEC, 100.3)])  # stable 1
    assert tracker.update([result(ELEC, 100.4)])  # stable 2


def test_penalty_in_one_model_holds_the_others() -> None:
    """Every other model being stable is not enough while one is in PENALTY."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    tracker.update([result(ELEC, 100.0), result(NG, 100.0)])
    assert not tracker.update([result(ELEC, 100.0), result(NG, 100.0, IterationStatus.PENALTY)])


def test_penalty_without_objective_still_blocks() -> None:
    """A model with no objective still holds the run open while in PENALTY."""
    tracker = ConvergenceTracker(epsilon=0.01, required_iterations=1)
    tracker.update([result(ELEC, 100.0), result(MAGIC, None)])
    assert not tracker.update([result(ELEC, 100.0), result(MAGIC, None, IterationStatus.PENALTY)])
    assert tracker.update([result(ELEC, 100.0), result(MAGIC, None)])


VALID = {
    'iteration_limit': 15,
    'epsilon': 0.001,
    'convergence_iterations': 2,
    'worker_processes': 6,
    'monitor_delta_mode': 'absolute',
}


@pytest.mark.parametrize('epsilon', [0.001, 0.5, 1.0])
def test_config_accepts_epsilon_ratios(epsilon: float) -> None:
    """Epsilon is a ratio in (0, 1]."""
    config = JacobiConfig(**{**VALID, 'epsilon': epsilon})
    assert config.epsilon == epsilon
    assert config.monitor_delta_mode is DeltaMode.ABSOLUTE


@pytest.mark.parametrize('epsilon', [0.0, -0.1, 1.5, 100.0])
def test_config_rejects_epsilon_outside_ratio_range(epsilon: float) -> None:
    """Zero, negative, and absolute-scale tolerances are rejected."""
    with pytest.raises(ValidationError):
        JacobiConfig(**{**VALID, 'epsilon': epsilon})


def test_shipped_config_loads() -> None:
    """The jacobi_config.toml beside the iterator validates."""
    from src.integrator.jacobi.jacobi_iterator import JacobiIterator

    config = JacobiIterator().config
    assert 0 < config.epsilon <= 1
    assert config.convergence_iterations >= 1
