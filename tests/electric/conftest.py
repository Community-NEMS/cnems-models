"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/3/26

Common test fixtures

"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer
from tests.electric.mini_model import MiniModelData, mini_configs, patch_loaders


@pytest.fixture
def config_set() -> tuple[CommonConfig, ElecConfig]:
    """Build a CommonConfig and ElecConfig for testing."""
    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    return common_config, elec_config


@pytest.fixture
def learning_config_set() -> tuple[CommonConfig, ElecConfig]:
    """Build a (CommonConfig, ElecConfig) pair for the linear-learning micro dataset.

    Points at ``tests/electric/learning/linear_micro_data`` (single region 'CA', single tech
    'NG_Fired_Plant') with capacity expansion + linear learning enabled.
    """
    config_path = Path(
        PROJECT_ROOT, 'tests/electric/learning/linear_micro_data/linear_learning_config.toml'
    )
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    return common_config, elec_config


@pytest.fixture
def unsolved_model(config_set) -> tuple[CommonConfig, ElecConfig, PowerModel]:
    """Build an un-solved PowerModel for testing."""
    common_config, elec_config = config_set

    sequencer = ElectricitySequencer()
    elec_model = sequencer.build_model(common_config, elec_config)
    return common_config, elec_config, elec_model


@pytest.fixture
def solved_model(config_set) -> tuple[CommonConfig, ElecConfig, PowerModel]:
    """Build a solved PowerModel for testing."""
    common_config, elec_config = config_set

    sequencer = ElectricitySequencer()
    elec_model = sequencer.build_model(common_config, elec_config)
    sequencer.solve_model()
    return common_config, elec_config, elec_model


@pytest.fixture
def mini_data() -> MiniModelData:
    """A fresh copy of the bare mini model data (see ``tests/electric/mini_model.py``).

    Mutate it with the ``MiniModelData`` helpers before calling ``build_mini``.
    """
    return MiniModelData.default()


@pytest.fixture
def build_mini(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mini_data: MiniModelData
) -> Callable[..., tuple[ElectricitySequencer, PowerModel]]:
    """Factory building a PowerModel from the current state of ``mini_data``.

    The model goes through the real ``ElectricitySequencer.build_model`` (so ``validate_all``
    runs, strict).  Keyword arguments are ``ElecConfig`` overrides, e.g.
    ``build_mini(capacity_expansion=True)``; all switches default to off.
    """
    patch_loaders(monkeypatch, mini_data)

    def _build(**elec_switches: Any) -> tuple[ElectricitySequencer, PowerModel]:
        common_config, elec_config = mini_configs(tmp_path, mini_data.years, **elec_switches)
        sequencer = ElectricitySequencer()
        model = sequencer.build_model(common_config, elec_config)
        return sequencer, model

    return _build
