"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/2/26

Basic validators for pyomo (and other) objects

"""

import logging
import re

from pyomo.core import ConcreteModel

from src.models.electricity.elec_config import ReserveType

logger = logging.getLogger(__name__)


def region_check(model: ConcreteModel, region: str) -> bool:
    """Validate the region name (letters + numbers only + underscore)."""
    # screen against illegal names
    illegal_region_names = {'global', 'all'}
    if region in illegal_region_names:
        logger.error(
            'region name %s is not valid.  Reserved names: %s', region, illegal_region_names
        )
        return False

    # if this matches, return is true, fail -> false
    if re.match(r'[a-zA-Z0-9_]+\Z', region):  # string that has only letters and numbers
        return True
    logger.error(
        'region name %s is not valid.  Only letters, numbers, and underscore are accepted', region
    )
    return False


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
