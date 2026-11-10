"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package writer for the natural gas model:  packages the solved gas prices for the
electricity model, and the Henry Hub region's price for the Magic HSM test model.
"""

import logging
from collections.abc import Callable, Mapping

import pandas as pd

from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_HUB_PRICE_INDEX,
    NG_HUB_PRICE_VALUE,
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    NGHubPricePackage,
    NGPricePackage,
    UpdatePackage,
    UpdatePackageWriter,
)
from src.integrator.region_crosswalk import QuantityKind
from src.models.natural_gas.constants import HUB_REGION, PRICE_DOLLAR_YEAR
from src.models.natural_gas.ng_model import NGModel

logger = logging.getLogger(__name__)


class NGUpdateWriter(UpdatePackageWriter[NGModel]):
    """Writes the natural gas model's outbound packages:  prices for electricity and Magic HSM."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[NGModel], UpdatePackage]]:
        """The gas-price package for electricity and the hub-price package for Magic HSM."""
        return {NGPricePackage: self.price_package, NGHubPricePackage: self.hub_price_package}

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

    def hub_price_package(self, model: NGModel) -> NGHubPricePackage:
        """Package the solved price of ``HUB_REGION`` by model year, for Magic HSM.

        Parameters
        ----------
        model : NGModel
            A solved model with duals loaded.

        Returns
        -------
        NGHubPricePackage
            Prices in USD/MMBtu of ``PRICE_DOLLAR_YEAR``, one per model year.

        Raises
        ------
        ValueError
            If the model does not carry ``HUB_REGION``, or from the package's checks.
        """
        if HUB_REGION not in model.regions:
            raise ValueError(
                f'C-NGMM does not carry the hub region {HUB_REGION!r}; '
                f'it carries {sorted(model.regions)}'
            )
        prices = pd.Series(
            {
                int(gi.year): price
                for gi, price in model.poll_gas_price().items()
                if gi.region == HUB_REGION
            },
            name=NG_HUB_PRICE_VALUE,
        ).sort_index()
        prices.index = prices.index.set_names(NG_HUB_PRICE_INDEX)
        logger.info(
            'C-NGMM: %s price for Magic HSM:  %s',
            HUB_REGION,
            ', '.join(f'{p:.3f} USD/MMBtu ({y})' for y, p in prices.items()),
        )
        return NGHubPricePackage(
            elements=prices.to_frame(),
            hub_region=HUB_REGION,
            dollar_year=PRICE_DOLLAR_YEAR,
            source=ModelType.NATURAL_GAS,
        )
