"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/29/26

Tests for the coupled footprint preflight.

The test configs are partial on purpose. Electricity regions 7, 8 and 9 cover all of new_england,
part of middle_atlantic and none of the other seven gas divisions, and the gas model carries years
the electricity model does not.
"""

from pathlib import Path

import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.integrator.ng_preflight import SECTOR, PreflightReport, build_preflight
from src.models.electricity.constants import NG_PRICE_LINKED_TECHS
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import NGModel
from src.models.natural_gas.sequencer import NGSequencer

# population share of middle_atlantic in electricity regions 8 and 9
MIDDLE_ATLANTIC_COVERAGE = 0.2995 + 0.1759


@pytest.fixture(scope='module')
def elec_model() -> PowerModel:
    """Built electricity model on regions 7, 8 and 9. The preflight only reads it."""
    common, remainder = CommonConfig.from_toml(
        Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    )
    return ElectricitySequencer().build_model(common, ElecConfig(**remainder.pop('elec_config')))


@pytest.fixture(scope='module')
def ng_model() -> NGModel:
    """Built gas model on all divisions. The preflight only reads it."""
    common, remainder = CommonConfig.from_toml(
        Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    )
    return NGSequencer().build_model(common, NGConfig(**remainder.pop('natural_gas')))


def test_partial_coverage_is_refused_by_default(elec_model: PowerModel, ng_model: NGModel) -> None:
    """Every kind of shortfall in the test configs is named, and nothing is returned."""
    with pytest.raises(ValueError, match='coverage is partial') as exc:
        build_preflight(elec_model, ng_model)
    message = str(exc.value)
    assert 'middle_atlantic 0.48' in message
    assert 'not covered at all' in message
    assert 'no electricity counterpart' in message


def test_partly_covered_regions_are_topped_up(elec_model: PowerModel, ng_model: NGModel) -> None:
    """A partly covered division is handed over and topped up; an uncovered one is left alone."""
    pre: PreflightReport = build_preflight(elec_model, ng_model, allow_partial_coverage=True)
    coverage = dict(pre.ng_coverage)
    assert coverage['new_england'] == pytest.approx(1.0)
    assert coverage['middle_atlantic'] == pytest.approx(MIDDLE_ATLANTIC_COVERAGE)
    assert pre.ng_regions_reached == {'new_england', 'middle_atlantic'}
    assert pre.ownership_cells == {
        (ng_model.DEMAND, g, SECTOR, y) for g in pre.ng_regions_reached for y in pre.years
    }

    topup = dict(pre.topup_by_cell)
    assert {region for region, _ in topup} == {'middle_atlantic'}
    for y in pre.years:
        projection = ng_model._base_demand[('middle_atlantic', SECTOR, y)]
        assert topup[('middle_atlantic', y)] == pytest.approx(
            (1.0 - MIDDLE_ATLANTIC_COVERAGE) * projection
        )

    # owned cells leave the price response; an uncovered division's cells stay in it
    y = pre.years[0]
    assert ('middle_atlantic', SECTOR, y) not in pre.internal_demand_keys
    assert ('pacific', SECTOR, y) in pre.internal_demand_keys

    # the adjustment entries come from the electricity index, gas technologies only
    assert pre.fuel_adj_keys
    assert {key[1] for key in pre.fuel_adj_keys} <= set(NG_PRICE_LINKED_TECHS)
