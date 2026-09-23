"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

The C-HSM model object. It wraps ``HSMModule`` (``module.py``), which does the work one year at
a time, and holds what a sequencer or a coupling loop needs: the model years, a way to run them
all, a way to change prices, and the results to read back.
"""

import logging
import math
from pathlib import Path

import pandas as pd

from src.common.integrated_model import IntegratedModel
from src.models.hsm.data import load_brent_path, load_henry_hub_path
from src.models.hsm.hsm_config import HSMConfig
from src.models.hsm.module import HSMModule

logger = logging.getLogger(__name__)


class HSMModel(IntegratedModel):
    """C-HSM: US gas supply capacity and Canadian gas production, year by year.

    The model years run from ``history_year`` to ``final_aeo_year`` in ``input/hsm/setup.csv``
    (2023 to 2050). C-HSM is not an optimization model: each year is computed from prices and the
    state carried from the year before, so the years must be run in order.

    Parameters
    ----------
    hsm_config : HSMConfig
        The ``[hsm]`` settings.
    regions : list[str]
        Supply regions, from C-NGMM's ``ng_region_data.csv``.
    supply_cost_tiers : dict[str, list[tuple[float, float]]]
        ``{region: [(capacity_bcf, cost_per_mmbtu), ...]}`` in low, medium, high cost order,
        from C-NGMM's ``ng_supply_cost_tiers.csv``.
    henry_hub_prices : dict[int, float]
        Henry Hub price (1987 $/MMBtu) for every model year.
    output_path : Path
        Where optional debug files go when the debug switches in ``setup.csv`` are on.

    Raises
    ------
    ValueError
        If ``henry_hub_prices``, or either reference path (``hh_reference_path.csv``,
        ``brent_reference_path.csv``), misses a model year or has a blank or non-finite price.
        ``HSMModule`` would quietly use a flat 3.5 or 4.0 for a missing year, which is never
        what anyone wants.
    """

    def __init__(
        self,
        hsm_config: HSMConfig,
        regions: list[str],
        supply_cost_tiers: dict[str, list[tuple[float, float]]],
        henry_hub_prices: dict[int, float],
        output_path: Path,
    ) -> None:
        self.module = HSMModule(
            input_path=hsm_config.input_path,
            output_path=output_path,
            regions=regions,
            supply_cost_tiers=supply_cost_tiers,
            henry_hub_prices=henry_hub_prices,
            brent_prices={},
            brent_multiplier=hsm_config.brent_multiplier,
            calibration_file=hsm_config.calibration_file,
            onshore_engine_config=(
                Path(hsm_config.input_path) / hsm_config.onshore_engine_file
                if hsm_config.onshore_engine_file
                else None
            ),
        )
        self.years = list(range(self.module.history_year, self.module.final_aeo_year + 1))
        self._check_price_path(henry_hub_prices, 'Henry Hub')
        # The reference paths fill in whenever a price update is empty, and they set the
        # reference prices for the NA/AD split, so they must cover every year as well.
        input_path = Path(hsm_config.input_path)
        self._check_price_path(
            load_henry_hub_path(input_path / 'hh_reference_path.csv'), 'Henry Hub reference'
        )
        self._check_price_path(
            load_brent_path(input_path / 'brent_reference_path.csv'), 'Brent reference'
        )

    def _check_price_path(self, prices: dict[int, float], name: str) -> None:
        """Raise ``ValueError`` unless ``prices`` has a finite price for every model year."""
        missing = [y for y in self.years if y not in prices]
        if missing:
            raise ValueError(f'{name} path is missing model years {missing}')
        bad = sorted(y for y in self.years if not math.isfinite(float(prices[y])))
        if bad:
            raise ValueError(f'{name} path has blank or non-finite prices for years {bad}')

    def run(self) -> None:
        """Set up the Canada submodule, then run every model year in order."""
        logger.info('C-HSM: setting up the Canada submodule')
        self.module.setup_canada()
        logger.info('C-HSM: running years %d to %d', self.years[0], self.years[-1])
        for year in self.years:
            self.module.run_year(year)
        logger.info('C-HSM: run complete')

    def update_prices(self, henry_hub: dict[int, float], brent: dict[int, float]) -> None:
        """Replace the price paths before the next run.

        Parameters
        ----------
        henry_hub : dict[int, float]
            Henry Hub price (1987 $/MMBtu) by year. Empty means the reference path in
            ``hh_reference_path.csv``; otherwise it needs a finite price for every model year.
        brent : dict[int, float]
            Brent price (1987 $/bbl) by year. Empty means the reference path in
            ``brent_reference_path.csv``; otherwise it needs a finite price for every model year.
        """
        if henry_hub:
            self._check_price_path(henry_hub, 'Henry Hub')
        if brent:
            self._check_price_path(brent, 'Brent')
        self.module.update_prices(henry_hub, brent)

    def poll_us_natgas_capacity(self) -> dict[tuple, float]:
        """US gas production capacity, BCF/yr.

        Returns
        -------
        dict[tuple, float]
            ``{(region, cost_tier, gas_type, year): capacity_bcf}``, where ``gas_type`` is
            ``'na'`` (non-associated) or ``'ad'`` (associated-dissolved). Empty before ``run``.
        """
        return dict(self.module.results_us_gas_capacity)

    def poll_canada_natgas_production(self) -> pd.DataFrame:
        """Canadian non-associated gas production, BCF/yr.

        Returns
        -------
        pd.DataFrame
            Indexed by ``(NUMCAN, year)`` (1 = east, 2 = west), one column ``'value'``.
            Empty before ``run``.
        """
        return self.module.results_canada_na_prod.copy()

    def poll_canada_adgas_production(self) -> pd.DataFrame:
        """Canadian associated-dissolved gas production, BCF/yr, shaped like the one above."""
        return self.module.results_canada_ad_prod.copy()
