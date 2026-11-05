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
from tests.electric.mini_model import mini_configs

SHIPPED_PROPERTIES = PROJECT_ROOT / 'input/electricity/properties'


@pytest.fixture
def properties_dir(tmp_path: Path) -> Path:
    """A copy of the shipped properties folder, which has no rps_group.csv."""
    return Path(shutil.copytree(SHIPPED_PROPERTIES, tmp_path / 'properties'))


# ---------------------------------------------------------------- config


def test_rps_off_by_default(tmp_path: Path) -> None:
    """RPS is opt-in, and needs no policy folder without it."""
    _, elec_config = mini_configs(tmp_path, [2030])
    assert elec_config.rps_required is False


def test_policy_path_beside_input_path(tmp_path: Path) -> None:
    """The policy folder is a sibling of the parameters (``input_path``) folder."""
    (tmp_path / 'policy').mkdir()
    _, elec_config = mini_configs(tmp_path, [2030], capacity_expansion=True, rps_required=True)
    assert elec_config.policy_path == elec_config.input_path.parent / 'policy'
    assert elec_config.policy_path == tmp_path / 'policy'


@pytest.mark.parametrize(
    ('make_policy', 'switches', 'match'),
    [
        (True, {'rps_required': True}, 'requires capacity_expansion'),
        (False, {'rps_required': True, 'capacity_expansion': True}, 'requires a policy folder'),
    ],
    ids=['no_expansion', 'no_policy_folder'],
)
def test_rps_config_errors(tmp_path: Path, make_policy: bool, switches: dict, match: str) -> None:
    """Bad RPS switch combinations fail config validation."""
    if make_policy:
        (tmp_path / 'policy').mkdir()
    with pytest.raises(ValueError, match=match):
        mini_configs(tmp_path, [2030], **switches)


# ---------------------------------------------------------------- groups table


def test_rps_group_absent(properties_dir: Path) -> None:
    """The groups table is optional:  absent, it loads as an empty member list."""
    assert load_property_data(properties_dir)['rps_group'] == {'group_id': []}


def test_rps_group_rows(properties_dir: Path) -> None:
    """Each row is kept whole, in order, with blank cells as ''; checks come later."""
    (properties_dir / 'rps_group.csv').write_text(
        'group_id,tech,tech_group\nclean,13,\nclean,,is_vre\nwind,13,\n'
    )
    assert load_property_data(properties_dir)['rps_group'] == {
        'group_id': [('clean', '13', ''), ('clean', '', 'is_vre'), ('wind', '13', '')]
    }


def test_required_property_still_required(properties_dir: Path) -> None:
    """Making rps_group optional leaves the other property files required."""
    (properties_dir / 'tech.csv').unlink()
    with pytest.raises(FileNotFoundError):
        load_property_data(properties_dir)


# ---------------------------------------------------------------- requirements table


def test_rps_requirement_absent(tmp_path: Path) -> None:
    """The requirements table is optional:  absent, it loads as an empty dict."""
    assert load_policy_data(tmp_path) == {'rps_requirement': {}}


def test_rps_requirement_rows(tmp_path: Path) -> None:
    """Rows key on (group, region, type, first_year) -> share; first_year is an int.

    No region filter applies, so the ``all`` keyword comes through.
    """
    (tmp_path / 'rps_requirement.csv').write_text(
        'group_id,region,requirement_type,first_year,min_share_frac\n'
        'clean,7,BTM,2030,0.25\n'
        'clean,all,RLL,2031,0.5\n'
    )
    assert load_policy_data(tmp_path)['rps_requirement'] == {
        ('clean', '7', 'BTM', 2030): 0.25,
        ('clean', 'all', 'RLL', 2031): 0.5,
    }
