"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

A rough framework for iterative sequencers that drive several integrated models to a common
solution

"""

import logging
import tomllib
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model_sequencer import IterationResult
from src.common.models_modes import ModelType

logger = logging.getLogger(__name__)


class RunStatus(Enum):
    """How an iterative run ended.

    Attributes
    ----------
    CONVERGED
        Every model met the scheme's convergence criteria.
    ITERATION_LIMIT
        The iteration limit was reached before convergence.
    UNKNOWN
        The run ended without the scheme determining why.
    ERROR
        The run ended on a failure it could not recover from.
    """

    CONVERGED = 1
    ITERATION_LIMIT = 2
    UNKNOWN = 3
    ERROR = 4


class IterativeSequencerConfig(BaseModel):
    """A clear label for iterative sequencer configurations.

    Unknown keys are rejected, so a mistyped setting in the TOML fails rather than being ignored.
    """

    model_config = ConfigDict(extra='forbid')


class IterativeSequencer[ConfigT: IterativeSequencerConfig](ABC):
    """Base class for an iteration scheme (Jacobi, Gauss-Seidel, ...) over integrated models.

    An implementation owns the whole run:  claiming the output folder, setting up logging,
    building and solving the models each iteration, and routing update packages between them.
    Its own settings (iteration limit, convergence tolerance, ...) come from a TOML file that
    lives beside the implementation, read when the sequencer is created.

    Type Parameters
    ---------------
    ConfigT
        The :class:`IterativeSequencerConfig` subclass holding this scheme's settings.
    """

    def __init__(self) -> None:
        """Load the scheme's settings from its default config file."""
        self._config: ConfigT = self._read_config(self._default_config_path)

    @property
    def config(self) -> ConfigT:
        """The scheme's settings."""
        return self._config

    @property
    @abstractmethod
    def label(self) -> str:
        """Short human-readable name of the iteration scheme, for logs."""
        raise NotImplementedError()

    @property
    @abstractmethod
    def _config_class(self) -> type[ConfigT]:
        """The config class the TOML is validated against."""
        ...

    @property
    @abstractmethod
    def _default_config_path(self) -> Path:
        """The implementation's own config file, read by default."""
        ...

    def _read_config(self, path: Path) -> ConfigT:
        """Read and validate a scheme config file.

        Parameters
        ----------
        path : Path
            A TOML file whose top-level keys are the fields of :attr:`_config_class`.

        Returns
        -------
        ConfigT
            The validated settings.

        Raises
        ------
        FileNotFoundError
            If ``path`` does not exist.
        pydantic.ValidationError
            If the file's settings do not validate.
        """
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        config = self._config_class(**data)
        logger.info('%s settings from %s: %s', self.label, path, config)
        return config

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
    ) -> tuple[RunStatus, dict[int, list[IterationResult]]]:
        """Run the iterations to completion.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration, not yet holding a scenario output folder.
        remainder : dict
            The config file's sections other than ``[common]``, one per participating model.
        **kwargs
            Scheme-specific run options.

        Returns
        -------
        tuple of (RunStatus, dict of int to list of IterationResult)
            How the run ended, and each iteration's model results, keyed by iteration number.
        """
        ...
