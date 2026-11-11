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

from src.common.integrated_model_sequencer import IterationStatus
from src.models.electricity.electricity_model import PowerModel
from tests.electric.mini_model import MiniModelData, add_gas_ct, add_solar_expansion

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

    The binding requirement carries a dual; R1 is untouched.
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

    Costs rise by 160 MWh x $10 in each of the two years; R2 still runs only GasCT.
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
    """BTM 0.25 over ``all`` is one constraint on the two-region sum:  0.25 x 640 = 160 MWh."""
    gas_group.add_rps_requirement('gas', 'all', 'BTM', 2030, 0.25)
    model = _solve(build_mini, rps_required=True)

    assert list(model.rps_btm_lb) == [('gas', 'all', 2030), ('gas', 'all', 2031)]
    for year in gas_group.years:
        assert _generation(model, ['R1', 'R2'], ['Gas'], year) == pytest.approx(160.0)


def test_btm_with_year_aggregation(build_mini, gas_group: MiniModelData) -> None:
    """A year weight of 2 scales both sides alike:  the share is still of one year's load."""
    gas_group.add_rps_requirement('gas', 'R1', 'BTM', 2030, 0.5)
    model = _solve(build_mini, summary_years=[2031], aggregate_start_year=2030, rps_required=True)

    assert pyo.value(model.weight_year[2031]) == pytest.approx(2.0)
    assert _generation(model, ['R1'], ['Gas'], 2031) == pytest.approx(160.0)


def test_dumped_energy_does_not_count(build_mini, clean_group: MiniModelData) -> None:
    """Generation above load can't be dumped to meet a requirement (``demand_balance`` is ==).

    With 200 MW Solar in R1 (no trade, no storage) Solar could make 100 MW in each day hour, but
    only R1's 80 MW load can take it:  160 MWh, a share of 0.5, is the most Solar can count, and
    building more doesn't help.  0.5 solves with Solar exactly at load in the day hours; 0.55 is
    infeasible.
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
    """A zero-share BTM can never bind:  same solution as RPS off."""
    off = _solve(build_mini, capacity_expansion=True)
    assert not hasattr(off, 'rps_btm_lb')

    clean_group.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.0)
    on = _solve(build_mini, **RPS_ON)

    assert len(on.rps_btm_lb) == 0
    assert on.nconstraints() == off.nconstraints()
    assert pyo.value(on.total_cost) == pytest.approx(pyo.value(off.total_cost))


def test_later_btm_supersedes(build_mini, clean_group: MiniModelData) -> None:
    """BTM 0.25 in R2 from 2030 (met by the unforced 100 MW), then 0.5 from 2031 (200 MWh)."""
    clean_group.add_rps_requirement('clean', 'R2', 'BTM', 2030, 0.25)
    clean_group.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.5)
    model = _solve(build_mini, **RPS_ON)

    assert pyo.value(model.rps_min_share['clean', 'R2', 2030]) == pytest.approx(0.25)
    assert pyo.value(model.rps_min_share['clean', 'R2', 2031]) == pytest.approx(0.5)
    assert _generation(model, ['R2'], ['Solar'], 2030) >= 100.0 - 1e-6
    assert _generation(model, ['R2'], ['Solar'], 2031) == pytest.approx(200.0)
