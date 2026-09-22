"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package writer for the magic model:  fabricates mock updates for the other models.
"""

import math
import random
from typing import TYPE_CHECKING

from src.common.models_modes import ModelType
from src.common.update_package import (
    ElectricityPriceScaler,
    NGDemandPackage,
    UpdatePackage,
    UpdatePackageWriter,
    make_trans_update,
)

if TYPE_CHECKING:
    # magic_model imports this module for its sequencer, so the model is named by string below
    from src.models.magic.magic_model import MagicModel


class MagicUpdateWriter(UpdatePackageWriter['MagicModel']):
    """Writes mock electricity price, transmission cost, and NG demand updates."""

    def write(self, model: MagicModel) -> list[UpdatePackage]:
        """Make mock updates from the model's sequence number.

        Fabricates a sinusoid from the sequence number with a 10-cycle diminishment, centered on
        1.0 so the scaler stays positive: SupplyPrice is NonNegativeReals, and a bare cosine goes
        negative for sequence numbers 3-5, 11-13, ...

        Parameters
        ----------
        model : MagicModel
            The solved magic model; only its ``sequence_number`` is read.

        Returns
        -------
        list[UpdatePackage]
            An ``ElectricityPriceScaler``, a ``TransCostUpdate``, and an ``NGDemandPackage``.
        """
        # # TODO:  Temp patch for isolating NG updates
        # return []
        sequence_number = model.sequence_number
        cycle_point = sequence_number * math.pi / 4
        scale = 2 * 2 ** (-sequence_number / 3)
        scalar = 1 + scale * math.cos(cycle_point)
        update = ElectricityPriceScaler(
            receivers=(ModelType.ELECTRICITY,), scalar=scalar, techs=('4', '6')
        )

        # make a transmission cost update
        tcu = make_trans_update(
            new_cost=2000 - 5000 * random.random() * math.e ** (-sequence_number),
            year=2030,
        )

        # exponential decay toward 1.4:  exactly 1.0 at sequence number 1 (the first
        # iteration in the control loop), approaching 1.4 as the sequence advances
        ng_scalar = 1.4 - 0.4 * math.exp(-(sequence_number - 1) / 3)
        ng_demand = NGDemandPackage(receivers=(ModelType.NATURAL_GAS,), scalar=ng_scalar)

        return [update, tcu, ng_demand]
