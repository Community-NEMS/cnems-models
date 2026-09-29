"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/29/26

Tests for external ownership of gas model demand.

When another model supplies a demand cell, the gas model's price response must stop recomputing
it. Without a declaration the response rebuilds every sector from the construction-time baseline
and overwrites the supplied value.
"""

from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import GI, NGModel
from src.models.natural_gas.sequencer import NGSequencer


@pytest.fixture
def ng_model() -> NGModel:
    """A freshly built, unsolved NGModel from the standard gas test config."""
    config_path = Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    ng_config = NGConfig(**remainder.pop('natural_gas'))
    return NGSequencer().build_model(common_config, ng_config)


def _flat_prices(model: NGModel, price: float) -> dict[GI, float]:
    """Uniform price over every region and year the model carries."""
    return {GI(region=r, year=y): price for r in model.region_analyze for y in model.year}


def test_owned_cell_is_left_out_of_the_price_response(ng_model: NGModel) -> None:
    """A declared cell never enters the price target, while its neighbours still respond."""
    regions = sorted(ng_model.region_analyze)
    year = next(iter(ng_model.year))
    ng_model.set_reference_prices(_flat_prices(ng_model, 3.0))
    ng_model.declare_external(ng_model.DEMAND, regions[0], 'electric_power', year)

    target = ng_model.calculate_demand_from_price(_flat_prices(ng_model, 4.5))

    assert (regions[0], 'electric_power', year) not in target
    assert (regions[0], 'industrial', year) in target, 'other sectors must still respond'
    assert (regions[1], 'electric_power', year) in target, 'other regions must still respond'


def test_supplied_value_survives_the_price_response(ng_model: NGModel) -> None:
    """A declared cell written through the exact setter is untouched by a relaxed price update."""
    region = next(iter(ng_model.region_analyze))
    year = next(iter(ng_model.year))
    supplied = 1234.5
    ng_model.set_reference_prices(_flat_prices(ng_model, 3.0))
    ng_model.declare_external(ng_model.DEMAND, region, 'electric_power', year)

    ng_model.set_external_demand({GI(region=region, year=year): supplied})
    ng_model.update_demand_from_price(_flat_prices(ng_model, 4.5), alpha=0.5)

    assert value(ng_model.demand[region, 'electric_power', year]) == pytest.approx(supplied)
