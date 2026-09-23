"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

Result tables for a C-HSM run. Everything here reads a model that has already been run and
shapes the results into tables; nothing here changes the model.

Six CSVs are written, all in BCF/yr unless the column name says otherwise:

- ``hsm_us_gas_capacity.csv``: US production capacity by region, cost tier, gas type and year.
  This is what a coupled run would hand to C-NGMM.
- ``hsm_us_legacy_gas.csv``: gas from wells already producing in the base year.
- ``hsm_us_crude.csv``: crude oil from those same wells, thousand barrels (``crude_mbbl``).
- ``hsm_canada_na_prod.csv``, ``hsm_canada_ad_prod.csv``: Canadian non-associated and
  associated-dissolved production.
- ``hsm_canada_realized_na_prod.csv``: a copy of the non-associated production taken at the
  end of each year. It is identical to ``hsm_canada_na_prod.csv`` in a standalone run.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from src.models.hsm.hsm_model import HSMModel

logger = logging.getLogger(__name__)


def _extract_us_gas_capacity(m: HSMModel) -> pd.DataFrame:
    """US capacity as a table: ``region, cost_tier, gas_type, year, capacity_bcf``.

    The ``gas_type`` column is there only when the results carry it (``'na'`` or ``'ad'``),
    which is the case whenever ``us_ad_gas_share.csv`` and ``us_ad_elasticity.csv`` are present.
    """
    rows = []
    for key, v in sorted(m.module.results_us_gas_capacity.items()):
        row = {'region': key[0], 'cost_tier': key[1]}
        if len(key) == 4:
            row['gas_type'] = key[2]
        row.update({'year': key[-1], 'capacity_bcf': v})
        rows.append(row)
    return pd.DataFrame(rows)


def _canada_table(df: pd.DataFrame) -> pd.DataFrame:
    """Rename a Canada result to the C-NEMS column style: ``canada_region, year, production_bcf``.

    The NEMS index name ``NUMCAN`` (1 = east, 2 = west) becomes ``canada_region`` and the bare
    ``value`` column becomes ``production_bcf``. Values and row order are unchanged.
    """
    return df.rename_axis(index={'NUMCAN': 'canada_region'}).rename(
        columns={'value': 'production_bcf'}
    )


def _extract_us_legacy(m: HSMModel) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Gas and crude from wells already producing in the base year.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame] or None
        ``(gas, crude)`` with columns ``region, gas_type, year, gas_bcf`` and
        ``region, gas_type, year, crude_mbbl``. None if the producing-well data did not load.

    Notes
    -----
    If the optional well-level engine ran (see ``us_onshore.py``), crude comes from its last
    simulation, which includes new drilling, instead of from existing wells only.
    """
    legacy_model = getattr(m.module, 'onshore_legacy', None)
    if legacy_model is None:
        return None
    years = sorted({k[-1] for k in m.module.results_us_gas_capacity}) or list(
        range(m.module.history_year + 1, m.module.final_aeo_year + 1)
    )
    legacy = legacy_model.annual_production(years)
    if legacy.empty:
        return None
    crude_src = legacy
    sim = getattr(legacy_model, '_sim_cache', None)
    if sim is not None and not sim.empty:
        crude_src = sim[sim.year.isin(years)]
    return (
        legacy[['region', 'gas_type', 'year', 'gas_bcf']],
        crude_src[['region', 'gas_type', 'year', 'crude_mbbl']],
    )


def report(m: HSMModel, output_dir: Path) -> None:
    """Write the C-HSM result CSVs to ``output_dir``.

    A table with no results (for example, before the model has run) is skipped rather than
    written empty.

    Parameters
    ----------
    m : HSMModel
        A model that has been run.
    output_dir : Path
        Destination folder; created if needed.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    canada = {
        'hsm_canada_na_prod.csv': m.module.results_canada_na_prod,
        'hsm_canada_ad_prod.csv': m.module.results_canada_ad_prod,
        'hsm_canada_realized_na_prod.csv': m.module.results_canada_realized_na_prod,
    }
    for filename, df in canada.items():
        if not df.empty:
            _canada_table(df).to_csv(out / filename)

    if m.module.results_us_gas_capacity:
        _extract_us_gas_capacity(m).to_csv(
            out / 'hsm_us_gas_capacity.csv', index=False, encoding='utf-8'
        )

    legacy = _extract_us_legacy(m)
    if legacy is not None:
        gas, crude = legacy
        gas.to_csv(out / 'hsm_us_legacy_gas.csv', index=False, encoding='utf-8')
        crude.to_csv(out / 'hsm_us_crude.csv', index=False, encoding='utf-8')

    logger.info('C-HSM: results written to %s', out)
