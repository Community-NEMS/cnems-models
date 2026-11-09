"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Day-type calendars for the load-profile model.

A calendar says, for every day of one year, whether it is a weekday (1), a weekend / holiday (2) or
the peak day of its month (3).  load_model.NemsLoad counts the days of each (month, day type) in it
to get the day weights, and uses the same calendar to lay the 864 representative points on the 8760
hours.  All the date logic is here; the model only reads a finished calendar.

Ready-made calendars
--------------------
2018  the NEMS calendar.  Its day counts equal V_SYS_LOAD_WEIGHTS (240 weekday / 113 weekend / 12
      peak days).  Weekend = Saturday, Sunday and the 9 weekday holidays below; the peak day of a
      month is the non-holiday weekday nearest the 15th.  The holiday dates and the peak rule are a
      reconstruction that reproduces the weights: they are NOT stated in the NEMS repository.
      Needs no data.
2023  built from an hourly load file base_load_2023.csv (25 regions, 8760 hours) every time you
      run: weekend = Saturday, Sunday and the 11 US federal holidays (observed dates); peak = the
      non-holiday weekday of the month with the highest system load.  This is the default, so it
      needs the load file (``load_dir`` in ``[load_config]``).  If the load file is missing or
      unusable and you did not ask for 2023 by name, get_calendar uses 2018 and says so (it never
      falls back silently, and never when you named the year).  Nothing is stored: the calendar is
      rebuilt from the data at each run (the run writes the calendar it used into its output
      folder).

Any other year (or your own holidays / peak days)
-------------------------------------------------
You must give both
  holidays    "federal" (US federal holidays, observed dates), or your own list of (month, day) /
              dates
  peak days   {month: day} for the 12 months, or a load file (the peak is then the best weekday of
              each month)
or get_calendar stops with an error that says what is missing.  Given these, every other day is
classified by its real weekday: Saturday and Sunday = weekend, a listed holiday = weekend, a listed
peak day = peak, all the rest = weekday.

    from src.models.load.calendars import get_calendar
    cal, notes = get_calendar(load_csv="input/load")     # 2023 from base_load_2023.csv (else 2018)
    cal, notes = get_calendar(2018)                      # the NEMS calendar
    cal, notes = get_calendar(2025, load_csv="input/load", holidays="federal")
    cal, notes = get_calendar(2025, peak_days={1: 10, 2: 7, ...}, holidays=[(1, 1), (7, 4)])

Assumption for a load file: hour 1 of the file is 00:00-01:00 on 1 January of the year (the file
has no dates), and the load of the 25 regions is summed (one calendar for all regions, as in NEMS).
"""

from __future__ import annotations

import calendar as _calendar
import datetime as dt
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# the one place the three day types are named
DAY_TYPE_NAME = {
    1: 'weekday',
    2: 'weekend',
    3: 'peak',
}
DAY_TYPE_NUMBER = {name: number for number, name in DAY_TYPE_NAME.items()}
DEFAULT_YEAR = 2023
NEMS_YEAR = 2018

# NEMS reconstruction: 9 weekday holidays of 2018 (also valid for 2029, same weekday layout)
NEMS_2018_HOLIDAYS = {
    (1, 1): "New Year's Day",
    (2, 19): 'Presidents Day',
    (5, 28): 'Memorial Day',
    (7, 4): 'Independence Day',
    (9, 3): 'Labor Day',
    (10, 8): 'Columbus Day',
    (11, 12): 'Veterans Day (observed)',
    (11, 22): 'Thanksgiving',
    (12, 25): 'Christmas Day',
}
# US federal holidays 2023, observed dates (1 Jan was a Sunday -> Mon 2 Jan; 11 Nov a Saturday ->
# Fri 10 Nov)
HOLIDAYS_2023 = {
    (1, 2): "New Year's Day (observed)",
    (1, 16): 'Martin Luther King Jr. Day',
    (2, 20): 'Presidents Day',
    (5, 29): 'Memorial Day',
    (6, 19): 'Juneteenth',
    (7, 4): 'Independence Day',
    (9, 4): 'Labor Day',
    (10, 9): 'Columbus Day',
    (11, 10): 'Veterans Day (observed)',
    (11, 23): 'Thanksgiving',
    (12, 25): 'Christmas Day',
}


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def load_calendar(path: str | Path) -> pd.DataFrame:
    """Read a day-type calendar: one row per calendar day.

    Only reads; use ``data_validation.check_calendar`` to check it.

    Parameters
    ----------
    path : str or Path
        CSV with the columns ``date``, ``month``, ``day_type`` (other columns are ignored).
        ``day_type`` is the number used in the tables (1 weekday, 2 weekend, 3 peak day) or the
        words weekday / weekend / peak.

    Returns
    -------
    pd.DataFrame
        Columns ``date`` (datetime), ``month``, ``day_type`` (numbers).

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f'missing input file: {path}')
    return to_model_calendar(pd.read_csv(path, parse_dates=['date']))


def to_model_calendar(cal: pd.DataFrame) -> pd.DataFrame:
    """Reduce a calendar to the three columns the model needs, with day types as numbers 1/2/3.

    A word that is not weekday / weekend / peak becomes NaN, which
    ``data_validation.check_calendar`` reports.

    Parameters
    ----------
    cal : pd.DataFrame
        A calendar with the columns ``date``, ``month``, ``day_type`` (numbers or words).

    Returns
    -------
    pd.DataFrame
        Columns ``date``, ``month``, ``day_type`` (numbers).
    """
    out = cal[['date', 'month', 'day_type']].copy()
    if not pd.api.types.is_numeric_dtype(out['day_type']):
        out['day_type'] = out['day_type'].astype(str).str.strip().str.lower().map(DAY_TYPE_NUMBER)
    return out.reset_index(drop=True)


def load_file_path(load_csv: str | Path, year: int) -> str:
    """Return where the hourly load file of ``year`` is.

    The file is named ``base_load_<year>.csv``.

    Parameters
    ----------
    load_csv : str or Path
        Any of: that file itself (any name works); a folder, in which ``base_load_<year>.csv`` is
        used, so one folder can hold the files of several years; or a name containing ``{year}``,
        e.g. ``"input/load/base_load_{year}.csv"``.
    year : int
        The calendar year.

    Returns
    -------
    str
        The path of the file.
    """
    load_csv = str(load_csv)
    if Path(load_csv).is_dir():
        return str(Path(load_csv) / f'base_load_{year}.csv')
    return load_csv.replace('{year}', str(year))


def display_path(path: str | Path) -> str:
    """Return a path as it is shown in logs: relative to the working folder, else its name only.

    Keeps the absolute location of the user's machine out of log files that get shared.

    Parameters
    ----------
    path : str or Path
        A file or folder.

    Returns
    -------
    str
        The path relative to the current working directory (forward slashes), or the last part
        of the path when it lies outside the working directory.
    """
    p = Path(path)
    try:
        return p.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.name


def read_system_load(path: str | Path, year: int) -> pd.Series:
    """Read the hourly system load (MWh) of ``year``.

    Hour 1 is 00:00-01:00 on 1 January.  Only the system total is used (the peak days are chosen
    on it).

    Parameters
    ----------
    path : str or Path
        The load file or its folder, see :func:`load_file_path`.  Columns: ``hour`` (1..8760 or
        8784) and ``base_load_mwh``; an optional ``region`` column is allowed, in which case the
        regions are summed.
    year : int
        The calendar year.

    Returns
    -------
    pd.Series
        Hourly load named ``load_mwh``, indexed by timestamp.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If columns, hours or values are missing, repeated or not numeric.
    """
    path = load_file_path(path, year)
    if not Path(path).exists():
        raise FileNotFoundError(
            f'load file not found: {path} (give base_load_{year}.csv, or the folder that holds it)'
        )
    d = pd.read_csv(path)
    missing = [c for c in ('hour', 'base_load_mwh') if c not in d.columns]
    if missing:
        raise ValueError(
            f'{path}: missing columns {missing} (needs hour, base_load_mwh; region is optional)'
        )
    n = 24 * (366 if _calendar.isleap(year) else 365)
    if not pd.api.types.is_numeric_dtype(d['base_load_mwh']) or d['base_load_mwh'].isna().any():
        raise ValueError(f'{path}: base_load_mwh has missing or non-numeric values')
    if 'region' in d.columns:
        if not d.groupby('region').size().eq(n).all():
            raise ValueError(f'{path}: every region needs {n} hours')
    elif d.duplicated('hour').any():
        raise ValueError(
            f'{path}: hours are repeated; add a region column or give one row per hour'
        )
    system = d.groupby('hour').base_load_mwh.sum().sort_index()
    if len(system) != n or not (system.index == range(1, n + 1)).all():
        raise ValueError(f'{path}: {year} needs hours 1..{n}, found {len(system)} distinct hours')
    return pd.Series(
        system.to_numpy(),
        index=pd.date_range(f'{year}-01-01', periods=n, freq='h'),
        name='load_mwh',
    )


# --------------------------------------------------------------------------- #
# building
# --------------------------------------------------------------------------- #
def federal_holidays(year: int) -> dict[dt.date, str]:
    """Return the US federal holidays of ``year`` (observed dates) from pandas' federal calendar.

    Parameters
    ----------
    year : int
        The calendar year.

    Returns
    -------
    dict of date to str
        ``{date: name}``.
    """
    from pandas.tseries.holiday import USFederalHolidayCalendar

    start, end = (
        pd.Timestamp(year, 1, 1).to_pydatetime(),
        pd.Timestamp(year, 12, 31).to_pydatetime(),
    )
    h = USFederalHolidayCalendar().holidays(start, end, return_name=True)
    return {pd.Timestamp(str(d)).date(): str(n) for d, n in h.items()}


def holiday_dict(year: int, holidays: str | Mapping | Collection | None) -> dict[dt.date, str]:
    """Return ``{date: name}`` of the holidays of ``year``.

    Parameters
    ----------
    year : int
        The calendar year.
    holidays : str, dict, list or None
        ``"federal"``, a dict or list of (month, day) pairs / dates, or None (no holidays).

    Returns
    -------
    dict of date to str
        The holidays; a holiday given without a name is called ``'holiday'``.

    Raises
    ------
    ValueError
        If ``holidays`` is a string other than ``"federal"``, or a holiday is not in ``year``.
    """
    if holidays is None:
        return {}
    if isinstance(holidays, str):
        if holidays.lower() == 'federal':
            return federal_holidays(year)
        raise ValueError(
            f'holidays must be "federal" or a list of (month, day) pairs / dates, not {holidays!r}'
        )
    items = holidays.items() if isinstance(holidays, dict) else [(h, '') for h in holidays]
    out = {}
    for k, name in items:
        d = dt.date(year, int(k[0]), int(k[1])) if isinstance(k, tuple) else pd.Timestamp(k).date()
        if d.year != year:
            raise ValueError(f'holiday {d} is not in {year}')
        out[d] = name or 'holiday'  # a holiday without a name still counts as one
    return out


def build_calendar(
    year: int,
    holidays: str | Mapping | Collection | None,
    peak_days: Mapping[int, int] | str | None = None,
    load: pd.Series | None = None,
    peak_measure: str = 'peak_hour',
    weekday_only: bool = True,
) -> pd.DataFrame:
    """Build the day-type calendar of a year: one row per day.

    Weekend = Saturday, Sunday or a holiday; peak = the month's peak day; weekday = every other
    day.

    Parameters
    ----------
    year : int
        The calendar year.
    holidays : str, dict or list
        ``"federal"``, or a dict / list of (month, day) pairs or dates (see :func:`holiday_dict`).
    peak_days : dict or str, optional
        ``{month: day}`` (all 12 months), or ``"nearest15"`` (the weekday nearest the 15th), or
        None to use ``load``.
    load : pd.Series, optional
        Hourly system load, from :func:`read_system_load`; used to pick the peak day when
        ``peak_days`` is None, and for the diagnostic columns whenever it is given.
    peak_measure : {'peak_hour', 'energy'}
        ``"peak_hour"``: the day holding the month's highest hourly load; ``"energy"``: the day
        with the highest daily total.
    weekday_only : bool, default True
        True: only non-holiday weekdays can be the peak day; False: any day.

    Returns
    -------
    pd.DataFrame
        Columns ``date``, ``month``, ``day``, ``weekday_name``, ``holiday``, ``day_type`` (words),
        plus ``daily_peak_mw``, ``daily_energy_mwh``, ``highest_day_of_month_any_type`` and
        ``peak_day_by_other_measure`` when a load is given.

    Raises
    ------
    ValueError
        If ``peak_measure`` is unknown, the load has the wrong length, peak days are missing or
        not allowed, or neither ``peak_days`` nor ``load`` is given.
    """
    if peak_measure not in ('peak_hour', 'energy'):
        raise ValueError('peak_measure must be "peak_hour" or "energy"')
    hol = holiday_dict(year, holidays)
    days = pd.date_range(f'{year}-01-01', f'{year}-12-31', freq='D')
    cal = pd.DataFrame(
        {'date': days.date, 'month': days.month, 'day': days.day, 'weekday_name': days.day_name()},
        index=days,
    )
    cal['holiday'] = [hol.get(d, '') for d in cal['date']]
    cal['is_weekend_or_holiday'] = (days.dayofweek >= 5) | (cal['holiday'] != '')
    key = 'daily_peak_mw' if peak_measure == 'peak_hour' else 'daily_energy_mwh'
    if load is not None:
        if len(load) != 24 * len(days):
            raise ValueError(f'the load has {len(load)} hours, {year} has {24 * len(days)}')
        daily = load.resample('D').agg(daily_energy_mwh='sum', daily_peak_mw='max')
        cal = cal.join(daily)
    candidates = cal[~cal.is_weekend_or_holiday] if weekday_only else cal

    if isinstance(peak_days, dict) or peak_days == 'nearest15':
        peak_dates = []
        for m in range(1, 13):
            wk = candidates[candidates.month == m]
            if peak_days == 'nearest15':
                # nearest the 15th, earlier on a tie
                peak_dates.append(min(wk.index, key=lambda ts: (abs(ts.day - 15), ts.day)))
            else:
                if m not in peak_days:
                    raise ValueError(
                        f'peak_days has no entry for month {m}; it needs all 12 months'
                    )
                d = pd.Timestamp(year, m, int(peak_days[m]))
                if d not in candidates.index:
                    raise ValueError(
                        f'peak day {year}-{m:02d}-{int(peak_days[m]):02d} must be a non-holiday '
                        'weekday (or build with weekday_only=False)'
                        if weekday_only
                        else f'bad peak day {d.date()}'
                    )
                peak_dates.append(d)
    elif peak_days is None and load is not None:
        peak_dates = list(candidates.groupby('month')[key].idxmax().to_numpy())
    else:
        raise ValueError(
            'peak days are not defined: give peak_days={month: day} for all 12 months, or a load '
            'file (the peak day is then the best weekday of each month)'
        )

    cal['day_type'] = np.where(cal.is_weekend_or_holiday, 'weekend', 'weekday')
    cal.loc[peak_dates, 'day_type'] = 'peak'
    if load is not None:
        any_day = cal.groupby('month')[key].idxmax()
        cal['highest_day_of_month_any_type'] = False
        cal.loc[any_day.to_numpy(), 'highest_day_of_month_any_type'] = True
        other = 'daily_energy_mwh' if key == 'daily_peak_mw' else 'daily_peak_mw'
        cal['peak_day_by_other_measure'] = False
        cal.loc[cal.groupby('month')[other].idxmax().to_numpy(), 'peak_day_by_other_measure'] = True
    cols = ['date', 'month', 'day', 'weekday_name', 'holiday', 'day_type']
    extra = [
        c
        for c in (
            'daily_peak_mw',
            'daily_energy_mwh',
            'peak_day_by_other_measure',
            'highest_day_of_month_any_type',
        )
        if c in cal.columns
    ]
    return cal[cols + extra].reset_index(drop=True)


def peak_notes(cal: pd.DataFrame) -> list[str]:
    """Describe every month whose highest-load day is not the peak day.

    Parameters
    ----------
    cal : pd.DataFrame
        A calendar from :func:`build_calendar` with a load (it needs the load columns).

    Returns
    -------
    list of str
        One line per such month; empty if the calendar has no load columns.
    """
    if 'highest_day_of_month_any_type' not in cal.columns:
        return []
    moved = cal[cal.highest_day_of_month_any_type & (cal.day_type != 'peak')]
    notes = []
    for _, r in moved.iterrows():
        pk = cal[(cal.month == r.month) & (cal.day_type == 'peak')].date.iloc[0]
        notes.append(
            f"{r.date} ({r.weekday_name}{', ' + r.holiday if r.holiday else ''}) has the month's "
            f'highest load but is not a weekday; the peak day is {pk}'
        )
    return notes


def hourly_daytypes(cal: pd.DataFrame) -> pd.DataFrame:
    """Lay a calendar out as one row per hour.

    Parameters
    ----------
    cal : pd.DataFrame
        A calendar with the columns ``date``, ``month``, ``day_type``.

    Returns
    -------
    pd.DataFrame
        Columns ``month``, ``day``, ``hour`` (1..24), ``hour_index`` (0..) and ``day_type``.
    """
    d = pd.to_datetime(cal['date'])
    n = len(cal) * 24
    return pd.DataFrame(
        {
            'month': np.repeat(cal.month.to_numpy(), 24),
            'day': np.repeat(d.dt.day.to_numpy(), 24),
            'hour': np.tile(np.arange(1, 25), len(cal)),
            'hour_index': np.arange(n),
            'day_type': np.repeat(cal.day_type.to_numpy(), 24),
        }
    )


# --------------------------------------------------------------------------- #
# choosing a calendar
# --------------------------------------------------------------------------- #
def resolve_calendar(
    year: int | None = None,
    load_csv: str | Path | None = None,
    holidays: str | Mapping | Collection | None = None,
    peak_days: Mapping[int, int] | str | None = None,
    **build_options: Any,
) -> tuple[pd.DataFrame, list[str]]:
    """Do what :func:`get_calendar` does, but return the full table.

    Parameters
    ----------
    year, load_csv, holidays, peak_days
        See :func:`get_calendar`.
    **build_options
        Passed to :func:`build_calendar`.

    Returns
    -------
    calendar : pd.DataFrame
        The calendar with day types as words and the diagnostic columns.
    notes : list of str
        Messages to show the user.

    Raises
    ------
    FileNotFoundError, ValueError
        If the calendar cannot be built and the year was named, or holidays / peak days are
        missing for a year without a ready-made calendar.
    """
    notes = []
    named = year is not None
    y = DEFAULT_YEAR if year is None else int(year)
    custom = holidays is not None or peak_days is not None

    if y == NEMS_YEAR and not custom:
        cal = build_calendar(y, NEMS_2018_HOLIDAYS, 'nearest15', **build_options)
        notes.append(
            'calendar 2018 (NEMS: its day counts equal V_SYS_LOAD_WEIGHTS; holidays and peak '
            'rule are a reconstruction)'
        )
        return cal, notes

    if y == DEFAULT_YEAR and not custom:  # 2023: built from the load file at each launch
        try:
            if not load_csv:
                raise FileNotFoundError(
                    'no load file given (load_dir in [load_config], or load_csv=): the 2023 peak '
                    'days come from the data'
                )
            cal = build_calendar(
                y, HOLIDAYS_2023, load=read_system_load(load_csv, y), **build_options
            )
            notes.append(f'calendar {y} built from {display_path(load_csv)}')
            return cal, notes
        except (FileNotFoundError, ValueError) as e:
            if named:
                raise
            notes.append(
                f'NOTE: the default calendar {y} could not be built ({e}); '
                'using the NEMS calendar 2018 instead'
            )
            cal = build_calendar(NEMS_YEAR, NEMS_2018_HOLIDAYS, 'nearest15', **build_options)
            return cal, notes

    # any other year, or 2018 / 2023 with your own holidays or peak days
    if holidays is None:
        raise ValueError(
            f'calendar {y}: holidays are needed. Give holidays="federal" (US federal holidays) or '
            'a list of (month, day) pairs.  Ready-made calendars exist only for 2018 and 2023 '
            '(with their own holidays).'
        )
    if peak_days is None and not load_csv:
        raise ValueError(
            f'calendar {y}: peak days are needed. Give peak_days={{month: day}} for the 12 months, '
            'or a load file (columns hour, base_load_mwh; region optional) so that the peak day '
            'can be taken from the data.'
        )
    load = read_system_load(load_csv, y) if load_csv else None
    cal = build_calendar(y, holidays, peak_days, load, **build_options)
    notes.append(
        f'calendar {y} built from your holidays and '
        + ('peak days' if peak_days is not None or not load_csv else display_path(load_csv))
    )
    return cal, notes


def get_calendar(
    year: int | None = None,
    load_csv: str | Path | None = None,
    holidays: str | Mapping | Collection | None = None,
    peak_days: Mapping[int, int] | str | None = None,
    **build_options: Any,
) -> tuple[pd.DataFrame, list[str]]:
    """Choose or build the day-type calendar.

    Parameters
    ----------
    year : int, optional
        None = the default (2023 from ``load_csv``, or 2018 with a note if that fails); 2018 =
        NEMS; 2023; any other year needs ``holidays`` and ``peak_days`` or ``load_csv``.  A year
        you name is never replaced.
    load_csv : str or Path, optional
        Hourly load file (``hour``, ``base_load_mwh``; ``region`` optional): builds 2023, or gives
        the peak days of another year.
    holidays : str, dict or list, optional
        ``"federal"`` or a list of (month, day) / dates.
    peak_days : dict, optional
        ``{month: day}``.
    **build_options
        ``peak_measure`` (``"peak_hour"`` / ``"energy"``) and ``weekday_only`` (True).

    Returns
    -------
    calendar : pd.DataFrame
        Columns ``date``, ``month``, ``day_type`` (numbers 1/2/3), as NemsLoad needs.
    notes : list of str
        Messages to show the user.
    """
    cal, notes = resolve_calendar(year, load_csv, holidays, peak_days, **build_options)
    return to_model_calendar(cal), notes


# --------------------------------------------------------------------------- #
# reading the optional holiday / peak-day files
# --------------------------------------------------------------------------- #
def read_holidays(spec: str | Path | None) -> str | list | None:
    """Read the holidays setting.

    Parameters
    ----------
    spec : str, Path or None
        None, ``"federal"``, or a CSV with a ``date`` column or ``month`` and ``day`` columns.

    Returns
    -------
    str, list or None
        None and ``"federal"`` unchanged; otherwise a list of dates or (month, day) pairs.
    """
    if spec is None or str(spec).lower() == 'federal':
        return None if spec is None else str(spec)
    t = pd.read_csv(spec)
    if 'date' in t.columns:
        return list(pd.to_datetime(t['date']))
    return [(int(m), int(d)) for m, d in zip(t['month'], t['day'], strict=True)]


def read_peak_days(path: str | Path | None) -> dict[int, int] | None:
    """Read the peak-days file.

    Parameters
    ----------
    path : str, Path or None
        None, or a CSV with the columns ``month`` and ``day`` (12 rows).

    Returns
    -------
    dict of int to int or None
        ``{month: day}``, or None if no file is given.
    """
    if path is None:
        return None
    t = pd.read_csv(path)
    return {int(m): int(d) for m, d in zip(t['month'], t['day'], strict=True)}
