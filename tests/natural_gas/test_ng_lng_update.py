"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/5/26

Tests for ``NGModel.update_lng_export``: updating the LNG export demand curve on a built model
must give the same solution as rebuilding the model on the changed data.  Guards against the
LNG consumer-surplus term in the objective freezing the curve at build time while the segment
caps move.
"""

from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

import src.models.natural_gas.sequencer as ng_sequencer
from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import GI, NGModel
from src.models.natural_gas.sequencer import NGSequencer

# west_south_central is an LNG exporter, so this subset carries an LNG demand curve
PARTIAL_REGIONS = ['west_south_central', 'mountain', 'pacific']
CAPACITY_SCALE = 1.3
SOLVER = 'highs'


@pytest.fixture
def partial_config_set() -> tuple[CommonConfig, NGConfig]:
    """A ``(CommonConfig, NGConfig)`` pair filtered down to :data:`PARTIAL_REGIONS`.

    Returns
    -------
    tuple[CommonConfig, NGConfig]
        The NG test TOML, with ``region_filter`` narrowed to three divisions.
    """
    config_path = Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    ng_config = NGConfig(**remainder.pop('natural_gas'))
    ng_config.region_filter = PARTIAL_REGIONS
    return common_config, ng_config


def _solve(sequencer: NGSequencer) -> tuple[float, float, dict[GI, float]]:
    """Solve and return (objective, total LNG export, prices).

    Parameters
    ----------
    sequencer : NGSequencer
        Sequencer holding a built model.

    Returns
    -------
    tuple[float, float, dict[GI, float]]
        Objective value, LNG export summed over regions and years, and ``poll_gas_price()``.
    """
    sequencer.solve_model(solver_name=SOLVER)
    m = sequencer.model
    lng = sum(value(m.lng_export_demand[r, y]) for r in m.region_analyze for y in m.year)
    return value(m.total_cost), lng, m.poll_gas_price()


def _base_capacity(m: NGModel) -> dict[GI, float]:
    """Read the full-curve LNG export volume per (region, year) back off a built model.

    Parameters
    ----------
    m : NGModel
        Built model.

    Returns
    -------
    dict[GI, float]
        {GI(region, year): BCF}, the volume at the curve's last breakpoint.
    """
    last = m.lng_breaks.last()
    q_frac_last = m.lng_demand_curve_shape['q_frac'][-1]
    return {
        GI(r, y): value(m.q_lng[r, last, y]) / q_frac_last
        for r in m.lng_exporting_region
        for y in m.year
    }


def _rebuilt_reference(
    config_set: tuple[CommonConfig, NGConfig],
    monkeypatch: pytest.MonkeyPatch,
    world_price: float,
) -> tuple[float, float, dict[GI, float]]:
    """Build and solve a model whose input data carries the updated LNG curve.

    Parameters
    ----------
    config_set : tuple[CommonConfig, NGConfig]
        Run configuration.
    monkeypatch : pytest.MonkeyPatch
        Used to wrap ``load_all`` in the sequencer for this build only.
    world_price : float
        World LNG price to write into the loaded curve shape.

    Returns
    -------
    tuple[float, float, dict[GI, float]]
        As :func:`_solve`.
    """
    real_load_all = ng_sequencer.load_all

    def load_all(**kwargs):
        data = real_load_all(**kwargs)
        data['lng_export'] = {
            r: {y: v * CAPACITY_SCALE for y, v in table.items()}
            for r, table in data['lng_export'].items()
        }
        data['lng_demand_curve'] = dict(data['lng_demand_curve'], world_price=world_price)
        return data

    with monkeypatch.context() as mp:
        mp.setattr(ng_sequencer, 'load_all', load_all)
        sequencer = NGSequencer()
        sequencer.build_model(*config_set)
    return _solve(sequencer)


def _assert_same(
    got: tuple[float, float, dict[GI, float]], expected: tuple[float, float, dict[GI, float]]
) -> None:
    """Assert two :func:`_solve` results agree to solver tolerance."""
    assert got[0] == pytest.approx(expected[0], rel=1e-6)
    assert got[1] == pytest.approx(expected[1], rel=1e-6)
    assert got[2] == pytest.approx(expected[2], rel=1e-6, abs=1e-6)


# 9.0 leaves exports at full capacity; 1.5 puts them on the interior of the curve, where a stale
# objective also moves volumes and prices, not just the objective value
@pytest.mark.parametrize('world_price', [9.0, 1.5], ids=['capacity-bound', 'interior'])
@pytest.mark.parametrize('solve_first', [False, True], ids=['fresh', 'after-solve'])
def test_update_matches_rebuild(
    partial_config_set: tuple[CommonConfig, NGConfig],
    monkeypatch: pytest.MonkeyPatch,
    world_price: float,
    solve_first: bool,
) -> None:
    """Updating the LNG curve on a built (or already solved) model equals a rebuild.

    Parameters
    ----------
    partial_config_set : tuple[CommonConfig, NGConfig]
        Fixture.
    monkeypatch : pytest.MonkeyPatch
        Fixture.
    world_price : float
        World LNG price to update to.
    solve_first : bool
        Solve before updating, as an iterating integrator would.
    """
    sequencer = NGSequencer()
    m = sequencer.build_model(*partial_config_set)
    if solve_first:
        _solve(sequencer)
    capacity = {gi: cap * CAPACITY_SCALE for gi, cap in _base_capacity(m).items()}
    m.update_lng_export(capacity=capacity, world_price=world_price)

    _assert_same(
        _solve(sequencer), _rebuilt_reference(partial_config_set, monkeypatch, world_price)
    )


def test_zero_width_segment(partial_config_set: tuple[CommonConfig, NGConfig]) -> None:
    """Collapsing a region-year's LNG curve to zero solves, and restoring it recovers the base.

    Parameters
    ----------
    partial_config_set : tuple[CommonConfig, NGConfig]
        Fixture.
    """
    sequencer = NGSequencer()
    m = sequencer.build_model(*partial_config_set)
    base = _solve(sequencer)
    base_capacity = _base_capacity(m)
    gi = next(iter(base_capacity))

    m.update_lng_export(capacity={gi: 0.0})
    for k in m.lng_segs:
        assert value(m.lng_surplus_intercept[gi.region, k, gi.year]) == 0.0
        assert value(m.lng_surplus_slope[gi.region, k, gi.year]) == 0.0
    _solve(sequencer)
    assert value(m.lng_export_demand[gi.region, gi.year]) == pytest.approx(0.0, abs=1e-6)

    m.update_lng_export(capacity={gi: base_capacity[gi]})
    _assert_same(_solve(sequencer), base)
