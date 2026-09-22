"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

A "Magic" model used for test & development
"""

import logging
from collections.abc import Sequence
from time import sleep

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model import IntegratedModel
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage
from src.models.magic.update_reader import MagicUpdateReader
from src.models.magic.update_writer import MagicUpdateWriter

logger = logging.getLogger(__name__)


class MagicModel(IntegratedModel):
    """Testing Implement."""

    @property
    def label(self) -> str:
        """Display name."""
        return 'Magic'

    def __init__(self, sequence_number: int = 0):
        """Initialize the magic model.

        Parameters
        ----------
        sequence_number : int, default 0
            Seeds the fabricated output signal read by :class:`MagicUpdateWriter`.
        """
        super().__init__()
        self.sequence_number = sequence_number
        sleep(3)
        logger.info('magic model initialized')

    def solve(self):
        """Solve the magic model."""
        sleep(3)
        logger.info('magic model solved')
        return IterationStatus.BEST


class MagicConfig(ModelConfig):
    """Configuration class for MagicModel."""

    def __init__(self, /, **kwargs):
        super().__init__()
        logger.info('MagicConfig initialized with %s', kwargs)


class MagicSequencer(IntegratedModelSequencer[MagicModel, MagicConfig, None]):
    """Testing Implement."""

    def __init__(self):
        super().__init__()
        self._model_config: MagicConfig | None = None
        self._model: MagicModel | None = None
        self._reader = MagicUpdateReader()
        self._writer = MagicUpdateWriter()
        self._last_status: IterationStatus | None = None

    @property
    def model(self) -> MagicModel:
        """The built magic model.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model`.
        """
        if self._model is None:
            raise RuntimeError('MagicModel was not initialized')
        return self._model

    @property
    def reader(self) -> MagicUpdateReader:
        """The no-handler reader shell."""
        return self._reader

    @property
    def writer(self) -> MagicUpdateWriter:
        """Writes the mock updates."""
        return self._writer

    @property
    def last_status(self) -> IterationStatus | None:
        """Status of the most recent solve, or ``None`` before one."""
        return self._last_status

    def build_model(
        self,
        common_config: CommonConfig,
        model_config: MagicConfig,
        update_packages: Sequence[UpdatePackage] | None = None,
        **kwargs,
    ) -> MagicModel:
        """Build the magic model.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : MagicConfig
            Magic model configuration.
        update_packages : Sequence[UpdatePackage], optional
            Updates to read-in data, applied by :class:`MagicUpdateReader`.
        **kwargs
            ``sequence_number`` (int, default 0) seeds the fabricated output signal.

        Returns
        -------
        MagicModel
            The built model (also retained as :attr:`model`).
        """
        self._reader.read(update_packages, None)
        self._model = MagicModel(sequence_number=kwargs.pop('sequence_number', 0))
        self._model_config = model_config
        return self.model

    def update_model(self, **kwargs) -> MagicModel:
        """Not implemented; the magic model is rebuilt each iteration rather than updated.

        Raises
        ------
        NotImplementedError
            Always.
        """
        raise NotImplementedError()

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
        """Solve the magic model.

        Returns
        -------
        tuple[ModelType, IterationStatus]
            :attr:`ModelType.MAGIC` paired with the solve status, always
            :attr:`IterationStatus.BEST`.
        """
        self._last_status = self.model.solve()
        return ModelType.MAGIC, self._last_status

    def full_postprocess(self, **kwargs):
        """No-op; the magic model has no results to write."""
        logger.info('postprocessing magic model...done')

    def iteration_postprocess(self, **kwargs):
        """No-op; the magic model has nothing to do between iterations."""
        logger.info('iterative postprocessing magic model...done')

    def get_objective_value(self) -> float | None:
        """Get ``None``; the magic model has no objective to report."""
        return None
