"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/4/26

Update package reader for the Magic HSM test model:  sets its hub price from C-NGMM's.
"""

import logging
from dataclasses import dataclass
from functools import singledispatchmethod

from src.common.update_package import (
    NG_HUB_PRICE_VALUE,
    NGHubPricePackage,
    UpdatePackage,
    UpdatePackageReader,
)

logger = logging.getLogger(__name__)


@dataclass
class MagicHSMData:
    """Magic HSM's held data, which inbound packages change.

    Attributes
    ----------
    regions : list of str
        C-NGMM's domestic regions.
    years : list of int
        The run's summary years, sorted.
    base_capacity : dict of (str, int) to float
        C-NGMM's Q0 at build, Bcf/yr, by ``(region, year)``.
    hub_price : dict of int to float
        The hub price by year, USD/MMBtu of ``dollar_year``.
    dollar_year : int
        The dollar year of ``hub_price``.
    """

    regions: list[str]
    years: list[int]
    base_capacity: dict[tuple[str, int], float]
    hub_price: dict[int, float]
    dollar_year: int


class MagicHSMUpdateReader(UpdatePackageReader[MagicHSMData]):
    """Applies inbound update packages to Magic HSM's held data, by type."""

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: MagicHSMData) -> None:  # type: ignore[bad-override]
        """Reject a package type with no registered handler.

        Raises
        ------
        NotImplementedError
            Always; handlers for supported package types are registered below.
        """
        raise NotImplementedError(
            f'Missing single dispatch handler for type: {type(update_package)}'
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    # TODO:  As we work toward more direct model updates, refactor the readers such that
    #        this "type" hack below is not necessary.
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: NGHubPricePackage, data: MagicHSMData) -> None:
        """Set the hub price from C-NGMM's.

        Parameters
        ----------
        update_package : NGHubPricePackage
            C-NGMM's price in its hub region, by model year.
        data : MagicHSMData
            The held data; ``data.hub_price`` is replaced.

        Raises
        ------
        ValueError
            If the package's dollar year is not ``data.dollar_year``, or its years are not
            ``data.years``.
        """
        if update_package.dollar_year != data.dollar_year:
            raise ValueError(
                f'The hub price is in {update_package.dollar_year} dollars and the reference '
                f'price in {data.dollar_year} dollars'
            )
        received = update_package.elements[NG_HUB_PRICE_VALUE]
        prices = {
            int(year): float(price)
            for year, price in zip(received.index.to_list(), received.to_list(), strict=True)
        }
        if set(prices) != set(data.years):
            raise ValueError(
                f'The hub price covers years {sorted(prices)}; Magic HSM holds {data.years}'
            )
        data.hub_price = prices
        logger.info(
            'Magic HSM: hub price from %s:  %s',
            update_package.hub_region,
            ', '.join(f'{p:.3f} USD/MMBtu ({y})' for y, p in sorted(prices.items())),
        )
