"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

Tests for CommonConfig.make_scenario_dir, which redirects a run to a new scenario
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
    """An unclaimed scenario name is used as-is for the output folder."""
    config = CommonConfig(**config_kwargs)
    folder = config.make_scenario_dir()

    assert config.scenario_name == BASE_SCENARIO
    assert folder == config.output_folder == config.output_path / BASE_SCENARIO
    assert folder.is_dir()


def test_output_folder_requires_make_scenario_dir(config_kwargs):
    """Reading ``output_folder`` before ``make_scenario_dir`` raises and creates nothing."""
    config = CommonConfig(**config_kwargs)

    with pytest.raises(RuntimeError, match='make_scenario_dir'):
        _ = config.output_folder
    assert not (config.output_path / BASE_SCENARIO).exists()


def test_second_make_scenario_dir_call_raises(config_kwargs):
    """A config claims one folder; a repeat call raises rather than claiming another."""
    config = CommonConfig(**config_kwargs)
    folder = config.make_scenario_dir()

    with pytest.raises(RuntimeError, match='already created'):
        config.make_scenario_dir()
    assert config.output_folder == folder
    assert not (config.output_path / f'{BASE_SCENARIO}_1').exists()


def test_back_to_back_configs_reserve_distinct_dirs(config_kwargs):
    """Each config claims its own folder; ``scenario_name`` stays as given."""
    first = CommonConfig(**config_kwargs)
    first.make_scenario_dir()
    second = CommonConfig(**config_kwargs)
    second.make_scenario_dir()

    assert first.output_folder.name == BASE_SCENARIO
    assert second.output_folder.name == f'{BASE_SCENARIO}_1'
    assert first.scenario_name == second.scenario_name == BASE_SCENARIO


def test_non_directory_occupant_is_skipped(config_kwargs):
    """A plain file holding the scenario name also counts as taken."""
    output_path = config_kwargs['output_path']
    output_path.mkdir(parents=True)
    (output_path / BASE_SCENARIO).touch()

    config = CommonConfig(**config_kwargs)
    config.make_scenario_dir()

    assert config.output_folder.name == f'{BASE_SCENARIO}_1'
    assert (output_path / BASE_SCENARIO).is_file()


@pytest.mark.parametrize('taken', [1, 2, 3])
def test_existing_scenario_dir_is_enumerated(config_kwargs, caplog, capsys, taken):
    """A taken scenario folder pushes the run to the next free ``<base>_<n>`` folder."""
    output_path = config_kwargs['output_path']
    existing = [BASE_SCENARIO] + [f'{BASE_SCENARIO}_{n}' for n in range(1, taken)]
    for name in existing:
        (output_path / name / 'electricity').mkdir(parents=True)
        (output_path / name / 'electricity' / 'stale.csv').touch()

    config = CommonConfig(**config_kwargs)
    with caplog.at_level(logging.DEBUG, logger='src.common.common_config'):
        config.make_scenario_dir()

    assert config.scenario_name == BASE_SCENARIO
    assert config.output_folder == output_path / f'{BASE_SCENARIO}_{taken}'
    # the redirect is announced on stderr only, never logged
    assert f'{BASE_SCENARIO}_{taken}' in capsys.readouterr().err
    assert not caplog.records
    # the chosen folder is created empty, and every earlier run is left intact
    assert not any(config.output_folder.iterdir())
    for name in existing:
        assert (output_path / name / 'electricity' / 'stale.csv').is_file()
