"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/29/26

Tests for applying update packages to a built model through ``update_model``.

A driver that keeps its models between solves must end each update with the same optimization
problem a rebuild with the same packages would give, on the same instance, whatever was applied
before.
"""

from pathlib import Path

import pandas as pd
import pytest
from pyomo.environ import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    NGElectricalDemandPackage,
    NGPricePackage,
)
from src.models.electricity.constants import INITIAL_NG_PRICE
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.sequencer import NGSequencer


def _price_package(region_years: list[tuple[str, int]], price: float) -> NGPricePackage:
    """One gas price for every listed electricity ``(region, year)``."""
    return NGPricePackage(
        elements=pd.DataFrame(
            {NG_PRICE_VALUE: price},
            index=pd.MultiIndex.from_tuples(region_years, names=NG_PRICE_INDEX),
        ),
        source=ModelType.NATURAL_GAS,
    )


def test_electricity_update_and_kept_solver_match_a_rebuild() -> None:
    """An update in place gives a rebuild's prices, and the kept solver re-solves to its objective.

    The first package's cheap gas moves dispatch, so a kept solver that ignored the update would
    fail.  It is applied twice, then a narrower package must also undo it on the rows it leaves out.
    """
    common, remainder = CommonConfig.from_toml(
        Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    )
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    sequencer = ElectricitySequencer()
    model = sequencer.build_model(common, elec_config)
    # the two are added in dispatch_cost, so they must share an index
    assert set(model.ng_fuel_adj.index_set()) == set(model.supply_price.index_set())
    sequencer.solve_iteration()
    solver = sequencer._opt
    region_years = sorted({(key[0], key[3]) for key in model.ng_fuel_adj})
    full = _price_package(region_years, INITIAL_NG_PRICE - 3.0)
    part = _price_package(region_years[:1], INITIAL_NG_PRICE + 2.0)
    rebuilt = {id(package): ElectricitySequencer() for package in (full, part)}
    for package in (full, part):
        rebuilt[id(package)].build_model(common, elec_config, update_packages=[package])

    def assert_prices_match(package: NGPricePackage) -> None:
        target = rebuilt[id(package)].model
        for key in model.ng_fuel_adj:
            effective = value(model.supply_price[key]) + value(model.ng_fuel_adj[key])
            assert effective == pytest.approx(value(target.supply_price[key]), rel=1e-12)

    assert sequencer.update_model([full]) is model
    assert_prices_match(full)
    stale = value(model.total_cost)  # the old dispatch priced at the new fuel cost
    held = sequencer.solve_iteration().objective_value
    expected = rebuilt[id(full)].solve_iteration().objective_value
    assert sequencer._opt is solver
    assert held is not None and expected is not None
    assert held < stale * (1 - 1e-4)
    assert held == pytest.approx(expected, rel=1e-9)

    for package in (full, part):
        assert sequencer.update_model([package]) is model
        assert_prices_match(package)


def test_gas_update_and_kept_solver_match_a_rebuild() -> None:
    """With every power-sector cell supplied, demand and the re-solve match a rebuild's."""
    common, remainder = CommonConfig.from_toml(
        Path(PROJECT_ROOT, 'tests/natural_gas/basic_ng_config.toml')
    )
    ng_config = NGConfig(**remainder.pop('natural_gas'))
    sequencer = NGSequencer()
    model = sequencer.build_model(common, ng_config)
    sequencer.solve_iteration()
    solver = sequencer._opt
    cells = [(str(r), int(y)) for r in model.region_analyze for y in model.year]
    for region, year in cells:
        model.declare_external(model.DEMAND, region, 'electric_power', year)
    burn = [1.3 * value(model.demand[r, 'electric_power', y]) for r, y in cells]
    # a region the model does not hold is dropped, as a rebuild ignores it
    package = NGElectricalDemandPackage(
        elements=pd.DataFrame(
            {NG_ELEC_DEMAND_VALUE: burn + [5.0]},
            index=pd.MultiIndex.from_tuples(
                cells + [('not_a_region', cells[0][1])], names=NG_ELEC_DEMAND_INDEX
            ),
        ),
        source=ModelType.ELECTRICITY,
    )
    rebuilt = NGSequencer()
    rebuilt.build_model(common, ng_config, update_packages=[package])

    for _ in range(2):
        assert sequencer.update_model([package]) is model
        for key in model.demand:
            assert value(model.demand[key]) == pytest.approx(
                value(rebuilt.model.demand[key]), rel=1e-12
            )
    held = sequencer.solve_iteration().objective_value
    expected = rebuilt.solve_iteration().objective_value
    assert sequencer._opt is solver
    assert held is not None and expected is not None
    assert held == pytest.approx(expected, rel=1e-6)
