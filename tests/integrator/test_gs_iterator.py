"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Tests for the Gauss-Seidel iterator.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import parse_config_file
from src.common.integrated_model_sequencer import IterationStatus
from src.common.iterative_sequencer import RunStatus
from src.common.log_setup import _retire_previous_setup
from src.common.models_modes import ModelType
from src.integrator.gauss_seidel.gs_iterator import GaussSeidelIterator


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Undo the run's root logging setup, so later tests in this worker do not log through it."""
    root = logging.getLogger()
    level = root.level
    yield
    _retire_previous_setup()
    root.setLevel(level)


@pytest.mark.usefixtures('restore_logging')
def test_run_solves_in_order_and_writes_results(tmp_path: Path) -> None:
    """Electricity then gas each iteration on held models, timed, with result files at the end.

    Electricity region 7 and New England cover each other fully, the smallest such pair.  New
    England alone is short of gas, so every gas solve is PENALTY, which, as in Jacobi, keeps the
    run going to the iteration limit.
    """
    common, remainder = parse_config_file(PROJECT_ROOT / 'run_configs' / 'gs_compare.toml')
    common = common.model_copy(update={'output_path': tmp_path, 'summary_years': [2025]})
    remainder['elec_config']['region_filter'] = ['7']
    remainder['natural_gas']['region_filter'] = ['new_england']
    iterator = GaussSeidelIterator()
    iterator._config = iterator.config.model_copy(update={'iteration_limit': 2})

    status, results = iterator.run(common, remainder)

    assert status is RunStatus.ITERATION_LIMIT
    assert list(results) == [1, 2]
    for iteration, iteration_results in results.items():
        assert [r.model_type for r in iteration_results] == [
            ModelType.ELECTRICITY,
            ModelType.NATURAL_GAS,
        ]
        assert iteration_results[1].status is IterationStatus.PENALTY
        for result in iteration_results:
            assert {'update', 'solve', 'collect'} <= set(result.timings)
            assert ('build' in result.timings) == (iteration == 1)
    assert any((common.output_folder / 'electricity' / 'variables').iterdir())
    assert any((common.output_folder / 'natural_gas').iterdir())
