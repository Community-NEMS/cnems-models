"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Abstract writer that creates a model's outbound update packages.
"""

from abc import ABC, abstractmethod

from src.common.integrated_model import IntegratedModel
from src.common.update_package.update_package import UpdatePackage


class UpdatePackageWriter[ModelT: IntegratedModel](ABC):
    """Builds one model's outbound update packages from its solved model instance.

    Whether the solve is usable enough to write from is the sequencer's call; the writer assumes
    it is.

    Type Parameters
    ---------------
    ModelT
        The :class:`~src.common.integrated_model.IntegratedModel` the packages are read from.
    """

    @abstractmethod
    def write(self, model: ModelT) -> list[UpdatePackage]:
        """Create the outbound update packages.

        Parameters
        ----------
        model : ModelT
            The solved model to read results from.

        Returns
        -------
        list[UpdatePackage]
            Packages bound for other models.
        """
        ...
