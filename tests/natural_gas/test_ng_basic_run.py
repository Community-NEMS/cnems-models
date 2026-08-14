"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/14/26

A basic test run for the natural gas model, mirroring tests/electric/test_basic_run.py:
it locks down the objective value, variable count, and constraint count of a solved model
so that unintended changes to the formulation or the input data show up as a failure.

"""

from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.sequencer import NGSequencer

# Test configurations with expected outputs, captured from a run of the current code:
# Run Type      Total Cost    Variables   Constraints
# ------------  ------------  ----------  -----------
# BasicConfig   -371795726.11        1476         1530
#
# dev notes:
# 1.  unlike the electricity equivalent, these values are NOT merely assumed good.  The config
#     runs the model at full resolution (9 census divisions x the 6 years in summary_years),
#     which is the same case src/models/natural_gas/README.md reports a reference solve for:
#     1476 vars / 1530 constraints and an objective of -371795726.1060 under HiGHS.  All three
#     agree, so this pins the model against an independently documented run.
# 2.  a negative total cost is expected, not a defect: the LNG consumer-surplus term is
#     subtracted in the minimisation form.
# 3.  model size scales with common_config.summary_years, the only year knob the model reads,
#     so trimming that list in the config file will move all three numbers.
configs = [
    ('BasicConfig', -371795726.11, 1476, 1530),
]


class TestNGBasicRun:
    """Basic no-frills runs of the natural gas market model."""

    @pytest.mark.parametrize(
        'config_info,expected_total_cost,expected_nvariables,expected_nconstraints',
        configs,
        ids=['BasicConfig'],
    )
    def test_basic_run(
        self,
        config_info: str,
        expected_total_cost: float,
        expected_nvariables: int,
        expected_nconstraints: int,
    ) -> None:
        """Build and solve the NG model, comparing results to captured values.

        Parameters
        ----------
        config_info : str
            Name of the case, used to select any per-case config adjustments.
        expected_total_cost : float
            Captured objective value.
        expected_nvariables : int
            Captured variable count.
        expected_nconstraints : int
            Captured constraint count.
        """
        config_path = Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
        common_config, remainder = CommonConfig.from_toml(config_path)

        # introduce the NGConfig.  Note the TOML section is [natural_gas], not [ng_config]
        ng_config = NGConfig(**remainder.pop('natural_gas'))

        # make adjustments based on the config_info
        if config_info == 'BasicConfig':
            pass  # no adjustments; the config file is the basic case

        sequencer = NGSequencer()
        ng_model = sequencer.build_model(common_config, ng_config)
        status = sequencer.solve_model()

        # solve_model reports failure by return value rather than raising, so a bad solve would
        # otherwise be read below as a garbage objective instead of an obvious failure
        assert status is IterationStatus.USABLE, f'solve failed with status {status}'

        # for test development/capture:
        print(value(ng_model.total_cost), ng_model.nvariables(), ng_model.nconstraints())

        # rel is loosened from the electricity test's default 1e-6: this is a convex QP solved by
        # a barrier method, and which solver gets picked varies by environment
        assert value(ng_model.total_cost) == pytest.approx(expected_total_cost, rel=1e-4), (
            f'found {value(ng_model.total_cost)} total cost'
        )
        assert ng_model.nvariables() == expected_nvariables, (
            f'found {ng_model.nvariables()} variables'
        )
        assert ng_model.nconstraints() == expected_nconstraints, (
            f'found {ng_model.nconstraints()} constraints'
        )
