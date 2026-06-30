"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26

A listing of the individual models that may grow

"""

from enum import Enum, unique


@unique
class ModelType(Enum):
    ELECTRICITY = 'electricity'
    NATURAL_GAS = 'natural_gas'
    INTEGRATOR = 'integrator'


@unique
class RunMode(Enum):
    """Defines the different modes that the model can be run in."""

    STANDALONE = 'standalone'
