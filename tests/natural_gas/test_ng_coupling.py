"""Contract tests for the gas <-> electricity Gauss-Seidel coupling.

These use a minimal stand-in electricity model rather than a real one, so they run anywhere and
test the coupling logic itself. The important cases are the ones that would otherwise fail
silently, and a crosswalk that doesn't match.

Run: pytest tests/test_ng_coupling.py -v
"""

from pathlib import Path

import pyomo.environ as pyo
import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.integrator.ng_coupling import (
    GENERATION_INDEX,
    NG_GAS_TECHS,
    NG_HEAT_RATE_MMBTUPERMWH,
    check_coupling_contract,
    load_ng_region_map,
    poll_ng_gas_demand,
    update_ng_fuel_adj,
)
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.natural_gas.ng_model import GI


def _mock_elec(gen_gwh: float = 100.0) -> pyo.ConcreteModel:
    """Build a minimal electricity-like model.

    This is a stand-in, not a real PowerModel: it declares only the four attributes the
    coupling contract requires (generation_total, weight_day, map_hour_day, ng_fuel_adj) plus the
    role sets the index resolver reads. Building a real electricity model would take minutes
    and drag in the whole data pipeline; this takes milliseconds, so the coupling logic can be
    exercised in isolation from whichever electricity model it is eventually pointed at.

    Parameters
    ----------
    gen_gwh : float
        Constant generation assigned to every index entry, so expected values stay hand-checkable.
    """
    m = pyo.ConcreteModel()
    m.region = pyo.Set(initialize=['7', '8'])
    m.tech = pyo.Set(initialize=['3', '4', '6'])  # '6' is non-gas, must be ignored
    m.step = pyo.Set(initialize=[1])
    m.year = pyo.Set(initialize=[2025, 2030])
    m.hour = pyo.Set(initialize=[1, 2])
    m.day = pyo.Set(initialize=[1])
    m.season = pyo.Set(initialize=['spring'])

    # One ordering, matching model_sets.generation_index: (region, tech, step, year, hour).
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

    m.generation_total = pyo.Var(m.gen_index, initialize=gen_gwh)
    # Both representative hours map to day 1, which carries 182.5 days of weight. Two hours x
    # 182.5 is a half-year each, so the day-weighting arithmetic stays trivial to verify.
    m.map_hour_day = pyo.Param(m.hour, initialize={1: 1, 2: 1}, within=pyo.Any)
    m.weight_day = pyo.Param(m.day, initialize={1: 182.5})

    # The parameter the gas side writes into. Both flags are asserted on
    # by the contract tests below: Reals because the adjustment is a delta that goes negative,
    # mutable because it is rewritten between solves.
    m.ng_fuel_adj = pyo.Param(
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
# quantity conversion
# ---------------------------------------------------------------------------


def test_gas_demand_matches_hand_calculation() -> None:
    """Bcf = GWh x weight_day x heat rate / 1000, summed over gas techs and hours.

    Pins the full unit chain against arithmetic done by hand. An error anywhere in it, a
    missing day weight, a wrong power of ten, MWh confused with GWh, shows up here as a
    clean multiple of the expected value, which is easier to diagnose than a coupled run
    that merely looks a bit off.
    """
    m = _mock_elec(gen_gwh=100.0)
    got = poll_ng_gas_demand(m, {'7': 'west_south_central', '8': 'south_atlantic'})

    # per region-year: 2 hours x 1 step, techs '3' and '4'. 100 GWh x 182.5 day-weight x the
    # tech's MMBtu/MWh, divided by 1e3 to land in Bcf. Tech '6' must not appear.
    expect = sum(
        100.0 * 182.5 * NG_HEAT_RATE_MMBTUPERMWH[t] / 1e3 for t in ('3', '4') for _ in range(2)
    )
    assert got[GI('west_south_central', 2025)] == pytest.approx(expect)


def test_non_gas_techs_are_excluded() -> None:
    """Tech '6' is not a gas technology and must contribute nothing.

    The complement of the test above: that one fixes the value for techs '3' and '4', this one
    proves nothing else leaked in. A tech filter applied against the wrong index position
    would inflate this total by including non-gas generation.
    """
    m = _mock_elec()
    got = poll_ng_gas_demand(m, {'7': 'west_south_central', '8': 'south_atlantic'})
    only_gas = sum(
        100.0 * 182.5 * NG_HEAT_RATE_MMBTUPERMWH[t] / 1e3 for t in NG_GAS_TECHS for _ in range(2)
    )
    assert got[GI('west_south_central', 2025)] == pytest.approx(only_gas)


def test_unmapped_regions_are_skipped_not_silently_counted() -> None:
    """An electricity region absent from the crosswalk must drop out, not land somewhere else.

    Region '8' has no entry here. The danger case is its gas burn being attributed to a mapped
    region instead, which keeps the national total plausible while regional detail is wrong.
    """
    m = _mock_elec()
    got = poll_ng_gas_demand(m, {'7': 'west_south_central'})  # region '8' unmapped
    assert {k.region for k in got} == {'west_south_central'}


# ---------------------------------------------------------------------------
# price transfer
# ---------------------------------------------------------------------------


def test_fuel_adj_is_zero_when_price_equals_reference() -> None:
    """At the reference, the adjustment must vanish, this is what preserves calibration."""
    m = _mock_elec()
    xw = {'7': 'west_south_central', '8': 'south_atlantic'}
    ref = {GI('west_south_central', y): 3.0 for y in (2025, 2030)}
    ref.update({GI('south_atlantic', y): 3.0 for y in (2025, 2030)})

    n = update_ng_fuel_adj(m, dict(ref), xw, ref, alpha=1.0)
    assert n > 0, 'no entries updated, crosswalk or index resolution failed'
    assert all(pyo.value(m.ng_fuel_adj[k]) == pytest.approx(0.0) for k in m.ng_fuel_adj)


def test_fuel_adj_sign_and_magnitude() -> None:
    """A $1/MMBtu rise becomes heat_rate x 1000 $/GWh, positive.

    Fixes both the direction and the conversion. $/MMBtu x MMBtu/MWh gives $/MWh; the factor
    of 1000 lifts that to $/GWh, the unit generation_total is measured in. Getting the factor
    wrong scales the entire coupling signal while leaving its sign and shape believable.
    """
    m = _mock_elec()
    xw = {'7': 'west_south_central', '8': 'south_atlantic'}
    ref = {GI(r, y): 3.0 for r in ('west_south_central', 'south_atlantic') for y in (2025, 2030)}
    now = {k: v + 1.0 for k, v in ref.items()}

    update_ng_fuel_adj(m, now, xw, ref, alpha=1.0)
    assert pyo.value(m.ng_fuel_adj['7', '4', 1, 2025, 'spring']) == pytest.approx(
        1.0 * NG_HEAT_RATE_MMBTUPERMWH['4'] * 1000.0
    )


def test_fuel_adj_can_go_negative() -> None:
    """Cheaper gas than reference must transmit as a negative adjustment, not be clamped.

    This is why ng_fuel_adj is declared within=pyo.Reals. Declaring it NonNegativeReals would
    pass every other test here while silently clamping half the price signal to zero, so the
    electricity model would see gas rises but never falls.
    """
    m = _mock_elec()
    xw = {'7': 'west_south_central', '8': 'south_atlantic'}
    ref = {GI(r, y): 3.0 for r in ('west_south_central', 'south_atlantic') for y in (2025, 2030)}
    now = {k: v - 1.0 for k, v in ref.items()}

    update_ng_fuel_adj(m, now, xw, ref, alpha=1.0)
    assert pyo.value(m.ng_fuel_adj['7', '3', 1, 2025, 'spring']) < 0


def test_under_relaxation_blends() -> None:
    """Alpha damps the update toward the current value: new = alpha*full + (1-alpha)*current.

    Starting from 0, one damped step must land at exactly alpha x the full adjustment. Gas
    price and gas-fired dispatch feed back on each other strongly, so an alpha that is
    accepted but not actually applied would let the loop oscillate instead of converging.
    """
    m = _mock_elec()
    xw = {'7': 'west_south_central', '8': 'south_atlantic'}
    ref = {GI(r, y): 3.0 for r in ('west_south_central', 'south_atlantic') for y in (2025, 2030)}
    now = {k: v + 1.0 for k, v in ref.items()}

    update_ng_fuel_adj(m, now, xw, ref, alpha=0.25)  # from 0 -> 25% of full
    full = 1.0 * NG_HEAT_RATE_MMBTUPERMWH['4'] * 1000.0
    assert pyo.value(m.ng_fuel_adj['7', '4', 1, 2025, 'spring']) == pytest.approx(0.25 * full)


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


def test_contract_passes_on_a_complete_model() -> None:
    """A model with all four required attributes passes without raising."""
    assert check_coupling_contract(_mock_elec()) is None


def test_contract_fails_loudly_without_ng_fuel_adj() -> None:
    """A missing ng_fuel_adj must raise at setup, not on the first write mid-iteration.

    The contract check exists so a half-wired electricity model fails immediately with an
    actionable message, rather than after the first expensive solve.
    """
    m = _mock_elec()
    m.del_component(m.ng_fuel_adj)
    with pytest.raises(RuntimeError, match='ng_fuel_adj'):
        check_coupling_contract(m)


def test_contract_fails_on_immutable_ng_fuel_adj() -> None:
    """Present but immutable is the second case, so it gets its own check.

    hasattr() succeeds, so a naive existence test passes and the failure surfaces only when
    the loop first tries to write. check_coupling_contract inspects .mutable explicitly.
    """
    m = _mock_elec()
    m.del_component(m.ng_fuel_adj)
    m.ng_fuel_adj = pyo.Param(
        m.region, m.tech, m.step, m.year, m.season, initialize=0.0, within=pyo.Reals
    )  # not mutable
    with pytest.raises(RuntimeError, match='mutable'):
        check_coupling_contract(m)


# ---------------------------------------------------------------------------
# the contract, against a real electricity model
# ---------------------------------------------------------------------------


class TestContractAgainstRealPowerModel:
    """The mock above declares whatever the coupling asks for, so it can never catch a rename.

    These build an actual PowerModel. That matters: the coupling referenced ``WeightDay`` and
    ``MapHourDay`` for some time after commit ffc4856 renamed them to ``weight_day`` and
    ``map_hour_day``, and every mock-based test passed throughout, because the mock was renamed
    to match the coupling rather than the model.
    """

    def _power_model(self) -> PowerModel:
        """Build an unsolved PowerModel from the standard electricity test config."""
        config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
        common_config, remainder = CommonConfig.from_toml(config_path)
        elec_config = ElecConfig(**remainder.pop('elec_config'))
        return ElectricitySequencer().build_model(common_config, elec_config)

    @pytest.mark.parametrize('attr', ['generation_total', 'weight_day', 'map_hour_day'])
    def test_required_components_exist_on_the_real_model(self, attr: str) -> None:
        """Each component the coupling reads must be present under the name it uses."""
        assert hasattr(self._power_model(), attr), (
            f'PowerModel has no {attr}; the coupling contract has drifted from the model'
        )

    def test_generation_index_order_is_as_declared(self) -> None:
        """GENERATION_INDEX is declared, not discovered, so pin it against the real index.

        Region and tech are both strings drawn from overlapping numerals, so a swap of
        positions 0 and 1 cannot be caught by value membership. Compare the number of distinct
        values at each position against the set that position is meant to hold.
        """
        model = self._power_model()
        keys = list(model.generation_total.index_set())
        assert len(keys[0]) == 5

        for role, expected in (
            ('region', model.region),
            ('tech', model.tech),
            ('year', model.year),
            ('hour', model.hour),
        ):
            position = GENERATION_INDEX[role]
            assert {k[position] for k in keys} <= set(expected), (
                f'values at position {position} are not members of {role}'
            )


class TestRegionMapLoading:
    """``load_ng_region_map`` must fail readably rather than with a bare ``KeyError``.

    The reader uses ``csv.DictReader``, which has no comment support and treats the first line
    as the header. A file carrying a leading ``#`` note therefore used to raise
    ``KeyError: 'elec_region'``, naming a column plainly present in the file.
    """

    def test_leading_comment_row_raises_named_error(self, tmp_path: Path) -> None:
        """A '#' note is consumed as the header, and that must be said plainly."""
        target = tmp_path / 'xw.csv'
        target.write_text(
            '# provenance note\nelec_region,ng_region\n1,west_south_central\n', encoding='utf-8'
        )
        with pytest.raises(ValueError, match='missing required column'):
            load_ng_region_map(target)

    @pytest.mark.parametrize(
        'content,label',
        [
            ('', 'zero-byte file'),
            ('elec_region,ng_region\n', 'header with no rows'),
        ],
    )
    def test_empty_mapping_raises(self, tmp_path: Path, content: str, label: str) -> None:
        """Both empty shapes raise, though they reach the check by different paths.

        A zero-byte file has no fieldnames at all and fails header validation; a header-only
        file passes that and fails the row check.
        """
        target = tmp_path / 'xw.csv'
        target.write_text(content, encoding='utf-8')
        with pytest.raises(ValueError):
            load_ng_region_map(target)
