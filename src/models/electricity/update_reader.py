"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package readers for the electricity model:  ``ElecUpdateReader`` applies inbound packages to
``ParamData`` before a build, and ``ElecModelUpdateReader`` applies them to a built ``PowerModel``.
"""

import logging
from functools import singledispatchmethod

import pandas as pd
from pyomo.environ import value

from src.common.update_package import (
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    ElectricityPriceScaler,
    NGPricePackage,
    UpdatePackage,
    UpdatePackageReader,
    check_region_year_keys,
)
from src.models.electricity.constants import (
    INITIAL_NG_PRICE,
    NG_PRICE_LINKED_TECHS,
    PRICE_COST_PROPORTION,
    SUPPLY_PRICE_CHANGE_WARN_FRACTION,
)
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.param_data import ParamData

logger = logging.getLogger(__name__)


class ElecUpdateReader(UpdatePackageReader[ParamData]):
    """Applies inbound update packages to the electricity model's ``ParamData``, by package type.

    Packages modify ``ParamData.param_frames`` in place, before the ``PowerModel`` is built.
    """

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: ParamData) -> None:  # type: ignore[bad-override]
        """Reject a package type with no registered handler.

        Parameters
        ----------
        update_package : UpdatePackage
            The package received.
        data : ParamData
            The loaded parameter data.

        Raises
        ------
        NotImplementedError
            Always; handlers for supported package types are registered below.
        """
        raise NotImplementedError(
            f'Missing single dispatch method for type: {type(update_package)}'
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, electricity_price_scalar: ElectricityPriceScaler, data: ParamData) -> None:
        """Scale the supply prices of the named techs by the scalar multiplier.

        Parameters
        ----------
        electricity_price_scalar : ElectricityPriceScaler
            Update package naming the techs to scale and the multiplier to apply.
        data : ParamData
            The loaded parameter data; ``data.param_frames`` is modified in place.

        Notes
        -----
        ``supply_price`` is indexed by ``(region, tech, step, year, season)``, so the tech
        filter is built from the index level rather than a column.  The frame is modified in
        place, in ``data.param_frames``.
        """
        prices = data.param_frames['supply_price']
        tech_mask = prices.index.get_level_values('tech').isin(electricity_price_scalar.techs)
        if not tech_mask.any():
            logger.warning(
                'No supply_price rows matched techs %s; prices unchanged',
                electricity_price_scalar.techs,
            )
            return
        prices.loc[tech_mask, :] *= electricity_price_scalar.scalar
        logger.info(
            'Scaled supply_price by factor %0.3f for %d of %d rows (techs %s)',
            electricity_price_scalar.scalar,
            tech_mask.sum(),
            len(prices),
            electricity_price_scalar.techs,
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, ng_price_update: NGPricePackage, data: ParamData) -> None:
        """Move the supply price of the gas-linked techs with the received natural gas price.

        A share ``PRICE_COST_PROPORTION`` of each ``NG_PRICE_LINKED_TECHS`` row of ``supply_price``
        is taken to be fuel cost embedding a gas price of ``INITIAL_NG_PRICE``, so the row is
        scaled by ``1 + PRICE_COST_PROPORTION * (price - INITIAL_NG_PRICE) / INITIAL_NG_PRICE``
        using the price received for its ``(region, year)``.  The package must cover exactly the
        regions and years the model holds (:func:`check_region_year_keys`).

        Parameters
        ----------
        ng_price_update : NGPricePackage
            Gas prices in $/MMBtu indexed by electricity ``(region, year)``.
        data : ParamData
            The loaded parameter data; ``data.param_frames`` is modified in place.

        Raises
        ------
        ValueError
            If the package's ``(region, year)`` entries are not exactly the held regions x years.

        Notes
        -----
        The adjustment is a ratio, so it is indifferent to the x1000 price hack in
        ``ParamData.__init__``.
        """
        new_price = ng_price_update.elements[NG_PRICE_VALUE]
        check_region_year_keys(
            new_price.index,
            data.model_sets.region_analyze,
            set(data.model_sets.year_map.values()),
            name='NG prices',
        )
        # locate the NG-based techs in the parameter data for supply prices...
        prices = data.param_frames['supply_price']
        tech_mask = prices.index.get_level_values('tech').isin(NG_PRICE_LINKED_TECHS)
        if not tech_mask.any():
            logger.warning(
                'No supply_price rows matched gas-linked techs %s; prices unchanged',
                NG_PRICE_LINKED_TECHS,
            )
            return
        factor = 1 + PRICE_COST_PROPORTION * (new_price - INITIAL_NG_PRICE) / INITIAL_NG_PRICE
        held_keys = pd.MultiIndex.from_arrays(
            [prices.index.get_level_values('region'), prices.index.get_level_values('year')],
            names=NG_PRICE_INDEX,
        )
        row_factor = pd.Series(factor.reindex(held_keys).to_numpy(), index=prices.index)
        covered = tech_mask & row_factor.notna().to_numpy()
        if not covered.any():
            logger.warning('Received NG prices cover no held gas-linked rows; prices unchanged')
            return
        prior = prices.loc[covered, 'cost'].copy()
        prices.loc[covered, 'cost'] *= row_factor[covered]
        # screen for large swings; the scaling is multiplicative, so the relative change is the
        # row factor less one (a zero prior stays zero and cannot swing)
        big_moves = (row_factor[covered] - 1).abs() >= SUPPLY_PRICE_CHANGE_WARN_FRACTION
        if big_moves.any():
            flagged = list(
                zip(
                    prior.index[big_moves].to_list(),
                    prior[big_moves].to_list(),
                    prices.loc[covered, 'cost'][big_moves].to_list(),
                    strict=True,
                )
            )
            logger.warning(
                'Received NG prices changed supply_price by %d%% or more for %d gas-linked rows.  '
                '(%s, prior, new) (up to 10 shown):  %s',
                round(SUPPLY_PRICE_CHANGE_WARN_FRACTION * 100),
                len(flagged),
                prices.index.names,
                flagged[:10],
            )
        logger.info(
            'Scaled supply_price for %d of %d gas-linked rows (techs %s) by factors %0.3f to %0.3f',
            covered.sum(),
            tech_mask.sum(),
            NG_PRICE_LINKED_TECHS,
            row_factor[covered].min(),
            row_factor[covered].max(),
        )


class ElecModelUpdateReader(UpdatePackageReader[PowerModel]):
    """Applies inbound update packages to a built ``PowerModel``, by package type.

    The built-model counterpart of :class:`ElecUpdateReader`, for a driver that keeps the model
    between solves rather than rebuilding it.  Packages change mutable parameters in place.
    """

    # pyrefly does not see singledispatchmethod as a descriptor, so flags the override
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: PowerModel) -> None:  # type: ignore[bad-override]
        """Reject a package type with no registered handler.

        Parameters
        ----------
        update_package : UpdatePackage
            The package received.
        data : PowerModel
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
    def _(self, ng_price_update: NGPricePackage, data: PowerModel) -> None:
        """Write the received gas price into ``ng_fuel_adj`` for the gas-linked techs.

        Each ``NG_PRICE_LINKED_TECHS`` entry is set to :func:`ng_price_adjustment` of its built
        ``supply_price`` and the price received for its ``(region, year)``, so the effective price
        ``supply_price + ng_fuel_adj`` is what a rebuild with the same package would give, for a
        model built without update packages (``ElectricitySequencer.update_model`` enforces it).
        The package must cover exactly the regions and years the model holds
        (:func:`check_region_year_keys`).  The result depends only on the package, so applying one
        twice is the same as applying it once.

        Parameters
        ----------
        ng_price_update : NGPricePackage
            Gas prices in $/MMBtu indexed by electricity ``(region, year)``.
        data : PowerModel
            The built model; ``ng_fuel_adj`` is modified in place.

        Raises
        ------
        ValueError
            If the package's ``(region, year)`` entries are not exactly the held regions x years.
        """
        received = ng_price_update.elements[NG_PRICE_VALUE]
        check_region_year_keys(received.index, data.regions, data.year, name='NG prices')
        prices = {
            (str(r), int(y)): float(p)
            for (r, y), p in zip(received.index.to_list(), received.to_list(), strict=True)
        }
        written = 0
        for key in data.ng_fuel_adj:
            # ng_fuel_adj and supply_price keys flatten to (region, tech, step, year, season)
            region, tech, _, year, _ = key
            if tech not in NG_PRICE_LINKED_TECHS:
                continue
            price = prices[(str(region), int(year))]
            # pyrefly: ignore[bad-argument-type]  - supply_price is initialized, never None
            supply_price = float(value(data.supply_price[key]))
            data.ng_fuel_adj[key] = ng_price_adjustment(supply_price, price, tech)
            written += 1
        logger.info(
            'Set ng_fuel_adj for %d gas-linked entries (techs %s) from received NG prices',
            written,
            NG_PRICE_LINKED_TECHS,
        )


def ng_price_adjustment(supply_price: float, price: float, tech: str) -> float:
    """Return the change in a gas-linked tech's supply price when gas costs ``price``.

    The one place the gas price link is defined for a built model; changing the link means
    changing only this function.  It is the multiplicative link of the pre-build
    ``NGPricePackage`` handler:  a ``PRICE_COST_PROPORTION`` share of the supply price is taken to
    be fuel bought at ``INITIAL_NG_PRICE``, so ``supply_price + ng_price_adjustment(...)`` equals
    that handler's scaled supply price.

    Parameters
    ----------
    supply_price : float
        The row's supply price as built, in the model's units ($/GWh).
    price : float
        The gas price received for the row's ``(region, year)``, in $/MMBtu.
    tech : str
        The row's technology.  Unused by the multiplicative link; a heat-rate based link needs it.

    Returns
    -------
    float
        The adjustment, in the units of ``supply_price``.
    """
    return supply_price * PRICE_COST_PROPORTION * (price - INITIAL_NG_PRICE) / INITIAL_NG_PRICE
