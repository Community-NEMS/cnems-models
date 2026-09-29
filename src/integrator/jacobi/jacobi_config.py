"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Settings for the Jacobi iterator, read from ``jacobi_config.toml`` beside this module.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field, PositiveInt

from src.common.iterative_sequencer import IterativeSequencerConfig
from src.integrator.iteration_monitor import DeltaMode

# the iterator's own settings file, read by default
DEFAULT_JACOBI_CONFIG_PATH = Path(__file__).with_name('jacobi_config.toml')


class JacobiConfig(IterativeSequencerConfig):
    """Settings for :class:`~src.integrator.jacobi.jacobi_iterator.JacobiIterator`.

    No field has a default:  the TOML is the single source of the values.

    Attributes
    ----------
    iteration_limit : int
        The most iterations to run.
    epsilon : float
        Convergence tolerance, as a ratio in (0, 1]:  a model is stable in an iteration when its
        objective changed by less than this fraction of the previous iteration's.
    convergence_iterations : int
        How many consecutive iterations every objective-bearing model must be stable for the
        run to stop.
    worker_processes : int
        Size of the worker pool the models solve in.
    monitor_delta_mode : DeltaMode
        How the iteration monitor shows each model's objective change.
    """

    iteration_limit: PositiveInt
    epsilon: Annotated[float, Field(gt=0, le=1)]
    convergence_iterations: PositiveInt
    worker_processes: PositiveInt
    monitor_delta_mode: DeltaMode
