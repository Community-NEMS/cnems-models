"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/2/26

Update package reader for C-HSM:  sets the Henry Hub path from C-NGMM's hub price, in the loaded
data, before the build.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import singledispatchmethod
from pathlib import Path

from src.common.update_package import (
    NG_HUB_PRICE_VALUE,
    NGHubPricePackage,
    UpdatePackage,
    UpdatePackageReader,
)
from src.models.hsm.data import HENRY_HUB_COLUMNS, load_price_path

logger = logging.getLogger(__name__)


@dataclass
class HSMData:
    """C-HSM's loaded inputs, which inbound packages change before a build.

    Attributes
    ----------
    regions : list[str]
        The US supply regions, from C-NGMM's inputs (``load_ng_supply_side``).
    supply_cost_tiers : dict[str, list[tuple[float, float]]]
        ``{region: [(capacity_bcf, cost_per_mmbtu), ...]}``, from C-NGMM's inputs.
    henry_hub_1987 : dict[int, float]
        The Henry Hub path C-HSM runs on, 1987 USD/MMBtu, every model year.
    reference_1987 : dict[int, float]
        The coupling reference path in 1987 USD/MMBtu, every model year.
    reference_usd : dict[int, float]
        The same path in ``reference_dollar_year`` USD/MMBtu.
    reference_dollar_year : int
        The dollar year of ``reference_usd``.
    """

    regions: list[str]
    supply_cost_tiers: dict[str, list[tuple[float, float]]]
    henry_hub_1987: dict[int, float]
    reference_1987: dict[int, float]
    reference_usd: dict[int, float]
    reference_dollar_year: int


def load_reference_pair(path: Path, dollar_year: int) -> tuple[dict[int, float], dict[int, float]]:
    """Read the coupling reference path in 1987 USD and in ``dollar_year`` USD.

    Parameters
    ----------
    path : Path
        A CSV with a ``year`` column, a 1987 USD column (``HENRY_HUB_COLUMNS``) and
        ``hh_<dollar_year>usd_per_mmbtu``.
    dollar_year : int
        The dollar year of the second column.

    Returns
    -------
    tuple of (dict of int to float, dict of int to float)
        The 1987 USD path and the ``dollar_year`` USD path, by year.

    Raises
    ------
    ValueError
        From ``load_price_path``, or if the two columns do not cover the same years or a price
        is not positive.
    """
    reference_1987 = load_price_path(path, HENRY_HUB_COLUMNS, 'Henry Hub reference')
    reference_usd = load_price_path(
        path, (f'hh_{dollar_year}usd_per_mmbtu',), f'Henry Hub reference ({dollar_year} USD)'
    )
    if set(reference_1987) != set(reference_usd):
        raise ValueError(f'{path}: the two price columns cover different years')
    bad = sorted(y for y in reference_usd if reference_1987[y] <= 0 or reference_usd[y] <= 0)
    if bad:
        raise ValueError(f'{path} has non-positive reference prices for years {bad}')
    return reference_1987, reference_usd


def fill_ratio(ratio: dict[int, float], years: Sequence[int]) -> dict[int, float]:
    """Spread ratios known at some years to every year.

    Linear between known years, and held at the nearest known value outside them.

    Parameters
    ----------
    ratio : dict of int to float
        Ratios at the known years; at least one.
    years : Sequence of int
        The years to fill.

    Returns
    -------
    dict of int to float
        A ratio for every year in ``years``.
    """
    known = sorted(ratio)
    filled: dict[int, float] = {}
    for year in years:
        if year <= known[0]:
            filled[year] = ratio[known[0]]
        elif year >= known[-1]:
            filled[year] = ratio[known[-1]]
        else:
            lo = max(k for k in known if k <= year)
            hi = min(k for k in known if k >= year)
            weight = 0.0 if hi == lo else (year - lo) / (hi - lo)
            filled[year] = ratio[lo] + weight * (ratio[hi] - ratio[lo])
    return filled


class HSMUpdateReader(UpdatePackageReader[HSMData]):
    """Applies inbound update packages to C-HSM's loaded ``HSMData``, by type."""

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: HSMData) -> None:  # type: ignore[bad-override]
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
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: NGHubPricePackage, data: HSMData) -> None:
        """Index the reference Henry Hub path to C-NGMM's hub price.

        At each package year ``R = price / reference``, both in the package's dollar year.
        :func:`fill_ratio` spreads ``R`` to every model year, and ``R`` scales the 1987 USD
        reference path.  No deflator is applied.

        Parameters
        ----------
        update_package : NGHubPricePackage
            C-NGMM's price in its Henry Hub region, by model year.
        data : HSMData
            The loaded data; ``data.henry_hub_1987`` is replaced.

        Raises
        ------
        ValueError
            If the package's dollar year is not the reference path's, or a package year has no
            reference price.
        """
        if update_package.dollar_year != data.reference_dollar_year:
            raise ValueError(
                f'The hub price is in {update_package.dollar_year} dollars and the reference '
                f'path in {data.reference_dollar_year} dollars; convert one of them first'
            )
        received = update_package.elements[NG_HUB_PRICE_VALUE]
        prices = {
            int(year): float(price)
            for year, price in zip(received.index.to_list(), received.to_list(), strict=True)
        }
        missing = sorted(year for year in prices if year not in data.reference_usd)
        if missing:
            raise ValueError(f'No reference Henry Hub price for hub-price years {missing}')
        ratio = {year: price / data.reference_usd[year] for year, price in prices.items()}
        years = sorted(data.reference_1987)
        filled = fill_ratio(ratio, years)
        data.henry_hub_1987 = {y: data.reference_1987[y] * filled[y] for y in years}
        logger.info(
            'C-HSM Henry Hub path from the %s price; ratio to reference by year:  %s',
            update_package.hub_region,
            {y: round(r, 3) for y, r in sorted(ratio.items())},
        )
