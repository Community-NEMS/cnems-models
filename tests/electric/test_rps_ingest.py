"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests for the RPS config switches and the raw (unchecked) reads of the RPS group / requirement
tables through the declared property and policy sources.
"""

import shutil
from pathlib import Path

import pytest

from definitions import PROJECT_ROOT
from src.models.electricity.data_ingestor import load_policy_data, load_property_data
from src.models.electricity.elec_config import ElecConfig
from tests.electric.mini_model import mini_configs

SHIPPED_PROPERTIES = PROJECT_ROOT / 'input/electricity/properties'


@pytest.fixture
def properties_dir(tmp_path: Path) -> Path:
    """A copy of the shipped properties folder, without its rps_group.csv."""
    properties = Path(shutil.copytree(SHIPPED_PROPERTIES, tmp_path / 'properties'))
    (properties / 'rps_group.csv').unlink(missing_ok=True)
    return properties


# ---------------------------------------------------------------- config


def test_rps_without_expansion_allowed(tmp_path: Path) -> None:
    """A generation requirement can be met by redispatch, so expansion is not required."""
    _, elec_config = mini_configs(tmp_path, [2030], rps_required=True)
    assert elec_config.rps_required and not elec_config.capacity_expansion


def test_rps_needs_policy_folder(tmp_path: Path) -> None:
    """RPS without a policy folder fails config validation."""
    _, base = mini_configs(tmp_path, [2030])
    base.policy_path.rmdir()
    with pytest.raises(ValueError, match='requires a policy folder'):
        ElecConfig(**(base.model_dump() | {'rps_required': True}))


# ---------------------------------------------------------------- groups table


def test_rps_group_absent(properties_dir: Path) -> None:
    """The groups table is optional:  absent, it loads as an empty member list."""
    assert load_property_data(properties_dir)['rps_group'] == {'group_id': []}


def test_rps_group_rows(properties_dir: Path) -> None:
    """Each row is kept whole, in order, with blank cells as '' and notes ignored."""
    (properties_dir / 'rps_group.csv').write_text(
        'group_id,tech,notes\nclean,13,offshore wind\nclean,14,\nwind,,blank tech\n'
    )
    assert load_property_data(properties_dir)['rps_group'] == {
        'group_id': [('clean', '13'), ('clean', '14'), ('wind', '')]
    }


# ---------------------------------------------------------------- requirements table


def test_rps_requirement_absent(tmp_path: Path) -> None:
    """The requirements table is optional:  absent, it loads as an empty dict."""
    assert load_policy_data(tmp_path) == {'rps_requirement': {}}


def test_rps_requirement_rows(tmp_path: Path) -> None:
    """Rows key on (group, region, type, first_year) -> share; first_year is an int.

    No region filter applies, so the ``all`` keyword comes through.
    """
    (tmp_path / 'rps_requirement.csv').write_text(
        'group_id,region,requirement_type,first_year,min_share_frac,notes\n'
        'clean,7,BTM,2030,0.25,a note\n'
        'clean,all,RLL,2031,0.5,\n'
    )
    assert load_policy_data(tmp_path)['rps_requirement'] == {
        ('clean', '7', 'BTM', 2030): 0.25,
        ('clean', 'all', 'RLL', 2031): 0.5,
    }
