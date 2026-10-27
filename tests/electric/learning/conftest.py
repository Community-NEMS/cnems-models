"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/16/26

Fixtures for the capacity-expansion learning tests.

``learning_config_set`` (the linear micro dataset) lives in ``tests/electric/conftest.py``
because non-learning tests also use it.
"""

from pathlib import Path

import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer


@pytest.fixture
def nonlinear_configs() -> tuple[CommonConfig, ElecConfig]:
    """Basic test configs with expansion, reserve margin and nonlinear learning enabled."""
    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    elec_config.capacity_expansion = True
    elec_config.reserve_margin_required = True
    elec_config.expansion_learning_type = ExpansionLearningType.NONLINEAR
    return common_config, elec_config


@pytest.fixture
def nonlinear_model(nonlinear_configs: tuple[CommonConfig, ElecConfig]) -> PowerModel:
    """Unsolved model built from ``nonlinear_configs``."""
    return ElectricitySequencer().build_model(*nonlinear_configs)
