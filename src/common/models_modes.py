"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26

A listing of the individual models and run modes

"""

from enum import Enum, unique


@unique
class ModelType(Enum):
    """The individual models available to a run."""

    ALL = 'all'  # indicator implying all models
    ELECTRICITY = 'electricity'
    NATURAL_GAS = 'natural_gas'
    # INTEGRATOR = 'integrator'
    MAGIC = 'magic'  # for testing/dev


@unique
class RunMode(Enum):
    """Defines the different modes that the model can be run in."""

    STANDALONE = 'standalone'
    INTEGRATED = 'integrated'
