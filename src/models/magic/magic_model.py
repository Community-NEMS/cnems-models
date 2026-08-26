"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

A "Magic" model used for test & development
"""

import logging
import math
import random
from time import sleep

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model import IntegratedModel
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import (
    ElectricityPriceScaler,
    NGDemandPackage,
    UpdatePackage,
    make_trans_update,
)

logger = logging.getLogger(__name__)


class MagicModel(IntegratedModel):
    """Testing Implement."""

    def __init__(self):
        super().__init__()
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
        self, common_config: CommonConfig, model_config: MagicConfig, update_packages=None, **kwargs
    ) -> MagicModel:
        """Build the magic model.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : MagicConfig
            Magic model configuration.
        update_packages : list[UpdatePackage], optional
            Updates to read-in data.
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

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
        """Solve the magic model.

        Returns
        -------
        tuple[ModelType, IterationStatus]
            :attr:`ModelType.MAGIC` paired with the solve status, always
            :attr:`IterationStatus.BEST`.
        """
        status = self.model.solve()
        return ModelType.MAGIC, status

    def full_postprocess(self, **kwargs):
        """No-op; the magic model has no results to write."""
        logger.info('postprocessing magic model...done')

    def iteration_postprocess(self, **kwargs):
        """No-op; the magic model has nothing to do between iterations."""
        logger.info('iterative postprocessing magic model...done')

    def get_objective_value(self) -> float | None:
        """Get ``None``; the magic model has no objective to report."""
        return None

    def get_outbound_updates(self) -> list[UpdatePackage]:
        """Get outbound updates."""
        # poll the model / sequencer to make the updates and return them
        my_updates = self.make_mock_updates()
        return my_updates

    def make_mock_updates(self) -> list[UpdatePackage]:
        """Make mock updates.

        fabricate a sinusoid from the sequence number with a 10-cycle diminishment.
        centered on 1.0 so the scaler stays positive: SupplyPrice is NonNegativeReals, and a
        bare cosine goes negative for sequence numbers 3-5, 11-13, ...
        """
        cycle_point = self._sequence_number * math.pi / 4
        scale = 2 * 2 ** (-self._sequence_number / 3)
        scalar = 1 + scale * math.cos(cycle_point)
        update = ElectricityPriceScaler(
            receivers=(ModelType.ELECTRICITY,), scalar=scalar, techs=('4', '6')
        )

        # make a transmission cost update
        tcu = make_trans_update(
            new_cost=2000 - 5000 * random.random() * math.e ** (-self._sequence_number),
            year=2030,
        )

        # exponential decay toward 1.4:  exactly 1.0 at sequence number 1 (the first
        # iteration in the control loop), approaching 1.4 as the sequence advances
        ng_scalar = 1.4 - 0.4 * math.exp(-(self._sequence_number - 1) / 3)
        ng_demand = NGDemandPackage(receivers=(ModelType.NATURAL_GAS,), scalar=ng_scalar)

        return [update, tcu, ng_demand]
