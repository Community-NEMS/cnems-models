"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/4/26

Update package writer for the Magic HSM test model:  packages its capacity for C-NGMM.
"""

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import pandas as pd

from src.common.models_modes import ModelType
from src.common.update_package import (
    HSM_CAPACITY_INDEX,
    HSM_CAPACITY_VALUE,
    HSMSupplyCapacityPackage,
    UpdatePackage,
    UpdatePackageWriter,
)

if TYPE_CHECKING:
    # magic_hsm_model imports this module for its sequencer, so the model is named by string below
    from src.models.magic_hsm.magic_hsm_model import MagicHSMModel

logger = logging.getLogger(__name__)


class MagicHSMUpdateWriter(UpdatePackageWriter['MagicHSMModel']):
    """Writes Magic HSM's outbound packages:  its capacity, for C-NGMM."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[MagicHSMModel], UpdatePackage]]:
        """The capacity package."""
        return {HSMSupplyCapacityPackage: self.capacity_package}

    def capacity_package(self, model: MagicHSMModel) -> HSMSupplyCapacityPackage:
        """Package the solved capacity for every region and year of the model.

        Parameters
        ----------
        model : MagicHSMModel
            A solved model.

        Returns
        -------
        HSMSupplyCapacityPackage
            Capacity in Bcf/yr by C-NGMM ``(region, year)``.

        Raises
        ------
        ValueError
            If the model has not been solved.
        """
        if not model.capacity:
            raise ValueError('Magic HSM has no capacity to send; solve it first')
        series = pd.Series(model.capacity, name=HSM_CAPACITY_VALUE).sort_index()
        series.index = series.index.set_names(HSM_CAPACITY_INDEX)
        by_year = series.groupby(level='year').sum()
        logger.info(
            'Magic HSM: capacity for C-NGMM:  %s',
            ', '.join(f'{bcf:,.0f} Bcf ({year})' for year, bcf in by_year.items()),
        )
        return HSMSupplyCapacityPackage(elements=series.to_frame(), source=ModelType.MAGIC_HSM)
