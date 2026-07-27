"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/16/26

A rough framework for sequencers (runners) that build & solve models to common-ize control signals

"""

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel

from src.common.common_config import CommonConfig
from src.common.integrated_model import IntegratedModel


class IterationStatus(Enum):
    """A non-pyomo basis for making continuation decisions."""

    BEST = 1
    USABLE = 2
    ERROR = 3


class IntegratedModelSequencer[ModelT: IntegratedModel, ConfigT: BaseModel](ABC):
    """A sequencer for a model that may be subject to integrated runs.

    Generic in the model and config types so an implementation can name the concrete pair it
    handles (e.g. ``IntegratedModelSequencer[PowerModel, ElecConfig]``) without narrowing an
    inherited parameter type, which would be a Liskov violation.

    Type Parameters
    ---------------
    ModelT
        The concrete :class:`~src.common.integrated_model.IntegratedModel` this sequencer builds.
    ConfigT
        The model-specific pydantic config that :meth:`build_model` consumes.
    """

    @property
    @abstractmethod
    def model(self) -> ModelT:
        """The model instance this sequencer owns."""
        ...

    @abstractmethod
    def build_model(self, common_config: CommonConfig, model_config: ConfigT, **kwargs) -> ModelT:
        """Build a model new model instance."""
        ...

    @abstractmethod
    def update_model(self, **kwargs) -> ModelT:
        """Update the model with some new data, etc."""
        ...

    @abstractmethod
    def solve_model(self, **kwargs) -> IterationStatus:
        """Solve the model."""
        ...

    @abstractmethod
    def full_postprocess(self, **kwargs):
        """Perform "full" postprocessing of the model results."""
        ...

    @abstractmethod
    def iteration_postprocess(self, **kwargs):
        """Perform postprocessing of the model results for each iteration."""
        ...
