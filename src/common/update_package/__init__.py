"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update packages passed between models, plus the reader/writer ABCs that apply and create them.

The package definitions live in ``update_package.py``; they are re-exported here so callers import
from ``src.common.update_package`` directly, as is ``route_updates`` from ``routing.py``.
"""

from src.common.update_package.reader import UpdatePackageReader
from src.common.update_package.routing import route_updates
from src.common.update_package.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    REGION_YEAR_INDEX,
    ElectricityPriceScaler,
    NGDemandScaler,
    NGElectricalDemandPackage,
    NGPricePackage,
    UpdatePackage,
)
from src.common.update_package.writer import UpdatePackageWriter

__all__ = [
    'NG_ELEC_DEMAND_INDEX',
    'NG_ELEC_DEMAND_VALUE',
    'NG_PRICE_INDEX',
    'NG_PRICE_VALUE',
    'REGION_YEAR_INDEX',
    'ElectricityPriceScaler',
    'NGDemandScaler',
    'NGElectricalDemandPackage',
    'NGPricePackage',
    'UpdatePackage',
    'UpdatePackageReader',
    'UpdatePackageWriter',
    'route_updates',
]
