"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package reader for the natural gas model:  applies inbound packages to the loaded ``NGData``.
"""

import logging
from collections.abc import Collection
from functools import singledispatchmethod

from src.common.update_package import (
    NG_ELEC_DEMAND_VALUE,
    NGDemandPackage,
    NGElectricalDemandPackage,
    UpdatePackage,
    UpdatePackageReader,
)
from src.models.natural_gas.data import NGData

logger = logging.getLogger(__name__)

# Which inbound update package supersedes which demand sector of ng_sector_data.csv.  When a
# package type listed here is inbound, the sector's AEO growth projection is gated off (see
# ``project_demand``) and the package's handler sets the sector's demand instead.  Grows as more
# models feed sector demand back.
# TODO:  This presupposes that the sector labels are fixed, but they are in "data" folders.
#        Determine if we want to lock these and if so, put the sector listing in some
#        /properties folder or such
SECTOR_SUPERSEDED_BY: dict[type[UpdatePackage], str] = {
    NGElectricalDemandPackage: 'electric_power',
}

# Fractional change (either direction) in a received (region, year) demand, relative to the held
# value it replaces, above which a warning is logged.
DEMAND_CHANGE_WARN_FRACTION = 0.5


class NGUpdateReader(UpdatePackageReader[NGData]):
    """Applies inbound update packages to the natural gas model's loaded ``NGData``, by type.

    Packages modify the data in place, after ``load_all`` and before the ``NGModel`` is built.
    """

    def superseded_sectors(self, update_packages: Collection[UpdatePackage]) -> frozenset[str]:
        """Name the demand sectors that inbound update packages supersede.

        Called before loading, so ``load_all`` can gate off the growth projection of those sectors.

        Parameters
        ----------
        update_packages : Collection[UpdatePackage]
            The packages about to be applied.

        Returns
        -------
        frozenset[str]
            Sectors from ``SECTOR_SUPERSEDED_BY`` whose package type is among the inbound packages.
        """
        return frozenset(
            sector
            for package_type, sector in SECTOR_SUPERSEDED_BY.items()
            if any(isinstance(package, package_type) for package in update_packages)
        )

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: NGData) -> None:  # type: ignore[bad-override]
        """Reject a package type with no registered handler.

        Parameters
        ----------
        update_package : UpdatePackage
            The package received.
        data : NGData
            The loaded data from ``load_all``.

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
    def _(self, update_package: NGDemandPackage, data: NGData) -> None:
        """Scale every entry of the projected ``demand`` table by the package scalar.

        Parameters
        ----------
        update_package : NGDemandPackage
            Carries the multiplier to apply.
        data : NGData
            The loaded data; ``data['demand']`` is modified in place.
        """
        demand = data['demand']
        for key in demand:
            demand[key] *= update_package.scalar
        logger.info(
            'Scaled NG demand by factor %0.3f (%d region-sector-year entries)',
            update_package.scalar,
            len(demand),
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: NGElectricalDemandPackage, data: NGData) -> None:
        """Replace the ``electric_power`` sector demand with the electricity model's gas burn.

        Every ``(region, year)`` the package carries overwrites the held demand for the sector
        named by ``SECTOR_SUPERSEDED_BY``.  Held entries the package omits keep their loaded
        values and are reported as warnings -- with growth gated off they sit at the base-year
        value.  Package entries beyond the held regions/years (filtered out of this run) are
        ignored.

        Parameters
        ----------
        update_package : NGElectricalDemandPackage
            Gas demand in Bcf/yr indexed by natural gas ``(region, year)``.
        data : NGData
            The loaded data; ``data['demand']`` is modified in place.
        """
        sector = SECTOR_SUPERSEDED_BY[NGElectricalDemandPackage]
        demand = data['demand']
        received = update_package.elements[NG_ELEC_DEMAND_VALUE]
        held = {(r, y) for (r, s, y) in demand if s == sector}
        replaced = 0
        big_moves: list[tuple[str, int, float, float]] = []
        for (region, year), bcf in zip(received.index.to_list(), received.to_list(), strict=True):
            if (region, year) not in held:
                continue
            prior = demand[(region, sector, year)]
            new = float(bcf)
            # screen for large swings; a zero prior counts as a large swing unless new is also zero
            if (prior == 0.0 and new != 0.0) or (
                prior != 0.0 and abs(new - prior) / abs(prior) > DEMAND_CHANGE_WARN_FRACTION
            ):
                big_moves.append((region, year, prior, new))
            demand[(region, sector, year)] = new
            replaced += 1
        if big_moves:
            logger.warning(
                'Received %s demand changed by more than %d%% for %d (region, year) entries.  '
                '(region, year, prior, new) (up to 10 shown):  %s',
                sector,
                round(DEMAND_CHANGE_WARN_FRACTION * 100),
                len(big_moves),
                big_moves[:10],
            )
        missing = sorted(held.difference(received.index))
        if missing:
            logger.warning(
                'Received %s demand does not cover %d of %d held (region, year) entries; those '
                'keep their base-year values.  Missing (up to 10 shown):  %s',
                sector,
                len(missing),
                len(held),
                missing[:10],
            )
        overage = len(received) - replaced
        if overage:
            logger.debug(
                'Received %s demand holds %d entries beyond the held index (ignored)',
                sector,
                overage,
            )
        logger.info(
            'Replaced %s demand for %d of %d held (region, year) entries from the electricity '
            'model',
            sector,
            replaced,
            len(held),
        )
