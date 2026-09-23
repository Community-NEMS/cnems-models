"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

Input readers for C-HSM that sit outside the NEMS-style readers in ``module.py``: the Henry Hub
price path, and the supply regions and cost tiers that C-HSM shares with C-NGMM.
"""

import logging
import math
from pathlib import Path

import pandas as pd

from src.models.natural_gas.data import load_region_data, load_supply_cost_tiers
from src.models.natural_gas.ng_config import NGConfig

logger = logging.getLogger(__name__)

# The column names the price files in input/hsm use for the 1987 $ price.
HENRY_HUB_COLUMNS = ('hh_1987_usd_per_mmbtu', 'henry_hub_1987_per_mmbtu')
BRENT_COLUMNS = ('brent_1987_per_bbl',)


def load_price_path(path: Path, columns: tuple[str, ...], name: str) -> dict[int, float]:
    """Read a price path keyed by calendar year, checking every row.

    Parameters
    ----------
    path : Path
        A CSV with a ``year`` column and one of ``columns``.
    columns : tuple[str, ...]
        Accepted names for the price column; the first one found is used.
    name : str
        What the price is, for error messages (e.g. ``'Henry Hub'``).

    Returns
    -------
    dict[int, float]
        Price by year.

    Raises
    ------
    ValueError
        If the file has none of the price columns, has no rows, repeats a year, or has a blank
        or non-finite price.
    """
    df = pd.read_csv(path, comment='#')
    col = next((c for c in columns if c in df.columns), None)
    if col is None:
        raise ValueError(f'{path} has no {name} price column; expected one of {columns}')
    if df.empty:
        raise ValueError(f'{path} has no prices')
    repeated = sorted(df.loc[df['year'].duplicated(), 'year'].astype(int).unique())
    if repeated:
        raise ValueError(f'{path} repeats years {repeated}')
    prices = {int(y): float(p) for y, p in zip(df['year'], df[col], strict=True)}
    bad = sorted(y for y, p in prices.items() if not math.isfinite(p))
    if bad:
        raise ValueError(f'{path} has blank or non-finite prices for years {bad}')
    logger.info('%s path read from %s (%d years)', name, path, len(prices))
    return prices


def load_henry_hub_path(path: Path) -> dict[int, float]:
    """Read a Henry Hub price path, 1987 $/MMBtu by year (see ``load_price_path``)."""
    return load_price_path(path, HENRY_HUB_COLUMNS, 'Henry Hub')


def load_brent_path(path: Path) -> dict[int, float]:
    """Read a Brent price path, 1987 $/bbl by year (see ``load_price_path``)."""
    return load_price_path(path, BRENT_COLUMNS, 'Brent')


def load_ng_supply_side(
    ng_input_path: Path,
) -> tuple[list[str], dict[str, list[tuple[float, float]]]]:
    """Read the supply regions and cost tiers from C-NGMM's input files.

    C-HSM and C-NGMM must agree on the regions and on the base capacity of each cost tier, so
    C-HSM reads them with C-NGMM's own loaders rather than keeping a second copy. C-HSM always
    covers every domestic region, whatever region filter a gas run uses.

    Parameters
    ----------
    ng_input_path : Path
        C-NGMM's input folder, holding ``ng_region_data.csv`` and ``ng_supply_cost_tiers.csv``.

    Returns
    -------
    tuple[list[str], dict[str, list[tuple[float, float]]]]
        The domestic regions (sorted), and ``{region: [(capacity_bcf, cost_per_mmbtu), ...]}``
        in low, medium, high cost order. C-HSM uses only the capacities.
    """
    ng_config = NGConfig(input_path=ng_input_path)
    regions = load_region_data(ng_config)['regions_domestic']
    cost_tiers = load_supply_cost_tiers(ng_config.input_path)
    return regions, cost_tiers
