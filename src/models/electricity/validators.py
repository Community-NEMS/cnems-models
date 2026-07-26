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
