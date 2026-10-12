"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package writer for the magic model:  fabricates mock updates for the other models.
"""

import math
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from src.common.models_modes import ModelType
from src.common.update_package import (
    ElectricityPriceScaler,
    NGDemandScaler,
    UpdatePackage,
    UpdatePackageWriter,
)

if TYPE_CHECKING:
    # magic_model imports this module for its sequencer, so the model is named by string below
    from src.models.magic.magic_model import MagicModel


class MagicUpdateWriter(UpdatePackageWriter['MagicModel']):
    """Writes mock electricity price and NG demand updates."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[MagicModel], UpdatePackage]]:
        """The mock electricity price and NG demand scalers."""
        return {
            ElectricityPriceScaler: self.price_scaler,
            NGDemandScaler: self.demand_scaler,
        }

    def price_scaler(self, model: MagicModel) -> ElectricityPriceScaler:
        """Make a mock electricity price scaler from the model's sequence number.

        Fabricates a sinusoid from the sequence number with a 10-cycle diminishment, centered on
        1.0 so the scaler stays positive: SupplyPrice is NonNegativeReals, and a bare cosine goes
        negative for sequence numbers 3-5, 11-13, ...

        Parameters
        ----------
        model : MagicModel
            The solved magic model; only its ``sequence_number`` is read.

        Returns
        -------
        ElectricityPriceScaler
        """
        sequence_number = model.sequence_number
        cycle_point = sequence_number * math.pi / 4
        scale = 2 * 2 ** (-sequence_number / 3)
        scalar = 1 + scale * math.cos(cycle_point)
        return ElectricityPriceScaler(
            receivers=(ModelType.ELECTRICITY,), scalar=scalar, techs=('4', '6')
        )

    def demand_scaler(self, model: MagicModel) -> NGDemandScaler:
        """Make a mock NG demand scaler from the model's sequence number.

        Exponential decay toward 1.4:  exactly 1.0 at sequence number 1 (the first iteration in
        the control loop), approaching 1.4 as the sequence advances.

        Parameters
        ----------
        model : MagicModel
            The solved magic model; only its ``sequence_number`` is read.

        Returns
        -------
        NGDemandScaler
        """
        ng_scalar = 1.4 - 0.4 * math.exp(-(model.sequence_number - 1) / 3)
        return NGDemandScaler(receivers=(ModelType.NATURAL_GAS,), scalar=ng_scalar)
