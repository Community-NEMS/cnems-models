"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/4/26

Magic HSM, a test model for the coupling with C-NGMM:  gas production capacity by C-NGMM region
and year that follows C-NGMM's hub price.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from pydantic import ConfigDict, Field, model_validator

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model import IntegratedModel
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import ExchangeSpec, UpdatePackage
from src.models.magic_hsm.update_reader import MagicHSMData, MagicHSMUpdateReader
from src.models.magic_hsm.update_writer import MagicHSMUpdateWriter
from src.models.natural_gas.data import (
    load_region_data,
    load_supply_anchors,
    load_supply_cost_tiers,
)

logger = logging.getLogger(__name__)


class MagicHSMModel(IntegratedModel):
    """Gas production capacity ``Q0 * (P / P_ref) ** e`` by C-NGMM region and year.

    ``Q0`` is C-NGMM's supply anchor at build, and ``P`` the hub price C-NGMM last sent, or
    ``P_ref`` before one arrives.

    Parameters
    ----------
    data : MagicHSMData
        The held data; :class:`MagicHSMUpdateReader` sets its hub price.
    price_elasticity : float
        ``e``.
    reference_price : float
        ``P_ref``, USD/MMBtu of ``data.dollar_year``.
    """

    def __init__(self, data: MagicHSMData, price_elasticity: float, reference_price: float) -> None:
        super().__init__()
        self.data = data
        self.price_elasticity = price_elasticity
        self.reference_price = reference_price
        # Bcf/yr by (region, year); empty until solve
        self.capacity: dict[tuple[str, int], float] = {}

    @property
    def label(self) -> str:
        """Display name."""
        return 'Magic HSM'

    @property
    def regions(self) -> frozenset[str]:
        """C-NGMM's domestic regions."""
        return frozenset(self.data.regions)

    def solve(self) -> IterationStatus:
        """Set :attr:`capacity` from the held hub price.

        Returns
        -------
        IterationStatus
            Always ``IterationStatus.BEST``; there is nothing to optimize.
        """
        self.capacity = {
            (region, year): q0
            * (self.data.hub_price[year] / self.reference_price) ** self.price_elasticity
            for (region, year), q0 in self.data.base_capacity.items()
        }
        return IterationStatus.BEST


class MagicHSMConfig(ModelConfig):
    """Settings from the ``[magic_hsm]`` TOML section.

    Attributes
    ----------
    ng_input_path : Path
        C-NGMM's input folder, for its regions and Q0; in a coupled run, the folder C-NGMM reads.
    price_elasticity : float
        ``e``, in [0, 1].
    reference_price : float
        ``P_ref``, the hub price at which capacity is C-NGMM's Q0, USD/MMBtu of
        ``reference_dollar_year``.
    reference_dollar_year : int
        The dollar year of ``reference_price``; C-NGMM's in a coupled run.
    """

    model_config = ConfigDict(extra='forbid')

    ng_input_path: Path
    price_elasticity: Annotated[float, Field(ge=0, le=1)]
    reference_price: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    reference_dollar_year: int

    @model_validator(mode='after')
    def check_path(self) -> MagicHSMConfig:
        """Resolve ``ng_input_path`` against PROJECT_ROOT and check that it is a directory."""
        self.ng_input_path = PROJECT_ROOT / self.ng_input_path
        if not self.ng_input_path.is_dir():
            raise ValueError(f'ng_input_path {self.ng_input_path} is not a directory')
        return self


class MagicHSMSequencer(IntegratedModelSequencer[MagicHSMModel, MagicHSMConfig, MagicHSMData]):
    """Sequencer for Magic HSM."""

    def __init__(self, exchange: ExchangeSpec | None = None) -> None:
        """Initialize the sequencer.

        Parameters
        ----------
        exchange : ExchangeSpec, optional
            The run's exchanges; this model's writer builds its share of them.  Omitted (a
            standalone run), the writer writes nothing.
        """
        super().__init__()
        exchange = exchange if exchange is not None else ExchangeSpec.empty()
        self._model: MagicHSMModel | None = None
        self._reader = MagicHSMUpdateReader()
        self._writer = MagicHSMUpdateWriter(
            exchange.outbound_for(ModelType.MAGIC_HSM), exchange.crosswalks
        )
        self._last_status: IterationStatus | None = None

    @property
    def model(self) -> MagicHSMModel:
        """The built model.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model`.
        """
        if self._model is None:
            raise RuntimeError('MagicHSMModel was not initialized')
        return self._model

    @property
    def reader(self) -> MagicHSMUpdateReader:
        """Sets the hub price from C-NGMM's."""
        return self._reader

    @property
    def writer(self) -> MagicHSMUpdateWriter:
        """Writes the capacity package for C-NGMM."""
        return self._writer

    @property
    def last_status(self) -> IterationStatus | None:
        """Status of the most recent solve, or ``None`` before one."""
        return self._last_status

    def build_model(
        self,
        common_config: CommonConfig,
        model_config: MagicHSMConfig,
        update_packages: Sequence[UpdatePackage] | None = None,
        **kwargs,
    ) -> MagicHSMModel:
        """Read C-NGMM's regions and Q0, apply inbound packages, and build the model.

        Parameters
        ----------
        common_config : CommonConfig
            Only ``summary_years`` is read:  the model's years, which are C-NGMM's.
        model_config : MagicHSMConfig
            The ``[magic_hsm]`` settings.
        update_packages : Sequence[UpdatePackage], optional
            Applied to the held data by :class:`MagicHSMUpdateReader`.

        Returns
        -------
        MagicHSMModel
            The built model (also retained as :attr:`model`).
        """
        regions = load_region_data(model_config.ng_input_path / 'properties')['regions_domestic']
        parameters = model_config.ng_input_path / 'parameters'
        tiers = load_supply_cost_tiers(parameters)
        anchors = load_supply_anchors(parameters)
        years = sorted({int(y) for y in common_config.summary_years})
        data = MagicHSMData(
            regions=regions,
            years=years,
            # Q0 as NGModel sets it at build:  the tier capacity total times q0_mult
            base_capacity={
                (r, y): sum(cap for cap, _ in tiers[r]) * anchors.get((r, y), (1.0, 1.0))[0]
                for r in regions
                for y in years
            },
            hub_price=dict.fromkeys(years, model_config.reference_price),
            dollar_year=model_config.reference_dollar_year,
        )
        self._reader.read(update_packages, data)
        self._model = MagicHSMModel(
            data, model_config.price_elasticity, model_config.reference_price
        )
        return self._model

    def update_model(self, update_packages: Sequence[UpdatePackage], **kwargs) -> MagicHSMModel:
        """Apply inbound packages to the held data; :meth:`solve_model` recomputes the capacity.

        Parameters
        ----------
        update_packages : Sequence[UpdatePackage]
            Applied in order by :class:`MagicHSMUpdateReader`.

        Returns
        -------
        MagicHSMModel
            The same model.

        Raises
        ------
        RuntimeError
            If called before :meth:`build_model`.
        """
        self._reader.read(update_packages, self.model.data)
        return self.model

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
        """Compute the capacity.

        Returns
        -------
        tuple[ModelType, IterationStatus]
            :attr:`ModelType.MAGIC_HSM` and :attr:`IterationStatus.BEST`.
        """
        self._last_status = self.model.solve()
        return ModelType.MAGIC_HSM, self._last_status

    def full_postprocess(self, **kwargs) -> None:
        """No-op; Magic HSM writes no result files."""

    def iteration_postprocess(self, **kwargs) -> None:
        """No-op; Magic HSM has nothing to do between iterations."""

    def get_objective_value(self) -> float | None:
        """Get ``None``; Magic HSM has no objective."""
        return None
