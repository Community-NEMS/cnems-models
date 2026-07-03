"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26
"""

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
    temporal_resolution: str
    aggregate_years: bool
    aggregate_start_year: int | None
    summary_years: list[int]

    @model_validator(mode='after')
    def check_year_aggregation(self):
        if self.aggregate_years and self.aggregate_start_year is None:
            raise ValueError('aggregate_start_year must be set when aggregate_years is True')
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
