"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Bookkeeping shared by the iterative sequencers' control loops.

The Jacobi and Gauss-Seidel iterators differ in when their models solve and what each solve
sees.  What they do with the results is the same, and lives here:  accepting a model's outbound
packages or holding its last accepted ones, flattening the accepted packages in circuit order,
logging and displaying each iteration, and deciding how a run ended.  Each function logs through
the logger its caller passes, so a run's log lines name the iterator that ran it.
"""

import logging
from collections.abc import Sequence

from rich.console import Console

from src.common.integrated_model_sequencer import (
    ALLOW_OUTBOUND_UPDATES,
    ALLOW_TERMINATION,
    IterationResult,
)
from src.common.iterative_sequencer import RunStatus
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage
from src.integrator.convergence import ConvergenceTracker
from src.integrator.iteration_monitor import IterationMonitor

logger = logging.getLogger(__name__)


def accept_packages(
    result: IterationResult,
    accepted: dict[ModelType, list[UpdatePackage]],
    iteration: int,
    log: logging.Logger = logger,
) -> None:
    """Refresh a sender's accepted packages from its result, or keep its last accepted ones.

    A sender whose solve status is outside ``ALLOW_OUTBOUND_UPDATES`` keeps its last accepted
    packages, so its receivers see its last good solution rather than falling back to raw input
    data.

    Parameters
    ----------
    result : IterationResult
        The sender's result for this iteration.
    accepted : dict of ModelType to list of UpdatePackage
        The last accepted packages per sender, modified in place.
    iteration : int
        The iteration number, for the log.
    log : logging.Logger
        The caller's logger.
    """
    if result.status in ALLOW_OUTBOUND_UPDATES:
        accepted[result.model_type] = list(result.update_packages)
        return
    held = accepted.get(result.model_type)
    log.warning(
        'Iteration %d: rejected %d update package(s) from %s (status %s); %s',
        iteration,
        len(result.update_packages),
        result.model_type.value,
        result.status.name,
        f'resending its last accepted {len(held)} package(s)'
        if held is not None
        else 'it has no accepted packages to resend',
    )


def show_iteration(
    iteration: int,
    results: Sequence[IterationResult],
    monitor: IterationMonitor,
    console: Console,
    log: logging.Logger = logger,
    log_results: bool = True,
) -> None:
    """Log each model's result, then log and print the iteration monitor's block.

    Parameters
    ----------
    iteration : int
        The iteration number.
    results : sequence of IterationResult
        This iteration's results.
    monitor : IterationMonitor
        The run's monitor, which remembers objectives across iterations.
    console : Console
        Where the block is printed.
    log : logging.Logger
        The caller's logger.
    log_results : bool
        Log each result first.  A caller that logs each result as it arrives passes False.
    """
    if log_results:
        for result in results:
            log.info('\n%s', result.pprint(indent=2))
    block = monitor.record(iteration, results)
    log.info('\n%s', block.plain)
    console.print(block, highlight=False)


def log_progress(
    tracker: ConvergenceTracker,
    iteration: int,
    epsilon: float,
    iteration_limit: int,
    log: logging.Logger = logger,
) -> None:
    """Log each model's relative objective change and the iteration count.

    Parameters
    ----------
    tracker : ConvergenceTracker
        Updated with this iteration's results.
    iteration : int
        The iteration number.
    epsilon : float
        The convergence tolerance, for the log.
    iteration_limit : int
        The most iterations the run may take, for the log.
    log : logging.Logger
        The caller's logger.
    """
    log.info(
        'Iteration %d relative objective changes (epsilon %g): %s',
        iteration,
        epsilon,
        {
            model.value: 'n/a' if change is None else f'{change:.3g}'
            for model, change in tracker.changes.items()
        },
    )
    log.info('Done with iteration %d/%d', iteration, iteration_limit)


def final_status(
    converged: bool,
    iterations_run: int,
    iteration_limit: int,
    final_results: Sequence[IterationResult],
    log: logging.Logger = logger,
) -> RunStatus:
    """Decide and log how a run ended.

    Parameters
    ----------
    converged : bool
        Whether the stopping test passed.
    iterations_run : int
        How many iterations ran.
    iteration_limit : int
        The most iterations the run could take.
    final_results : sequence of IterationResult
        The last iteration's results; models outside ``ALLOW_TERMINATION`` are named.
    log : logging.Logger
        The caller's logger.

    Returns
    -------
    RunStatus
        ``CONVERGED``, ``ITERATION_LIMIT``, or ``UNKNOWN`` if the run stopped for neither reason.
    """
    if converged:
        log.info('Converged after %d iteration(s)', iterations_run)
        return RunStatus.CONVERGED
    if iterations_run >= iteration_limit:
        holding = [
            f'{r.model_type.value} ({r.status.name})'
            for r in final_results
            if r.status not in ALLOW_TERMINATION
        ]
        log.warning(
            'Did not converge within %d iterations%s',
            iteration_limit,
            f'; ended with {", ".join(holding)}' if holding else '',
        )
        return RunStatus.ITERATION_LIMIT
    return RunStatus.UNKNOWN
