"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/16/26

A model that conforms to the "integration standards"

"""

from abc import ABC, abstractmethod


class IntegratedModel(ABC):
    """Base class for models that conform to the integration standards."""

    @property
    @abstractmethod
    def label(self) -> str:
        """Short human-readable name of the model, for run monitors and logs."""
        raise NotImplementedError()

    @property
    @abstractmethod
    def regions(self) -> frozenset[str]:
        """The regions the built model carries, as ``str`` ids; empty for a model without regions.

        Update package writers and readers check crosswalked data against this set.
        """
        raise NotImplementedError()
