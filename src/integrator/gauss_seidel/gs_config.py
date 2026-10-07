"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Settings for the Gauss-Seidel iterator, read from ``gs_config.toml`` beside this module.
"""

from pathlib import Path
from typing import Annotated

from pydantic import Field, PositiveInt

from src.common.iterative_sequencer import IterativeSequencerConfig
from src.integrator.iteration_monitor import DeltaMode

# the iterator's own settings file, read by default
DEFAULT_GS_CONFIG_PATH = Path(__file__).with_name('gs_config.toml')


class GaussSeidelConfig(IterativeSequencerConfig):
    """Settings for :class:`~src.integrator.gauss_seidel.gs_iterator.GaussSeidelIterator`.

    No field has a default:  the TOML is the single source of the values.  The stopping fields
    mean the same as :class:`~src.integrator.jacobi.jacobi_config.JacobiConfig`'s.

    Attributes
    ----------
    iteration_limit : int
        The most iterations to run.
    epsilon : float
        Convergence tolerance, as a ratio in (0, 1]:  a model is stable in an iteration when its
        objective changed by less than this fraction of the previous iteration's.
    convergence_iterations : int
        How many consecutive iterations every model must be stable for the run to stop.
    monitor_delta_mode : DeltaMode
        How the iteration monitor shows each model's objective change.
    allow_partial_coverage : bool
        Passed to ``build_preflight``:  accept electricity and gas regions that do not fully
        cover each other.
    """

    iteration_limit: PositiveInt
    epsilon: Annotated[float, Field(gt=0, le=1)]
    convergence_iterations: PositiveInt
    monitor_delta_mode: DeltaMode
    allow_partial_coverage: bool
