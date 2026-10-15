"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package writer for the natural gas model:  packages the solved gas prices for the
electricity model.
"""

import logging
from collections.abc import Callable, Mapping

import pandas as pd

from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    NGPricePackage,
    UpdatePackage,
    UpdatePackageWriter,
)
from src.integrator.region_crosswalk import QuantityKind
from src.models.natural_gas.ng_model import NGModel

logger = logging.getLogger(__name__)


class NGUpdateWriter(UpdatePackageWriter[NGModel]):
    """Writes the natural gas model's outbound packages:  its prices, for the electricity model."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[NGModel], UpdatePackage]]:
        """The gas-price package."""
        return {NGPricePackage: self.price_package}

    def price_package(self, model: NGModel) -> NGPricePackage:
        """Package the solved gas prices for the electricity model.

        The regional prices from :meth:`NGModel.poll_gas_price` (duals of ``demand_balance``,
        $/MMBtu) are averaged into electricity regions with the run's electricity <-> natural gas
        crosswalk.

        Parameters
        ----------
        model : NGModel
            A solved model with duals loaded.

        Returns
        -------
        NGPricePackage

        Raises
        ------
        KeyError
            If the writer holds no electricity <-> natural gas crosswalk.
        ValueError
            From ``RegionCrosswalk.apply``, if the model's regions are not closed under the
            crosswalk.
        """
        ng_prices = model.poll_gas_price()
        series = pd.Series(
            {(gi.region, gi.year): price for gi, price in ng_prices.items()}, name=NG_PRICE_VALUE
        )
        series.index = series.index.set_names(NG_PRICE_INDEX)
        crosswalk = self.crosswalks.get(ModelType.NATURAL_GAS, ModelType.ELECTRICITY)
        elec_prices = crosswalk.apply(
            series, ModelType.NATURAL_GAS, QuantityKind.INTENSIVE, model_regions=model.regions
        )
        by_year = elec_prices.groupby(level='year').agg(['min', 'max'])
        for year, row in by_year.iterrows():
            logger.info(
                'C-NGMM: %d gas price to electricity regions:  %0.2f to %0.2f $/MMBtu',
                year,
                row['min'],
                row['max'],
            )
        return NGPricePackage(elements=elec_prices.to_frame(), source=ModelType.NATURAL_GAS)
