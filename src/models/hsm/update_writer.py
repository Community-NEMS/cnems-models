"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/2/26

Update package writer for C-HSM:  packages US gas production capacity for C-NGMM.
"""

import logging
from collections.abc import Callable, Mapping

import pandas as pd

from src.common.models_modes import ModelType
from src.common.update_package import (
    HSM_CAPACITY_INDEX,
    HSM_CAPACITY_VALUE,
    HSMSupplyCapacityPackage,
    UpdatePackage,
    UpdatePackageWriter,
)
from src.models.hsm.hsm_model import HSMModel

logger = logging.getLogger(__name__)


class HSMUpdateWriter(UpdatePackageWriter[HSMModel]):
    """Writes C-HSM's outbound packages:  its US gas capacity, for C-NGMM."""

    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[HSMModel], UpdatePackage]]:
        """The capacity package."""
        return {HSMSupplyCapacityPackage: self.capacity_package}

    def capacity_package(self, model: HSMModel) -> HSMSupplyCapacityPackage:
        """Sum capacity over cost tier and gas type for each region and summary year.

        Parameters
        ----------
        model : HSMModel
            A run model with ``summary_years`` set.

        Returns
        -------
        HSMSupplyCapacityPackage
            Capacity in Bcf/yr for every region of the model in every summary year.

        Raises
        ------
        ValueError
            If the model has no summary years, its results name a region it does not hold, or
            they miss a region or summary year.
        """
        if not model.summary_years:
            raise ValueError('C-HSM has no summary years to report; build it with a run config')
        regions = model.regions
        totals = {(region, year): 0.0 for region in regions for year in model.summary_years}
        # keys are (region, cost_tier, gas_type, year), or (region, cost_tier, year) with the
        # NA/AD split off.  Years outside the summary years are not sent
        seen: set[tuple[str, int]] = set()
        for (region, *_, year), bcf in model.poll_us_natgas_capacity().items():
            if str(region) not in regions:
                raise ValueError(f'C-HSM results name a region it does not hold:  {region}')
            key = (str(region), int(year))
            if key in totals:
                totals[key] += float(bcf)
                seen.add(key)
        missing = sorted(set(totals) - seen)
        if missing:
            raise ValueError(f'C-HSM results have no capacity for {missing[:10]}')
        series = pd.Series(totals, name=HSM_CAPACITY_VALUE).sort_index()
        series.index = series.index.set_names(HSM_CAPACITY_INDEX)
        by_year = series.groupby(level='year').sum()
        logger.info(
            'C-HSM: US gas capacity for C-NGMM:  %s',
            ', '.join(f'{bcf:,.0f} Bcf ({year})' for year, bcf in by_year.items()),
        )
        return HSMSupplyCapacityPackage(elements=series.to_frame(), source=ModelType.HSM)
