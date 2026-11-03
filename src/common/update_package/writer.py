"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Abstract writer that creates a model's outbound update packages.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Mapping

from src.common.integrated_model import IntegratedModel
from src.common.update_package.update_package import UpdatePackage
from src.integrator.region_crosswalk import CrosswalkSet


class UpdatePackageWriter[ModelT: IntegratedModel](ABC):
    """Builds one model's outbound update packages from its solved model instance.

    Which packages it builds is set at construction (``outbound``, normally from the run's
    ``ExchangeSpec``); with none, it writes nothing, as in a standalone run.  Whether the solve is
    usable enough to write from is the sequencer's call; the writer assumes it is.

    Type Parameters
    ---------------
    ModelT
        The :class:`~src.common.integrated_model.IntegratedModel` the packages are read from.
    """

    def __init__(
        self,
        outbound: Collection[type[UpdatePackage]] = frozenset(),
        crosswalks: CrosswalkSet | None = None,
    ) -> None:
        """Set the packages to write and the crosswalks to write them with.

        Parameters
        ----------
        outbound : Collection of type[UpdatePackage], default empty
            The package types to build each :meth:`write`.
        crosswalks : CrosswalkSet, optional
            Region crosswalks to the receiving models; empty if omitted.

        Raises
        ------
        ValueError
            If ``outbound`` names a type this writer has no builder for.
        """
        self._outbound = frozenset(outbound)
        self._crosswalks = crosswalks if crosswalks is not None else CrosswalkSet()
        unsupported = sorted(t.__name__ for t in self._outbound - set(self.builders))
        if unsupported:
            raise ValueError(
                f'{type(self).__name__} cannot write {unsupported}; it writes '
                f'{sorted(t.__name__ for t in self.builders)}'
            )

    @property
    def outbound(self) -> frozenset[type[UpdatePackage]]:
        """The package types this writer builds."""
        return self._outbound

    @property
    def crosswalks(self) -> CrosswalkSet:
        """Region crosswalks to the receiving models."""
        return self._crosswalks

    @property
    @abstractmethod
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[ModelT], UpdatePackage]]:
        """``{package type: builder from the solved model}``, one per type the writer supports."""
        ...

    def write(self, model: ModelT) -> list[UpdatePackage]:
        """Create the outbound update packages.

        Parameters
        ----------
        model : ModelT
            The solved model to read results from.

        Returns
        -------
        list[UpdatePackage]
            One package per type in :attr:`outbound`, ordered by type name; empty when there are
            none, without reading the model.
        """
        return [
            self.builders[package_type](model)
            for package_type in sorted(self._outbound, key=lambda t: t.__name__)
        ]
