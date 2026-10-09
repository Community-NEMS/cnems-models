"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/16/26

A model that conforms to the "integration standards"

"""

from abc import ABC, abstractmethod
from collections.abc import Hashable
from warnings import deprecated


class IntegratedModel(ABC):
    """Base class for models that conform to the integration standards.

    Also carries the ownership registry. A model coupled to another may no longer own all of its
    inputs, and its internal updates must leave the externally supplied ones alone. The
    integrator declares that once at wire-up rather than passing an exclusion at every call, so
    no model has to learn another's vocabulary.
    """

    @property
    @abstractmethod
    def label(self) -> str:
        """Short human-readable name of the model, for run monitors and logs."""
        raise NotImplementedError()

    @deprecated('not needed')
    def declare_external(self, quantity: str, *index: Hashable) -> None:
        """Record that another model owns this slice, so internal updates must leave it alone.

        The registry is created on first use. The pyomo model subclasses call
        ``ConcreteModel.__init__`` directly rather than a cooperative ``super().__init__``, so an
        ``__init__`` here would not run for them.

        Parameters
        ----------
        quantity : str
            Name of the owned quantity, a constant published by the model whose input it is, for
            example ``NGModel.DEMAND``.
        *index : Hashable
            The slice, in the quantity's own index order. Matching is exact.
        """
        try:
            owned = self._external_owned
        except AttributeError:
            owned = self._external_owned = set()
        owned.add((quantity, *index))

    @deprecated('not needed')
    def is_external(self, quantity: str, *index: Hashable) -> bool:
        """Return whether this exact slice has been declared owned by another model.

        Matching is exact rather than by prefix or wildcard, so a declaration cannot protect
        more than it names.

        Parameters
        ----------
        quantity : str
            Name of the quantity.
        *index : Hashable
            The slice, in the quantity's own index order.

        Returns
        -------
        bool
            True when the slice has been declared external.
        """
        return (quantity, *index) in getattr(self, '_external_owned', frozenset())
