"""
Created as part of C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/10/26
"""

import tomllib
from enum import Enum, unique
from logging import getLogger
from pathlib import Path

from pydantic import ValidationError, model_validator

from definitions import PROJECT_ROOT
from src.common.common_config import ModelConfig

logger = getLogger(__name__)


# dev note: in the future, this may become a data element.  For now, it is needed to validate
#           current data
@unique
class ReserveType(Enum):
    """Operating reserve products the model can require."""

    SPINNING = 'spinning'
    REGULATION = 'regulation'
    FLEX = 'flex'

    def __lt__(self, other):
        """Compare to other reserve type."""
        return self.value < other.value


@unique
class ExpansionLearningType(Enum):
    """How capacity expansion costs respond to cumulative builds."""

    DISABLED = 'disabled'
    LINEAR = 'linear'
    NONLINEAR = 'nonlinear'


@unique
class LoadScaleMode(Enum):
    """Which scalar set is used to grow base-year load across model years."""

    END_USE = 'end_use'
    ANNUAL = 'annual'


@unique
class RPSRequirementType(Enum):
    """How a Regional Portfolio Standard requirement is enforced.

    BTM (Build To Minima) holds the group's in-region annual generation at or above the minimum
    share of the region's annual load (retail sales), building or redispatching as needed.  Only
    BTM is supported for now; other types are stubbed below.
    """

    BTM = 'BTM'
    # RLL = 'RLL'  # stub:  Retirement Lower Limit, not yet supported


class ElecConfig(ModelConfig):
    """Settings from the ``[elec_config]`` TOML section, controlling the electricity model."""

    input_path: Path
    region_filter: list[str] | None = None
    regional_exchange: bool
    capacity_expansion: bool
    expansion_learning_type: ExpansionLearningType
    reserve_margin_required: bool
    spinning_reserve_required: bool
    ramping_required: bool
    load_scale_mode: LoadScaleMode
    rps_required: bool = False

    # TODO:  Clean this up when we refactor the path to scenario data.
    #        We should be referencing the top-level data folder with
    #        input_path and expect /policy within
    @property
    def policy_path(self) -> Path:
        """The ``policy`` folder, a sibling of the ``input_path`` (``parameters``) folder."""
        return self.input_path.parent / 'policy'

    @model_validator(mode='after')
    def check_paths(self):
        """Resolve ``input_path`` against PROJECT_ROOT and check that it is a directory."""
        self.input_path = PROJECT_ROOT / self.input_path
        if not self.input_path.is_dir():
            raise ValueError(f'Input path {self.input_path} is not a directory')
        return self

    @model_validator(mode='after')
    def check_switch_logic(self):
        """Reject switch combinations the formulation cannot support."""
        # check for logic violations in switches
        # reserve margin requires capacity expansion
        if self.reserve_margin_required and not self.capacity_expansion:
            raise ValueError('reserve_margin_required requires capacity_expansion')

        # must have capacity expansion if learning is on
        if (
            self.expansion_learning_type is not ExpansionLearningType.DISABLED
            and not self.capacity_expansion
        ):
            raise ValueError(
                'expansion_learning_type (other than disabled) requires capacity_expansion'
            )

        # the RPS requirements table lives in policy_path
        if self.rps_required and not self.policy_path.is_dir():
            raise ValueError(f'rps_required requires a policy folder at {self.policy_path}')
        return self

    @classmethod
    def from_toml(cls, path: Path) -> ElecConfig:
        """Parse the ``[elec_config]`` section of ``path`` into an ``ElecConfig``."""
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
        config = ElecConfig.from_toml(Path(PROJECT_ROOT / 'run_configs/basic_elec_config.toml'))
    except ValidationError as e:
        print([t['msg'] for t in e.errors()])
        raise
    print(config)
    print(config.model_dump_json())
    config_other = ElecConfig.model_validate(config.model_dump(mode='json'))
    print(config_other)
    print(f'configs compare equally: {config == config_other}')
