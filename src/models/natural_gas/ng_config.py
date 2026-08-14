"""
Created as part of C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/14/26
"""

import tomllib
from logging import getLogger
from pathlib import Path

from pydantic import BaseModel, ValidationError, model_validator

from definitions import PROJECT_ROOT
from src.models.natural_gas.data import REGIONS

logger = getLogger(__name__)


class NGConfig(BaseModel):
    """Settings from the ``[ng_config]`` TOML section, controlling the Natural Gas model."""

    input_path: Path
    region_filter: list[str] | None = None

    @model_validator(mode='after')
    def check_paths(self):
        """Resolve ``input_path`` against PROJECT_ROOT and check that it is a directory."""
        self.input_path = PROJECT_ROOT / self.input_path
        if not self.input_path.is_dir():
            raise ValueError(f'Input path {self.input_path} is not a directory')
        return self

    @model_validator(mode='after')
    def check_region_filter(self):
        if self.region_filter is not None and len(self.region_filter) < len(REGIONS):
            logger.warning(
                'REGION SUBSET (%d of %d): results are NOT comparable to a full run, dropped '
                'regions take their production, demand, and trade with them. For mechanics and '
                'timing tests only.',
                len(self.region_filter),
                len(REGIONS),
            )
        return self

    @classmethod
    def from_toml(cls, path: Path) -> NGConfig:
        """Parse the ``[ng_config]`` section of ``path`` into an ``NGConfig``."""
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        try:
            config = NGConfig(**data['natural_gas'])
        except KeyError:
            logger.error('[ng_config] section not found in TOML')
            raise
        except ValidationError as e:
            for error in e.errors():
                # TODO:  This could be prettier in output
                logger.error(error)
            raise
        logger.info(f'Created NGConfig object from TOML file: {path}')
        return config


# some simple testing...
if __name__ == '__main__':
    try:
        config = NGConfig.from_toml(Path(PROJECT_ROOT / 'run_configs/basic_ng_config.toml'))
    except ValidationError as e:
        print([t['msg'] for t in e.errors()])
        raise
    print(config)
    print(config.model_dump_json())
    config_other = NGConfig.model_validate(config.model_dump(mode='json'))
    print(config_other)
    print(f'configs compare equally: {config == config_other}')
