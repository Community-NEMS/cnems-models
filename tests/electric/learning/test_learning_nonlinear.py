"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/16/26

Tests for nonlinear capacity-expansion learning, on the mini model
(``tests/electric/mini_model.py``).

Solar is buildable in both regions and both years (``add_solar_learning``):  initial capital cost
$1000/MW, learning rate 0.1, and a learning baseline of 100 MW.  ``multi_learner_model`` adds a
second buildable Solar step and a second learning tech (Nuclear) to check which builds count
toward a tech's experience.  The tests evaluate the nonlinear objective at fixed build levels on
an unsolved model, so they need no nonlinear solver.  The solved nonlinear case is pinned in
``test_basic_run.py``.
"""

import pytest
from pyomo.common.numeric_types import value

from src.models.electricity.electricity_model import PowerModel
from tests.electric.mini_model import add_solar_learning

INITIAL_COST = 1000.0
RATE = 0.1
BASELINE = 100.0
# large against the 100 MW baseline, so the discount is far above pytest.approx's tolerance
_BUILD_MW = 50.0


@pytest.fixture
def nonlinear_model(mini_data, build_mini) -> PowerModel:
    """Unsolved mini model with Solar expansion under nonlinear learning."""
    add_solar_learning(mini_data)
    _, model = build_mini(capacity_expansion=True, expansion_learning_type='nonlinear')
    return model


SOLAR_STEP_2_COST = 1500.0
NUCLEAR_COST = 4000.0


@pytest.fixture
def multi_learner_model(mini_data, build_mini) -> PowerModel:
    """``nonlinear_model`` plus a second buildable Solar step and a second learning tech.

    Solar step 2 (initial cost ``SOLAR_STEP_2_COST``) shares Solar's learning rate and baseline.
    Nuclear (initial cost ``NUCLEAR_COST``) learns on its own curve, with the same rate and
    baseline as Solar so that pooling it in would discount Solar by a resolvable amount.
    """
    add_solar_learning(mini_data)
    mini_data.attributes['tech']['steps']['Solar'] = '1/2'
    mini_data.set_buildable('Solar', 2)
    mini_data.add_operating_costs('Solar', generation_cost=0.0, fom_cost=5.0, step=2)
    mini_data.add_rows(
        'capacity_factor_hourly',
        {
            (r, 'Solar', 2, hr): 0.5 if hr % 2 == 0 else 0.0
            for r, hr in mini_data.every('region', 'hour')
        },
    )

    mini_data.add_tech(
        'Nuclear', flags=['is_conventional', 'is_dispatchable', 'is_generator'], buildable=[1]
    )
    mini_data.add_operating_costs('Nuclear', generation_cost=10.0, fom_cost=50.0)
    mini_data.add_rows('learning_rate', {('Nuclear',): RATE})
    mini_data.add_rows('supply_curve_learning', {('Nuclear',): BASELINE})

    for tech, step, cost in (('Solar', 2, SOLAR_STEP_2_COST), ('Nuclear', 1, NUCLEAR_COST)):
        mini_data.add_rows(
            'capital_cost',
            {(r, tech, step, y): cost for r, y in mini_data.every('region', 'year')},
        )
        mini_data.add_rows(
            'capital_cost_initial', {(r, tech, step): cost for (r,) in mini_data.every('region')}
        )

    _, model = build_mini(capacity_expansion=True, expansion_learning_type='nonlinear')
    return model


def _multiplier(experience_mw: float) -> float:
    """The learning curve written out longhand, for the mini model's Solar."""
    return ((BASELINE + experience_mw) / BASELINE) ** -RATE


@pytest.mark.parametrize(
    'bad_value,expected_message',
    [
        (0.0, 'Value not in parameter domain PositiveReals'),
        (-5.0, 'Value not in parameter domain PositiveReals'),
        (float('nan'), 'Value not in parameter domain PositiveReals'),
    ],
    ids=['zero', 'negative', 'nan'],
)
def test_unusable_learning_baseline_is_rejected(mini_data, build_mini, bad_value, expected_message):
    """An unusable ``supply_curve_learning`` is caught at construction, not in the objective.

    The curve divides by this baseline and raises the result to a fractional power, so any of these
    would otherwise surface as a division error, a domain error, or a silent NaN from somewhere
    inside the objective, with nothing to say which technology caused it.

    ``within=PositiveReals`` rejects all three at construction.
    """
    add_solar_learning(mini_data)
    mini_data.add_rows('supply_curve_learning', {('Solar',): bad_value})

    with pytest.raises(ValueError) as caught:
        build_mini(capacity_expansion=True, expansion_learning_type='nonlinear')

    message = str(caught.value)
    assert expected_message in message
    # The offending technology has to be identifiable, which is the point of catching it here.
    assert 'Solar' in message


def _expansion_cost_with(model: PowerModel, builds: dict[tuple, float]) -> float:
    """Evaluate ``capacity_expansion_cost`` with only ``builds`` nonzero.

    Parameters
    ----------
    model : PowerModel
        Model built with nonlinear learning.
    builds : dict[tuple, float]
        ``capacity_builds`` key -> MW; every other build is set to 0.

    Returns
    -------
    float
        The evaluated expansion cost.
    """
    for key, var in model.capacity_builds.items():
        var.set_value(builds.get(key, 0.0))
    return value(model.capacity_expansion_cost)


def test_nonlinear_learning_objective(nonlinear_model: PowerModel) -> None:
    """The expansion cost matches the curve written out by hand, and learning is subadditive.

    With every build at ``level`` MW, the two 2030 builds pay full price and the two 2031 builds
    are discounted by the 2 x ``level`` MW built in 2030:
    ``2 x level x 1000 x (1 + multiplier(2 x level))``.
    """
    model = nonlinear_model
    assert len(model.capacity_builds) == 4  # R1/R2 x 2030/2031

    def cost_at(level: float) -> float:
        return _expansion_cost_with(model, dict.fromkeys(model.capacity_builds, level))

    # No builds means no expansion cost, whatever the multiplier does.
    assert cost_at(0.0) == pytest.approx(0.0, abs=1e-9)

    for level in (_BUILD_MW, 2.5 * _BUILD_MW):
        expected = 2 * level * INITIAL_COST * (1 + _multiplier(2 * level))
        assert cost_at(level) == pytest.approx(expected)

    # Learning makes cost subadditive in cumulative builds: 2.5x the capacity costs less than 2.5x
    # the money.  That is the property the nonlinear mode exists to produce.
    assert cost_at(2.5 * _BUILD_MW) < 2.5 * cost_at(_BUILD_MW)


def test_nonlinear_build_does_not_discount_itself(nonlinear_model: PowerModel) -> None:
    """A build must be discounted by prior years' capacity, not by its own.

    The shared curve takes whatever cumulative quantity it is handed; this checks the objective
    hands it the right one.  First-year builds have no prior experience, so building only in the
    first year must cost exactly the undiscounted price.  Needs a build only, no solver.
    """
    model = nonlinear_model
    first_year = min(model.year)
    keys = [k for k in model.capacity_builds if k[3] == first_year]
    assert len(keys) == 2
    cost = _expansion_cost_with(model, dict.fromkeys(keys, _BUILD_MW))
    assert cost == pytest.approx(len(keys) * _BUILD_MW * INITIAL_COST)


def test_nonlinear_experience_pools_across_regions(nonlinear_model: PowerModel) -> None:
    """A later build is discounted by earlier builds of the same tech in *other* regions.

    Build in R1 in 2030, then price a 2031 build in R2.  If the cumulative term were region-local
    the later build would be charged the undiscounted price.  The closed form is asserted rather
    than an inequality, so a wrong aggregation cannot pass by discounting some other amount.
    Needs a build only, no solver.
    """
    model = nonlinear_model
    source = ('R1', 'Solar', 1, min(model.year))
    later = ('R2', 'Solar', 1, max(model.year))
    full_price = _BUILD_MW * INITIAL_COST

    # With no prior experience anywhere, the later build is charged undiscounted.
    assert _expansion_cost_with(model, {later: _BUILD_MW}) == pytest.approx(full_price)

    pooled = _expansion_cost_with(model, {source: _BUILD_MW, later: _BUILD_MW})
    # Guard: the discount must be resolvable at approx's tolerance, or this test proves nothing.
    assert pooled != pytest.approx(2 * full_price)
    assert pooled == pytest.approx(full_price + full_price * _multiplier(_BUILD_MW))


def test_nonlinear_experience_pools_across_steps(multi_learner_model: PowerModel) -> None:
    """A later build is discounted by earlier builds of the same tech in *other* steps.

    Build Solar step 1 in R1 in 2030, then price a 2031 Solar step 2 build in R2.  If the
    cumulative term were step-local, the later build would be charged its undiscounted price.
    Needs a build only, no solver.
    """
    model = multi_learner_model
    source = ('R1', 'Solar', 1, min(model.year))
    later = ('R2', 'Solar', 2, max(model.year))
    source_price = _BUILD_MW * INITIAL_COST
    later_price = _BUILD_MW * SOLAR_STEP_2_COST

    assert _expansion_cost_with(model, {later: _BUILD_MW}) == pytest.approx(later_price)

    pooled = _expansion_cost_with(model, {source: _BUILD_MW, later: _BUILD_MW})
    assert pooled != pytest.approx(source_price + later_price)
    assert pooled == pytest.approx(source_price + later_price * _multiplier(_BUILD_MW))


def test_nonlinear_experience_stays_within_tech(multi_learner_model: PowerModel) -> None:
    """Earlier builds of a *different* tech do not discount a later build.

    Build Nuclear in R1 in 2030, then price a 2031 Solar build in R2.  Nuclear's curve has the
    same rate and baseline as Solar's, so pooling it into Solar's experience would discount the
    Solar build by ``_multiplier(_BUILD_MW)``.  Both builds must be charged in full.  Needs a
    build only, no solver.
    """
    model = multi_learner_model
    other = ('R1', 'Nuclear', 1, min(model.year))
    later = ('R2', 'Solar', 1, max(model.year))

    cost = _expansion_cost_with(model, {other: _BUILD_MW, later: _BUILD_MW})
    assert cost == pytest.approx(_BUILD_MW * (NUCLEAR_COST + INITIAL_COST))
