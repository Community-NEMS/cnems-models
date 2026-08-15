"""Contract tests for the gas <-> electricity Gauss-Seidel coupling.

These use a minimal stand-in electricity model rather than a real one, so they run anywhere and
test the coupling logic itself. The important cases are the ones that would otherwise fail
silently: a wrong index order, and a crosswalk that matches nothing.

Run: pytest tests/test_ng_coupling.py -v
"""

from __future__ import annotations

import pyomo.environ as pyo
import pytest

from src.integrator.ng_coupling import (
    NG_GAS_TECHS,
    NG_HEAT_RATE_MMBTUPERMWH,
    check_coupling_contract,
    load_ng_region_map,
    poll_ng_gas_demand,
    update_ng_fuel_adj,
)
from src.models.natural_gas.ng_model import GI


def _mock_elec(order: str = 'r_tech_step_year_hour', gen_gwh: float = 100.0):
    """Build a minimal electricity-like model with a chosen generation index order.

    This is a stand-in, not a real PowerModel: it declares only the four attributes the
    coupling contract requires (generation_total, WeightDay, MapHourDay, NGFuelAdj) plus the
    role sets the index resolver reads. Building a real electricity model would take minutes
    and drag in the whole data pipeline; this takes milliseconds, so the coupling logic can be
    exercised in isolation from whichever electricity model it is eventually pointed at.

    Parameters
    ----------
    order : str
        Which generation-index ordering to build. The two orderings in use across models of
        this lineage; see the module docstring for why that matters.
    gen_gwh : float
        Constant generation assigned to every index entry, so expected values stay hand-checkable.
    """
    m = pyo.ConcreteModel()
    # Regions are ints here on purpose: real models use int or str region ids, and the
    # crosswalk must cope with both (see test_region_map_accepts_both_key_types).
    m.region = pyo.Set(initialize=[7, 8])
    m.tech = pyo.Set(initialize=[3, 4, 6])  # 6 is non-gas, must be ignored
    m.step = pyo.Set(initialize=[1])
    m.year = pyo.Set(initialize=[2025, 2030])
    m.hour = pyo.Set(initialize=[1, 2])
    m.day = pyo.Set(initialize=[1])
    m.season = pyo.Set(initialize=['spring'])

    # The two index orderings. Both are 5-tuples of the same types, which is precisely why
    # unpacking positionally against the wrong one raises nothing and returns wrong numbers.
    if order == 'r_tech_step_year_hour':
        m.gen_index = pyo.Set(
            dimen=5,
            initialize=[
                (r, t, s, y, h)
                for r in m.region
                for t in m.tech
                for s in m.step
                for y in m.year
                for h in m.hour
            ],
        )
    else:  # tech, year, region, step, hour
        m.gen_index = pyo.Set(
            dimen=5,
            initialize=[
                (t, y, r, s, h)
                for r in m.region
                for t in m.tech
                for s in m.step
                for y in m.year
                for h in m.hour
            ],
        )

    # A Var, not a Param: the coupling reads solved generation, and initialize= stands in for
    # a solution so no solver is needed.
    m.generation_total = pyo.Var(m.gen_index, initialize=gen_gwh)
    # Both representative hours map to day 1, which carries 182.5 days of weight. Two hours x
    # 182.5 is a half-year each, so the day-weighting arithmetic stays trivial to verify.
    m.MapHourDay = pyo.Param(m.hour, initialize={1: 1, 2: 1}, within=pyo.Any)
    m.WeightDay = pyo.Param(m.day, initialize={1: 182.5})

    # The parameter the gas side writes into. Both flags are asserted on
    # by the contract tests below: Reals because the adjustment is a delta that goes negative,
    # mutable because it is rewritten between solves.
    m.NGFuelAdj = pyo.Param(
        m.region,
        m.tech,
        m.step,
        m.year,
        m.season,
        initialize=0.0,
        within=pyo.Reals,
        mutable=True,
    )
    return m


# ---------------------------------------------------------------------------
# index-order discovery, the silent-failure case
# ---------------------------------------------------------------------------


def test_gas_demand_identical_under_both_index_orders():
    """The same physical situation must give the same answer whatever the index order."""
    xw = {7: 'west_south_central', 8: 'south_atlantic'}
    a = poll_ng_gas_demand(_mock_elec('r_tech_step_year_hour'), xw)
    b = poll_ng_gas_demand(_mock_elec('tech_year_r_step_hour'), xw)
    assert a == pytest.approx(b)
    assert a, 'expected non-empty gas demand'


# ---------------------------------------------------------------------------
# quantity conversion
# ---------------------------------------------------------------------------


def test_gas_demand_matches_hand_calculation():
    """Bcf = GWh x WeightDay x heat rate / 1000, summed over gas techs and hours.

    Pins the full unit chain against arithmetic done by hand. An error anywhere in it, a
    missing day weight, a wrong power of ten, MWh confused with GWh, shows up here as a
    clean multiple of the expected value, which is easier to diagnose than a coupled run
    that merely looks a bit off.
    """
    m = _mock_elec(gen_gwh=100.0)
    got = poll_ng_gas_demand(m, {7: 'west_south_central', 8: 'south_atlantic'})

    # per region-year: 2 hours x 1 step, techs 3 and 4. 100 GWh x 182.5 day-weight x the
    # tech's MMBtu/MWh, divided by 1e3 to land in Bcf. Tech 6 must not appear.
    expect = sum(
        100.0 * 182.5 * NG_HEAT_RATE_MMBTUPERMWH[t] / 1e3 for t in (3, 4) for _ in range(2)
    )
    assert got[GI('west_south_central', 2025)] == pytest.approx(expect)


def test_non_gas_techs_are_excluded():
    """Tech 6 is not a gas technology and must contribute nothing.

    The complement of the test above: that one fixes the value for techs 3 and 4, this one
    proves nothing else leaked in. A tech filter applied against the wrong index position
    would inflate this total by including non-gas generation.
    """
    m = _mock_elec()
    got = poll_ng_gas_demand(m, {7: 'west_south_central', 8: 'south_atlantic'})
    only_gas = sum(
        100.0 * 182.5 * NG_HEAT_RATE_MMBTUPERMWH[t] / 1e3 for t in NG_GAS_TECHS for _ in range(2)
    )
    assert got[GI('west_south_central', 2025)] == pytest.approx(only_gas)


def test_unmapped_regions_are_skipped_not_silently_counted():
    """An electricity region absent from the crosswalk must drop out, not land somewhere else.

    Region 8 has no entry here. The danger case is its gas burn being attributed to a mapped
    region instead, which keeps the national total plausible while regional detail is wrong.
    """
    m = _mock_elec()
    got = poll_ng_gas_demand(m, {7: 'west_south_central'})  # region 8 unmapped
    assert {k.region for k in got} == {'west_south_central'}


# ---------------------------------------------------------------------------
# price transfer
# ---------------------------------------------------------------------------


def test_fuel_adj_is_zero_when_price_equals_reference():
    """At the reference, the adjustment must vanish, this is what preserves calibration."""
    m = _mock_elec()
    xw = {7: 'west_south_central', 8: 'south_atlantic'}
    ref = {GI('west_south_central', y): 3.0 for y in (2025, 2030)}
    ref.update({GI('south_atlantic', y): 3.0 for y in (2025, 2030)})

    n = update_ng_fuel_adj(m, dict(ref), xw, ref, alpha=1.0)
    assert n > 0, 'no entries updated, crosswalk or index resolution failed'
    assert all(pyo.value(m.NGFuelAdj[k]) == pytest.approx(0.0) for k in m.NGFuelAdj)


def test_fuel_adj_sign_and_magnitude():
    """A $1/MMBtu rise becomes heat_rate x 1000 $/GWh, positive.

    Fixes both the direction and the conversion. $/MMBtu x MMBtu/MWh gives $/MWh; the factor
    of 1000 lifts that to $/GWh, the unit generation_total is measured in. Getting the factor
    wrong scales the entire coupling signal while leaving its sign and shape believable.
    """
    m = _mock_elec()
    xw = {7: 'west_south_central', 8: 'south_atlantic'}
    ref = {GI(r, y): 3.0 for r in ('west_south_central', 'south_atlantic') for y in (2025, 2030)}
    now = {k: v + 1.0 for k, v in ref.items()}

    update_ng_fuel_adj(m, now, xw, ref, alpha=1.0)
    assert pyo.value(m.NGFuelAdj[7, 4, 1, 2025, 'spring']) == pytest.approx(
        1.0 * NG_HEAT_RATE_MMBTUPERMWH[4] * 1000.0
    )


def test_fuel_adj_can_go_negative():
    """Cheaper gas than reference must transmit as a negative adjustment, not be clamped.

    This is why NGFuelAdj is declared within=pyo.Reals. Declaring it NonNegativeReals would
    pass every other test here while silently clamping half the price signal to zero, so the
    electricity model would see gas rises but never falls.
    """
    m = _mock_elec()
    xw = {7: 'west_south_central', 8: 'south_atlantic'}
    ref = {GI(r, y): 3.0 for r in ('west_south_central', 'south_atlantic') for y in (2025, 2030)}
    now = {k: v - 1.0 for k, v in ref.items()}

    update_ng_fuel_adj(m, now, xw, ref, alpha=1.0)
    assert pyo.value(m.NGFuelAdj[7, 3, 1, 2025, 'spring']) < 0


def test_under_relaxation_blends():
    """Alpha damps the update toward the current value: new = alpha*full + (1-alpha)*current.

    Starting from 0, one damped step must land at exactly alpha x the full adjustment. Gas
    price and gas-fired dispatch feed back on each other strongly, so an alpha that is
    accepted but not actually applied would let the loop oscillate instead of converging.
    """
    m = _mock_elec()
    xw = {7: 'west_south_central', 8: 'south_atlantic'}
    ref = {GI(r, y): 3.0 for r in ('west_south_central', 'south_atlantic') for y in (2025, 2030)}
    now = {k: v + 1.0 for k, v in ref.items()}

    update_ng_fuel_adj(m, now, xw, ref, alpha=0.25)  # from 0 -> 25% of full
    full = 1.0 * NG_HEAT_RATE_MMBTUPERMWH[4] * 1000.0
    assert pyo.value(m.NGFuelAdj[7, 4, 1, 2025, 'spring']) == pytest.approx(0.25 * full)


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


def test_contract_passes_on_a_complete_model():
    """A model with all four required attributes passes and returns the resolved index order."""
    assert check_coupling_contract(_mock_elec())['region'] == 0


def test_contract_fails_loudly_without_ng_fuel_adj():
    """A missing NGFuelAdj must raise at setup, not on the first write mid-iteration.

    The contract check exists so a half-wired electricity model fails immediately with an
    actionable message, rather than after the first expensive solve.
    """
    m = _mock_elec()
    m.del_component(m.NGFuelAdj)
    with pytest.raises(RuntimeError, match='NGFuelAdj'):
        check_coupling_contract(m)


def test_contract_fails_on_immutable_ng_fuel_adj():
    """Present but immutable is the second case, so it gets its own check.

    hasattr() succeeds, so a naive existence test passes and the failure surfaces only when
    the loop first tries to write. check_coupling_contract inspects .mutable explicitly.
    """
    m = _mock_elec()
    m.del_component(m.NGFuelAdj)
    m.NGFuelAdj = pyo.Param(
        m.region, m.tech, m.step, m.year, m.season, initialize=0.0, within=pyo.Reals
    )  # not mutable
    with pytest.raises(RuntimeError, match='mutable'):
        check_coupling_contract(m)


def test_region_map_accepts_both_key_types():
    """The crosswalk must answer to int and str region ids identically.

    Electricity models in this lineage disagree on whether region ids are ints or strings.
    load_ng_region_map keys the mapping under both so either lookup succeeds; if it did not,
    a type mismatch would match nothing and gas demand would come back empty.
    """
    xw = load_ng_region_map()
    assert xw, 'crosswalk is empty'
    assert xw.get(1) == xw.get('1'), 'int and str keys must agree'
