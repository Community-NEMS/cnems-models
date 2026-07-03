"""
Created as part of C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/10/26
"""

from enum import Enum, unique
from logging import getLogger
from pathlib import Path

import tomllib
from pydantic import BaseModel, ValidationError, model_validator

from definitions import PROJECT_ROOT

logger = getLogger(__name__)


# dev note: in the future, this may become a data element.  For now, it is needed to validate
#           current data
@unique
class ReserveType(Enum):
    SPINNING = 'spinning'
    REGULATION = 'regulation'
    FLEX = 'flex'


@unique
class ExpansionLearningType(Enum):
    DISABLED = 'disabled'
    LINEAR = 'linear'
    NONLINEAR = 'nonlinear'


@unique
class LoadScaleMode(Enum):
    END_USE = 'end_use'
    ANNUAL = 'annual'


class ElecConfig(BaseModel):
    input_path: Path
    output_path: Path
    region_filter: list[str]
    regional_exchange: bool
    capacity_expansion: bool
    expansion_learning_type: ExpansionLearningType
    reserve_margin_required: bool
    spinning_reserve_required: bool
    ramping_required: bool
    load_scale_mode: LoadScaleMode

    @model_validator(mode='after')
    def check_paths(self):
        self.input_path = PROJECT_ROOT / self.input_path
        self.output_path = PROJECT_ROOT / self.output_path
        if not self.input_path.is_dir():
            raise ValueError(f'Input path {self.input_path} is not a directory')
        if not self.output_path.is_dir():
            raise ValueError(f'Output path {self.output_path} is not a directory')
        return self

    @model_validator(mode='after')
    def check_switch_logic(self):
        # check for logic violations in switches
        # reserve margin requires capacity expansion
        if self.reserve_margin_required and not self.capacity_expansion:
            raise ValueError('reserve_margin_required requires capacity_expansion')

        # must have capacity expansion if learning is on
        if (
            self.expansion_learning_type is not ExpansionLearningType.DISABLED
            and not self.capacity_expansion
        ):
            raise ValueError('expansion_learning_type requires capacity_expansion')
        return self

    @classmethod
    def from_toml(cls, path: Path) -> 'ElecConfig':
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        try:
            config = ElecConfig(**data['elec_config'])
        except KeyError:
            logger.error('[elec_config] section not found in TOML')
            raise
        except ValidationError as e:
            for error in e.errors():
                # TODO:  This could be prettier in output
                logger.error(error)
            raise
        return config

    # @classmethod
    # def from_meta_config(cls, meta_config: 'MetaConfig') -> 'ElecConfig':
    #     return cls.from_toml(**meta_config.elec_config)
    #


# some simple testing...
if __name__ == '__main__':
    try:
        config = ElecConfig.from_toml(Path(PROJECT_ROOT / 'run_configs/sample_elec_config.toml'))
    except ValidationError as e:
        print([t['msg'] for t in e.errors()])
        raise
    print(config)
    print(config.model_dump_json())
    config_other = ElecConfig.model_validate(config.model_dump(mode='json'))
    print(config_other)
    print(f'configs compare equally: {config == config_other}')
