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

verbose = True

# Test configurations with expected outputs, captured from a run of the current code:
# Run Type          Total Cost ($)   Variables   Constraints
# ----------------  ---------------  ----------  -----------
# basic_config      -480902641083.88       1476         1530
# partial_regions   -319146064790.54        342          342
#
# dev notes:
# 1.  unlike the electricity equivalent, these values are NOT merely assumed good.  The config
#     runs the model at full resolution (9 census divisions x the 6 years in summary_years),
#     which is the same case src/models/natural_gas/README.md reports a reference solve for:
#     1476 vars / 1530 constraints.
# 2.  a negative total cost is expected, not a defect: the LNG consumer-surplus term is
#     subtracted in the minimisation form.
# 3.  model size scales with common_config.summary_years, the only year knob the model reads,
#     so trimming that list in the config file will move all three numbers.
# 4.  THESE VALUES MOVED when bcf_to_mmbtu became the physical BCF->MMBtu conversion
#     (mmbtu_per_bcf = 1.036e6 in ng_scalars.csv) instead of a 1e3 scaling constant, so that
#     total_cost is denominated in dollars. The move is exactly the ratio of the two factors:
#         -371795726.1059678 x 1036 = -385180372245.78   (basic_config)
#         -180937210.1642696 x 1036 = -187450949730.18   (partial_regions)
#     Every term in the objective carries that factor, so the rescale is linear and changed
#     no quantity, flow or price: production and prices matched to solver tolerance across the
#     change and the variable and constraint counts are unchanged.
# 5.  THESE VALUES MOVED AGAIN when the supply curve's origin was reconciled with the
#     committed-production floor. QBASE_1, the curve's lowest breakpoint, and QMIN were built
#     from unrelated inputs, 0.565 x Q0 against 0.20 x Q0, so production_total reported a
#     quantity 31,510 BCF away from the point the marginal price was read at. Both now derive
#     from the same fraction, which is the condition NGMM Eq 8's identity assumes. 2030
#     production is essentially unchanged, 44,639 BCF against 44,661 before, while prices fall
#     from 2.44-4.73 to 1.84-3.51 $/MMBtu because the old curve was evaluated about 35 percent
#     further along than the production it reported. Counts are unchanged because the
#     producer-cost coefficients moved into mutable Params, which add neither a variable nor a
#     constraint.
configs = [
    ('basic_config', -480902641083.88, 1476, 1530),
    ('partial_regions', -319146064790.54, 342, 342),
]


class TestNGBasicRun:
    """Basic no-frills runs of the natural gas market model."""

    @pytest.mark.parametrize(
        'config_info,expected_total_cost,expected_nvariables,expected_nconstraints',
        configs,
        ids=['Basic Config', 'Partial Regions'],
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
        if config_info == 'basic_config':
            pass  # no adjustments; the config file is the basic case
        elif config_info == 'partial_regions':
            # chop down the regions with a filter
            ng_config.region_filter = ['west_south_central', 'mountain', 'pacific']

        sequencer = NGSequencer()
        ng_model = sequencer.build_model(common_config, ng_config)
        status = sequencer.solve_model()

        # solve_model reports failure by return value rather than raising, so a bad solve would
        # otherwise be read below as a garbage objective instead of an obvious failure
        assert status is IterationStatus.USABLE, f'solve failed with status {status}'

        # for test development/capture:
        if verbose:
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
