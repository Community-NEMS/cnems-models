"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/26/26

Operating reserve accounting on the mini model (``tests/electric/mini_model.py``).

Wind generation and solar capacity set part of the regulation and flex requirements.  Their
contribution to the requirement is independent of whether those techs can also supply reserves
to meet it.

Reserve suppliers are drawn from the full capacity index, so capacity that is buildable but not
yet existing can supply reserves, and a reserve requirement can drive new builds.
"""

import pyomo.environ as pyo
import pytest

from src.common.integrated_model_sequencer import IterationStatus
from src.models.electricity.elec_config import ReserveType
from tests.electric.mini_model import MiniModelData

WIND_MW = 20.0
WIND_CF = 0.5
SOLAR_MW = 50.0
LOAD_MW = 80.0


def add_vre_and_reserves(data: MiniModelData, vre_limit: float = 0.0) -> MiniModelData:
    """Add Wind (20 MW, CF 0.5 every hour) and Solar (50 MW, CF 0.5 by day), with Gas reserves.

    Gas can supply every reserve type.  Wind and Solar get ``vre_limit`` for every type.

    Parameters
    ----------
    data : MiniModelData
        the base mini data, edited in place.
    vre_limit : float
        reserve tech limit for Wind and Solar, for every reserve type.

    Returns
    -------
    MiniModelData
        ``data``, for chaining.
    """
    data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    data.add_capacity('Wind', WIND_MW)
    data.add_operating_costs('Wind', generation_cost=0.0, fom_cost=5.0)
    data.add_capacity('Solar', SOLAR_MW)
    data.add_operating_costs('Solar', generation_cost=0.0, fom_cost=5.0)
    data.add_rows(
        'capacity_factor_hourly',
        {(r, 'Wind', 1, hr): WIND_CF for r, hr in data.every('region', 'hour')}
        | {
            (r, 'Solar', 1, hr): 0.5 if hr % 2 == 0 else 0.0
            for r, hr in data.every('region', 'hour')
        },
    )
    gas_limits = {'spinning': 0.5, 'regulation': 0.25, 'flex': 1.0}
    data.add_rows('reserve_tech_limit', {(rt, 'Gas'): lim for rt, lim in gas_limits.items()})
    data.add_rows('reserve_cost', {(rt, 'Gas'): 1.0 for rt in gas_limits})
    for tech in ('Wind', 'Solar'):
        data.add_rows('reserve_tech_limit', {(rt, tech): vre_limit for rt in gas_limits})
        data.add_rows('reserve_cost', {(rt, tech): 1.0 for rt in gas_limits})
    return data


@pytest.mark.parametrize('vre_limit', [0.0, 0.5], ids=['vre_not_supplier', 'vre_supplier'])
def test_vre_contributor_sets(build_mini, mini_data, vre_limit):
    """Every (r, y, h) lists Wind and Solar as contributors, whether or not they supply reserves."""
    add_vre_and_reserves(mini_data, vre_limit)
    _, model = build_mini(spinning_reserve_required=True)

    for r, y, hr in model.elec_load.index_set():
        assert set(model.wind_generation[r, y, hr]) == {('Wind', 1)}
        assert set(model.solar_generation[r, y, hr]) == {('Solar', 1)}

    vre_suppliers = {idx for idx in model.reserves_procurement_index if idx[2] in ('Wind', 'Solar')}
    assert bool(vre_suppliers) == (vre_limit > 0)


def test_vre_requirements_procured(build_mini, mini_data):
    """With costly reserves, Gas procures exactly the hand-computed requirement in every hour.

    Per hour (unit weights):  spinning 0.03 x 80 = 2.4;  regulation 0.01 x 80 + 0.005 x 10 wind
    + 0.003 x 50 solar = 1.0;  flex 0.1 x 10 wind + 0.04 x 50 solar = 3.0.  The solar terms
    count capacity, so they apply at night too.
    """
    add_vre_and_reserves(mini_data)
    sequencer, model = build_mini(spinning_reserve_required=True)
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST

    wind_gen = WIND_MW * WIND_CF
    expected = {
        ReserveType.SPINNING: 0.03 * LOAD_MW,
        ReserveType.REGULATION: 0.01 * LOAD_MW + 0.005 * wind_gen + 0.003 * SOLAR_MW,
        ReserveType.FLEX: 0.1 * wind_gen + 0.04 * SOLAR_MW,
    }
    for r, y, hr in model.elec_load.index_set():
        assert pyo.value(model.generation_total[r, 'Wind', 1, y, hr]) == pytest.approx(wind_gen)
        for res_type, requirement in expected.items():
            procured = sum(
                pyo.value(model.reserves_procurement[r, res_type, tech, step, y, hr])
                for (tech, step) in model.eligible_reserves[r, res_type, y, hr]
            )
            assert procured == pytest.approx(requirement), (r, y, hr, res_type)


PEAKER_LIMIT = 1.0


def add_buildable_peaker(data: MiniModelData) -> MiniModelData:
    """Add a buildable Peaker, with no existing capacity, as the only reserve supplier.

    Peaker is buildable in both regions and years at $1000/MW, generates at $50/MWh (above
    Gas), and can supply ``PEAKER_LIMIT`` of its capacity for every reserve type.

    Parameters
    ----------
    data : MiniModelData
        the base mini data, edited in place.

    Returns
    -------
    MiniModelData
        ``data``, for chaining.
    """
    data.add_tech('Peaker', flags=['is_dispatchable', 'is_generator'], buildable=[1])
    data.add_operating_costs('Peaker', generation_cost=50.0, fom_cost=1.0)
    data.add_rows(
        'capital_cost', {(r, 'Peaker', 1, y): 1000.0 for r, y in data.every('region', 'year')}
    )
    res_types = [rt.value for rt in ReserveType]
    data.add_rows('reserve_tech_limit', {(rt, 'Peaker'): PEAKER_LIMIT for rt in res_types})
    data.add_rows('reserve_cost', {(rt, 'Peaker'): 1.0 for rt in res_types})
    return data


def test_buildable_capacity_supplies_reserves(build_mini, mini_data):
    """A buildable location with no existing capacity gets a reserve variable for every type."""
    add_buildable_peaker(mini_data)
    _, model = build_mini(spinning_reserve_required=True, capacity_expansion=True)

    for r, y, hr in model.elec_load.index_set():
        assert (r, 'Peaker', 1, y) not in model.available_capacity
        for res_type in ReserveType:
            assert (r, res_type, 'Peaker', 1, y, hr) in model.reserves_procurement


def test_reserve_requirement_drives_builds(build_mini, mini_data):
    """With Peaker the only supplier, each region builds exactly the capacity reserves need.

    Per hour (unit weights, no VRE):  spinning 0.03 x 80 = 2.4 and regulation 0.01 x 80 = 0.8,
    flex 0.  Both share Peaker's capacity, which does not generate (Gas is cheaper), so each
    region builds 3.2 MW in the first year and nothing after.
    """
    add_buildable_peaker(mini_data)
    sequencer, model = build_mini(spinning_reserve_required=True, capacity_expansion=True)
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST

    first, second = mini_data.years
    needed = (0.03 + 0.01) * LOAD_MW
    for r in mini_data.regions:
        assert pyo.value(model.capacity_builds[r, 'Peaker', 1, first]) == pytest.approx(needed)
        assert pyo.value(model.capacity_builds[r, 'Peaker', 1, second]) == pytest.approx(0.0)
    assert sum(pyo.value(v) for v in model.unmet_load.values()) == pytest.approx(0.0)
