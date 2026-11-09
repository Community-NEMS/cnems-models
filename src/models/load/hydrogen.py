"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/7/26

Hydrogen (electrolyzer) demand of the load model.

Hydrogen is industrial end use 4.  It has no load shape, so the load decomposition (basic and delta
approach) leaves it out and the hydrogen demand is added afterwards as an hourly profile in MW, see
``NemsLoad.basic_profile`` / ``delta_profile``.  This module builds that profile:

* from the NEMS hydrogen module (``qelhm_hr``, by EMM region): the hour-of-day pattern of the
  year, spread evenly over all days of the calendar, see :func:`even_profiles_from_nems`;
* from an annual value (the ``euse = 4`` rows of the loads): flat over the year, which
  ``NemsLoad`` does itself when it is given no profile.

It also checks that the loads carry the hydrogen rows (:func:`check_hydrogen_rows`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from .load_model import H2_KEY, NemsLoad
from .nems_aeo_reader import extract_hydrogen_qelhm_hr

logger = logging.getLogger(__name__)

HYDROGEN_ENERGY_RTOL = 1e-9


def even_profile(model: NemsLoad, mwh_by_hour_of_day: np.ndarray) -> np.ndarray:
    """Spread the annual energy of each hour of the day evenly over all days of the year.

    For every hour of the day ``h`` the power is ``MW_h = E_h / n_days``, the same on every day of
    every month and day type, so that ``sum_h MW_h * n_days = sum_h E_h``.

    Parameters
    ----------
    model : NemsLoad
        The load model; supplies the number of days of the calendar and the grid.
    mwh_by_hour_of_day : ndarray
        Annual energy in MWh, one value per hour of the day (24).

    Returns
    -------
    ndarray
        Power in MW at each of the representative points (864); every (month, day type) carries
        the same 24 values.

    Raises
    ------
    ValueError
        If there are not ``hours_per_day`` values or one is negative.
    """
    e = np.asarray(mwh_by_hour_of_day, dtype=float)
    if e.shape != (model.hours_per_day,):
        raise ValueError(f'expected {model.hours_per_day} values, one per hour of the day')
    if (e < 0).any() or not np.isfinite(e).all():
        raise ValueError('hydrogen energy must be finite and not negative')
    return np.tile(e / model.n_days, model.n_months * model.n_daytypes)


def even_profiles_from_nems(
    model: NemsLoad, restart_file: str | Path, years: Iterable[int]
) -> dict[int, dict[int, np.ndarray]]:
    """Build the hydrogen profile of every region and year from the NEMS hydrogen module.

    The module gives the energy of each hour of the day summed over the days of each season
    (``qelhm_hr``).  The four seasons are added up to the annual energy of each hour of the day and
    spread evenly over the year (:func:`even_profile`): the annual energy and the hour-of-day
    pattern are kept, the seasonal pattern is not.

    Parameters
    ----------
    model : NemsLoad
        The load model.
    restart_file : str or Path
        The NEMS restart file.
    years : iterable of int
        The years to build.

    Returns
    -------
    dict of int to dict of int to ndarray
        ``{year: {region: MW at the 864 points}}`` for the 25 regions.

    Raises
    ------
    ValueError
        If the energy of a profile differs from the energy in the file, or the file is not usable
        (see :func:`extract_hydrogen_qelhm_hr`).
    """
    years = list(years)
    long = extract_hydrogen_qelhm_hr(restart_file, years)
    out: dict[int, dict[int, np.ndarray]] = {}
    for (y, r), g in long.groupby(['year', 'region_id']):
        by_hour = g.groupby('hour_of_day').mwh.sum().sort_index().to_numpy()
        profile = even_profile(model, by_hour)
        energy = float((profile * model.weights).sum())
        total = float(by_hour.sum())
        if abs(energy - total) > HYDROGEN_ENERGY_RTOL * max(total, 1.0):
            raise ValueError(f'hydrogen {y} region {r}: profile energy {energy} != file {total}')
        out.setdefault(int(y), {})[int(r)] = profile
    return out


def annual_by_region(profiles: dict[int, dict[int, np.ndarray]], model: NemsLoad) -> pd.DataFrame:
    """Return the annual energy of hydrogen profiles by year and region.

    Parameters
    ----------
    profiles : dict
        ``{year: {region: MW at the points}}`` from :func:`even_profiles_from_nems`.
    model : NemsLoad
        The load model (day weights).

    Returns
    -------
    pd.DataFrame
        Columns ``year, region, mwh``.
    """
    rows = [
        (y, r, float((p * model.weights).sum()))
        for y, by_region in profiles.items()
        for r, p in by_region.items()
    ]
    return pd.DataFrame(rows, columns=['year', 'region', 'mwh'])


def check_hydrogen_rows(
    loads: pd.DataFrame, years: Iterable[int], area: str, areas: Iterable[int], where: str
) -> None:
    """Require hydrogen rows (industrial end use 4) for every area and year.

    A load file that leaves hydrogen out is an error, not zero hydrogen: give the rows with 0 if
    there is none.

    Parameters
    ----------
    loads : pd.DataFrame
        Annual loads with columns ``year, sector, euse, <area>, mwh``.
    years : iterable of int
        The projection years that need the rows.
    area : {'census', 'region'}
        The geography column of ``loads``.
    areas : iterable of int
        The values it must hold (census divisions 1-9 or EMM regions 1-25).
    where : str
        What the table is, for the message (a file name or a source).

    Raises
    ------
    ValueError
        If a row is missing, or a value is empty or negative.
    """
    sector, euse = H2_KEY
    h = loads[(loads.sector == sector) & (loads.euse == euse)]
    need = pd.MultiIndex.from_product(
        [sorted(set(years)), sorted(set(areas))], names=['year', area]
    )
    have = h.set_index(['year', area]).mwh
    missing = need.difference(have.index.tolist())
    if len(missing):
        y, a = missing[0]
        raise ValueError(
            f'{where}: {len(missing)} hydrogen rows are missing (industrial end use 4, '
            f'e.g. year {y}, {area} {a}). Give a row for every year and {area}, with 0 where '
            'there is no hydrogen; hydrogen is never filled in silently.'
        )
    bad = have.loc[need]
    if not np.isfinite(bad).all() or (bad < 0).any():
        raise ValueError(f'{where}: hydrogen mwh must be finite and not negative')
