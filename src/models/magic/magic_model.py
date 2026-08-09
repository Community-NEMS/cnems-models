"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

A "Magic" model used for test & development
"""

import logging
import math
from time import sleep

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model import IntegratedModel
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import ElectricityPriceScaler, UpdatePackage

logger = logging.getLogger(__name__)


class MagicModel(IntegratedModel):
    """Testing Implement."""

    def __init__(self):
        super().__init__()
        sleep(5)
        logger.info('magic model initialized')

    def solve(self):
        """Solve the magic model."""
        logger.info('magic model solved')
        return IterationStatus.BEST


class MagicConfig(ModelConfig):
    """Configuration class for MagicModel."""

    def __init__(self, /, **kwargs):
        super().__init__()
        logger.info('MagicConfig initialized with %s', kwargs)


class MagicSequencer(IntegratedModelSequencer[MagicModel, MagicConfig]):
    """Testing Implement."""

    def __init__(self):
        super().__init__()
        self._model_config: MagicConfig | None = None
        self._model: MagicModel | None = None
        self._sequence_number: int = 0

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

    def build_model(
        self, common_config: CommonConfig, model_config: MagicConfig, **kwargs
    ) -> MagicModel:
        """Build the magic model.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : MagicConfig
            Magic model configuration.
        **kwargs
            ``sequence_number`` (int, default 0) seeds the fabricated output signal.

        Returns
        -------
        MagicModel
            The built model (also retained as :attr:`model`).
        """
        self._sequence_number = kwargs.pop('sequence_number', 0)
        self._model = MagicModel()
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

    def solve_model(self, **kwargs):
        """Solve the magic model.

        Returns
        -------
        IterationStatus
            Always :attr:`IterationStatus.BEST`.
        """
        status = self.model.solve()
        return ModelType.MAGIC, status

    def full_postprocess(self, **kwargs):
        """No-op; the magic model has no results to write."""
        logger.info('postprocessing magic model...done')

    def iteration_postprocess(self, **kwargs):
        """No-op; the magic model has nothing to do between iterations."""
        logger.info('iterative postprocessing magic model...done')

    def get_outbound_updates(self) -> list[UpdatePackage]:
        """Get outbound updates."""
        # fabricate a sinusoid from the sequence number with a 10-cycle diminishment.
        # centered on 1.0 so the scaler stays positive: SupplyPrice is NonNegativeReals, and a
        # bare cosine goes negative for sequence numbers 3-5, 11-13, ...
        cycle_point = self._sequence_number * math.pi / 4
        scale = 2 * 2 ** (-self._sequence_number / 3)
        scalar = 1 + scale * math.cos(cycle_point)
        update = ElectricityPriceScaler(
            receivers=(ModelType.ELECTRICITY,), scalar=scalar, techs=('4', '6')
        )
        return [update]
