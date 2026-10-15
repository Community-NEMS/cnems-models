"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/9/26

Which run-config section of a TOML each model reads, and the config class built from it.

"""

from src.common.common_config import ModelConfig
from src.common.models_modes import ModelType
from src.models.electricity.elec_config import ElecConfig
from src.models.magic.magic_model import MagicConfig
from src.models.natural_gas.ng_config import NGConfig

# each model's config section in the run config, and the config class it builds.  MAGIC may run
# without a section; the others need theirs
CONFIG_SECTIONS: dict[ModelType, tuple[str, type[ModelConfig]]] = {
    ModelType.ELECTRICITY: ('elec_config', ElecConfig),
    ModelType.NATURAL_GAS: ('natural_gas', NGConfig),
    ModelType.MAGIC: ('magic_config', MagicConfig),
}
OPTIONAL_SECTIONS: frozenset[ModelType] = frozenset({ModelType.MAGIC})
