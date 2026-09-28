"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

A rough framework for iterative (integrated) runs that drive several models to a common solution

"""

from abc import ABC, abstractmethod
from typing import Any

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model_sequencer import IterationResult
from src.common.models_modes import ModelType


class IterativeRun(ABC):
    """Base class for an iteration scheme (Jacobi, Gauss-Seidel, ...) over integrated models.

    An implementation owns the whole run:  claiming the output folder, setting up logging,
    building and solving the models each iteration, and routing update packages between them.
    """

    @property
    @abstractmethod
    def label(self) -> str:
        """Short human-readable name of the iteration scheme, for logs."""
        raise NotImplementedError()

    @abstractmethod
    def _process_configs(
        self, common_config: CommonConfig, remainder: dict[str, Any]
    ) -> dict[ModelType, ModelConfig]:
        """Build the config of each model selected by ``common_config.models_to_run``.

        Implementations also warn about config combinations that may give odd integrated
        results (e.g. region filters that leave a partner model partly uncovered).  The model
        configs live with the models, outside ``src.common``, which is why this is left to the
        implementation.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its ``models_to_run`` selects the models.
        remainder : dict
            The config file's sections other than ``[common]``, one per participating model.

        Returns
        -------
        dict of ModelType to ModelConfig
            One config per selected model.
        """
        ...

    @abstractmethod
    def run(
        self, common_config: CommonConfig, remainder: dict[str, Any], **kwargs
    ) -> dict[int, list[IterationResult]]:
        """Run the iterations to completion.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration, not yet holding a scenario output folder.
        remainder : dict
            The config file's sections other than ``[common]``, one per participating model.
        **kwargs
            Scheme-specific run options (e.g. an iteration limit).

        Returns
        -------
        dict of int to list of IterationResult
            Each iteration's model results, keyed by iteration number.
        """
        ...
