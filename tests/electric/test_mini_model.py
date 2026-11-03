"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/24/26

Tests on the hand-computable mini model (``tests/electric/mini_model.py``):  the bare base, and
augmentations of it, including building a tech where it has no existing capacity.
"""

import pyomo.environ as pyo
import pytest

from src.common.integrated_model_sequencer import IterationStatus
from tests.electric.mini_model import MiniModelData


def add_solar_expansion(data: MiniModelData) -> MiniModelData:
    """Make Solar existing in R1 only, but buildable in both regions, and short R2 by day.

    Solar runs at CF 0.5 in the day hours (2, 4) and 0 at night.  R2's day load rises to 150 MW
    against 100 MW of Gas, so the 50 MW gap needs exactly 100 MW of new Solar in R2 in 2030.
    """
    data.set_buildable('Solar', 1)
    data.add_capacity('Solar', 50.0, regions=['R1'])
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
    data.set_load('R2', {1: 50.0, 2: 150.0, 3: 50.0, 4: 150.0})
    return data


@pytest.mark.parametrize('capacity_expansion', [False, True], ids=['dispatch', 'expansion'])
def test_base_builds(build_mini, capacity_expansion):
    """The bare base builds (and validates) with and without capacity expansion."""
    _, model = build_mini(capacity_expansion=capacity_expansion)

    # Gas only:  2 regions x 2 years, x 4 hours for generation
    assert len(model.capacity_total) == 4
    assert len(model.generation_total) == 16
    assert len(model.demand_balance) == 16
    if capacity_expansion:
        assert len(model.capacity_builds) == 0  # nothing is buildable in the base


def test_base_solves(build_mini):
    """Gas serves the flat 80 MW load everywhere; the objective is computable by hand."""
    sequencer, model = build_mini()
    _, status = sequencer.solve_model()

    assert status is IterationStatus.BEST
    assert sum(pyo.value(v) for v in model.unmet_load.values()) == pytest.approx(0.0)
    # dispatch:  80 MW x 4 hr x 2 regions x 2 years x $30  +  FOM:  100 MW x $10 x 4 (r, y)
    assert pyo.value(model.total_cost) == pytest.approx(80 * 4 * 2 * 2 * 30 + 100 * 10 * 4)


def test_augment_with_storage(build_mini, mini_data):
    """A third tech type (storage) can be bolted onto the base through the editing helpers."""
    mini_data.add_tech('Battery', flags=['is_storage'])
    mini_data.add_capacity('Battery', 20.0)
    mini_data.add_operating_costs('Battery', generation_cost=1.0, fom_cost=2.0)
    mini_data.add_rows('storage_efficiency', {('Battery',): 0.85})
    mini_data.add_rows('storage_duration', {('Battery',): 4.0})

    _, model = build_mini()

    assert len(model.storage_level) == 16  # 2 regions x 2 years x 4 hours
    assert len(model.storage_first_hour_balance) == 8  # ... x 2 first hours


def test_new_location_enters_capacity_index(build_mini, mini_data):
    """With expansion on, a buildable location with no existing capacity joins the index."""
    add_solar_expansion(mini_data)
    _, model = build_mini(capacity_expansion=True)

    for year in mini_data.years:
        new_key = ('R2', 'Solar', 1, year)
        assert new_key in model.capacity_total
        assert new_key in model.capacity_builds
        assert new_key not in model.available_capacity
        # its balance is just total == builds to date:  no existing-capacity constant
        balance = model.capacity_balance[new_key]
        assert pyo.value(balance.lower) == pytest.approx(0.0)
        # the existing R1 location stays and is buildable too
        assert ('R1', 'Solar', 1, year) in model.capacity_builds


def test_new_location_absent_without_expansion(build_mini, mini_data):
    """With expansion off, only existing capacity is indexed."""
    add_solar_expansion(mini_data)
    _, model = build_mini(capacity_expansion=False)

    assert ('R1', 'Solar', 1, 2030) in model.capacity_total
    assert ('R2', 'Solar', 1, 2030) not in model.capacity_total


def test_omitted_cost_excludes_location(build_mini, mini_data):
    """Omitting a location's capital cost is how it is kept out of the buildable set."""
    add_solar_expansion(mini_data)
    mini_data.drop_rows('capital_cost', [('R2', 'Solar', 1, y) for y in mini_data.years])
    _, model = build_mini(capacity_expansion=True)

    assert ('R2', 'Solar', 1, 2030) not in model.capacity_total
    assert ('R1', 'Solar', 1, 2030) in model.capacity_builds


def test_new_location_is_built(build_mini, mini_data):
    """R2's daytime shortfall is met by building Solar there, all of it in the first year."""
    add_solar_expansion(mini_data)
    sequencer, model = build_mini(capacity_expansion=True)
    _, status = sequencer.solve_model()

    assert status is IterationStatus.BEST
    assert pyo.value(model.capacity_builds['R2', 'Solar', 1, 2030]) == pytest.approx(100.0)
    assert pyo.value(model.capacity_builds['R2', 'Solar', 1, 2031]) == pytest.approx(0.0)
    # building in R1 does not pay:  $1000/MW against at most $60/MW of displaced gas
    assert pyo.value(model.capacity_builds['R1', 'Solar', 1, 2030]) == pytest.approx(0.0)
    assert sum(pyo.value(v) for v in model.unmet_load.values()) == pytest.approx(0.0)
    assert pyo.value(model.generation_total['R2', 'Solar', 1, 2030, 2]) == pytest.approx(50.0)


def test_build_carries_forward(build_mini, mini_data):
    """A build stays indexed and in service after the location's last capital-cost year.

    R2 Solar is buildable only in 2030, so 2031 has no build variable, but the 100 MW built in
    2030 still covers R2's daytime shortfall in 2031.
    """
    add_solar_expansion(mini_data)
    mini_data.drop_rows('capital_cost', [('R2', 'Solar', 1, 2031)])
    sequencer, model = build_mini(capacity_expansion=True)

    assert ('R2', 'Solar', 1, 2031) in model.capacity_total
    assert ('R2', 'Solar', 1, 2031) not in model.capacity_builds

    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST
    assert pyo.value(model.capacity_builds['R2', 'Solar', 1, 2030]) == pytest.approx(100.0)
    assert pyo.value(model.capacity_total['R2', 'Solar', 1, 2031]) == pytest.approx(100.0)
    assert sum(pyo.value(v) for v in model.unmet_load.values()) == pytest.approx(0.0)
