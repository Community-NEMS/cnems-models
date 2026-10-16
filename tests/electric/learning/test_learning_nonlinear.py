"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/16/26

Tests for nonlinear capacity-expansion learning, built on the basic test dataset.

Most evaluate the nonlinear objective at fixed build levels on an unsolved model, so they
need no nonlinear solver.  The solved nonlinear case is pinned in ``test_basic_run.py``.
"""

from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer


@pytest.mark.parametrize(
    'bad_value,expected_message',
    [
        (0.0, 'Value not in parameter domain PositiveReals'),
        (-5.0, 'Value not in parameter domain PositiveReals'),
        (float('nan'), 'Value not in parameter domain PositiveReals'),
    ],
    ids=['zero', 'negative', 'nan'],
)
def test_unusable_learning_baseline_is_rejected(monkeypatch, bad_value, expected_message):
    """An unusable ``supply_curve_learning`` is caught at construction, not in the objective.

    The curve divides by this baseline and raises the result to a fractional power, so any of these
    would otherwise surface as a division error, a domain error, or a silent NaN from somewhere
    inside the objective, with nothing to say which technology caused it.

    ``within=PositiveReals`` rejects all three at construction.
    """
    from src.models.electricity import param_data as param_data_module

    original_init = param_data_module.ParamData.__init__
    poisoned = {}

    def poisoned_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        baselines = self.param_dicts['supply_curve_learning']
        key = min(baselines)
        # keys are single element tuples, while the error names the technology itself
        poisoned['tech'] = key[0] if isinstance(key, tuple) else key
        baselines[key] = bad_value

    monkeypatch.setattr(param_data_module.ParamData, '__init__', poisoned_init)

    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    elec_config.capacity_expansion = True
    elec_config.expansion_learning_type = ExpansionLearningType.NONLINEAR

    with pytest.raises(ValueError) as caught:
        ElectricitySequencer().build_model(common_config, elec_config)

    message = str(caught.value)
    assert expected_message in message
    # The offending technology has to be identifiable, which is the point of catching it here.
    assert str(poisoned['tech']) in message


# Builds are in MW against a supply_curve_learning baseline of ~154,000 MW.  1 GW keeps the learning
# discount ~50x above pytest.approx's tolerance, the scale the characterization test uses.
_BUILD_MW = 1000.0


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


def test_nonlinear_learning_objective_characterization(nonlinear_model: PowerModel) -> None:
    """Record what the nonlinear expansion cost expression evaluates to today.

    This is a **characterization** test, not an acceptance test: it pins current behavior so that
    refactoring the learning multiplier cannot change the formulation silently.  It does not assert
    that these values are correct, the provenance of ``supply_curve_learning`` is unresolved, so
    the numbers are not yet interpretable.

    Evaluates the expression at fixed build levels rather than solving, so it needs no nonlinear
    solver and runs wherever the model can be constructed.
    """
    model = nonlinear_model
    assert len(model.capacity_builds) == 48

    def cost_at(level: float) -> float:
        return _expansion_cost_with(model, dict.fromkeys(model.capacity_builds, level))

    # No builds means no expansion cost, whatever the multiplier does.
    assert cost_at(0.0) == pytest.approx(0.0, abs=1e-9)

    # builds are in MW; 1 and 2.5 GW keep the values captured before the move to MW units
    at_one = cost_at(1000.0)
    at_two_and_a_half = cost_at(2500.0)
    assert at_one == pytest.approx(133777599845.11543)
    assert at_two_and_a_half == pytest.approx(329888419799.9818)

    # Learning makes cost subadditive in cumulative builds: 2.5x the capacity costs less than 2.5x
    # the money.  That is the property the nonlinear mode exists to produce, so it is asserted
    # separately from the pinned values, which are only a snapshot.
    assert at_two_and_a_half < 2.5 * at_one


def test_nonlinear_build_does_not_discount_itself(nonlinear_model: PowerModel) -> None:
    """A build must be discounted by prior years' capacity, not by its own.

    The shared curve takes whatever cumulative quantity it is handed; this checks the objective
    hands it the right one.  First-year builds have no prior experience, so building only in the
    first year must cost exactly the undiscounted price.  Needs a build only, no solver.
    """
    model = nonlinear_model
    first_year = min(model.year)
    keys = [k for k in model.capacity_builds if k[3] == first_year]
    cost = _expansion_cost_with(model, dict.fromkeys(keys, _BUILD_MW))
    undiscounted = sum(_BUILD_MW * value(model.capital_cost_initial[k[:3]]) for k in keys)
    assert cost == pytest.approx(undiscounted)


def test_nonlinear_experience_pools_across_regions(nonlinear_model: PowerModel) -> None:
    """A later build is discounted by earlier builds of the same tech in *other* regions.

    Build in one region in the first year, then price a later-year build of the same technology in
    a different region.  If the cumulative term were region-local the later build would be charged
    the undiscounted price.  The closed form is asserted rather than an inequality, so a wrong
    aggregation cannot pass by discounting some other amount.  Needs a build only, no solver.
    """
    model = nonlinear_model
    tech, step = '1', 3
    source = ('7', tech, step, min(model.year))
    later = ('8', tech, step, max(model.year))
    assert {source, later} <= set(model.capacity_builds)
    source_cost = _BUILD_MW * value(model.capital_cost_initial[source[:3]])
    later_cost = _BUILD_MW * value(model.capital_cost_initial[later[:3]])

    # With no prior experience anywhere, the later build is charged undiscounted.
    assert _expansion_cost_with(model, {later: _BUILD_MW}) == pytest.approx(later_cost)

    pooled = _expansion_cost_with(model, {source: _BUILD_MW, later: _BUILD_MW})
    baseline = value(model.supply_curve_learning[tech])
    multiplier = ((baseline + _BUILD_MW) / baseline) ** -value(model.learning_rate[tech])
    # Guard: the discount must be resolvable at approx's tolerance, or this test proves nothing.
    assert pooled != pytest.approx(source_cost + later_cost)
    assert pooled == pytest.approx(source_cost + later_cost * multiplier)
