"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26
"""

import json
import re
import tomllib
from logging import getLogger
from pathlib import Path

from pydantic import BaseModel, ValidationError, model_validator

from definitions import PROJECT_ROOT
from src.common.models_modes import ModelType, RunMode

logger = getLogger(__name__)


class CommonConfig(BaseModel):
    mode: RunMode
    models_to_run: list[ModelType]
    common_data_path: Path
    residential_data_path: Path
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
        # the output root is not tracked in git, so create it on demand (fresh clones/CI)
        try:
            self.output_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # includes FileExistsError when the path is a non-directory
            raise ValueError(f'Output path {self.output_path} is not a usable directory: {e}')
        self.residential_data_path = PROJECT_ROOT / self.residential_data_path
        if not self.residential_data_path.is_dir():
            raise ValueError(
                f'Residential data path {self.residential_data_path} is not a directory'
            )
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


def parse_config_file(path_to_config: Path) -> tuple[CommonConfig, dict]:
    """
    Parse a config file on the path given.

    Renders the "common" config and retains the remainder for subsequent parsing as needed by
    individual modules. Supports both TOML (`.toml`) and JSON (`.json`) sources; JSON is used by
    the GUI's config editor, which persists edited configs as a single combined JSON file.

    Parameters
    ----------
    path_to_config : Path
        Path to the top-level config file, either TOML or JSON.

    Returns
    -------
    tuple[CommonConfig, dict]
        The parsed common config and the remaining (unparsed) sections.
    """
    if path_to_config.suffix == '.json':
        with open(path_to_config) as f:
            data = json.load(f)
        try:
            common_config = CommonConfig.model_validate(data.pop('common'))
        except KeyError:
            logger.error('"common" section not found in JSON config')
            raise
        except ValidationError as e:
            for error in e.errors():
                logger.error(error)
            raise
        return common_config, data

    common_config, remainder = CommonConfig.from_toml(path_to_config)

    return common_config, remainder
