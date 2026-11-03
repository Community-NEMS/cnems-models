"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Tests for the electricity sequencer's PENALTY check on unmet load.

The check reads solved values through ``pyo.value``, which passes plain numbers (and ``None``)
straight through, so a mock model holding a dict of floats stands in for a solved ``PowerModel``.
"""

from types import SimpleNamespace
from typing import cast

import pytest

from src.models.electricity.constants import UNMET_LOAD_PENALTY_TOL
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer


def mock_model(unmet: dict[tuple, float | None]) -> PowerModel:
    """A stand-in for a solved model carrying only ``unmet_load``."""
    return cast(PowerModel, SimpleNamespace(unmet_load=unmet))


@pytest.mark.parametrize(
    ('unmet', 'expected'),
    [
        ({}, False),
        ({('1', 2030, 1): 0.0, ('2', 2030, 1): 0.0}, False),
        ({('1', 2030, 1): UNMET_LOAD_PENALTY_TOL / 2}, False),  # decimal dust
        ({('1', 2030, 1): UNMET_LOAD_PENALTY_TOL}, False),  # the cutoff itself is not over it
        ({('1', 2030, 1): -1e-6}, False),  # negative dust from the solver
        ({('1', 2030, 1): None}, False),  # an unsolved value is not a shortfall
        ({('1', 2030, 1): 0.0, ('2', 2030, 7): 2 * UNMET_LOAD_PENALTY_TOL}, True),
        ({('1', 2030, 1): 350.0}, True),
    ],
)
def test_in_penalty(unmet: dict[tuple, float | None], expected: bool) -> None:
    """Any index with unmet load over the tolerance puts the solve in PENALTY."""
    assert ElectricitySequencer._in_penalty(mock_model(unmet)) is expected


def test_in_penalty_logs_the_shortfall(caplog: pytest.LogCaptureFixture) -> None:
    """A PENALTY finding is logged with the total and the count of short indices."""
    unmet = {('1', 2030, 1): 1.5, ('1', 2030, 2): 2.5, ('2', 2030, 1): 0.0}
    with caplog.at_level('WARNING', logger='src.models.electricity.sequencer'):
        assert ElectricitySequencer._in_penalty(mock_model(unmet))
    assert 'Unmet load of 4 across 2 (region, year, hour) index(es)' in caplog.text
