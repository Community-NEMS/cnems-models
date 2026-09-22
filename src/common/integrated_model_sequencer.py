"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/16/26

A rough framework for sequencers (runners) that build & solve models to common-ize control signals

"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model import IntegratedModel
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage, UpdatePackageReader, UpdatePackageWriter

logger = logging.getLogger(__name__)


class IterationStatus(Enum):
    """A non-pyomo basis for making continuation decisions."""

    BEST = 1
    USABLE = 2
    ERROR = 3


@dataclass
class IterationResult:
    """One model's worth of finished work for a single iteration, returned from a pool worker.

    Must stay picklable -- workers are spawned, so every field crosses a process boundary.

    Attributes
    ----------
    model_type : ModelType
        The model that produced this result.
    status : IterationStatus
        The solve status reported by the model's sequencer.
    objective_value : float | None
        The solved objective value, or ``None`` for models that have no objective.
    update_packages : list of UpdatePackage
        The packages this model wants routed onward to its receivers.
    label : str
        The model's display name (``IntegratedModel.label``); falls back to the model type.
    """

    model_type: ModelType
    status: IterationStatus
    objective_value: float | None
    update_packages: list[UpdatePackage]
    label: str = ''

    def __post_init__(self) -> None:
        """Default the label to the model type's value when the sequencer supplied none."""
        if not self.label:
            self.label = self.model_type.value

    def pprint(self, indent: int = 0) -> str:
        """Render a 4-line summary: model, status, objective value, and update package types.

        Returns
        -------
        str
            The packages are named by class only -- their payloads are not summarized.
        """
        obj_value = 'n/a' if self.objective_value is None else f'{self.objective_value:,.2f}'
        package_names = [type(package).__name__ for package in self.update_packages]
        ind = ' ' * indent if indent else ''
        return ind.join(
            (
                '',
                f'IterationResult for model: {self.model_type.value}\n',
                f'  status:               {self.status.name}\n',
                f'  objective value:      {obj_value}\n',
                f'  update packages sent: {package_names}',
            )
        )


class IntegratedModelSequencer[ModelT: IntegratedModel, ConfigT: ModelConfig, DataT](ABC):
    """A sequencer for a model that may be subject to integrated runs.

    Generic in the model, config, and loaded-data types so an implementation can name the
    concrete types it handles (e.g. ``IntegratedModelSequencer[PowerModel, ElecConfig,
    ParamData]``) without narrowing an inherited parameter type, which would be a Liskov
    violation.

    Type Parameters
    ---------------
    ModelT
        The concrete :class:`~src.common.integrated_model.IntegratedModel` this sequencer builds.
    ConfigT
        The model-specific pydantic config that :meth:`build_model` consumes.
    DataT
        The loaded-data object that :attr:`reader` applies inbound update packages to.

    Attributes
    ----------
    OUTBOUND_STATUSES : frozenset of IterationStatus
        Solve statuses the :attr:`writer` can read results from; any other :attr:`last_status`
        sends no updates.  Override per model.
    """

    OUTBOUND_STATUSES: ClassVar[frozenset[IterationStatus]] = frozenset(
        {IterationStatus.BEST, IterationStatus.USABLE}
    )

    @property
    @abstractmethod
    def model(self) -> ModelT:
        """The model instance this sequencer owns."""
        ...

    @abstractmethod
    def build_model(
        self,
        common_config: CommonConfig,
        model_config: ConfigT,
        update_packages: Sequence[UpdatePackage] | None = None,
        **kwargs,
    ) -> ModelT:
        """Build a model new model instance."""
        ...

    @abstractmethod
    def update_model(self, **kwargs) -> ModelT:
        """Update the model with some new data, etc."""
        ...

    @abstractmethod
    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
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

    @property
    @abstractmethod
    def reader(self) -> UpdatePackageReader[DataT]:
        """Applies inbound update packages to this model's loaded data."""
        ...

    @property
    @abstractmethod
    def writer(self) -> UpdatePackageWriter[ModelT]:
        """Builds this model's outbound update packages from the solved model."""
        ...

    @property
    @abstractmethod
    def last_status(self) -> IterationStatus | None:
        """Status of the most recent solve, or ``None`` before one."""
        ...

    def get_outbound_updates(self) -> list[UpdatePackage]:
        """Get the outbound update packages written by :attr:`writer`.

        Returns
        -------
        list[UpdatePackage]
            The writer's packages, or nothing if :attr:`last_status` is not among
            ``OUTBOUND_STATUSES`` -- an unusable solve leaves no results to read.
        """
        if self.last_status not in self.OUTBOUND_STATUSES:
            logger.warning(
                '%s: no usable solve (status %s); sending no updates',
                type(self).__name__,
                self.last_status,
            )
            return []
        return self.writer.write(self.model)

    @abstractmethod
    def get_objective_value(self) -> float | None:
        """Get the solved objective value, or ``None`` for a model with no objective."""
        return None

    def full_run(
        self, common_config: CommonConfig, model_config: ConfigT, **kwargs
    ) -> IterationResult:
        """All-in-one function for running the model for use in multiprocessing.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : ConfigT
            The model-specific config this sequencer builds from.
        **kwargs
            Forwarded to :meth:`build_model` and :meth:`solve_model`.

        Returns
        -------
        IterationResult
            The solve status, objective value, and any packages bound for other models.
        """
        self.build_model(common_config, model_config, **kwargs)
        model_type, status = self.solve_model(**kwargs)
        # a failed solve leaves no solution loaded, so there is no objective to read
        objective_value = None if status is IterationStatus.ERROR else self.get_objective_value()
        return IterationResult(
            model_type=model_type,
            status=status,
            objective_value=objective_value,
            update_packages=self.get_outbound_updates(),
            label=self.model.label,
        )
