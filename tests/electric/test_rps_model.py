"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests of the RPS Build To Minima constraint (``rps_btm_lb``) on the mini model, solved:  the
group's annual in-region generation must be at least the minimum share of the region key's annual
load.  Expected generation is worked by hand from the mini model's data (unit time weights, four
hours a year:  two night, two day).
"""

from collections.abc import Iterable

import pyomo.environ as pyo
import pytest

from cnems.common.integrated_model_sequencer import IterationStatus
from cnems.models.electricity.electricity_model import PowerModel
from tests.electric.mini_model import (
    MiniModelData,
    add_battery,
    add_gas_ct,
    add_lines,
    add_solar_expansion,
)

RPS_ON = {'capacity_expansion': True, 'rps_required': True}


def _generation(
    model: PowerModel, regions: Iterable[str], techs: Iterable[str], year: int
) -> float:
    """Solved annual (day-weighted) generation of ``techs`` over ``regions`` in ``year``."""
    regions, techs = set(regions), set(techs)
    return sum(
        pyo.value(model.weight_day[model.map_hour_day[hr]] * var)
        for (r, t, _step, y, hr), var in model.generation_total.items()
        if r in regions and t in techs and y == year
    )


def _solve(build_mini, **switches) -> PowerModel:
    """Build and solve the mini model, which must solve to optimality."""
    sequencer, model = build_mini(**switches)
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST
    return model


@pytest.fixture
def clean_group(mini_data: MiniModelData) -> MiniModelData:
    """``add_solar_expansion`` (R2 builds 100 MW Solar unforced), with group ``clean`` = Solar.

    Annual load:  R1 320 MWh (80 MW x 4 h), R2 400 MWh (50 / 150 MW night / day).  Solar yields
    1 MWh per MW a year (CF 0.5 in the two day hours).
    """
    add_solar_expansion(mini_data)
    mini_data.add_rps_group('clean', techs=['Solar'])
    return mini_data


@pytest.fixture
def gas_group(mini_data: MiniModelData) -> MiniModelData:
    """``add_gas_ct``:  GasCT ($20/MWh) serves all load unforced; group ``gas`` = Gas ($30/MWh).

    Annual load is 320 MWh in each region; there is no Solar.
    """
    add_gas_ct(mini_data)
    mini_data.add_rps_group('gas', techs=['Gas'])
    return mini_data


def test_btm_forces_builds(build_mini, clean_group: MiniModelData) -> None:
    """BTM 0.5 in R2:  Solar >= 0.5 x 400 = 200 MWh, so 200 MW instead of the 100 unforced.

    The objective of this test is to show that a binding requirement forces capacity builds:  R2
    builds 200 MW of Solar where it would build 100 MW unforced, R1 is untouched, and the binding
    constraint carries a dual.
    """
    clean_group.add_rps_requirement('clean', 'R2', 'BTM', 2030, 0.5)
    model = _solve(build_mini, **RPS_ON)

    assert list(model.rps_btm_lb) == [('clean', 'R2', 2030), ('clean', 'R2', 2031)]
    for year in clean_group.years:
        assert _generation(model, ['R2'], ['Solar'], year) == pytest.approx(200.0)
        assert _generation(model, ['R1'], ['Solar'], year) == pytest.approx(50.0)
    assert pyo.value(model.capacity_builds['R2', 'Solar', 1, 2030]) == pytest.approx(200.0)
    assert model.rps_btm_lb['clean', 'R2', 2030] in model.dual, 'dual is missing'


def test_btm_redispatch_without_expansion(build_mini, gas_group: MiniModelData) -> None:
    """With the fleet fixed, BTM 0.5 on Gas in R1 shifts 160 MWh from GasCT to Gas.

    The objective of this test is to show that, with capacity expansion off, a requirement is met
    by redispatch alone:  R1 runs its existing Gas for half of its 320 MWh load in place of the
    cheaper GasCT, costs rise by 160 MWh x $10 in each of the two years, and R2 still runs only
    GasCT.
    """
    off = _solve(build_mini)
    gas_group.add_rps_requirement('gas', 'R1', 'BTM', 2030, 0.5)
    on = _solve(build_mini, rps_required=True)

    assert not hasattr(on, 'capacity_builds')
    for year in gas_group.years:
        assert _generation(off, ['R1'], ['Gas'], year) == pytest.approx(0.0)
        assert _generation(on, ['R1'], ['Gas'], year) == pytest.approx(160.0)
        assert _generation(on, ['R1'], ['GasCT'], year) == pytest.approx(160.0)
        assert _generation(on, ['R2'], ['Gas'], year) == pytest.approx(0.0)
    assert pyo.value(on.total_cost) == pytest.approx(pyo.value(off.total_cost) + 2 * 1600.0)


def test_btm_all_is_one_constraint(build_mini, gas_group: MiniModelData) -> None:
    """BTM 0.25 over ``all`` is one constraint on the two-region sum:  0.25 x 640 = 160 MWh.

    The objective of this test is to show that a requirement on the region key ``all`` writes one
    constraint per year on the regions' combined generation and load, not one per region.
    """
    gas_group.add_rps_requirement('gas', 'all', 'BTM', 2030, 0.25)
    model = _solve(build_mini, rps_required=True)

    assert list(model.rps_btm_lb) == [('gas', 'all', 2030), ('gas', 'all', 2031)]
    for year in gas_group.years:
        assert _generation(model, ['R1', 'R2'], ['Gas'], year) == pytest.approx(160.0)


def test_btm_with_year_aggregation(build_mini, gas_group: MiniModelData) -> None:
    """A year weight of 2 scales both sides alike:  the share is still of one year's load.

    The objective of this test is to show that year aggregation leaves a requirement unchanged:
    with 2031 standing for two years, R1 still generates 0.5 x 320 = 160 MWh of Gas in 2031.
    """
    gas_group.add_rps_requirement('gas', 'R1', 'BTM', 2030, 0.5)
    model = _solve(build_mini, summary_years=[2031], aggregate_start_year=2030, rps_required=True)

    assert pyo.value(model.weight_year[2031]) == pytest.approx(2.0)
    assert _generation(model, ['R1'], ['Gas'], 2031) == pytest.approx(160.0)


def test_unmeetable_btm_is_infeasible(build_mini, clean_group: MiniModelData) -> None:
    """A requirement above what the load can take makes the solve fail.

    The objective of this test is to show that a requirement the model cannot meet ends in an
    infeasible solve (``IterationStatus.ERROR``), not a solution that counts surplus generation.
    With 200 MW Solar in R1 (no trade, no storage) Solar could make 100 MW in each day hour, but
    ``demand_balance`` is an equality, so only R1's 80 MW load can take it:  160 MWh, a share of
    0.5, is the most Solar can count, and building more doesn't help.  A share of 0.5, at that
    limit, solves with Solar exactly at load in the day hours; 0.55, just past it, is infeasible.
    """
    clean_group.add_capacity('Solar', 200.0, regions=['R1'])
    clean_group.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.5)
    model = _solve(build_mini, **RPS_ON)
    for year in clean_group.years:
        assert _generation(model, ['R1'], ['Solar'], year) == pytest.approx(160.0)

    clean_group.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.55)
    sequencer, _ = build_mini(**RPS_ON)
    _, status = sequencer.solve_model()
    assert status is IterationStatus.ERROR


def test_zero_btm_writes_no_constraint(build_mini, clean_group: MiniModelData) -> None:
    """A zero-share BTM can never bind, so it writes no constraint.

    The objective of this test is to show that a model with a zero-share requirement is the same
    as one with RPS off:  the same constraint count and cost.
    """
    off = _solve(build_mini, capacity_expansion=True)
    assert not hasattr(off, 'rps_btm_lb')

    clean_group.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.0)
    on = _solve(build_mini, **RPS_ON)

    assert len(on.rps_btm_lb) == 0
    assert on.nconstraints() == off.nconstraints()
    assert pyo.value(on.total_cost) == pytest.approx(pyo.value(off.total_cost))


def test_later_btm_supersedes(build_mini, clean_group: MiniModelData) -> None:
    """BTM 0.25 in R2 from 2030 (met by the unforced 100 MW), then 0.5 from 2031 (200 MWh).

    The objective of this test is to show that a later requirement for the same group and region
    replaces the earlier one from its first year:  2030 holds the 0.25 share and 2031 the 0.5.
    """
    clean_group.add_rps_requirement('clean', 'R2', 'BTM', 2030, 0.25)
    clean_group.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.5)
    model = _solve(build_mini, **RPS_ON)

    assert pyo.value(model.rps_min_share['clean', 'R2', 2030]) == pytest.approx(0.25)
    assert pyo.value(model.rps_min_share['clean', 'R2', 2031]) == pytest.approx(0.5)
    assert _generation(model, ['R2'], ['Solar'], 2030) >= 100.0 - 1e-6
    assert _generation(model, ['R2'], ['Solar'], 2031) == pytest.approx(200.0)


# ------------------------------------------------------------ losses debited from the credit


def _buildable_solar(data: MiniModelData) -> MiniModelData:
    """Solar, none existing, buildable in both regions at $1000/MW:  CF 0.5 by day, 0 at night.

    It yields 1 MWh per MW a year.  Group ``clean`` = Solar.
    """
    data.set_buildable('Solar', 1)
    data.add_operating_costs('Solar', generation_cost=0.0, fom_cost=5.0)
    data.add_rows(
        'capacity_factor_hourly',
        {
            (r, 'Solar', 1, hr): 0.5 if hr % 2 == 0 else 0.0
            for r, hr in data.every('region', 'hour')
        },
    )
    data.add_rows(
        'capital_cost', {(r, 'Solar', 1, y): 1000.0 for r, y in data.every('region', 'year')}
    )
    return data.add_rps_group('clean', techs=['Solar'])


def _clean_mix(
    data: MiniModelData, share: float, regions: Iterable[str] = ('R1', 'R2')
) -> MiniModelData:
    """``_buildable_solar`` plus Wind (CF 0.4 all hours, $3000/MW) in ``clean``; BTM in ``regions``.

    Each region's 80 MW load is 320 MWh a year.  With nothing to store or export, each hour can
    count at most its 80 MW load, so the night half of a requirement above 160 MWh needs Wind.
    """
    _buildable_solar(data)
    data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    data.set_buildable('Wind', 1)
    data.add_operating_costs('Wind', generation_cost=0.0, fom_cost=5.0)
    data.add_rows(
        'capacity_factor_hourly',
        {(r, 'Wind', 1, hr): 0.4 for r, hr in data.every('region', 'hour')},
    )
    data.add_rows(
        'capital_cost', {(r, 'Wind', 1, y): 3000.0 for r, y in data.every('region', 'year')}
    )
    data.add_rps_group('clean', techs=['Wind'])
    for region in regions:
        data.add_rps_requirement('clean', region, 'BTM', 2030, share)
    return data


def _capacity(model: PowerModel, region: str, tech: str, year: int) -> float:
    """Solved ``capacity_total`` of ``tech`` (step 1) in ``region`` and ``year``."""
    return pyo.value(model.capacity_total[region, tech, 1, year])


@pytest.mark.parametrize('r2_bound', [True, False], ids=['both_bound', 'r2_pass_through'])
def test_trade_cycling_earns_no_credit(
    build_mini, mini_data: MiniModelData, r2_bound: bool
) -> None:
    """Trading back and forth loses 2% a leg, but every leg touching the key is debited.

    The objective of this test is to show that sending surplus Solar back and forth on the lines,
    so that it is lost as transmission loss, earns R1 no credit:  R1 builds exactly what it would
    with no lines at all, and nothing is traded.

    BTM 0.7 in R1 needs 224 MWh.  The day hours can count only the 80 MW load (160 MWh), so
    64 MWh must come from Wind at night:  80 MW (0.4 x 2 h x 80).  By day that Wind makes 32 MW,
    leaving 48 MW for Solar:  96 MW.  ``both_bound``:  R2 has the same BTM, so its own import
    debit stops it cycling.  ``r2_pass_through``:  R2 has no BTM and no load, so it can only send
    R1's surplus back; the loss on R1's export leg is debited to R1.
    """
    _clean_mix(mini_data, 0.7, regions=['R1', 'R2'] if r2_bound else ['R1'])
    if not r2_bound:
        mini_data.set_load('R2', dict.fromkeys(mini_data.hours, 0.0))
    add_lines(mini_data, 400.0)
    model = _solve(build_mini, regional_exchange=True, **RPS_ON)

    assert _capacity(model, 'R1', 'Solar', 2030) == pytest.approx(96.0)
    assert _capacity(model, 'R1', 'Wind', 2030) == pytest.approx(80.0)
    traded = sum(pyo.value(v) for v in model.trade_interregional.values())
    assert traded == pytest.approx(0.0, abs=1e-6)


def test_storage_cycling_earns_no_credit(build_mini, mini_data: MiniModelData) -> None:
    """A battery with spare power could lose surplus by charging and discharging at once.

    The objective of this test is to show that charging and discharging storage in the same time
    segment, so that surplus Solar is lost as storage loss, earns no credit, and so never happens
    in the solution.  The 60 MW, 15-minute Battery has little energy to shift but plenty of power
    to cycle; its losses are debited, so cycling earns nothing and costs a little.
    """
    _clean_mix(mini_data, 0.7)
    add_battery(mini_data, 60.0, duration=0.25)
    model = _solve(build_mini, **RPS_ON)

    cycled = sum(
        min(pyo.value(model.storage_inflow[k]), pyo.value(model.storage_outflow[k]))
        for k in model.storage_inflow
    )
    assert cycled == pytest.approx(0.0, abs=1e-6)


def test_storage_losses_are_debited(build_mini, mini_data: MiniModelData) -> None:
    """Solar stored for the night earns credit net of the battery's loss.

    The objective of this test is to show that the debit is exact:  Solar must generate the
    requirement plus the battery's loss, and no more.

    BTM 0.75 in R1 needs 240 MWh.  By day Solar can count only the 80 MW load (160 MWh); the rest
    must charge the 85%-efficient Battery, C MWh a year, for the night.  The credit is
    160 + C - 0.15 C = 240, so C = 80 / 0.85 and Solar generates 160 + C:  0.15 C above the
    requirement.
    """
    _buildable_solar(mini_data)
    add_battery(mini_data, 100.0)
    mini_data.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.75)
    model = _solve(build_mini, **RPS_ON)

    charge = 80.0 / 0.85
    for year in mini_data.years:
        charged = sum(
            pyo.value(model.weight_day[model.map_hour_day[hr]] * var)
            for (r, _t, _s, y, hr), var in model.storage_inflow.items()
            if r == 'R1' and y == year
        )
        assert charged == pytest.approx(charge)
        assert _generation(model, ['R1'], ['Solar'], year) == pytest.approx(160.0 + charge)
