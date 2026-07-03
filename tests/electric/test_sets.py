from logging import getLogger
from pathlib import Path

import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.utilities import annual_count

logger = getLogger(__name__)


def test_sets(unsolved_model):
    """test to ensure the years set is injested properly"""
    common_config, _, elec_model = unsolved_model

    # check that the years are set correctly
    config_years = common_config.summary_years

    assert common_config.aggregate_years == False, 'aggregate_years should be False in test case'
    assert elec_model.year == config_years, 'years in model do not match config'

    # Assert the years in this non-aggregated base model match the settings
    # Assert the weighted sum of hours is 8760
    assert sum(annual_count(t, elec_model) for t in elec_model.hour) == 8760, (
        'Annualized hours do not add up!'
    )


def test_hours_set():
    """test to ensure the total Load is consistently calculated for different time mappings"""

    def get_tot_load(sw_temporal):
        """sum total load using hours and dayweights"""
        # generate configs
        config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
        common_config, remainder = CommonConfig.from_toml(config_path)
        elec_config = ElecConfig(**remainder.pop('elec_config'))

        # override to simplify testing
        common_config.summary_years = [2025]
        elec_config.region_filter = ['7']
        common_config.temporal_resolution = sw_temporal

        # build the Load dataframe for testing
        model_sets = ModelSets(common_config, elec_config)
        param_data = ParamData(common_config, elec_config, model_sets)

        all_frames = param_data.param_frames
        tot_load1 = pd.merge(
            all_frames['Load'].reset_index(), all_frames['MapHourDay'].reset_index(), on='hour'
        )
        tot_load1 = pd.merge(tot_load1, all_frames['WeightDay'], on='day')
        tot_load1.loc[:, 'tot_load'] = tot_load1['Load'] * tot_load1['WeightDay']
        return sum(tot_load1.tot_load)

    # total load for 4 days, 1 hour per day
    tot_load_d4h1 = get_tot_load('d4h1')
    # total load for 8 days, 12 hours per day
    tot_load_d8h12 = get_tot_load('d8h12')

    # check that sum of load matches regardless of hours per day
    assert tot_load_d4h1 > 0.0, 'no load discovered.  check test setup'
    assert tot_load_d4h1 == pytest.approx(tot_load_d8h12), (
        'some diff in load calculated via different hour mappings'
    )
