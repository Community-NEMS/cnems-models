"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26

A listing of the individual models and run modes

"""

from collections.abc import Sequence
from enum import Enum, unique


@unique
class ModelType(Enum):
    """The individual models available to a run."""

    ALL = 'all'  # indicator implying all models
    ELECTRICITY = 'electricity'
    NATURAL_GAS = 'natural_gas'
    MAGIC = 'magic'  # for testing/dev
    LOAD = 'load'  # hourly load profiles; standalone only for now


@unique
class RunMode(Enum):
    """Defines the different modes that the model can be run in."""

    STANDALONE = 'standalone'
    INTEGRATED_JACOBI = 'integrated jacobi'
    INTEGRATED_GS = 'integrated gs'


# models that ``ModelType.ALL`` does not include; they run only when named explicitly
EXPLICIT_ONLY_MODELS: frozenset[ModelType] = frozenset({ModelType.MAGIC, ModelType.LOAD})


def resolve_models_to_run(models: Sequence[ModelType]) -> list[ModelType]:
    """Expand a ``models_to_run`` list into the concrete models it names.

    ``ModelType.ALL`` expands to every production model, in enum order; the dev/test MAGIC model
    and the LOAD model (which takes no part in integrated runs yet) are included only when also
    named explicitly.  A list without ``ALL`` is returned as given.

    Parameters
    ----------
    models : sequence of ModelType
        The configured ``models_to_run``.

    Returns
    -------
    list of ModelType
        The models to run; never contains ``ModelType.ALL``.
    """
    if ModelType.ALL not in models:
        return list(models)
    excluded = (EXPLICIT_ONLY_MODELS - set(models)) | {ModelType.ALL}
    return [model for model in ModelType if model not in excluded]
