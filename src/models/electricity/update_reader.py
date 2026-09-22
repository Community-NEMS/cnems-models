"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package reader for the electricity model:  applies inbound packages to ``ParamData``.
"""

import logging
from functools import singledispatchmethod

import pandas as pd

from src.common.update_package import (
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    ElectricityPriceScaler,
    NGPricePackage,
    TransCostUpdate,
    UpdatePackage,
    UpdatePackageReader,
)
from src.models.electricity.constants import (
    INITIAL_NG_PRICE,
    NG_PRICE_LINKED_TECHS,
    PRICE_COST_PROPORTION,
    SUPPLY_PRICE_CHANGE_WARN_FRACTION,
)
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
    def _(self, trans_cost_update: TransCostUpdate, data: ParamData) -> None:
        """Merge an incoming transmission cost update into the ``tran_cost`` frame.

        Rows carried by the update replace the matching rows of ``tran_cost``; entries the
        update does not cover keep their loaded values and are reported as warnings.  Entries
        in the update beyond the held index (regions/years filtered out of this run) are
        ignored.

        Parameters
        ----------
        trans_cost_update : TransCostUpdate
            Update package holding a frame indexed like ``tran_cost``
            ``(destination_region, source_region, year)`` with a single ``cost`` column.
        data : ParamData
            The loaded parameter data; ``data.param_frames`` is modified in place.

        Notes
        -----
        Retaining uncovered entries matters:  ``TranCost`` is a dense pyomo Param with no
        default, so a hole in the frame fails model construction.  Incoming values must be in
        the same units as the loaded frame, which the price hack in ``ParamData.__init__`` scales
        by 1000.
        """
        old = data.param_frames['tran_cost']
        new = trans_cost_update.elements
        if old.empty:
            logger.warning(
                'Held tran_cost is empty; taking all %d received rows without a coverage check',
                len(new),
            )
            data.param_frames['tran_cost'] = new
            return
        if set(new.columns) != set(old.columns):
            logger.warning(
                'Received tran_cost columns %s do not match the held columns %s',
                list(new.columns),
                list(old.columns),
            )
        missing = self._report_index_gaps(old.index, new.index, name='tran_cost')
        # align level names so combine_first matches on position, as the gap report does; the
        # reindex drops the overages that combine_first's index union would otherwise carry in
        new = new.rename_axis(old.index.names)
        data.param_frames['tran_cost'] = new.combine_first(old).reindex(old.index)[old.columns]
        logger.info(
            'Updated tran_cost:  %d of %d rows replaced, %d retained from the loaded data',
            len(old) - len(missing),
            len(old),
            len(missing),
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, ng_price_update: NGPricePackage, data: ParamData) -> None:
        """Move the supply price of the gas-linked techs with the received natural gas price.

        A share ``PRICE_COST_PROPORTION`` of each ``NG_PRICE_LINKED_TECHS`` row of ``supply_price``
        is taken to be fuel cost embedding a gas price of ``INITIAL_NG_PRICE``, so the row is
        scaled by ``1 + PRICE_COST_PROPORTION * (price - INITIAL_NG_PRICE) / INITIAL_NG_PRICE``
        using the price received for its ``(region, year)``.  Held rows the package does not
        cover keep their loaded values and are reported as warnings; package entries beyond the
        held regions/years (filtered out of this run) are ignored.

        Parameters
        ----------
        ng_price_update : NGPricePackage
            Gas prices in $/MMBtu indexed by electricity ``(region, year)``.
        data : ParamData
            The loaded parameter data; ``data.param_frames`` is modified in place.

        Notes
        -----
        The adjustment is a ratio, so it is indifferent to the x1000 price hack in
        ``ParamData.__init__``.
        ``SupplyPrice`` is a dense pyomo Param with no default, which is why uncovered rows are
        retained rather than dropped.
        """
        # locate the NG-based techs in the parameter data for supply prices...
        prices = data.param_frames['supply_price']
        tech_mask = prices.index.get_level_values('tech').isin(NG_PRICE_LINKED_TECHS)
        if not tech_mask.any():
            logger.warning(
                'No supply_price rows matched gas-linked techs %s; prices unchanged',
                NG_PRICE_LINKED_TECHS,
            )
            return
        new_price = ng_price_update.elements[NG_PRICE_VALUE]
        factor = 1 + PRICE_COST_PROPORTION * (new_price - INITIAL_NG_PRICE) / INITIAL_NG_PRICE
        held_keys = pd.MultiIndex.from_arrays(
            [prices.index.get_level_values('region'), prices.index.get_level_values('year')],
            names=NG_PRICE_INDEX,
        )
        self._report_index_gaps(
            held_keys[tech_mask].unique(), new_price.index, name='supply_price (NG price)'
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

    @staticmethod
    def _report_index_gaps(
        old: pd.Index, new: pd.Index, name: str, max_report: int = 10
    ) -> pd.Index:
        """Report entries of a held index that newly received data fails to cover.

        Parameters
        ----------
        old : pd.Index
            Index of the currently held data.
        new : pd.Index
            Index of the newly received data.
        name : str
            Frame name, used in the log entries.
        max_report : int, default 10
            Maximum number of missing entries to name in the warning.

        Returns
        -------
        pd.Index
            Entries of ``old`` absent from ``new``; empty if coverage is complete.

        Raises
        ------
        ValueError
            If the indexes have a different number of levels, making them un-alignable.

        Notes
        -----
        Entries of ``new`` absent from ``old`` are overages and are logged at debug level only.
        Level *order* is the contract (see ``index_cols`` in ``param_sources.toml``), so
        differing level names are warned about and then compared by position.
        """
        if old.nlevels != new.nlevels:
            raise ValueError(
                f'Cannot compare the index of {name}:  held data has {old.nlevels} level(s), '
                f'received data has {new.nlevels}.'
            )
        if tuple(old.names) != tuple(new.names):
            logger.warning(
                'Index level names for %s differ:  held %s vs. received %s.  '
                'Comparing by level position',
                name,
                tuple(old.names),
                tuple(new.names),
            )
            new = new.set_names(old.names)
        missing = old.difference(new)
        if len(missing) > 0:
            logger.warning(
                'Received data for %s does not cover %d of %d held entries.  '
                'Missing (up to %d shown):  %s',
                name,
                len(missing),
                len(old),
                max_report,
                list(missing[:max_report]),
            )
        else:
            logger.info('Received data for %s covers all %d held entries', name, len(old))
        overage = len(new.difference(old))
        if overage > 0:
            logger.debug(
                'Received data for %s holds %d entries beyond the held index (ignored)',
                name,
                overage,
            )
        return missing
