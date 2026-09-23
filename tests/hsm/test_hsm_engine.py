"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

Tests for the optional well-level engine in src/models/hsm/us_onshore.py (OnshoreEngine). The engine
is off in the basic config; these tests build it from input/hsm/us_onshore_engine.csv.

The fast tests simulate a few years directly, which takes a few seconds. The full 2023-2050 run
takes about two minutes, so it only runs when the environment variable CHSM_ENGINE_FULL_RUN is set
to 1. See src/models/hsm/ENGINE_PLAN.md for what the engine still needs before its results are used.

dev notes:
1.  The captured values (3,181 continuous projects, 972 result rows) come from the shipped decks
    and settings. They move if either changes on purpose.
2.  The full run checks the national level against C-NGMM's supply anchors with a loose 3.5% band,
    because the shipped engine calibration is approximate (ENGINE_PLAN.md, step 3).
"""

import math
import os
from pathlib import Path

import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.hsm.data import load_henry_hub_path, load_ng_supply_side
from src.models.hsm.hsm_config import HSMConfig
from src.models.hsm.hsm_model import HSMModel
from src.models.hsm.us_onshore import OnshoreEngine, read_engine_settings
from src.models.natural_gas.data import load_supply_anchors, load_supply_cost_tiers

INPUT = Path(PROJECT_ROOT, 'input/hsm')
CONFIG_PATH = Path(PROJECT_ROOT, 'tests/hsm/basic_hsm_config.toml')
ENGINE_SETTINGS = {
    'onshore_engine_file': 'us_onshore_engine.csv',
    'calibration_file': 'us_gas_calibration_engine.csv',
}


@pytest.fixture(scope='module')
def engine() -> OnshoreEngine:
    """The engine built from the shipped decks and settings."""
    return OnshoreEngine(
        onshore_path=str(INPUT / 'onshore'),
        mapping_path=str(INPUT / 'mapping.csv'),
        engine_config_path=str(INPUT / 'us_onshore_engine.csv'),
    )


@pytest.fixture(scope='module')
def regions() -> list[str]:
    """The nine supply regions, from C-NGMM."""
    return load_ng_supply_side(Path(PROJECT_ROOT, 'input/natural_gas'))[0]


@pytest.fixture(scope='module')
def hsm_config() -> HSMConfig:
    """The test config with the engine switched on."""
    _, remainder = CommonConfig.from_toml(CONFIG_PATH)
    return HSMConfig(**remainder.pop('hsm'), **ENGINE_SETTINGS)


def _national(engine, regions, gas_price, oil_price=None, last_year=2027) -> pd.DataFrame:
    """National gas (BCF) and crude (thousand bbl) by gas type in ``last_year``, flat prices."""
    years = range(2024, last_year + 1)
    gas = {(r, y): gas_price for r in regions for y in years}
    oil = {(r, y): oil_price for r in regions for y in years} if oil_price else {}
    sim = engine.simulate(gas, oil, 2024, last_year)
    return sim[sim.year == last_year].groupby('gas_type')[['gas_bcf', 'crude_mbbl']].sum()


class TestEngineBuild:
    """The engine loads the decks and simulates sensible numbers."""

    def test_loads_continuous_projects(self, engine: OnshoreEngine) -> None:
        """All continuous projects are loaded, each with a region and a gas type."""
        assert len(engine._cont) == 3181
        assert engine._cont['region'].notna().all()
        assert set(engine._cont['gas_type']) == {'na', 'ad'}

    def test_simulate_shape(self, engine: OnshoreEngine, regions: list[str]) -> None:
        """A short simulation gives finite, non-negative gas and crude for both gas types."""
        gas = {(r, y): 3.5 for r in regions for y in range(2024, 2027)}
        sim = engine.simulate(gas, {}, 2024, 2026)
        assert list(sim.columns) == ['region', 'gas_type', 'year', 'gas_bcf', 'crude_mbbl']
        assert set(sim['year']) == {2024, 2025, 2026}
        assert set(sim['gas_type']) == {'na', 'ad'}
        values = sim[['gas_bcf', 'crude_mbbl']].to_numpy().ravel()
        assert all(math.isfinite(v) and v >= 0 for v in values)

    def test_empty_range(self, engine: OnshoreEngine) -> None:
        """Years before the engine's first year give an empty table with the right columns."""
        sim = engine.simulate({}, {}, 2024, 2023)
        assert sim.empty
        assert list(sim.columns) == ['region', 'gas_type', 'year', 'gas_bcf', 'crude_mbbl']


class TestEngineResponse:
    """Drilling, and so production, moves the right way with prices."""

    def test_gas_price(self, engine: OnshoreEngine, regions: list[str]) -> None:
        """Non-associated gas in 2027 rises with the gas price (2023 $/MMBtu)."""
        low, mid, high = (_national(engine, regions, p) for p in (1.0, 3.5, 5.0))
        assert low.loc['na', 'gas_bcf'] < mid.loc['na', 'gas_bcf'] < high.loc['na', 'gas_bcf']

    def test_oil_price(self, engine: OnshoreEngine, regions: list[str]) -> None:
        """Associated gas and crude in 2027 rise with the oil price (2023 $/bbl)."""
        low = _national(engine, regions, 3.5, oil_price=60.0)
        high = _national(engine, regions, 3.5, oil_price=95.0)
        assert low.loc['ad', 'gas_bcf'] < high.loc['ad', 'gas_bcf']
        assert low['crude_mbbl'].sum() < high['crude_mbbl'].sum()


class TestEngineSwitch:
    """The config decides whether the engine runs."""

    def test_off_by_default(self, tmp_path: Path) -> None:
        """The basic config runs the reduced form."""
        _, remainder = CommonConfig.from_toml(CONFIG_PATH)
        config = HSMConfig(**remainder.pop('hsm'))
        regions, tiers = load_ng_supply_side(config.ng_input_path)
        prices = load_henry_hub_path(config.input_path / config.henry_hub_file)
        model = HSMModel(config, regions, tiers, prices, output_path=tmp_path)
        assert model.module._onshore_engine is None

    def test_on_when_configured(self, hsm_config: HSMConfig, tmp_path: Path) -> None:
        """Naming the settings file switches the engine on."""
        regions, tiers = load_ng_supply_side(hsm_config.ng_input_path)
        prices = load_henry_hub_path(hsm_config.input_path / hsm_config.henry_hub_file)
        model = HSMModel(hsm_config, regions, tiers, prices, output_path=tmp_path)
        assert isinstance(model.module._onshore_engine, OnshoreEngine)

    @pytest.mark.parametrize(
        'change,message',
        [
            (('discount_rate,0.10', 'discount_rte,0.10'), 'unknown settings'),
            (('discount_rate,0.10\n', ''), 'missing settings'),
            (('discount_rate,0.10', 'discount_rate,'), 'non-numeric values'),
            (('discount_rate,0.10', 'discount_rate,0.10\ndiscount_rate,0.12'), 'repeated settings'),
        ],
        ids=['misspelled', 'missing', 'blank', 'repeated'],
    )
    def test_bad_settings_are_rejected(
        self, tmp_path: Path, change: tuple[str, str], message: str
    ) -> None:
        """A misspelled, missing, blank or repeated setting stops the engine from building.

        Parameters
        ----------
        tmp_path : Path
            Temporary folder for the edited settings file.
        change : tuple[str, str]
            Text in the shipped settings file and what to replace it with.
        message : str
            Text the error must contain.
        """
        text = (INPUT / 'us_onshore_engine.csv').read_text(encoding='utf-8')
        assert change[0] in text
        path = tmp_path / 'settings.csv'
        path.write_text(text.replace(change[0], change[1], 1), encoding='utf-8')
        with pytest.raises(ValueError, match=message):
            read_engine_settings(str(path))

    def test_missing_settings_file_is_rejected(self) -> None:
        """A settings file that does not exist stops the run at the config."""
        with pytest.raises(ValueError, match='not found'):
            HSMConfig(
                input_path='input/hsm',
                ng_input_path='input/natural_gas',
                onshore_engine_file='no_such_file.csv',
            )


@pytest.mark.skipif(
    os.environ.get('CHSM_ENGINE_FULL_RUN') != '1',
    reason='full engine run takes about two minutes; set CHSM_ENGINE_FULL_RUN=1 to run it',
)
def test_full_engine_run(hsm_config: HSMConfig, tmp_path: Path) -> None:
    """A full 2023-2050 engine run gives complete results near C-NGMM's national anchors."""
    regions, tiers = load_ng_supply_side(hsm_config.ng_input_path)
    prices = load_henry_hub_path(hsm_config.input_path / hsm_config.henry_hub_file)
    model = HSMModel(hsm_config, regions, tiers, prices, output_path=tmp_path)
    model.run()
    results = model.poll_us_natgas_capacity()
    assert len(results) == 972
    assert all(math.isfinite(v) and v >= 0 for v in results.values())

    anchors = load_supply_anchors(hsm_config.ng_input_path)
    cost_tiers = load_supply_cost_tiers(hsm_config.ng_input_path)
    for year in range(2025, 2051, 5):
        engine_total = sum(v for k, v in results.items() if k[-1] == year)
        anchor_total = sum(
            sum(c for c, _ in cost_tiers[r]) * anchors.get((r, year), (1.0, 1.0))[0]
            for r in regions
        )
        assert engine_total == pytest.approx(anchor_total, rel=0.035), f'{year}'
