"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Tests for the natural gas sequencer's PENALTY check on unserved demand.

The check reads solved values through pyomo's ``value``, which passes plain numbers (and
``None``) straight through, so a mock model holding a dict of floats stands in for a solved
``NGModel``.
"""

from types import SimpleNamespace
from typing import cast

import pytest

from src.models.natural_gas.constants import UNSERVED_PENALTY_TOL
from src.models.natural_gas.ng_model import NGModel
from src.models.natural_gas.sequencer import NGSequencer


def mock_model(unserved: dict[tuple, float | None]) -> NGModel:
    """A stand-in for a solved model carrying only ``unserved``."""
    return cast(NGModel, SimpleNamespace(unserved=unserved))


@pytest.mark.parametrize(
    ('unserved', 'expected'),
    [
        ({}, False),
        ({('new_england', 2030): 0.0, ('pacific', 2030): 0.0}, False),
        ({('new_england', 2030): UNSERVED_PENALTY_TOL / 2}, False),  # decimal dust
        ({('new_england', 2030): UNSERVED_PENALTY_TOL}, False),  # the cutoff is not over it
        ({('new_england', 2030): -1e-6}, False),  # negative dust from the solver
        ({('new_england', 2030): None}, False),  # an unsolved value is not a shortfall
        ({('new_england', 2030): 0.0, ('pacific', 2050): 2 * UNSERVED_PENALTY_TOL}, True),
        ({('new_england', 2030): 176.3}, True),
    ],
)
def test_in_penalty(unserved: dict[tuple, float | None], expected: bool) -> None:
    """Any region-year with unserved demand over the tolerance puts the solve in PENALTY."""
    assert NGSequencer._in_penalty(mock_model(unserved)) is expected


def test_in_penalty_logs_each_short_region_year(caplog: pytest.LogCaptureFixture) -> None:
    """A PENALTY finding lists the total and every short region-year, sorted; dust is left out."""
    unserved = {
        ('pacific', 2050): 121.9,
        ('new_england', 2025): 176.3,
        ('mountain', 2025): UNSERVED_PENALTY_TOL / 2,
    }
    with caplog.at_level('WARNING', logger='src.models.natural_gas.sequencer'):
        assert NGSequencer._in_penalty(mock_model(unserved))
    assert '298.2 Bcf of demand unserved in 2 region-year(s)' in caplog.text
    assert 'solve status PENALTY: new_england 2025 176.3 Bcf, pacific 2050 121.9 Bcf' in caplog.text
    assert 'mountain' not in caplog.text


def test_no_penalty_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A fully served solve logs no shortfall."""
    with caplog.at_level('WARNING', logger='src.models.natural_gas.sequencer'):
        assert not NGSequencer._in_penalty(mock_model({('pacific', 2050): 0.0}))
    assert caplog.text == ''
