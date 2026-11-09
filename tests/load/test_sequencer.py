"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Written with:  Claude Sonnet 5.5 (Anthropic)
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Tests of the load model's integration with the project:  config, scenario map, sequencer, and its
place in the run modes.
"""

import tomllib

import pandas as pd
import pytest
from pydantic import ValidationError

from definitions import PROJECT_ROOT
from main import _run_standalone
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.common.models_modes import ModelType, resolve_models_to_run
from src.models.load.load_config import LoadConfig, nems_scenario_for, read_scenario_map
from src.models.load.sequencer import LoadSequencer, clear_previous_results, load_output_folder

DEMO_CONFIG = PROJECT_ROOT / 'run_configs' / 'basic_load_config.toml'
SCENARIO_MAP = PROJECT_ROOT / 'input' / 'load' / 'scenario_map.csv'
DEMO_CENSUS = 'tests/load/data/demo_census_loads_SYNTHETIC.csv'


@pytest.fixture
def demo_sections(tmp_path) -> tuple[CommonConfig, dict]:
    """The demo run config, with its results redirected to ``tmp_path``."""
    with open(DEMO_CONFIG, 'rb') as f:
        data = tomllib.load(f)
    data['common']['output_path'] = str(tmp_path)
    common_config = CommonConfig(**data.pop('common'))
    return common_config, data


# ---- scenario map
# ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ('project_name', 'nems_name'), [('baseline', 'cb2026'), ('high_elec_demand', 'higheldmd')]
)
def test_scenario_map_gives_the_nems_folder(project_name, nems_name):
    """Scenario map gives the nems folder."""
    assert nems_scenario_for(project_name, SCENARIO_MAP) == nems_name


def test_unknown_scenario_names_the_valid_ones():
    """Unknown scenario names the valid ones."""
    with pytest.raises(ValueError, match="'no_such_one'.*baseline.*high_elec_demand"):
        nems_scenario_for('no_such_one', SCENARIO_MAP)


def test_scenario_map_rejects_duplicates_and_missing_columns(tmp_path):
    """Scenario map rejects duplicates and missing columns."""
    dup = tmp_path / 'dup.csv'
    dup.write_text('scenario_name,nems_scenario\nbaseline,cb2026\nbaseline,hm2026\n')
    with pytest.raises(ValueError, match='more than once'):
        read_scenario_map(dup)
    bad = tmp_path / 'bad.csv'
    bad.write_text('scenario,folder\nbaseline,cb2026\n')
    with pytest.raises(ValueError, match='missing'):
        read_scenario_map(bad)


# ---- config
# ---------------------------------------------------------------------------------------
def test_demo_config_parses(demo_sections):
    """Demo config parses."""
    _, sections = demo_sections
    config = LoadConfig(**sections['load_config'])
    assert config.census_division_loads == PROJECT_ROOT / DEMO_CENSUS
    assert config.hourly8760 and config.base_year == 2023


@pytest.mark.parametrize(
    ('settings', 'message'),
    [
        ({'base_year': 2023}, 'exactly one source'),
        (
            {
                'census_division_loads': DEMO_CENSUS,
                'emm_region_loads': DEMO_CENSUS,
                'base_year': 2023,
            },
            'exactly one source',
        ),
        ({'census_division_loads': DEMO_CENSUS}, 'exactly one of base_year and base_freeze_year'),
        (
            {'census_division_loads': DEMO_CENSUS, 'base_year': 2023, 'base_freeze_year': 2026},
            'exactly one of base_year and base_freeze_year',
        ),
        (
            {
                'census_division_loads': DEMO_CENSUS,
                'base_year': 2023,
                'loss_adj': 1.0,
                'ulossadj_file': 'input/load/ulossadj_by_year.csv',
            },
            'not both',
        ),
        ({'census_division_loads': 'no/such/file.csv', 'base_year': 2023}, 'is not a file'),
    ],
)
def test_config_rejects_inconsistent_settings(settings, message):
    """Config rejects inconsistent settings."""
    with pytest.raises(ValidationError, match=message.replace('exactly one source', 'exactly one')):
        LoadConfig(**settings)


def test_basic_method_needs_no_base_year():
    """Basic method needs no base year."""
    assert LoadConfig(census_division_loads=DEMO_CENSUS, method='basic').base_year is None


# ---- sequencer
# ------------------------------------------------------------------------------------
def test_sequencer_writes_the_hourly_file_for_the_electricity_model(demo_sections):
    """Sequencer writes the hourly file for the electricity model."""
    common_config, sections = demo_sections
    sequencer = LoadSequencer()
    sequencer.build_model(common_config, LoadConfig(**sections['load_config']))
    model_type, status = sequencer.solve_model()
    sequencer.full_postprocess()

    assert (model_type, status) == (ModelType.LOAD, IterationStatus.BEST)
    assert sequencer.get_objective_value() is None
    assert sequencer.get_outbound_updates() == []
    folder = load_output_folder(common_config)
    hourly = pd.read_csv(folder / 'hourly_load_mwh.csv')
    assert list(hourly.columns) == ['region_id', 'region', 'year', 'hour', 'sales_mwh', 'load_mwh']
    assert sorted(hourly.year.unique()) == common_config.summary_years
    assert {str(r) for r in hourly.region_id.unique()} == sequencer.model.regions
    assert len(hourly) == 25 * 8760 * len(common_config.summary_years)
    used = pd.read_csv(folder / 'scenario_used.csv').iloc[0]
    assert used.scenario_name == common_config.scenario_name
    assert used.source_file.endswith('demo_census_loads_SYNTHETIC.csv')


def test_results_go_to_a_fixed_folder_and_a_rerun_replaces_them(demo_sections):
    """Results go to output/load/<scenario>, never suffixed; a rerun removes stale files."""
    common_config, sections = demo_sections
    folder = load_output_folder(common_config)
    assert folder == common_config.output_path / 'load' / common_config.scenario_name
    folder.mkdir(parents=True)
    (folder / 'stale.csv').write_text('x')
    (folder / 'old_year').mkdir()
    (folder / 'run.log').write_text('keep me')
    for _ in range(2):
        sequencer = LoadSequencer()
        sequencer.build_model(common_config, LoadConfig(**sections['load_config']))
        sequencer.solve_model()
    assert (folder / 'hourly_load_mwh.csv').is_file()
    assert not (folder / 'stale.csv').exists()
    assert not (folder / 'old_year').exists()
    assert (folder / 'run.log').read_text() == 'keep me'
    assert not (common_config.output_path / f'{common_config.scenario_name}_1').exists()
    assert not (common_config.output_path / 'load' / f'{common_config.scenario_name}_1').exists()


def test_clear_previous_results_creates_a_missing_folder(tmp_path):
    """Clearing a folder that does not exist yet creates it."""
    folder = tmp_path / 'load' / 'new_scenario'
    clear_previous_results(folder)
    assert folder.is_dir()


def test_nems_scenario_is_found_from_the_project_scenario(tmp_path, demo_sections):
    """Nems scenario is found from the project scenario."""
    common_config, _ = demo_sections
    # one scenario folder holds a restart file; the other does not
    (tmp_path / 'nems' / 'cb2026' / 'run1').mkdir(parents=True)
    (tmp_path / 'nems' / 'cb2026' / 'run1' / 'restart.rlx').write_bytes(b'x' * 2000)
    config = LoadConfig(nems_outputs_dir=tmp_path / 'nems', base_freeze_year=2026)
    sequencer = LoadSequencer()

    baseline = common_config.model_copy(update={'scenario_name': 'baseline'})
    model = sequencer.build_model(baseline, config)
    assert model.nems_scenario == 'cb2026'
    assert model.restart_file is not None and model.restart_file.name == 'restart.rlx'

    high = common_config.model_copy(update={'scenario_name': 'high_elec_demand'})
    with pytest.raises(FileNotFoundError, match='higheldmd'):
        sequencer.build_model(high, config)
    other = common_config.model_copy(update={'scenario_name': 'something_else'})
    with pytest.raises(ValueError, match='something_else'):
        sequencer.build_model(other, config)


def test_model_accessors_need_a_built_model():
    """Model accessors need a built model."""
    with pytest.raises(RuntimeError, match='build_model'):
        _ = LoadSequencer().model


# ---- run modes
# ------------------------------------------------------------------------------------
def test_all_excludes_load_unless_named():
    """All excludes load unless named."""
    assert ModelType.LOAD not in resolve_models_to_run([ModelType.ALL])
    assert ModelType.LOAD in resolve_models_to_run([ModelType.ALL, ModelType.LOAD])
    assert resolve_models_to_run([ModelType.LOAD]) == [ModelType.LOAD]


def test_standalone_dispatch_runs_the_load_model(demo_sections):
    """Standalone dispatch runs the load model."""
    common_config, sections = demo_sections
    _run_standalone(common_config, sections)
    assert (load_output_folder(common_config) / 'hourly_load_mwh.csv').is_file()
