"""Tests for ``ModelSets`` and the set/index construction that feeds the electricity model."""

from logging import getLogger
from pathlib import Path

import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.model_sets import ModelSets, _parse_steps
from src.models.electricity.param_data import ParamData
from src.models.electricity.utilities import annual_count

logger = getLogger(__name__)


def test_sets(unsolved_model):
    """Test to ensure the years set is injested properly."""
    common_config, _, elec_model = unsolved_model

    # check that the years are set correctly
    config_years = common_config.summary_years

    assert not common_config.aggregate_years, 'aggregate_years should be False in test case'
    assert elec_model.year == config_years, 'years in model do not match config'

    # Assert the years in this non-aggregated base model match the settings
    # Assert the weighted sum of hours is 8760
    assert sum(annual_count(t, elec_model) for t in elec_model.hour) == 8760, (
        'Annualized hours do not add up!'
    )


def test_hours_set():
    """Test to ensure the total Load is consistently calculated for different time mappings."""

    def get_tot_load(temporal_resolution):
        """Sum total load using hours and dayweights."""
        # generate configs
        config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
        common_config, remainder = CommonConfig.from_toml(config_path)
        elec_config = ElecConfig(**remainder.pop('elec_config'))

        # override to simplify testing
        common_config.summary_years = [2025]
        elec_config.region_filter = ['7']
        common_config.temporal_resolution = temporal_resolution

        # build the Load dataframe for testing
        model_sets = ModelSets(common_config, elec_config)
        param_data = ParamData(common_config, elec_config, model_sets)

        all_frames = param_data.param_frames
        tot_load1 = pd.merge(
            all_frames['elec_load'].reset_index(),
            all_frames['map_hour_day'].reset_index(),
            on='hour',
        )
        tot_load1 = pd.merge(tot_load1, all_frames['weight_day'], on='day')
        tot_load1.loc[:, 'tot_load'] = tot_load1['Load'] * tot_load1['weight_day']
        return sum(tot_load1.tot_load)

    # total load for 4 days, 24 hours per day
    tot_load_d4h24 = get_tot_load('d4h24')
    # total load for 8 days, 12 hours per day
    tot_load_d8h12 = get_tot_load('d8h12')

    # check that sum of load matches regardless of hours per day
    assert tot_load_d4h24 > 0.0, 'no load discovered.  check test setup'
    assert tot_load_d4h24 == pytest.approx(tot_load_d8h12), (
        'some diff in load calculated via different hour mappings'
    )


@pytest.mark.parametrize(
    'raw, expected',
    [
        ('1/2/3', [1, 2, 3]),
        ('1', [1]),
        ('/1/2/', [1, 2]),  # stray leading/trailing slashes
        ('1//2', [1, 2]),  # empty interior segment
        (' 1 / 2 ', [1, 2]),  # padding around the values
        ('2/1/2', [1, 2]),  # unsorted + duplicated
        ('', []),  # tech with no supply curve entries
        ('  ', []),
        ('/', []),
    ],
)
def test_parse_steps(raw: str, expected: list[int]):
    """Slash-separated step lists parse to sorted unique ints, tolerating slack formatting."""
    assert _parse_steps(raw, 'test_tech') == expected


@pytest.mark.parametrize('raw', ['1/x', '1/2.5', 'one'])
def test_parse_steps_rejects_non_integers(raw: str):
    """A non-integer segment is an error, not a silently dropped step."""
    with pytest.raises(ValueError, match='test_tech'):
        _parse_steps(raw, 'test_tech')


def test_tech_steps_matches_supply_curve(config_set):
    """Every tech's declared steps match the steps actually present in SupplyCurve.csv."""
    common_config, elec_config = config_set
    model_sets = ModelSets(common_config, elec_config)

    supply_curve = pd.read_csv(
        Path(PROJECT_ROOT, 'input/electricity/cem_inputs/SupplyCurve.csv'),
        dtype={'tech': str},
    )
    observed = {
        tech: sorted(set(grp['step'])) for tech, grp in supply_curve.groupby('tech', sort=False)
    }

    assert model_sets.tech_steps, 'no tech steps parsed.  check test setup'
    assert model_sets.tech_steps == observed


def test_tech_descriptors_cover_every_tech(config_set):
    """label/abbreviation/color are populated for every tech, with 6-char upper-case codes."""
    common_config, elec_config = config_set
    model_sets = ModelSets(common_config, elec_config)

    techs = set(model_sets.tech)
    for descriptor in (
        model_sets.tech_label,
        model_sets.tech_abbreviation,
        model_sets.tech_color,
    ):
        assert set(descriptor) == techs
        assert all(value for value in descriptor.values()), 'blank descriptor found'

    assert all(
        len(abbr) == 6 and abbr.isupper() for abbr in model_sets.tech_abbreviation.values()
    ), 'abbreviations must be 6 upper-case characters'
    assert len(set(model_sets.tech_abbreviation.values())) == len(techs), (
        'abbreviations must be unique'
    )
