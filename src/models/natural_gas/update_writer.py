"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package writer for the natural gas model:  packages the solved gas prices for the
electricity model.
"""

import logging

import pandas as pd

from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    NGPricePackage,
    UpdatePackage,
    UpdatePackageWriter,
)
from src.integrator.region_crosswalk import QuantityKind, crosswalk_values
from src.models.natural_gas.ng_model import NGModel

logger = logging.getLogger(__name__)


class NGUpdateWriter(UpdatePackageWriter[NGModel]):
    """Writes the natural gas model's outbound packages:  its prices, for the electricity model."""

    def write(self, model: NGModel) -> list[UpdatePackage]:
        """Package the solved gas prices for the electricity model.

        The regional prices from :meth:`NGModel.poll_gas_price` (duals of ``demand_balance``,
        $/MMBtu) are averaged into electricity regions with the population-weighted crosswalk and
        sent as one :class:`NGPricePackage`.

        Parameters
        ----------
        model : NGModel
            A solved model with duals loaded.

        Returns
        -------
        list[UpdatePackage]
            A single ``NGPricePackage``.
        """
        ng_prices = model.poll_gas_price()
        series = pd.Series(
            {(gi.region, gi.year): price for gi, price in ng_prices.items()}, name=NG_PRICE_VALUE
        )
        series.index = series.index.set_names(NG_PRICE_INDEX)
        elec_prices = crosswalk_values(
            series, ModelType.NATURAL_GAS, ModelType.ELECTRICITY, QuantityKind.INTENSIVE
        )
        by_year = elec_prices.groupby(level='year').agg(['min', 'max'])
        for year, row in by_year.iterrows():
            logger.info(
                'C-NGMM: %d gas price to electricity regions:  %0.2f to %0.2f $/MMBtu',
                year,
                row['min'],
                row['max'],
            )
        package = NGPricePackage(elements=elec_prices.to_frame(), source=ModelType.NATURAL_GAS)
        return [package]
