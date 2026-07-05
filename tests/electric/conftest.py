"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/3/26

Common test fixtures

"""

from pathlib import Path

import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.runner import run_elec_model


@pytest.fixture
def config_set() -> tuple[CommonConfig, ElecConfig]:
    """build a CommonConfig and ElecConfig for testing"""
    config_path = Path(PROJECT_ROOT, 'tests/electric/basic_elec_config.toml')
    common_config, remainder = CommonConfig.from_toml(config_path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    return common_config, elec_config


@pytest.fixture
def unsolved_model(config_set) -> tuple[CommonConfig, ElecConfig, PowerModel]:
    """build an un-solved PowerModel for testing"""
    common_config, elec_config = config_set

    elec_model = run_elec_model(common_config, elec_config, solve=False)
    return common_config, elec_config, elec_model


@pytest.fixture
def solved_model(config_set) -> tuple[CommonConfig, ElecConfig, PowerModel]:
    """build a solved PowerModel for testing"""
    common_config, elec_config = config_set

    elec_model = run_elec_model(common_config, elec_config, solve=True)
    return common_config, elec_config, elec_model
