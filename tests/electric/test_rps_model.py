"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests of the RPS constraints (``rps_btm_lb``, ``rps_rll_lb``) and RLL retirement holds on the mini
model, solved, with the expected capacities worked by hand from the mini model's data.
"""

from collections.abc import Iterable

import pyomo.environ as pyo
import pytest

from src.common.integrated_model_sequencer import IterationStatus
from src.models.electricity.electricity_model import PowerModel
from tests.electric.mini_model import MiniModelData, add_solar_expansion

RPS_ON = {'capacity_expansion': True, 'rps_required': True}


def _capacity(model: PowerModel, regions: Iterable[str], techs: Iterable[str], year: int) -> float:
    """Solved total capacity of ``techs`` over ``regions`` in ``year``, summed over steps."""
    regions, techs = set(regions), set(techs)
    return sum(
        pyo.value(var)
        for (r, t, _step, y), var in model.capacity_total.items()
        if r in regions and t in techs and y == year
    )


def _solve(build_mini, **switches: bool) -> PowerModel:
    sequencer, model = build_mini(**switches)
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST
    return model


@pytest.fixture
def clean_group(mini_data: MiniModelData) -> MiniModelData:
    """``add_solar_expansion`` (R2 builds 100 MW Solar unforced), with group ``clean`` = Solar."""
    add_solar_expansion(mini_data)
    mini_data.add_rps_group('clean', techs=['Solar'])
    return mini_data


def test_btm_forces_builds(build_mini, clean_group: MiniModelData) -> None:
    """BTM 0.6 in R2:  Solar >= 0.6 (100 Gas + Solar), so 150 MW instead of the 100 needed.

    The binding requirement carries a dual; R1 is untouched.
    """
    clean_group.add_rps_requirement('clean', 'R2', 'BTM', 2030, 0.6)
    model = _solve(build_mini, **RPS_ON)

    assert list(model.rps_btm_lb) == [('clean', 'R2', 2030), ('clean', 'R2', 2031)]
    assert len(model.rps_rll_lb) == 0
    for year in clean_group.years:
        assert _capacity(model, ['R2'], ['Solar'], year) == pytest.approx(150.0)
        assert _capacity(model, ['R1'], ['Solar'], year) == pytest.approx(50.0)
    assert pyo.value(model.capacity_builds['R2', 'Solar', 1, 2030]) == pytest.approx(150.0)
    key = model.rps_btm_lb['clean', 'R2', 2030]
    assert key in model.dual, 'dual is missing'


def test_btm_all_met_in_aggregate(build_mini, clean_group: MiniModelData) -> None:
    """BTM 0.45 over ``all`` is one constraint on the two-region sum, met in the cheaper region.

    Solar >= 0.45 / 0.55 x 200 MW Gas = 163.6 MW; there are 150 (50 existing in R1 + 100 built in
    R2), so 13.6 MW more is built in R1, where it costs half as much.  R1 alone stays below 0.45.
    """
    clean_group.add_rows(
        'capital_cost', {('R1', 'Solar', 1, y): 500.0 for (y,) in clean_group.every('year')}
    )
    clean_group.add_rps_requirement('clean', 'all', 'BTM', 2030, 0.45)
    model = _solve(build_mini, **RPS_ON)

    assert list(model.rps_btm_lb) == [('clean', 'all', 2030), ('clean', 'all', 2031)]
    needed = 0.45 / 0.55 * 200.0
    for year in clean_group.years:
        solar = _capacity(model, ['R1', 'R2'], ['Solar'], year)
        assert solar == pytest.approx(needed)
        assert solar / (solar + 200.0) == pytest.approx(0.45)
        assert _capacity(model, ['R2'], ['Solar'], year) == pytest.approx(100.0)
        r1_solar = _capacity(model, ['R1'], ['Solar'], year)
        assert r1_solar / (r1_solar + 100.0) < 0.45
    assert pyo.value(model.capacity_builds['R1', 'Solar', 1, 2030]) == pytest.approx(needed - 150)


def test_rll_blocks_retirement(build_mini, mini_data: MiniModelData) -> None:
    """Retirable Gas retires to the 80 MW load (saving FOM), except where an RLL holds it.

    RLL 0.95 on Gas in R1:  existing share 1.0, so the floor is 0.95 x 100 = 95 MW.
    """
    mini_data.set_retirable('Gas', 1)
    mini_data.add_rps_group('firm', techs=['Gas'])
    mini_data.add_rps_requirement('firm', 'R1', 'RLL', 2030, 0.95)
    model = _solve(build_mini, **RPS_ON)

    assert list(model.rps_rll_lb) == [('firm', 'R1', 2030), ('firm', 'R1', 2031)]
    assert pyo.value(model.rps_rll_floor['firm', 'R1', 2030]) == pytest.approx(95.0)
    for year in mini_data.years:
        assert _capacity(model, ['R1'], ['Gas'], year) == pytest.approx(95.0)
        assert _capacity(model, ['R2'], ['Gas'], year) == pytest.approx(80.0)


def test_rll_hold_then_floor(build_mini, clean_group: MiniModelData) -> None:
    """Below its minimum an RLL holds retirements; once the existing share meets it, a floor.

    Retirable Gas would retire to R1's 80 MW load.  RLL 0.8 on Gas in R1:  in 2030 the existing
    share is 100 / 150 (R1's 50 MW Solar), below 0.8, so no Gas may retire; in 2031 R1 has no
    existing Solar, the share is 1.0, and the floor is 0.8 x 100 = 80 MW.
    """
    clean_group.set_retirable('Gas', 1)
    clean_group.drop_rows('available_capacity', [('R1', 'Solar', 1, 2031)])
    clean_group.add_rps_group('firm', techs=['Gas'])
    clean_group.add_rps_requirement('firm', 'R1', 'RLL', 2030, 0.8)
    model = _solve(build_mini, **RPS_ON)

    # verify that the index is removed from the retirement index, the retirement-disabling mechanism.
    assert ('R1', 'Gas', 1, 2030) not in model.capacity_retirements
    # verify it *is* in the retirement capable index when above floor
    assert ('R1', 'Gas', 1, 2031) in model.capacity_retirements
    assert list(model.rps_rll_lb) == [('firm', 'R1', 2031)]
    assert pyo.value(model.rps_rll_floor['firm', 'R1', 2031]) == pytest.approx(80.0)
    assert _capacity(model, ['R1'], ['Gas'], 2030) == pytest.approx(100.0)
    assert _capacity(model, ['R1'], ['Gas'], 2031) == pytest.approx(80.0)


def test_inert_requirements_write_no_constraint(build_mini, clean_group: MiniModelData) -> None:
    """An RLL hold on a group that cannot retire, and a zero-share BTM:  same solution as off.

    R2 has no existing Solar, so its RLL is a hold, but Solar is not retirable; the zero BTM in R1
    can never bind.
    """
    off = _solve(build_mini, capacity_expansion=True)
    assert not hasattr(off, 'rps_btm_lb')

    clean_group.add_rps_requirement('clean', 'R2', 'RLL', 2030, 0.1)
    clean_group.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.0)
    on = _solve(build_mini, **RPS_ON)

    assert len(on.rps_btm_lb) == len(on.rps_rll_lb) == 0
    assert on.nconstraints() == off.nconstraints()
    assert pyo.value(on.total_cost) == pytest.approx(pyo.value(off.total_cost))


def test_btm_supersedes_rll(build_mini, clean_group: MiniModelData) -> None:
    """RLL 0.25 in R1 from 2030, then BTM 0.4 from 2031:  one requirement per year.

    The RLL floor in 2030 is 0.25 x 150 = 37.5 MW; in 2031 Solar >= 0.4 (100 Gas + Solar), so R1
    has 66.7 MW.  (It is built in 2030, the same capital cost a year sooner, saving Gas fuel.)
    """
    clean_group.add_rps_requirement('clean', 'R1', 'RLL', 2030, 0.25)
    clean_group.add_rps_requirement('clean', 'R1', 'BTM', 2031, 0.4)
    model = _solve(build_mini, **RPS_ON)

    assert list(model.rps_rll_lb) == [('clean', 'R1', 2030)]
    assert list(model.rps_btm_lb) == [('clean', 'R1', 2031)]
    assert pyo.value(model.rps_rll_floor['clean', 'R1', 2030]) == pytest.approx(37.5)
    assert _capacity(model, ['R1'], ['Solar'], 2031) == pytest.approx(0.4 / 0.6 * 100.0)
