"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Settings from the ``[cdm]`` TOML section, controlling the Commercial Demand model (C-CDM).
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from definitions import PROJECT_ROOT
from src.common.common_config import ModelConfig


class CDMConfig(ModelConfig):
    """Settings from the ``[cdm]`` TOML section.

    Attributes
    ----------
    input_path : Path
        Folder of C-CDM input tables, relative to the repository root.
    case : str
        AEO2026 case whose inputs and results C-CDM follows: ``reference`` (the Counterfactual
        Baseline) or ``high_electricity_demand``.
    electricity_price_scale, natural_gas_price_scale : float
        Multipliers on the reference price path for a standalone price scenario; 1 runs at the
        reference prices, where C-CDM reproduces the AEO2026 end-use rows it scales to.
    """

    input_path: Path
    case: Literal['reference', 'high_electricity_demand'] = 'reference'
    electricity_price_scale: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    natural_gas_price_scale: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)

    @model_validator(mode='after')
    def check_paths(self) -> CDMConfig:
        """Resolve ``input_path`` against PROJECT_ROOT and check that it is a directory."""
        self.input_path = PROJECT_ROOT / self.input_path
        if not self.input_path.is_dir():
            raise ValueError(f'Input path {self.input_path} is not a directory')
        return self
