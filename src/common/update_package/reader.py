"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Abstract reader that applies inbound update packages to a model's loaded data.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from src.common.update_package.update_package import UpdatePackage

logger = logging.getLogger(__name__)


class UpdatePackageReader[DataT](ABC):
    """Applies inbound update packages to one model's loaded data, dispatching on package type.

    Data is modified in place, before the recipient model is built from it.

    Type Parameters
    ---------------
    DataT
        The recipient model's loaded-data object the packages are applied to.
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
