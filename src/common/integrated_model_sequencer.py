"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/16/26

A rough framework for sequencers (runners) that build & solve models to common-ize control signals

"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model import IntegratedModel
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage


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
    """

    model_type: ModelType
    status: IterationStatus
    objective_value: float | None
    update_packages: list[UpdatePackage]

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


class IntegratedModelSequencer[ModelT: IntegratedModel, ConfigT: ModelConfig](ABC):
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

    @abstractmethod
    def get_outbound_updates(self) -> list[UpdatePackage]:
        """Get the outbound update packages."""
        return []

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
        )
