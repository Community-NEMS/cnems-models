"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

The C-CDM model object: holds the inputs, the price paths and the latest results.
"""

import math
from logging import getLogger

from src.common.integrated_model import IntegratedModel
from src.models.cdm.cdm_config import CDMConfig
from src.models.cdm.commercial import Results, price_factor, solve
from src.models.cdm.data import FUELS, REGIONS, YEARS, CDMData

logger = getLogger(__name__)

Prices = dict[tuple[str, int], float]  # {(region, year): 2025 USD per MMBtu}


class CDMModel(IntegratedModel):
    """Commercial energy demand by census division, building type, service and year.

    The reference price path is fixed once, before the first run: by default the AEO2026
    commercial price for the configured case in every division, or a coupled model's own baseline
    through :meth:`set_reference_prices`. Demand responds to the ratio of current to reference
    prices (:func:`~src.models.cdm.commercial.price_factor`), so at the reference prices it
    reproduces the AEO2026 rows it scales to, whatever their level; servers are modeled and differ.
    """

    def __init__(self, data: CDMData, config: CDMConfig):
        """Store the inputs and set both price paths from AEO2026 and the configured scales."""
        self.data = data
        self.case = config.case
        self.reference: dict[tuple[str, str, int], float] = {
            (fuel, r, y): data.aeo_price[self.case, fuel, y]
            for fuel in FUELS
            for r in REGIONS
            for y in YEARS
        }
        scale = {
            'electricity': config.electricity_price_scale,
            'natural_gas': config.natural_gas_price_scale,
        }
        self.prices = {k: v * scale[k[0]] for k, v in self.reference.items()}
        if not all(math.isfinite(p) for p in self.prices.values()):
            raise ValueError('C-CDM: a price scale makes a price non-finite')
        self.results: Results | None = None

    @property
    def label(self) -> str:
        """Short name for logs and run monitors."""
        return 'C-CDM'

    @staticmethod
    def _checked(fuel: str, prices: Prices) -> dict[tuple[str, str, int], float]:
        out = {}
        for (region, year), price in prices.items():
            if region not in REGIONS or year not in YEARS:
                raise ValueError(f'C-CDM: no {fuel} price slot for ({region}, {year})')
            if not math.isfinite(price) or price <= 0.0:
                raise ValueError(
                    f'C-CDM: {fuel} price must be finite and positive at ({region}, {year})'
                )
            out[fuel, region, year] = float(price)
        return out

    def set_reference_prices(self, electricity: Prices, natural_gas: Prices) -> None:
        """Replace the reference price path, once, before the first run.

        A coupled model passes the baseline prices it would send if nothing changed, so C-CDM
        reproduces the AEO2026 rows it scales to at that baseline. Resetting it during iterations
        would absorb any price change into the baseline, so it is refused once results exist.

        Parameters
        ----------
        electricity, natural_gas : dict
            {(region, year): 2025 USD per MMBtu}; slots not given keep the AEO2026 price. The
            current price of each slot given is reset to its new reference.
        """
        if self.results is not None:
            raise RuntimeError('C-CDM: the reference price path is fixed once the model has run')
        new = self._checked('electricity', electricity) | self._checked('natural_gas', natural_gas)
        self.reference.update(new)
        self.prices.update(new)

    def update_prices(self, electricity: Prices, natural_gas: Prices) -> None:
        """Set current prices; slots not given keep their present value.

        Both fuels are checked before either is applied, so a rejected call changes nothing.

        Parameters
        ----------
        electricity, natural_gas : dict
            {(region, year): 2025 USD per MMBtu}.
        """
        new = self._checked('electricity', electricity) | self._checked('natural_gas', natural_gas)
        self.prices.update(new)

    def run(self) -> Results:
        """Compute the results at the current prices and keep them on the model."""
        factor = price_factor(self.data, self.reference, self.prices)
        self.results = solve(self.data, self.case, factor)
        logger.info('C-CDM: solved the %s case for %d-%d', self.case, YEARS[0], YEARS[-1])
        return self.results
