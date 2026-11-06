"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package readers for the natural gas model:  ``NGUpdateReader`` applies inbound packages to
the loaded ``NGData`` before a build, and ``NGModelUpdateReader`` applies them to a built
``NGModel``.
"""

import logging
from collections.abc import Collection
from functools import singledispatchmethod

from pyomo.environ import value

from src.common.update_package import (
    HSM_CAPACITY_VALUE,
    NG_ELEC_DEMAND_VALUE,
    HSMSupplyCapacityPackage,
    NGDemandScaler,
    NGElectricalDemandPackage,
    UpdatePackage,
    UpdatePackageReader,
    check_region_year_keys,
)
from src.models.natural_gas.data import NGData
from src.models.natural_gas.ng_model import NGModel

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

# Smallest positive Q0 [Bcf/yr] a received capacity may set; HiGHS has crashed on the narrower
# supply segments of a Q0 near 1e-8 Bcf/yr.  Zero is allowed.
MIN_POSITIVE_Q0 = 1.0


def _received_capacity(update_package: HSMSupplyCapacityPackage) -> dict[tuple[str, int], float]:
    """Read an ``HSMSupplyCapacityPackage`` into ``{(region, year): Bcf}``.

    Parameters
    ----------
    update_package : HSMSupplyCapacityPackage
        Capacity by natural gas ``(region, year)``.

    Returns
    -------
    dict of (str, int) to float

    Raises
    ------
    ValueError
        If any value is positive but below ``MIN_POSITIVE_Q0``.
    """
    received = update_package.elements[HSM_CAPACITY_VALUE]
    values = {
        (str(region), int(year)): float(bcf)
        for (region, year), bcf in zip(received.index.to_list(), received.to_list(), strict=True)
    }
    small = {key: bcf for key, bcf in values.items() if 0.0 < bcf < MIN_POSITIVE_Q0}
    if small:
        raise ValueError(
            f'Capacity below {MIN_POSITIVE_Q0} Bcf/yr but above zero for {small}; '
            'send zero or at least the minimum'
        )
    return values


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
    def _(self, update_package: NGDemandScaler, data: NGData) -> None:
        """Scale every entry of the projected ``demand`` table by the package scalar.

        Parameters
        ----------
        update_package : NGDemandScaler
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

        The package must cover exactly the regions and years the data holds
        (:func:`check_region_year_keys`); each entry is converted to Bcf with the data's
        ``mmbtu_per_bcf`` and overwrites the held demand for the sector named by
        ``SECTOR_SUPERSEDED_BY``.

        Parameters
        ----------
        update_package : NGElectricalDemandPackage
            Gas demand in MMBtu/yr indexed by natural gas ``(region, year)``.
        data : NGData
            The loaded data; ``data['demand']`` is modified in place.

        Raises
        ------
        ValueError
            If the package's ``(region, year)`` entries are not exactly the held regions x years.
        """
        sector = SECTOR_SUPERSEDED_BY[NGElectricalDemandPackage]
        demand = data['demand']
        received = update_package.elements[NG_ELEC_DEMAND_VALUE]
        check_region_year_keys(
            received.index, data['regions_analyze'], data['years'], name=f'{sector} demand'
        )
        mmbtu_per_bcf = data['qp_scalars']['mmbtu_per_bcf']
        big_moves: list[tuple[str, int, float, float]] = []
        for (region, year), mmbtu in zip(received.index.to_list(), received.to_list(), strict=True):
            key = (str(region), sector, int(year))
            prior = demand[key]
            new = float(mmbtu) / mmbtu_per_bcf
            # screen for large swings; a zero prior counts as a large swing unless new is also zero
            if (prior == 0.0 and new != 0.0) or (
                prior != 0.0 and abs(new - prior) / abs(prior) > DEMAND_CHANGE_WARN_FRACTION
            ):
                big_moves.append((str(region), int(year), prior, new))
            demand[key] = new
        if big_moves:
            logger.warning(
                'Received %s demand changed by more than %d%% for %d (region, year) entries.  '
                '(region, year, prior, new) (up to 10 shown):  %s',
                sector,
                round(DEMAND_CHANGE_WARN_FRACTION * 100),
                len(big_moves),
                big_moves[:10],
            )
        logger.info(
            'Replaced %s demand for %d (region, year) entries from the electricity model',
            sector,
            len(received),
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: HSMSupplyCapacityPackage, data: NGData) -> None:
        """Set the supply anchor Q0 of every held ``(region, year)`` to the received capacity.

        Writes ``q0_mult = capacity / tier total`` into ``data['supply_anchors']`` and keeps
        ``p0_mult``, so the built model has ``Q0 = capacity`` and the same P0.

        Parameters
        ----------
        update_package : HSMSupplyCapacityPackage
            Capacity in Bcf/yr indexed by natural gas ``(region, year)``.
        data : NGData
            The loaded data; ``data['supply_anchors']`` is modified in place.

        Raises
        ------
        ValueError
            If the package's ``(region, year)`` entries are not the held regions x years, a value
            is positive but below ``MIN_POSITIVE_Q0``, or a region with no tier capacity receives
            a positive value.
        """
        check_region_year_keys(
            update_package.elements.index,
            data['regions_analyze'],
            data['years'],
            name='HSM capacity',
        )
        anchors = data['supply_anchors']
        # check every entry before writing any, so a rejected package leaves the data as it was
        new_anchors: dict[tuple[str, int], tuple[float, float]] = {}
        for (region, year), bcf in _received_capacity(update_package).items():
            total = sum(cap for cap, _ in data['supply_cost_tiers'][region])
            if total == 0.0:
                if bcf > 0.0:
                    raise ValueError(
                        f'{region} has no tier capacity in ng_supply_cost_tiers.csv, so its Q0 '
                        f'cannot be set to {bcf} Bcf/yr'
                    )
                continue
            p0_mult = anchors.get((region, year), (1.0, 1.0))[1]
            new_anchors[(region, year)] = (bcf / total, p0_mult)
        anchors.update(new_anchors)
        logger.info(
            'Set Q0 from the HSM capacity for %d (region, year) entries before the build',
            len(update_package.elements),
        )


class NGModelUpdateReader(UpdatePackageReader[NGModel]):
    """Applies inbound update packages to a built ``NGModel``, by package type.

    The built-model counterpart of :class:`NGUpdateReader`, for a driver that keeps the model
    between solves rather than rebuilding it.  Supplied demand goes through the model's exact
    setter, ``NGModel.set_external_demand``.
    """

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: NGModel) -> None:  # type: ignore[bad-override]
        """Reject a package type with no registered handler.

        Parameters
        ----------
        update_package : UpdatePackage
            The package received.
        data : NGModel
            The built model.

        Raises
        ------
        NotImplementedError
            Always; handlers for supported package types are registered below.
        """
        raise NotImplementedError(
            f'No built-model handler for update package type: {type(update_package)}'
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: NGElectricalDemandPackage, data: NGModel) -> None:
        """Write the electricity model's gas burn into the built model's demand cells.

        The package must cover exactly the regions and years the model holds
        (:func:`check_region_year_keys`).  The values are converted to Bcf with the model's
        ``mmbtu_per_bcf`` and go to ``NGModel.set_external_demand`` for the sector named by
        ``SECTOR_SUPERSEDED_BY``, which writes them exactly, and large moves are reported, as the
        pre-build reader does.  Applying a package twice is the same as applying it once.

        Parameters
        ----------
        update_package : NGElectricalDemandPackage
            Gas demand in MMBtu/yr indexed by natural gas ``(region, year)``.
        data : NGModel
            The built model; its ``demand`` parameter is modified in place.

        Raises
        ------
        ValueError
            If the package's ``(region, year)`` entries are not exactly the held regions x years,
            or from ``set_external_demand``.
        """
        sector = SECTOR_SUPERSEDED_BY[NGElectricalDemandPackage]
        received = update_package.elements[NG_ELEC_DEMAND_VALUE]
        check_region_year_keys(received.index, data.regions, data.year, name=f'{sector} demand')
        mmbtu_per_bcf = data.qp_scalars['mmbtu_per_bcf']
        values: dict[tuple[str, int], float] = {
            (str(r), int(y)): float(mmbtu) / mmbtu_per_bcf
            for (r, y), mmbtu in zip(received.index.to_list(), received.to_list(), strict=True)
        }

        big_moves: list[tuple[str, int, float, float]] = []
        for (region, year), new in values.items():
            # pyrefly: ignore[bad-argument-type]  - an initialized mutable Param is never None
            prior = float(value(data.demand[region, sector, year]))
            # a zero prior counts as a large swing unless new is also zero
            if (prior == 0.0 and new != 0.0) or (
                prior != 0.0 and abs(new - prior) / abs(prior) > DEMAND_CHANGE_WARN_FRACTION
            ):
                big_moves.append((region, year, prior, new))

        data.set_external_demand(values, sector=sector)

        if big_moves:
            logger.warning(
                'Received %s demand changed by more than %d%% for %d (region, year) entries.  '
                '(region, year, prior, new) (up to 10 shown):  %s',
                sector,
                round(DEMAND_CHANGE_WARN_FRACTION * 100),
                len(big_moves),
                big_moves[:10],
            )
        logger.info('Wrote %s demand for %d (region, year) entries', sector, len(values))

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: HSMSupplyCapacityPackage, data: NGModel) -> None:
        """Set the built model's supply anchor Q0 to the received capacity and rebuild the curve.

        Writes through ``NGModel.update_supply_capacity`` with ``alpha=1``, which is idempotent and
        keeps P0.

        Parameters
        ----------
        update_package : HSMSupplyCapacityPackage
            Capacity in Bcf/yr indexed by natural gas ``(region, year)``.
        data : NGModel
            The built model; its supply-curve parameters are modified in place.

        Raises
        ------
        ValueError
            If the package's ``(region, year)`` entries are not the held regions x years, or a
            value is positive but below ``MIN_POSITIVE_Q0``.
        """
        check_region_year_keys(
            update_package.elements.index, data.regions, data.year, name='HSM capacity'
        )
        values = _received_capacity(update_package)
        data.update_supply_capacity(
            {(region, 'total', year): bcf for (region, year), bcf in values.items()}, alpha=1.0
        )
        logger.info(
            'Set Q0 from the HSM capacity for %d (region, year) entries:  national %.0f Bcf/yr',
            len(values),
            sum(values.values()),
        )
