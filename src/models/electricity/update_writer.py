"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  9/21/26

Update package writer for the electricity model:  packages the solved gas burn for the natural
gas model.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping
from logging import getLogger

import pandas as pd
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NGElectricalDemandPackage,
    UpdatePackage,
    UpdatePackageWriter,
)
from src.integrator.region_crosswalk import QuantityKind
from src.models.electricity.constants import NG_HEAT_RATE_MMBTU_PER_MWH
from src.models.electricity.electricity_model import PowerModel
from src.models.natural_gas.data import load_qp_scalars

logger = getLogger(__name__)

# where the natural gas model's scalars live; the gas burn sent back is denominated in Bcf, so
# the MMBtu-per-Bcf heat content is read from the gas model's own input rather than duplicated
NG_INPUT_DIR = PROJECT_ROOT / 'input' / 'natural_gas' / 'parameters'
# the heat content itself, filled by ng_mmbtu_per_bcf() on first use and then reused; None until
# then, so a run that never sends a demand package never reads the gas model's inputs
_MMBTU_PER_BCF: float | None = None


class ElecUpdateWriter(UpdatePackageWriter[PowerModel]):
    """Writes the electricity model's outbound packages:  its gas burn, for the NG model."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[PowerModel], UpdatePackage]]:
        """The gas-burn package."""
        return {NGElectricalDemandPackage: self.gas_demand_package}

    def gas_demand_package(self, model: PowerModel) -> NGElectricalDemandPackage:
        """Package the solved gas burn for the natural gas model.

        The annual gas demand of the gas-fired techs (:func:`gas_demand_by_region`, Bcf/yr for
        every region and year the model carries) is allocated onto natural gas regions with the
        run's electricity <-> natural gas crosswalk.

        Parameters
        ----------
        model : PowerModel
            A solved model; ``generation_total`` must hold values.

        Returns
        -------
        NGElectricalDemandPackage

        Raises
        ------
        KeyError
            If the writer holds no electricity <-> natural gas crosswalk.
        ValueError
            From ``RegionCrosswalk.apply``, if the model's regions are not closed under the
            crosswalk.
        """
        crosswalk = self.crosswalks.get(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
        ng_demand = crosswalk.apply(
            gas_demand_by_region(model),
            ModelType.ELECTRICITY,
            QuantityKind.EXTENSIVE,
            model_regions=model.regions,
        )
        for year, total in ng_demand.groupby(level='year').sum().items():
            logger.info(
                '%d gas burn sent to natural gas regions:  %0.1f Bcf over %d region(s)',
                year,
                total,
                ng_demand.xs(year, level='year').shape[0],
            )
        return NGElectricalDemandPackage(
            elements=ng_demand.to_frame(), source=ModelType.ELECTRICITY
        )


def ng_mmbtu_per_bcf() -> float:
    """The gas model's MMBtu-per-Bcf heat content, read once from its ``ng_scalars.csv``.

    The first call reads the file under ``NG_INPUT_DIR`` and stores the value in the module-level
    ``_MMBTU_PER_BCF``; later calls return that without touching the file again.

    Returns
    -------
    float
        ``mmbtu_per_bcf`` as the natural gas model itself reads it.
    """
    global _MMBTU_PER_BCF  # a lazily filled module cache, by design
    if _MMBTU_PER_BCF is None:
        _MMBTU_PER_BCF = load_qp_scalars(NG_INPUT_DIR)['mmbtu_per_bcf']
        logger.info('Read mmbtu_per_bcf = %0.4g from %s', _MMBTU_PER_BCF, NG_INPUT_DIR)
    return _MMBTU_PER_BCF


def gas_demand_by_region(instance: PowerModel) -> pd.Series:
    """Annual natural gas burned by the gas-fired techs of a solved model, by region and year.

    Sums the solved ``generation_total`` of every tech in ``NG_HEAT_RATE_MMBTU_PER_MWH`` over
    the representative hours, weighting each hour by the days its representative day stands for
    (the same ``weight_day`` the objective applies), and converts with the tech's heat rate::

        Bcf = MWh x MMBtu/MWh / mmbtu_per_bcf

    with ``mmbtu_per_bcf`` taken from the gas model's ``ng_scalars.csv`` via
    :func:`ng_mmbtu_per_bcf`.

    Parameters
    ----------
    instance : PowerModel
        A solved model; ``generation_total`` must hold values.

    Returns
    -------
    pd.Series
        Demand in Bcf/yr named ``NG_ELEC_DEMAND_VALUE``, indexed by ``NG_ELEC_DEMAND_INDEX``
        (electricity region id as ``str``, model year), sorted.  Every region the model carries
        (``instance.regions``) appears for every model year, with 0.0 where it burns no gas.
    """
    mmbtu: dict[tuple[str, int], float] = defaultdict(float)
    # pyrefly sees the pyomo components declared on PowerModel as None; they are Var/Param
    # instances on a built model, and value() of a solved Var is a float
    for region, tech, step, year, hour in instance.generation_total:  # pyrefly: ignore[not-iterable]
        heat_rate = NG_HEAT_RATE_MMBTU_PER_MWH.get(tech)
        if heat_rate is None:
            continue
        gen_mwh = value(instance.generation_total[region, tech, step, year, hour])
        days = value(instance.weight_day[instance.map_hour_day[hour]])
        # pyrefly: ignore[unsupported-operation]
        mmbtu[(str(region), int(year))] += gen_mwh * days * heat_rate
    if not mmbtu:
        logger.warning(
            'No generation rows for gas-fired techs %s; gas demand is zero everywhere',
            list(NG_HEAT_RATE_MMBTU_PER_MWH),
        )
    mmbtu_per_bcf = ng_mmbtu_per_bcf()
    # every (region, year) the model carries, so a region without gas-fired techs is sent as 0.0
    grid = pd.MultiIndex.from_product(
        [sorted(instance.regions), sorted(int(y) for y in instance.year)],
        names=NG_ELEC_DEMAND_INDEX,
    )
    series = pd.Series(
        [mmbtu.get(key, 0.0) / mmbtu_per_bcf for key in grid],
        index=grid,
        name=NG_ELEC_DEMAND_VALUE,
        dtype=float,
    )
    logger.debug('Polled gas demand for %d (region, year) pairs', len(series))
    return series
