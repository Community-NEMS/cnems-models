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

import pandas as pd

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

# index/value labels of the electricity model's ``tran_cost`` frame, which a TransCostUpdate
# must mirror -- see the "tran_cost" entry in src/models/electricity/param_sources.toml
TRANS_COST_INDEX = ['destination_region', 'source_region', 'year']


@dataclass(frozen=True)
class TransCostUpdate(UpdatePackage):
    """Collection of updates to transmission costs.

    Attributes
    ----------
    elements : pd.DataFrame
        Costs indexed by ``TRANS_COST_INDEX`` with a single ``TRANS_COST_VALUE`` column, matching
        the recipient's ``tran_cost`` frame.  Entries the recipient does not hold are ignored;
        held entries this frame omits keep their existing values and are logged as warnings.
    receivers : tuple of ModelType
        Fixed to the electricity model.
    """

    elements: pd.DataFrame
    receivers: tuple[ModelType, ...] = (ModelType.ELECTRICITY,)


def make_trans_update(new_cost: float, year: int) -> TransCostUpdate:
    """Cheap maker for a TransCostUpdate covering all region pairs in a single year.

    Parameters
    ----------
    new_cost : float
        Cost to apply to every ordered pair of distinct regions.
    year : int
        The year the costs apply to.

    Returns
    -------
    TransCostUpdate
        Package holding a frame indexed by ``TRANS_COST_INDEX``.
    """
    costs = []
    for region in (str(num) for num in range(1, 24)):
        for other in (str(num) for num in range(1, 24)):
            if region != other:
                costs.append((region, other, year, new_cost))
    df = pd.DataFrame(costs, columns=[*TRANS_COST_INDEX, 'cost'])
    df = df.set_index(TRANS_COST_INDEX)
    return TransCostUpdate(elements=df)
