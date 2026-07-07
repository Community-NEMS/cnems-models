"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26
"""

import re
from logging import getLogger
from pathlib import Path

import tomllib
from pydantic import BaseModel, ValidationError, model_validator

from definitions import PROJECT_ROOT
from src.common.models_modes import ModelType, RunMode

logger = getLogger(__name__)


class CommonConfig(BaseModel):
    mode: RunMode
    models_to_run: list[ModelType]
    common_data_path: Path
    output_path: Path
    scenario_name: str
    temporal_resolution: str
    aggregate_years: bool
    aggregate_start_year: int | None
    summary_years: list[int]

    @model_validator(mode='after')
    def check_year_aggregation(self):
        if self.aggregate_years and self.aggregate_start_year is None:
            raise ValueError('aggregate_start_year must be set when aggregate_years is True')
        return self

    @model_validator(mode='after')
    def check_paths(self):
        self.output_path = PROJECT_ROOT / self.output_path
        if not self.output_path.is_dir():
            raise ValueError(f'Output path {self.output_path} is not a directory')
        return self

    @model_validator(mode='after')
    def check_scenario_name(self):
        if len(self.scenario_name) < 4:
            raise ValueError('scenario_name must be at least 4 characters long')
        if not re.match(r'^[a-zA-Z0-9_]+$', self.scenario_name):
            raise ValueError(
                'scenario_name must contain only alphanumeric characters and underscores'
            )
        return self

    @classmethod
    def from_toml(cls, path: Path) -> tuple['CommonConfig', dict]:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        try:
            config = CommonConfig(**data.pop('common'))
        except KeyError:
            logger.error('[common] section not found in TOML')
            raise
        except ValidationError as e:
            for error in e.errors():
                logger.error(error)
            raise
        return config, data


if __name__ == '__main__':
    config, data = CommonConfig.from_toml(PROJECT_ROOT / 'run_configs/meta_config.toml')
    print(config)
    print(data)
