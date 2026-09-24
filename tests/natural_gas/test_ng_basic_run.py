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

import pytest
from pyomo.common.numeric_types import value
from pyomo.opt import SolverFactory

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import NGModel
from src.models.natural_gas.postprocessor import _extract_balance
from src.models.natural_gas.sequencer import UNSERVED_REPORT_TOL_BCF, NGSequencer, main

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


def _solve_scaled(
    scale: float,
    sector: str | None = None,
    years: list[int] | None = None,
    solver: str = 'highs',
) -> tuple[IterationStatus, NGModel]:
    """Build the test config with demand scaled, and solve it with a forced solver.

    Demand is scaled on the built model's mutable ``demand`` Param, where a coupled model's
    update lands.

    Parameters
    ----------
    scale : float
        Factor applied to demand.
    sector : str | None
        Scale only this sector; every sector when ``None``.
    years : list[int] | None
        Replace the config's ``summary_years``; keep them when ``None``.
    solver : str
        Passed to ``solve_model`` as ``solver_name``.

    Returns
    -------
    tuple[IterationStatus, NGModel]
        The solve status and the solved model.
    """
    config_path = Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    if years is not None:
        common_config.summary_years = years
    ng_config = NGConfig(**remainder.pop('natural_gas'))

    sequencer = NGSequencer()
    model = sequencer.build_model(common_config, ng_config)
    for r, s, y in model.demand:
        if sector is None or s == sector:
            model.demand[r, s, y].set_value(value(model.demand[r, s, y]) * scale)
    return sequencer.solve_model(solver_name=solver), model


class TestUnservedDemand:
    """The unserved-demand variable, which every run carries.

    Both cases force HiGHS.  The sequencer takes Gurobi first when ``gurobipy`` is importable,
    and Gurobi solves both cases with or without the variable, so an unforced run would not
    exercise it.
    """

    def test_shortfall_solves_and_is_reported(self, caplog: pytest.LogCaptureFixture) -> None:
        """Twice the demand exceeds what can reach New England and Pacific.

        Without the unserved-demand variable the model is infeasible.  With it the solve is
        usable, the shortfall is reported, and the price in a short region is the balance dual
        as solved, which is the unserved-demand penalty.
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

    def test_long_horizon_demand_cut_solves(self) -> None:
        """A ten-year horizon with the electric power sector cut to 0.537 of its base.

        That is roughly the gas burn the electricity model reports back when coupled.  Without
        the unserved-demand variable HiGHS returns ``unknown`` here, although the model is
        feasible and Gurobi and Ipopt solve it.  The variable is zero in the solution; why its
        presence lets HiGHS solve is not known, so this guards against a HiGHS upgrade or new
        data bringing the failure back.
        """
        status, model = _solve_scaled(0.537, sector='electric_power', years=list(range(2025, 2035)))

        assert status is IterationStatus.USABLE, f'solve failed with status {status}'
        assert sum(value(v) for v in model.unserved.values()) < UNSERVED_REPORT_TOL_BCF


class TestIpoptSolve:
    """``ipopt``, last in the solver probe, gives the same answer as HiGHS."""

    def test_prices_match_highs(self) -> None:
        """Forced ipopt reaches the pinned objective, with HiGHS's prices.

        Prices are the balance duals read through ``poll_gas_price``, so this checks the sign
        and scale of ipopt's duals, not only its objective.
        """
        if not SolverFactory('ipopt').available(exception_flag=False):
            pytest.skip('ipopt is not in this environment')

        status, ipopt_model = _solve_scaled(1.0, solver='ipopt')
        highs_status, highs_model = _solve_scaled(1.0)

        assert status is IterationStatus.USABLE, f'ipopt solve failed with status {status}'
        assert highs_status is IterationStatus.USABLE, f'highs solve failed with {highs_status}'
        # the basic_config objective pinned in `configs`; ipopt lands within about 1e-8 of it
        assert value(ipopt_model.total_cost) == pytest.approx(configs[0][1], rel=1e-6)
        highs_prices = highs_model.poll_gas_price()
        for gi, price in ipopt_model.poll_gas_price().items():
            assert price == pytest.approx(highs_prices[gi], abs=1e-6), f'price in {gi}'


class TestSequencerMain:
    """``main()`` must not present a failed solve as a successful run.

    The failure path used to log an objective read off a failed solve and write five result
    CSVs, so an infeasible configuration produced output indistinguishable from a good run.
    """

    def test_returns_one_on_solve_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An ERROR status exits non-zero rather than falling through."""
        monkeypatch.setattr(
            NGSequencer, 'solve_model', lambda self, **kwargs: IterationStatus.ERROR
        )
        monkeypatch.setattr(NGSequencer, 'full_postprocess', lambda self, **kwargs: None)
        assert main() == 1

    def test_no_results_written_on_solve_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Neither the objective read nor the CSV write happens after a failed solve."""
        monkeypatch.setattr(
            NGSequencer, 'solve_model', lambda self, **kwargs: IterationStatus.ERROR
        )
        touched: list[str] = []
        monkeypatch.setattr(
            NGSequencer, 'full_postprocess', lambda self, **kwargs: touched.append('postprocess')
        )
        # Patch the module-global `value` so a read of total_cost is observable rather than
        # merely assumed absent.
        import src.models.natural_gas.sequencer as seq_mod

        monkeypatch.setattr(seq_mod, 'value', lambda expr: touched.append('objective') or 0.0)

        assert main() == 1
        assert touched == [], f'failed solve still did: {touched}'

    def test_returns_zero_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The happy path is unchanged and still postprocesses."""
        touched: list[str] = []
        monkeypatch.setattr(
            NGSequencer, 'full_postprocess', lambda self, **kwargs: touched.append('postprocess')
        )
        assert main() == 0
        assert touched == ['postprocess']
