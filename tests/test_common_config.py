"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

Tests for the CommonConfig scenario-directory guard, which redirects a run to a new scenario
name rather than writing fresh results on top of an earlier run's output.
"""

import logging

import pytest

from src.common.common_config import CommonConfig

BASE_SCENARIO = 'mock_scenario'


@pytest.fixture
def config_kwargs(tmp_path):
    """Minimal valid ``CommonConfig`` kwargs rooted in ``tmp_path``.

    ``check_paths`` joins the configured paths onto ``PROJECT_ROOT``; absolute paths survive that
    join unchanged, so the temp directory stays the run's output root.
    """
    (tmp_path / 'residential').mkdir()
    return {
        'mode': 'standalone',
        'models_to_run': ['electricity'],
        'common_data_path': tmp_path / 'input',
        'residential_data_path': tmp_path / 'residential',
        'output_path': tmp_path / 'output',
        'scenario_name': BASE_SCENARIO,
        'temporal_resolution': 'default',
        'aggregate_years': False,
        'aggregate_start_year': None,
        'summary_years': [2025],
    }


def test_unused_scenario_name_is_kept(config_kwargs):
    """An unclaimed scenario name is left alone, and its directory is reserved."""
    config = CommonConfig(**config_kwargs)

    assert config.scenario_name == BASE_SCENARIO
    assert config.original_scenario_name is None
    assert (config.output_path / BASE_SCENARIO).is_dir()


def test_back_to_back_configs_reserve_distinct_dirs(config_kwargs):
    """Each construction claims its own dir, so a second config never reuses the first's."""
    first = CommonConfig(**config_kwargs)
    second = CommonConfig(**config_kwargs)

    assert first.scenario_name == BASE_SCENARIO
    assert second.scenario_name == f'{BASE_SCENARIO}_1'
    assert second.original_scenario_name == BASE_SCENARIO


def test_non_directory_occupant_is_skipped(config_kwargs):
    """A plain file holding the scenario name also counts as taken."""
    output_path = config_kwargs['output_path']
    output_path.mkdir(parents=True)
    (output_path / BASE_SCENARIO).touch()

    config = CommonConfig(**config_kwargs)

    assert config.scenario_name == f'{BASE_SCENARIO}_1'
    assert (output_path / BASE_SCENARIO).is_file()


@pytest.mark.parametrize('taken', [1, 2, 3])
def test_existing_scenario_dir_is_enumerated(config_kwargs, caplog, taken):
    """A taken scenario dir pushes the run to the next free ``<base>_<n>`` directory."""
    output_path = config_kwargs['output_path']
    existing = [BASE_SCENARIO] + [f'{BASE_SCENARIO}_{n}' for n in range(1, taken)]
    for name in existing:
        (output_path / name / 'electricity').mkdir(parents=True)
        (output_path / name / 'electricity' / 'stale.csv').touch()

    with caplog.at_level(logging.WARNING, logger='src.common.common_config'):
        config = CommonConfig(**config_kwargs)

    assert config.scenario_name == f'{BASE_SCENARIO}_{taken}'
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert config.original_scenario_name == BASE_SCENARIO
    # the chosen directory is reserved but empty, and every earlier run is left intact
    assert not any((config.output_path / config.scenario_name).iterdir())
    for name in existing:
        assert (output_path / name / 'electricity' / 'stale.csv').is_file()
