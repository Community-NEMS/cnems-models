"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/7/26

Tests for regions with no gas production.  Zero supply-tier capacity is the supported way to
express one:  it passes strict validation, builds with Q0 = 0, and solves with zero production.
``update_supply_capacity`` must reach the same state from a nonzero build, and a region omitted
from the tiers must fail with a named error rather than a bare KeyError.
"""

import math
from collections.abc import Callable
from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

import src.models.natural_gas.sequencer as ng_sequencer
from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import ALLOW_OUTBOUND_UPDATES
from src.models.natural_gas.data import NGData
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import GI, NGModel
from src.models.natural_gas.sequencer import NGSequencer

PARTIAL_REGIONS = ['west_south_central', 'mountain', 'pacific']
ZERO_REGION = 'mountain'
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


def _build(
    config_set: tuple[CommonConfig, NGConfig],
    monkeypatch: pytest.MonkeyPatch,
    edit: Callable[[NGData], None] | None = None,
) -> NGSequencer:
    """Build a model, optionally editing the loaded data first.

    Parameters
    ----------
    config_set : tuple[CommonConfig, NGConfig]
        Configs to build from.
    monkeypatch : pytest.MonkeyPatch
        Used to wrap ``load_all`` in the sequencer for this build only.
    edit : Callable[[NGData], None] | None
        Applied in place to the loaded data before validation.

    Returns
    -------
    NGSequencer
        Sequencer holding the built model.
    """
    real_load_all = ng_sequencer.load_all

    def load_all(**kwargs) -> NGData:
        data = real_load_all(**kwargs)
        if edit is not None:
            edit(data)
        return data

    sequencer = NGSequencer()
    with monkeypatch.context() as mp:
        mp.setattr(ng_sequencer, 'load_all', load_all)
        sequencer.build_model(*config_set)
    return sequencer


def _zero_tiers(data: NGData) -> None:
    """Set every supply tier capacity of :data:`ZERO_REGION` to zero, keeping the costs."""
    tiers = data['supply_cost_tiers']
    tiers[ZERO_REGION] = [(0.0, cost) for _, cost in tiers[ZERO_REGION]]


def _solve(sequencer: NGSequencer) -> tuple[float, dict[GI, float]]:
    """Solve, check the status is usable, and return (objective, prices).

    Parameters
    ----------
    sequencer : NGSequencer
        Sequencer holding a built model.

    Returns
    -------
    tuple[float, dict[GI, float]]
        Objective value and ``poll_gas_price()``.
    """
    _, status = sequencer.solve_model(solver_name=SOLVER)
    assert status in ALLOW_OUTBOUND_UPDATES
    m = sequencer.model
    return value(m.total_cost), m.poll_gas_price()


def _assert_zero_production(m: NGModel) -> None:
    """:data:`ZERO_REGION` has Q0 = 0 and produces nothing in every year."""
    for y in m.year:
        assert value(m.q0[ZERO_REGION, y]) == 0.0
        assert value(m.production_total[ZERO_REGION, y]) == pytest.approx(0.0, abs=1e-6)


def test_zero_tiers_build_and_solve(
    partial_config_set: tuple[CommonConfig, NGConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero tier capacity passes strict validation and solves with zero production.

    Parameters
    ----------
    partial_config_set : tuple[CommonConfig, NGConfig]
        Fixture.
    monkeypatch : pytest.MonkeyPatch
        Fixture.
    """
    assert partial_config_set[0].strict_validation
    sequencer = _build(partial_config_set, monkeypatch, _zero_tiers)
    _solve(sequencer)
    _assert_zero_production(sequencer.model)


def test_update_to_zero_matches_rebuild(
    partial_config_set: tuple[CommonConfig, NGConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``update_supply_capacity`` to zero gives the same solution as building with zero tiers.

    Parameters
    ----------
    partial_config_set : tuple[CommonConfig, NGConfig]
        Fixture.
    monkeypatch : pytest.MonkeyPatch
        Fixture.
    """
    sequencer = _build(partial_config_set, monkeypatch)
    m = sequencer.model
    m.update_supply_capacity({(ZERO_REGION, 'low_cost', y): 0.0 for y in m.year})
    updated_obj, updated_prices = _solve(sequencer)
    _assert_zero_production(m)

    rebuilt_obj, rebuilt_prices = _solve(_build(partial_config_set, monkeypatch, _zero_tiers))
    assert updated_obj == pytest.approx(rebuilt_obj, rel=1e-6)
    assert updated_prices.keys() == rebuilt_prices.keys()
    for gi, price in updated_prices.items():
        assert price == pytest.approx(rebuilt_prices[gi], rel=1e-4, abs=1e-4)


@pytest.mark.parametrize('capacity', [math.nan, math.inf], ids=['nan', 'inf'])
def test_update_rejects_non_finite(
    partial_config_set: tuple[CommonConfig, NGConfig],
    monkeypatch: pytest.MonkeyPatch,
    capacity: float,
) -> None:
    """A non-finite capacity raises and leaves Q0 untouched.

    Parameters
    ----------
    partial_config_set : tuple[CommonConfig, NGConfig]
        Fixture.
    monkeypatch : pytest.MonkeyPatch
        Fixture.
    capacity : float
        The non-finite capacity passed to ``update_supply_capacity``.
    """
    m = _build(partial_config_set, monkeypatch).model
    y = next(iter(m.year))
    before = value(m.q0[ZERO_REGION, y])
    with pytest.raises(ValueError, match='finite'):
        m.update_supply_capacity({(ZERO_REGION, 'low_cost', y): capacity})
    assert value(m.q0[ZERO_REGION, y]) == before


def test_missing_region_names_it(
    partial_config_set: tuple[CommonConfig, NGConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With validation off, a region omitted from the tiers raises a ValueError naming it.

    Parameters
    ----------
    partial_config_set : tuple[CommonConfig, NGConfig]
        Fixture.
    monkeypatch : pytest.MonkeyPatch
        Fixture.
    """
    partial_config_set[0].strict_validation = False
    with pytest.raises(ValueError, match=ZERO_REGION):
        _build(
            partial_config_set,
            monkeypatch,
            lambda data: data['supply_cost_tiers'].pop(ZERO_REGION),
        )
