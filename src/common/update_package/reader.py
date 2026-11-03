"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Abstract reader that applies inbound update packages to a model's loaded data.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from src.common.update_package.update_package import UpdatePackage

logger = logging.getLogger(__name__)


class UpdatePackageReader[DataT](ABC):
    """Applies inbound update packages to one model's loaded data, dispatching on package type.

    Data is modified in place, usually before the recipient model is built from it. A reader
    whose ``DataT`` is the built model instead updates that model between solves.

    Type Parameters
    ---------------
    DataT
        The recipient model's loaded-data object, or built model, the packages are applied to.
    """

    def read(self, update_packages: Sequence[UpdatePackage] | None, data: DataT) -> None:
        """Apply each package, in order, to ``data``.

        Parameters
        ----------
        update_packages : Sequence[UpdatePackage] or None
            Inbound packages; ``None`` or empty is a no-op.
        data : DataT
            The loaded data, modified in place.
        """
        if not update_packages:
            logger.info('%s received no update packages', type(self).__name__)
            return
        logger.info('%s received %d update packages', type(self).__name__, len(update_packages))
        for package in update_packages:
            logger.info('Applying update package: %s', type(package).__name__)
            self.apply_package(package, data)

    @abstractmethod
    def apply_package(self, update_package: UpdatePackage, data: DataT) -> None:
        """Apply one package to ``data``.

        Implement as a ``singledispatchmethod`` whose base case raises ``NotImplementedError`` for
        a package type with no registered handler.

        Parameters
        ----------
        update_package : UpdatePackage
            The package to apply.
        data : DataT
            The loaded data, modified in place.
        """
        ...


def check_region_year_keys(
    received: pd.Index,
    regions: Iterable[Any],
    years: Iterable[Any],
    name: str,
    max_report: int = 10,
) -> None:
    """Require a received ``(region, year)`` index to be exactly the recipient's regions x years.

    The receiving side of the crosswalk check:  with the sender closed under the crosswalk, the
    regions it reaches must be exactly the regions the recipient carries, in every year it carries.

    Parameters
    ----------
    received : pd.Index
        The package's ``(region, year)`` index.
    regions : Iterable
        The regions the recipient carries (e.g. a pyomo Set); compared as ``str``.
    years : Iterable
        The years the recipient carries (e.g. a pyomo Set); compared as ``int``.
    name : str
        What was received, for the message.
    max_report : int, default 10
        Most entries to name for each of the overage and underage.

    Raises
    ------
    ValueError
        If any held ``(region, year)`` is not received (underage) or any received one is not held
        (overage).
    """
    got = {(str(r), int(y)) for r, y in received}
    held_years = [int(y) for y in years]
    held = {(str(r), y) for r in regions for y in held_years}
    over, under = sorted(got - held), sorted(held - got)
    if over or under:
        raise ValueError(
            f'Received {name} must cover exactly the {len(held)} (region, year) entries the '
            f'recipient holds:  {len(under)} missing {under[:max_report]}; {len(over)} not held '
            f'{over[:max_report]} (up to {max_report} of each shown)'
        )
    logger.debug('Received %s covers all %d held (region, year) entries', name, len(held))
