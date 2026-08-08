"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

A package of data to apply as an update to the recipient model.

Dev Notes:
    - Plan is to use a registration-handler approach here
    - Everything must be serializable for planned use with multiprocessing
    - Will rely on some enumerations to sort things out for now

"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from src.common.models_modes import ModelType


@dataclass(frozen=True, kw_only=True)
class UpdatePackage(ABC):
    """Base class for a serializable payload handed from one model to another.

    Subclasses carry the actual update data and declare their own ``receivers``.  Frozen so a
    package can be shared across processes without a recipient mutating it.

    Attributes
    ----------
    update_id : UUID
        Unique identifier for this package.
    timestamp : datetime
        Creation time, for ordering and provenance.
    source : ModelType or None
        The model that produced the package, if known.
    version : int
        Payload schema version, to let handlers reject packages they predate.
    """

    update_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.now)
    source: ModelType | None = field(default=None)
    version: int = 1

    @property
    @abstractmethod
    def receivers(self) -> tuple[ModelType, ...]:
        """The models this package is intended for."""
        raise NotImplementedError()


# Dev Note:  This will move later after some experimentation
@dataclass(frozen=True)
class ElectricityPriceScaler(UpdatePackage):
    """Multiply the electricity model's supply prices for a set of techs by a scalar.

    Handled by ``ParamData.apply_update_package``, which scales the matching rows of the
    ``supply_price`` frame in place.

    Attributes
    ----------
    techs : tuple of str
        Tech codes to scale, matched against the ``tech`` level of the ``supply_price`` index.
    receivers : tuple of ModelType
        Fixed to the electricity model.
    scalar : float
        The multiplier to apply; 1.0 leaves prices unchanged.  Must be positive -- the model's
        ``SupplyPrice`` param is declared ``within=NonNegativeReals``.
    """

    techs: tuple[str, ...]
    receivers: tuple[ModelType, ...] = (ModelType.ELECTRICITY,)
    scalar: float = 1.0

    def __post_init__(self) -> None:
        """Reject a scalar that would drive supply prices out of ``NonNegativeReals``.

        Raises
        ------
        ValueError
            If ``scalar`` is not positive.
        """
        if self.scalar <= 0:
            raise ValueError(
                f'{type(self).__name__} requires a positive scalar; got {self.scalar}.  A '
                'non-positive multiplier drives SupplyPrice out of its NonNegativeReals domain.'
            )


my_update_package = ElectricityPriceScaler(techs=('4', '6'), scalar=1.5)  # multiply by 1.5
