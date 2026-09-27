"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/14/26

A basic test run for the natural gas model, mirroring tests/electric/test_basic_run.py:
it locks down the objective value, variable count, and constraint count of a solved model
so that unintended changes to the formulation or the input data show up as a failure.

"""

import logging
from pathlib import Path

import pandas as pd
import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationResult, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    NGElectricalDemandPackage,
    NGPricePackage,
)
from src.models.natural_gas.data import load_base_demand
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import NGModel
from src.models.natural_gas.postprocessor import _extract_balance
from src.models.natural_gas.sequencer import UNSERVED_REPORT_TOL_BCF, NGSequencer

verbose = True

# Test configurations with expected outputs, captured from a run of the current code:
# Run Type          Total Cost ($)   Variables   Constraints
# ----------------  ---------------  ----------  -----------
# basic_config      -480902641083.88       1530         1530
# partial_regions   -319146064790.54        342          342
#
# dev notes:
# 1.  unlike the electricity equivalent, these values are NOT merely assumed good.  The config
#     runs the model at full resolution (9 census divisions x the 6 years in summary_years),
#     which is the same case src/models/natural_gas/README.md reports a reference solve for:
#     1530 vars / 1530 constraints.
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
# 6.  basic_config's variable count moved from 1476 to 1530 when the unserved-demand variable,
#     until then created only for a region subset, was created for every run: 9 regions x 6
#     years = 54 variables.  It is zero at this demand level, so the objective and the
#     constraint count did not move, and partial_regions, which always had it, is unchanged.
configs = [
    ('basic_config', -480902641083.88, 1530, 1530),
    ('partial_regions', -319146064790.54, 342, 342),
]

# the same captured objective values, keyed by case name, so the full_run tests below pin to the
# numbers this table already locks in rather than to a second copy of them
expected_costs = {name: cost for name, cost, _, _ in configs}

# the three census divisions the 'partial_regions' case runs; also used by the full_run tests,
# which want the cheapest build that still exercises the real data path
PARTIAL_REGIONS = ['west_south_central', 'mountain', 'pacific']


@pytest.fixture
def partial_config_set() -> tuple[CommonConfig, NGConfig]:
    """A ``(CommonConfig, NGConfig)`` pair filtered down to :data:`PARTIAL_REGIONS`.

    Returns
    -------
    tuple[CommonConfig, NGConfig]
        The test TOML, with the NG config's ``region_filter`` narrowed to three divisions.
    """
    # TODO:  Bring the test data into the test folder when data format changes stabilize
    #        This currently relies on data outside the test environment
    config_path = Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    # note the TOML section is [natural_gas], not [ng_config]
    ng_config = NGConfig(**remainder.pop('natural_gas'))
    ng_config.region_filter = PARTIAL_REGIONS
    return common_config, ng_config


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
        _, status = sequencer.solve_model()

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


def _solve_scaled(scale: float) -> tuple[IterationStatus, NGModel]:
    """Build the test config with demand scaled, and solve it with HiGHS.

    Demand is scaled on the built model's mutable ``demand`` Param, where a coupled model's
    update lands.

    Parameters
    ----------
    scale : float
        Factor applied to demand.

    Returns
    -------
    tuple[IterationStatus, NGModel]
        The solve status and the solved model.
    """
    config_path = Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    ng_config = NGConfig(**remainder.pop('natural_gas'))

    sequencer = NGSequencer()
    model = sequencer.build_model(common_config, ng_config)
    for r, s, y in model.demand:
        model.demand[r, s, y].set_value(value(model.demand[r, s, y]) * scale)
    return sequencer.solve_model(solver_name='highs')[-1], model


class TestUnservedDemand:
    """The unserved-demand penalty, which every run carries.

    The test forces HiGHS, so the result does not depend on which solvers are installed.
    """

    def test_shortfall_solves_and_is_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        """Unmet demand is carried by ``unserved`` and priced at the penalty.

        Twice the base demand is more than can reach New England and Pacific.  The solve is
        usable, the shortfall is logged and written to the balance CSV, and the price in each short
        region is the penalty.
        """
        with caplog.at_level(logging.WARNING, logger='src.models.natural_gas.sequencer'):
            status, model = _solve_scaled(2.0)

        assert status is IterationStatus.USABLE, f'solve failed with status {status}'
        short = {
            (r, y)
            for r in model.region_analyze
            for y in model.year
            if value(model.unserved[r, y]) > UNSERVED_REPORT_TOL_BCF
        }
        assert short == {('new_england', y) for y in model.year} | {('pacific', 2050)}
        assert sum(value(v) for v in model.unserved.values()) == pytest.approx(1288.08, rel=1e-4)

        prices = {(gi.region, gi.year): p for gi, p in model.poll_gas_price().items()}
        for r, y in short:
            assert prices[r, y] == pytest.approx(1000.0, rel=1e-6), f'price in {r} {y}'

        # both slacks reach the balance CSV, row for row
        for row in _extract_balance(model).itertuples():
            assert row.unserved_bcf == pytest.approx(value(model.unserved[row.region, row.year]))
            assert row.slack_demand_bcf == pytest.approx(
                value(model.slack_demand[row.region, row.year])
            )

        assert 'demand unserved in 7 region-year(s)' in caplog.text


class TestSequencerFullRun:
    """``main()`` must not present a failed solve as a successful run.

    ``full_run`` is the entry point the integrator's pool workers call
    (``src/integrator/combine.py::driver``), and its only channel back to the caller is the
    :class:`~src.common.integrated_model_sequencer.IterationResult` it returns.  A failed solve
    leaves no solution loaded, so reading the objective off the model would report a garbage
    number as if it were a price; these tests pin the contract that the failure path reports
    ``ERROR`` with no objective instead.  (``full_run`` writes no result CSVs at all --
    postprocessing is a separate call -- so the old concern about a failed solve leaving output
    indistinguishable from a good run is now structural rather than a check made here.)
    """

    def test_reports_usable_on_success(
        self, partial_config_set: tuple[CommonConfig, NGConfig]
    ) -> None:
        """A good solve returns a fully populated ``IterationResult``."""
        common_config, ng_config = partial_config_set
        sequencer = NGSequencer()
        result = sequencer.full_run(common_config, ng_config)

        assert isinstance(result, IterationResult)
        assert result.model_type is ModelType.NATURAL_GAS
        assert result.status is IterationStatus.USABLE
        assert result.objective_value == pytest.approx(
            expected_costs['partial_regions'], rel=1e-4
        ), f'found {result.objective_value} total cost'
        # C-NGMM sends its solved gas price onward, crosswalked to electricity regions.  Select
        # by type:  the outbound list may carry other package kinds in future
        price_packages = [p for p in result.update_packages if isinstance(p, NGPricePackage)]
        assert len(price_packages) == 1, result.update_packages
        package = price_packages[0]
        assert package.source is ModelType.NATURAL_GAS
        assert list(package.elements.index.names) == NG_PRICE_INDEX
        prices = package.elements[NG_PRICE_VALUE]
        assert set(prices.index.get_level_values('year')) == set(common_config.summary_years)
        assert (prices > 0).all(), 'a zero price means a demand_balance dual was not read'
        regions = set(prices.index.get_level_values('region'))
        # TRE ('1') sits wholly inside west_south_central, one of PARTIAL_REGIONS; ISNE ('7')
        # sits wholly inside new_england, which this run does not solve
        assert '1' in regions
        assert '7' not in regions
        # the integrator logs results with pprint(), so a broken render breaks the run report
        assert 'natural_gas' in result.pprint()

    def test_no_objective_read_on_solve_error(
        self, partial_config_set: tuple[CommonConfig, NGConfig], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ERROR status yields ``objective_value=None`` without touching the objective."""
        common_config, ng_config = partial_config_set
        monkeypatch.setattr(
            NGSequencer,
            'solve_model',
            lambda self, **kwargs: (ModelType.NATURAL_GAS, IterationStatus.ERROR),
        )
        # patch the objective read so its absence is observed, not merely assumed
        touched: list[str] = []
        monkeypatch.setattr(
            NGSequencer, 'get_objective_value', lambda self: touched.append('objective')
        )

        sequencer = NGSequencer()
        result = sequencer.full_run(common_config, ng_config)

        assert result.status is IterationStatus.ERROR
        assert result.objective_value is None
        # no duals to read after a failed solve, so nothing is sent onward
        assert result.update_packages == []
        assert touched == [], f'failed solve still did: {touched}'
        # a failed solve still has to render, since that is how the failure gets reported
        assert 'ERROR' in result.pprint()


class TestInboundDemandPackage:
    """An ``NGElectricalDemandPackage`` sets the electric_power demand the model is built with."""

    def test_build_applies_package_and_gates_growth(
        self, partial_config_set: tuple[CommonConfig, NGConfig]
    ) -> None:
        """Covered entries take the package value; uncovered ones sit at base year, ungrown."""
        common_config, ng_config = partial_config_set
        index = pd.MultiIndex.from_tuples(
            [('west_south_central', 2030)], names=NG_ELEC_DEMAND_INDEX
        )
        package = NGElectricalDemandPackage(
            elements=pd.DataFrame({NG_ELEC_DEMAND_VALUE: [123.4]}, index=index)
        )
        base = load_base_demand(ng_config.input_path)

        model = NGSequencer().build_model(common_config, ng_config, update_packages=[package])

        assert value(model.demand['west_south_central', 'electric_power', 2030]) == pytest.approx(
            123.4
        )
        # not covered by the package:  held flat at the 2025 base, growth gated off
        assert value(model.demand['mountain', 'electric_power', 2030]) == pytest.approx(
            base['mountain']['electric_power']
        )
        # a sector no package supersedes still grows
        assert value(model.demand['mountain', 'industrial', 2030]) != pytest.approx(
            base['mountain']['industrial']
        )
