"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/9/26

Which run-config section of a TOML each model reads, the config class built from it, and checks
across model configs.

"""

from collections.abc import Mapping

from src.common.common_config import ModelConfig
from src.common.models_modes import ModelType
from src.models.electricity.elec_config import ElecConfig
from src.models.hsm.hsm_config import HSMConfig
from src.models.magic.magic_model import MagicConfig
from src.models.natural_gas.constants import PRICE_DOLLAR_YEAR
from src.models.natural_gas.ng_config import NGConfig

# each model's config section in the run config, and the config class it builds.  MAGIC may run
# without a section; the others need theirs
CONFIG_SECTIONS: dict[ModelType, tuple[str, type[ModelConfig]]] = {
    ModelType.ELECTRICITY: ('elec_config', ElecConfig),
    ModelType.NATURAL_GAS: ('natural_gas', NGConfig),
    ModelType.HSM: ('hsm', HSMConfig),
    ModelType.MAGIC: ('magic_config', MagicConfig),
}
OPTIONAL_SECTIONS: frozenset[ModelType] = frozenset({ModelType.MAGIC})


def check_model_configs(configs: Mapping[ModelType, ModelConfig]) -> None:
    """Reject model configs that cannot run together.

    With C-HSM and C-NGMM:

    - C-NGMM takes no region filter:  C-HSM sends every domestic gas region, and C-NGMM's reader
      requires its own regions.
    - Both read the same C-NGMM inputs:  C-HSM's capacity is fitted to C-NGMM's cost tiers, and
      C-NGMM converts it to Q0 with them.
    - C-HSM's reference path is in the dollar year of C-NGMM's prices.
    - The well-level engine is off; it is not coupled.

    Parameters
    ----------
    configs : Mapping of ModelType to ModelConfig
        One config per selected model.

    Raises
    ------
    ValueError
        If C-HSM and C-NGMM break a rule above.
    """
    hsm_config = configs.get(ModelType.HSM)
    ng_config = configs.get(ModelType.NATURAL_GAS)
    if not isinstance(hsm_config, HSMConfig) or not isinstance(ng_config, NGConfig):
        return
    if ng_config.region_filter is not None:
        raise ValueError(
            'C-HSM sends every domestic gas region, so a run with C-HSM takes no '
            f'[natural_gas] region_filter; got {ng_config.region_filter}'
        )
    if hsm_config.ng_input_path.resolve() != ng_config.input_path.resolve():
        raise ValueError(
            f'[hsm] ng_input_path ({hsm_config.ng_input_path}) must be the folder C-NGMM reads, '
            f'[natural_gas] input_path ({ng_config.input_path})'
        )
    if hsm_config.coupling_reference_dollar_year != PRICE_DOLLAR_YEAR:
        raise ValueError(
            f'[hsm] coupling_reference_dollar_year is {hsm_config.coupling_reference_dollar_year}, '
            f"but C-NGMM's prices are in {PRICE_DOLLAR_YEAR} dollars"
        )
    if hsm_config.onshore_engine_file is not None:
        raise ValueError(
            'The well-level engine is not coupled; leave [hsm] onshore_engine_file out of a '
            'coupled run'
        )
