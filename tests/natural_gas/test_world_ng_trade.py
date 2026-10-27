"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/27/26

Smoke tests for the world-trade scenario (``input/natural_gas_world_trade``):  Canada and Mexico
as pipeline partners and ``others`` as the world LNG market, with non-zero fixed pipeline volumes.
These pin that the scenario builds, solves and carries its trade through, not its results; its
volumes are rough placeholders (see that folder's ``ng_data_pedigree.md``).

Dev Note:  This file is DEVELOPMENTAL and may be removed/updated.  It is just a starter on
           evaluating trade mechanics
"""

import logging
import math
from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.models.natural_gas.data import interp_by_year, load_all, load_pipeline_trade
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import TI, NGModel
from src.models.natural_gas.postprocessor import _extract_balance
from src.models.natural_gas.sequencer import NGSequencer

CONFIG_PATH = Path(PROJECT_ROOT, 'tests/natural_gas/world_ng_trade_config.toml')
SOLVER = 'highs'


@pytest.fixture(scope='module')
def world_config_set() -> tuple[CommonConfig, NGConfig]:
    """The ``(CommonConfig, NGConfig)`` pair for the world-trade scenario."""
    common_config, remainder = CommonConfig.from_toml(CONFIG_PATH)
    return common_config, NGConfig(**remainder.pop('natural_gas'))


@pytest.fixture(scope='module')
def solved_world(
    world_config_set: tuple[CommonConfig, NGConfig],
) -> tuple[IterationStatus, NGModel]:
    """The world-trade model, built and solved once for the module."""
    sequencer = NGSequencer()
    model = sequencer.build_model(*world_config_set)
    _, status = sequencer.solve_model(solver_name=SOLVER)
    return status, model


# Dev Note:  this is the MAIN reason for this file:
def test_builds_and_solves(solved_world: tuple[IterationStatus, NGModel]) -> None:
    """The scenario solves to a usable (or penalty-priced) status."""
    status, _ = solved_world
    assert status in {IterationStatus.USABLE, IterationStatus.BEST, IterationStatus.PENALTY}, (
        f'solve failed with status {status}'
    )


def test_partners_from_region_file(solved_world: tuple[IterationStatus, NGModel]) -> None:
    """The partners are those the region file flags international, Mexico included."""
    _, model = solved_world
    assert set(model.region_int) == {'canada', 'mexico', 'others'}
    assert {i for _, i in model.pipe_trade_pairs} == {'canada', 'mexico'}
    assert {i for _, i in model.lng_export_pairs} == {'others'}
    assert ('west_south_central', 'mexico') in set(model.pipe_trade_pairs)


@pytest.mark.parametrize('year', [2025, 2040])
def test_balance_carries_pipeline_trade(
    solved_world: tuple[IterationStatus, NGModel],
    world_config_set: tuple[CommonConfig, NGConfig],
    year: int,
) -> None:
    """The balance table's pipeline totals match the CSV, interpolated where needed."""
    _, model = solved_world
    imports, exports = load_pipeline_trade(world_config_set[1].input_path / 'parameters')
    balance = _extract_balance(model)
    in_year = balance[balance['year'] == year]

    assert in_year['pipe_import_bcf'].sum() == pytest.approx(
        sum(interp_by_year(imports, key, year) for key in imports)
    )
    assert in_year['pipe_export_bcf'].sum() == pytest.approx(
        sum(interp_by_year(exports, key, year) for key in exports)
    )
    assert in_year['pipe_export_bcf'].sum() > 0.0


WSC_MEXICO = TI('west_south_central', 'mexico', 2030)


@pytest.fixture(scope='module')
def updated_world(world_config_set: tuple[CommonConfig, NGConfig]) -> NGModel:
    """The world-trade model, built once, with the WSC-Mexico 2030 export set to 1234.5."""
    model = NGSequencer().build_model(*world_config_set)
    model.update_pipeline_trade(exports={WSC_MEXICO: 1234.5})
    return model


def test_update_pipeline_trade(updated_world: NGModel) -> None:
    """A volume round-trips through the update."""
    ti = WSC_MEXICO
    assert value(updated_world.pipe_export[ti.region, ti.partner, ti.year]) == pytest.approx(1234.5)


@pytest.mark.parametrize(
    'kwargs',
    [
        {'exports': {WSC_MEXICO: -1.0}},
        {'exports': {WSC_MEXICO: math.nan}},
        {'imports': {TI('west_south_central', 'canada', 2030): 10.0}},
        {'imports': {TI('pacific', 'canada', 2031): 10.0}},
    ],
    ids=['negative', 'nan', 'uncarried pair', 'non-analysis year'],
)
def test_update_pipeline_trade_rejects(updated_world: NGModel, kwargs: dict) -> None:
    """A bad entry raises and changes nothing."""
    with pytest.raises(ValueError, match='update_pipeline_trade rejected'):
        updated_world.update_pipeline_trade(**kwargs)
    ti = WSC_MEXICO
    assert value(updated_world.pipe_export[ti.region, ti.partner, ti.year]) == pytest.approx(1234.5)


def test_unknown_partner_dropped_with_warning(
    world_config_set: tuple[CommonConfig, NGConfig], caplog: pytest.LogCaptureFixture
) -> None:
    """With validation off, a pair naming an unknown partner is dropped loudly, not silently."""
    common_config, ng_config = world_config_set
    data = load_all(common_config=common_config, ng_config=ng_config)
    typo = ('west_south_central', 'mexcio')
    data['pipe_trade_import'][typo] = {2025: 0.0}
    data['pipe_trade_export'][typo] = {2025: 1900.0}

    with caplog.at_level(logging.WARNING, logger='src.models.natural_gas.ng_model'):
        model = NGModel(model_data=data, common_config=common_config, ng_config=ng_config)

    assert typo not in set(model.pipe_trade_pairs)
    assert any('mexcio' in r.getMessage() for r in caplog.records)
