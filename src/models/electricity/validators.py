"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/2/26

Basic validators for pyomo (and other) objects

"""

import logging
import re
from typing import TYPE_CHECKING

from pyomo.core import ConcreteModel

from src.models.electricity.elec_config import ReserveType

if TYPE_CHECKING:
    from src.models.electricity.electricity_model import PowerModel

logger = logging.getLogger(__name__)


def tech_name_check(model: ConcreteModel, tech: str) -> bool:
    """Validate the tech name (letters + numbers only + underscore)."""
    if re.match(r'[a-zA-Z0-9_]+\Z', tech):
        return True
    logger.error(
        'tech name %s is not valid.  Only letters, numbers, and underscore are accepted', tech
    )
    return False


def reserve_procurement_check(model: ConcreteModel, idx: tuple) -> bool:
    """Validate the index uses the enumeration of ReserveType."""
    _, res_type, *_ = idx
    if not isinstance(res_type, ReserveType):
        logger.error(
            'received a bad reserve type %s in index %s.  '
            'Reserve type must be member of ReserveType enum.',
            res_type,
            idx,
        )
        return False
    return True


def reserve_tech_check(model: ConcreteModel, value: float, *idx) -> bool:
    """Validate entry for such that the ReserveType enum is used and value between 0 and 1."""
    res_type, *_ = idx
    if not isinstance(res_type, ReserveType):
        logger.error(
            'received a bad reserve type %s in index %s.  '
            'Reserve type must be member of ReserveType enum.',
            res_type,
            idx,
        )
        return False
    if not 0.0 <= value <= 1.0:
        logger.error(
            'value for reserve upper bound must be between 0 and 1. got: %.2f for index: %s',
            value,
            idx,
        )
        return False
    return True


# PowerModel must stay quoted: pyomo inspects this signature at runtime and, on py3.14, an
# unquoted TYPE_CHECKING-only name raises NameError there
def tech_hydro_seasonal_check(model: 'PowerModel', value: int, *idx) -> bool:  # noqa: UP037
    """Validate a seasonal-hydro supply curve step against the tech's declared steps.

    Parameters
    ----------
    model : PowerModel
        Model under construction; ``steps_for_tech`` must already be built.
    value : int
        Candidate step for the ``hydro_seasonal_steps`` member at ``idx``.
    *idx
        Index of the ``hydro_seasonal_steps`` member: ``(region, tech, year)``.

    Returns
    -------
    bool
        True if ``value`` is one of the steps declared for the tech.
    """
    _, tech, _ = idx
    # pyrefly: ignore[bad-index]  - pyomo Sets attached at runtime read as SetData
    valid_steps = model.steps_for_tech[tech]
    if not valid_steps:
        logger.error('No valid steps for tech: %s', tech)
        return False
    # pyrefly: ignore[not-iterable]  - pyomo Set membership is untyped
    if value not in valid_steps:
        logger.error('Step value %s is not valid for tech: %s', value, tech)
        return False
    return True
