"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Quality checks for the NEMS load-shape input tables.

load_model.py assumes its input tables are clean.  This module is where that is checked, so the
model code can stay short.  run.py calls it before every run (``validate_inputs = true`` in
``[load_config]``).

    from src.models.load.load_model import load_tables
    from src.models.load.data_validation import validate_tables, check_tables
    tables = load_tables("folder")
    issues = validate_tables(tables)       # list of (level, table, message); level = ERROR | WARN
    check_tables(tables)                   # same, but raises ValueError if there is any ERROR

What is checked
    1. every required table is present and has its required columns
    2. no missing or non-numeric values in the numeric columns that are used
    3. the grid: Month, DAY and HOUR run 1..N without gaps, and the same grid is used by the
       end-use shape table, the system shape table and the weights table
    4. V_SYS_LOAD_WEIGHTS: one row per (month, day type); weights >= 0; total 365 or 366 days
    5. every shape (one per LS_Name, one per region) has exactly one value per point, no gaps,
       no duplicates, no negative values
    6. every shape sums to 1 when weighted by days (warning outside the tolerance)
    7. structure: all 25 regions present, every shape name used in V_EMM_LDSMSTR_EU_SECT exists
       (INDH2XXX, the hydrogen end use, is the one name that has no shape on purpose)
    8. census map: the national-total row (28) exists and is positive wherever a region row is

This module is self-contained: it imports nothing from the rest of the package.
"""

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

# one finding of a check: (level, table, message), level = 'ERROR' | 'WARN'
Issue = tuple[str, str, str]

N_REGIONS = 25  # EMM regions 1..25 (rows 26-28 of the census map are AK, HI and the total)
EMM_REG_US_TOTAL = 28
H2_SHAPE = 'INDH2XXX'  # hydrogen end use: no shape file, the hourly demand is supplied instead
TOL_EU_SUM = 2e-3  # end-use shapes in the shipped data are within 1e-3 of 1
TOL_SYS_SUM = 1e-4  # system shapes are within 2e-7

REQUIRED_COLUMNS = {
    'V_SYS_LOAD_WEIGHTS': ['Month', 'DAY', 'WEIGHTS'],
    'V_SYS_LOAD_by_EMM_REGION': ['EMM_REG', 'Month', 'DAY', 'HOUR', 'PCT'],
    'V_SYS_LOAD_by_EndUse': ['LS_Name', 'Month', 'DAY', 'HOUR', 'PCT'],
    'V_EMM_LDSMSTR_EU_SECT': ['EMM_REG', 'SECTOR', 'EUSE', 'LSRnam'],
    'V_EMM_LDSMSTR_SECT_EU_DIM': ['SECTOR', 'EUSE', 'EUNAM'],
    'V_EMM_LDSMSTR_SECT_DIM': ['SECTOR_NUM', 'SECTOR_NM'],
    'V_EMM_LDSMSTR_RGN': ['EMM_REG', 'EMM_REG_NM', 'UQTDLS'],
    'V_EMM_LDSMSTR_RGN_CEN': ['EMM_REG', 'SECTOR', 'CENSUS', 'MappCtoN'],
}


def _grid(
    enduse_shape_tbl: pd.DataFrame, issues: list[Issue], add: Callable[[str, str, str], None]
) -> tuple[int, int, int]:
    """Return the sizes of the grid from the end-use shape table, checking each axis runs 1..N.

    Parameters
    ----------
    enduse_shape_tbl : pd.DataFrame
        ``V_SYS_LOAD_by_EndUse``.
    issues : list
        Unused.
    add : callable
        ``add(level, table, message)`` records a finding.

    Returns
    -------
    tuple of int
        Number of months, day types and hours per day.
    """
    sizes = {}
    for col, label in (('Month', 'months'), ('DAY', 'day types'), ('HOUR', 'hours per day')):
        vals = sorted(int(v) for v in enduse_shape_tbl[col].unique())
        if vals != list(range(1, vals[-1] + 1)):
            add(
                'ERROR',
                'V_SYS_LOAD_by_EndUse',
                f'{col} must run 1..N without gaps; found {vals[:6]}'
                + (' ...' if len(vals) > 6 else ''),
            )
        sizes[label] = vals[-1]
    return sizes['months'], sizes['day types'], sizes['hours per day']


def _shape_groups(
    df: pd.DataFrame,
    key: str,
    name: str,
    nm: int,
    nd: int,
    hp: int,
    issues: list[Issue],
    add: Callable[[str, str, str], None],
    weights: np.ndarray | None,
) -> dict[Any, float]:
    """Check completeness of every group (one shape each) and, if sound, its day-weighted sum.

    Parameters
    ----------
    df : pd.DataFrame
        A shape table with ``Month, DAY, HOUR, PCT`` and the ``key`` column.
    key : str
        The column that names the shape (one shape per value).
    name : str
        The table name, for the messages.
    nm, nd, hp : int
        Number of months, day types and hours per day.
    issues : list
        Unused.
    add : callable
        ``add(level, table, message)`` records a finding.
    weights : ndarray or None
        Days per point; None skips the sums.

    Returns
    -------
    dict
        ``{group: day-weighted sum}``; empty if ``weights`` is None.
    """
    n_load_points = nm * nd * hp
    q = ((df.Month - 1) * nd + (df.DAY - 1)) * hp + df.HOUR - 1
    bad_range = (
        (df.Month < 1)
        | (df.Month > nm)
        | (df.DAY < 1)
        | (df.DAY > nd)
        | (df.HOUR < 1)
        | (df.HOUR > hp)
    )
    if bad_range.any():
        add(
            'ERROR',
            name,
            f'{int(bad_range.sum())} rows outside the grid '
            f'(months 1..{nm}, day types 1..{nd}, hours 1..{hp})',
        )
        df, q = df[~bad_range], q[~bad_range]
    g = pd.DataFrame({'k': df[key].to_numpy(), 'q': q.to_numpy(), 'pct': df.PCT.to_numpy()})
    stats = g.groupby('k').agg(rows=('q', 'size'), distinct=('q', 'nunique'), lowest=('pct', 'min'))
    for k, r in stats.iterrows():
        if r.rows != r.distinct:
            add(
                'ERROR',
                name,
                f'{k}: {int(r.rows - r.distinct)} duplicated (month, day type, hour) rows',
            )
        if r.distinct != n_load_points:
            add('ERROR', name, f'{k}: {int(r.distinct)} distinct points, expected {n_load_points}')
        if r.lowest < 0:
            add('ERROR', name, f'{k}: negative values (minimum {r.lowest:g})')
    if weights is None:
        return {}
    g['w'] = weights[g.q.to_numpy()]
    return (g.assign(x=g.pct * g.w).groupby('k').x.sum()).to_dict()


def validate_tables(
    tables: dict[str, pd.DataFrame], tol_eu: float = TOL_EU_SUM, tol_sys: float = TOL_SYS_SUM
) -> list[Issue]:
    """Check the shape and structure tables (INPUT 1).

    Parameters
    ----------
    tables : dict of str to pd.DataFrame
        From ``load_model.load_tables``.
    tol_eu, tol_sys : float
        Tolerance of the day-weighted sums of the end-use and system shapes around 1.

    Returns
    -------
    list of tuple
        ``(level, table, message)``.  Level is "ERROR" (the model cannot run correctly) or "WARN"
        (it runs, but look at this).
    """
    issues: list[Issue] = []

    def add(level: str, table: str, msg: str) -> None:
        issues.append((level, table, msg))

    # 1. tables and columns --------------------------------------------------------------
    usable = True
    for t, cols in REQUIRED_COLUMNS.items():
        if t not in tables:
            add('ERROR', t, 'table is missing')
            usable = False
            continue
        miss = [c for c in cols if c not in tables[t].columns]
        if miss:
            add('ERROR', t, f'missing columns {miss}')
            usable = False
    if not usable:
        return issues  # nothing else can be checked

    # 2. missing / non-numeric values -----------------------------------------------------
    numeric = {
        'V_SYS_LOAD_WEIGHTS': ['Month', 'DAY', 'WEIGHTS'],
        'V_SYS_LOAD_by_EMM_REGION': ['EMM_REG', 'Month', 'DAY', 'HOUR', 'PCT'],
        'V_SYS_LOAD_by_EndUse': ['Month', 'DAY', 'HOUR', 'PCT'],
        'V_EMM_LDSMSTR_EU_SECT': ['EMM_REG', 'SECTOR', 'EUSE'],
        'V_EMM_LDSMSTR_RGN': ['EMM_REG', 'UQTDLS'],
        'V_EMM_LDSMSTR_RGN_CEN': ['EMM_REG', 'SECTOR', 'CENSUS', 'MappCtoN'],
    }
    for t, cols in numeric.items():
        for c in cols:
            s = pd.to_numeric(tables[t][c], errors='coerce')
            if s.isna().any():
                add('ERROR', t, f'{c}: {int(s.isna().sum())} missing or non-numeric values')
                usable = False
    if not usable:
        return issues

    enduse_shape_tbl, sys_shape_tbl, weights_tbl = (
        tables['V_SYS_LOAD_by_EndUse'],
        tables['V_SYS_LOAD_by_EMM_REGION'],
        tables['V_SYS_LOAD_WEIGHTS'],
    )

    # 3. grid -------------------------------------------------------------------------------
    nm, nd, hp = _grid(enduse_shape_tbl, issues, add)
    for t, df in (('V_SYS_LOAD_by_EMM_REGION', sys_shape_tbl), ('V_SYS_LOAD_WEIGHTS', weights_tbl)):
        for col, n, label in (('Month', nm, 'months'), ('DAY', nd, 'day types')):
            if df[col].min() < 1 or df[col].max() > n:
                add(
                    'ERROR',
                    t,
                    f'{col} runs {int(df[col].min())}..{int(df[col].max())}, '
                    f'but the end-use shape table has {n} {label}',
                )
    if sys_shape_tbl.HOUR.min() < 1 or sys_shape_tbl.HOUR.max() > hp:
        add(
            'ERROR',
            'V_SYS_LOAD_by_EMM_REGION',
            f'HOUR runs {int(sys_shape_tbl.HOUR.min())}..{int(sys_shape_tbl.HOUR.max())}, '
            f'expected 1..{hp}',
        )

    # 4. weights ------------------------------------------------------------------------------
    weights = None
    if len(weights_tbl) != nm * nd or weights_tbl[['Month', 'DAY']].drop_duplicates().shape[
        0
    ] != len(weights_tbl):
        add(
            'ERROR',
            'V_SYS_LOAD_WEIGHTS',
            f'needs exactly one row per (month, day type): {nm * nd} rows expected, '
            f'found {len(weights_tbl)}',
        )
    elif (weights_tbl.WEIGHTS < 0).any():
        add('ERROR', 'V_SYS_LOAD_WEIGHTS', 'negative weights')
    else:
        days = float(weights_tbl.WEIGHTS.sum())
        if round(days) not in (365, 366) or abs(days - round(days)) > 1e-9:
            add(
                'ERROR',
                'V_SYS_LOAD_WEIGHTS',
                f'weights add up to {days:g} days, expected 365 or 366',
            )
        else:  # sums of shapes are only meaningful with sound weights
            weights = np.zeros(nm * nd * hp)
            for m, d, x in weights_tbl[['Month', 'DAY', 'WEIGHTS']].itertuples(index=False):
                weights[
                    ((int(m) - 1) * nd + (int(d) - 1)) * hp : ((int(m) - 1) * nd + int(d)) * hp
                ] = x

    # 5-6. shapes: complete and day-weighted sum = 1 ------------------------------------------
    sums_eu = _shape_groups(
        enduse_shape_tbl, 'LS_Name', 'V_SYS_LOAD_by_EndUse', nm, nd, hp, issues, add, weights
    )
    sums_sy = _shape_groups(
        sys_shape_tbl, 'EMM_REG', 'V_SYS_LOAD_by_EMM_REGION', nm, nd, hp, issues, add, weights
    )
    for k, v in sums_eu.items():
        if abs(v - 1) > tol_eu:
            add(
                'WARN',
                'V_SYS_LOAD_by_EndUse',
                f'{k}: day-weighted sum is {v:.6f}, expected 1 (tolerance {tol_eu:g})',
            )
    for k, v in sums_sy.items():
        if abs(v - 1) > tol_sys:
            add(
                'WARN',
                'V_SYS_LOAD_by_EMM_REGION',
                f'region {k}: day-weighted sum is {v:.6f}, expected 1 (tolerance {tol_sys:g})',
            )

    # 7. structure ------------------------------------------------------------------------------
    regions = set(range(1, N_REGIONS + 1))
    eu_sect_tbl = tables['V_EMM_LDSMSTR_EU_SECT']
    for t, col, df in (
        ('V_SYS_LOAD_by_EMM_REGION', 'EMM_REG', sys_shape_tbl),
        ('V_EMM_LDSMSTR_RGN', 'EMM_REG', tables['V_EMM_LDSMSTR_RGN']),
        ('V_EMM_LDSMSTR_EU_SECT', 'EMM_REG', eu_sect_tbl),
    ):
        gone = sorted(regions - set(df[col].astype(int)))
        if gone:
            add('ERROR', t, f'no rows for EMM regions {gone}')
    shapes = {str(n).strip() for n in enduse_shape_tbl.LS_Name.unique()}
    st25 = eu_sect_tbl[eu_sect_tbl.EMM_REG <= N_REGIONS]
    unknown = sorted({str(n).strip() for n in st25.LSRnam} - shapes - {H2_SHAPE})
    if unknown:
        add(
            'ERROR',
            'V_EMM_LDSMSTR_EU_SECT',
            f'shape names with no shape in V_SYS_LOAD_by_EndUse: {unknown}',
        )
    pairs = set(zip(st25.SECTOR.astype(int), st25.EUSE.astype(int), strict=True))
    named = set(
        zip(
            tables['V_EMM_LDSMSTR_SECT_EU_DIM'].SECTOR.astype(int),
            tables['V_EMM_LDSMSTR_SECT_EU_DIM'].EUSE.astype(int),
            strict=True,
        )
    )
    if pairs - named:
        add(
            'WARN',
            'V_EMM_LDSMSTR_SECT_EU_DIM',
            f'no name for (sector, end use) {sorted(pairs - named)}',
        )
    secs = set(tables['V_EMM_LDSMSTR_SECT_DIM'].SECTOR_NUM.astype(int))
    if {s for s, _ in pairs} - secs:
        add(
            'WARN',
            'V_EMM_LDSMSTR_SECT_DIM',
            f'no name for sectors {sorted({s for s, _ in pairs} - secs)}',
        )

    # 8. census map
    # ---------------------------------------------------------------------------------
    rgn_cen_tbl = tables['V_EMM_LDSMSTR_RGN_CEN']
    tot = (
        rgn_cen_tbl[rgn_cen_tbl.EMM_REG == EMM_REG_US_TOTAL]
        .set_index(['SECTOR', 'CENSUS'])
        .MappCtoN
    )
    if tot.empty:
        add(
            'ERROR',
            'V_EMM_LDSMSTR_RGN_CEN',
            f'no rows for the national total (EMM_REG {EMM_REG_US_TOTAL})',
        )
    else:
        reg = rgn_cen_tbl[rgn_cen_tbl.EMM_REG <= N_REGIONS]
        reg = reg[reg.MappCtoN > 0]
        bad = [
            (int(s), int(c))
            for s, c in zip(reg.SECTOR, reg.CENSUS, strict=True)
            if not tot.get((s, c), 0) > 0
        ]
        if bad:
            add(
                'ERROR',
                'V_EMM_LDSMSTR_RGN_CEN',
                'region rows with a zero or missing total (sector, census): '
                f'{sorted(set(bad))[:8]}',
            )
    return issues


# --------------------------------------------------------------------------- #
# annual-load tables (the files given as ``emm_region_loads`` / ``census_division_loads`` in
# [load_config])
# --------------------------------------------------------------------------- #
LOADS_COLUMNS = ['year', 'region', 'sector', 'euse', 'mwh']
CENSUS_COLUMNS = ['year', 'sector', 'euse', 'census', 'mwh']


def _frame_issues(df: pd.DataFrame, columns: list[str], name: str, key: list[str]) -> list[Issue]:
    """Check that columns are present and numeric without gaps, and that ``key`` is unique."""
    issues: list[Issue] = []
    miss = [c for c in columns if c not in df.columns]
    if miss:
        old = [c for c in ('gwh', 'tbtu') if c in df.columns]
        hint = (
            (
                f"; this file has a '{old[0]}' column: the module takes annual energy in MWh "
                '(1 GWh = 1,000 MWh, 1 TBtu = 292,830 MWh), rename and convert it'
            )
            if old and 'mwh' in miss
            else ''
        )
        return [('ERROR', name, f'missing columns {miss} (needs {columns}){hint}')]
    for c in columns:
        s = pd.to_numeric(df[c], errors='coerce')
        if s.isna().any():
            issues.append(
                ('ERROR', name, f'{c}: {int(s.isna().sum())} missing or non-numeric values')
            )
    if not issues and df.duplicated(key).any():
        issues.append(('ERROR', name, f'{int(df.duplicated(key).sum())} duplicated rows for {key}'))
    return issues


def _valid_end_uses(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the (region, sector, euse) combinations that have a shape entry, hydrogen flagged."""
    st = tables['V_EMM_LDSMSTR_EU_SECT']
    st = st[st.EMM_REG <= N_REGIONS]
    return pd.DataFrame(
        {
            'region': st.EMM_REG.astype(int),
            'sector': st.SECTOR.astype(int),
            'euse': st.EUSE.astype(int),
            'h2': st.LSRnam.astype(str).str.strip() == H2_SHAPE,
        }
    )


def validate_loads(
    loads: pd.DataFrame, tables: dict[str, pd.DataFrame] | None = None
) -> list[Issue]:
    """Check the annual end-use loads by EMM region.

    With ``tables`` it also compares the (sector, end use) categories with those of
    V_EMM_LDSMSTR_EU_SECT: a category that is not there is an ERROR (the model would drop its
    energy without a message); a valid category that is absent is a WARN (the model treats it as
    0).

    Parameters
    ----------
    loads : pd.DataFrame
        Columns ``year, region, sector, euse, mwh``.
    tables : dict of str to pd.DataFrame, optional
        The shape tables.

    Returns
    -------
    list of tuple
        ``(level, table, message)``.
    """
    issues = _frame_issues(
        loads, LOADS_COLUMNS, 'annual loads', ['year', 'region', 'sector', 'euse']
    )
    if not issues and not loads.region.between(1, N_REGIONS).all():
        issues.append(
            (
                'ERROR',
                'annual loads',
                (
                    f'region must be 1..{N_REGIONS}, '
                    f'found {sorted(set(loads.region) - set(range(1, N_REGIONS + 1)))}'
                ),
            )
        )
    if issues or tables is None:
        return issues
    valid = _valid_end_uses(tables)
    key = ['region', 'sector', 'euse']
    L = loads[['year', 'region', 'sector', 'euse']].astype(int)
    extra = L.merge(valid[key], on=key, how='left', indicator=True).query("_merge == 'left_only'")
    if len(extra):
        pairs = sorted(set(zip(extra.region, extra.sector, extra.euse, strict=True)))
        issues.append(
            (
                'ERROR',
                'annual loads',
                f'{len(pairs)} (region, sector, end use) not in V_EMM_LDSMSTR_EU_SECT, '
                f'their energy would be dropped: {pairs[:6]}' + (' ...' if len(pairs) > 6 else ''),
            )
        )
    need = valid[~valid.h2][key]  # hydrogen comes in hourly, not as an annual load
    absent = []
    for y, g in L.groupby('year'):
        have = g[key].drop_duplicates()
        for r in sorted(set(g.region)):
            miss = need[need.region == r].merge(
                have[have.region == r], on=key, how='left', indicator=True
            )
            miss = miss[miss._merge == 'left_only']
            if len(miss):
                absent.append((int(cast(int, y)), int(r), len(miss)))
    if absent:
        issues.append(
            (
                'WARN',
                'annual loads',
                (
                    f'{len(absent)} (year, region) combinations lack some end uses, which are '
                    f'treated as 0 MWh; first: year {absent[0][0]}, region {absent[0][1]}, '
                    f'{absent[0][2]} end uses missing'
                ),
            )
        )
    return issues


def validate_census_loads(
    census: pd.DataFrame, tables: dict[str, pd.DataFrame] | None = None
) -> list[Issue]:
    """Check the annual end-use loads by census division.

    With ``tables``, a (sector, end use) that no region has in V_EMM_LDSMSTR_EU_SECT is an ERROR.

    Parameters
    ----------
    census : pd.DataFrame
        Columns ``year, sector, euse, census, mwh``.
    tables : dict of str to pd.DataFrame, optional
        The shape tables.

    Returns
    -------
    list of tuple
        ``(level, table, message)``.
    """
    issues = _frame_issues(
        census, CENSUS_COLUMNS, 'census loads', ['year', 'sector', 'euse', 'census']
    )
    if not issues and tables is not None:
        valid = _valid_end_uses(tables)[['sector', 'euse']].drop_duplicates()
        C = census[['sector', 'euse']].astype(int).drop_duplicates()
        extra = C.merge(valid, on=['sector', 'euse'], how='left', indicator=True).query(
            "_merge == 'left_only'"
        )
        if len(extra):
            pairs = sorted(zip(extra.sector, extra.euse, strict=True))
            issues.append(
                (
                    'ERROR',
                    'census loads',
                    f'{len(pairs)} (sector, end use) not in V_EMM_LDSMSTR_EU_SECT, '
                    f'their energy would be dropped: {pairs[:6]}'
                    + (' ...' if len(pairs) > 6 else ''),
                )
            )
    if not issues:
        extra = sorted(set(census.census.astype(int)) - set(range(1, 10)))
        if extra:
            issues.append(
                (
                    'WARN',
                    'census loads',
                    (
                        f'census codes {extra} are ignored: only divisions 1-9 are mapped '
                        '(10 California and 11 Total US overlap them)'
                    ),
                )
            )
    return issues


def validate_ulossadj(ulossadj: pd.Series, years: Iterable[int] | None = None) -> list[Issue]:
    """Check the year -> ULOSSADJ table.

    It must be numeric and positive, with no duplicate years and every year in ``years`` present.
    A missing year is an ERROR; nothing is carried forward.

    Parameters
    ----------
    ulossadj : pd.Series
        From ``load_model.load_ulossadj``.
    years : iterable of int, optional
        The years the run needs.

    Returns
    -------
    list of tuple
        ``(level, table, message)``.
    """
    issues: list[Issue] = []
    name = 'ULOSSADJ by year'
    if len(ulossadj) == 0:
        return [('ERROR', name, 'file has no rows')]
    vals = pd.to_numeric(ulossadj, errors='coerce')
    if vals.isna().any():
        issues.append(('ERROR', name, f'{int(vals.isna().sum())} missing or non-numeric values'))
    elif (vals <= 0).any():
        issues.append(
            ('ERROR', name, f'values must be positive, found {vals[vals <= 0].to_dict()}')
        )
    if ulossadj.index.duplicated().any():
        issues.append(
            (
                'ERROR',
                name,
                f'duplicate years {sorted(set(ulossadj.index[ulossadj.index.duplicated()]))}',
            )
        )
    if years is not None:
        gone = sorted({int(y) for y in years} - {int(y) for y in ulossadj.index})
        if gone:
            issues.append(
                (
                    'ERROR',
                    name,
                    (
                        f'no value for year(s) {gone}; '
                        f'file covers {int(ulossadj.index.min())}-{int(ulossadj.index.max())}'
                    ),
                )
            )
    return issues


def check_ulossadj(ulossadj: pd.Series, years: Iterable[int] | None = None) -> list[Issue]:
    """Run :func:`validate_ulossadj`, raising ``ValueError`` on any ERROR.

    Parameters
    ----------
    ulossadj : pd.Series
        From ``load_model.load_ulossadj``.
    years : iterable of int, optional
        The years the run needs.

    Returns
    -------
    list of tuple
        The warnings.
    """
    return _raise_on_errors(validate_ulossadj(ulossadj, years))


def validate_calendar(cal: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> list[Issue]:
    """Check a day-type calendar (it defines the day weights).

    Checks: dates are real, unique, all in one year, covering every day of it; month agrees with
    the date; month and day_type lie inside the grid of the tables; every month has at least one
    day of each day type (a day type with no day would have weight 0 and its profile would never
    be used: WARN).  The day counts are NOT compared with V_SYS_LOAD_WEIGHTS (they are meant to
    differ); see :func:`compare_calendar_to_nems`.

    Parameters
    ----------
    cal : pd.DataFrame
        Columns ``date, month, day_type``, one row per day.
    tables : dict of str to pd.DataFrame
        The shape tables.

    Returns
    -------
    list of tuple
        ``(level, table, message)``.
    """
    name = 'calendar'
    miss = [c for c in ('date', 'month', 'day_type') if c not in cal.columns]
    if miss:
        return [('ERROR', name, f'missing columns {miss} (needs date, month, day_type)')]
    issues = []
    dates = pd.to_datetime(cal['date'], errors='coerce')
    if dates.isna().any():
        issues.append(('ERROR', name, f'{int(dates.isna().sum())} dates cannot be read'))
    elif dates.duplicated().any():
        issues.append(('ERROR', name, f'{int(dates.duplicated().sum())} duplicated dates'))
    if any(pd.to_numeric(cal[c], errors='coerce').isna().any() for c in ('month', 'day_type')):
        return issues + [
            ('ERROR', name, 'month and day_type must be numbers (1/2/3 or weekday/weekend/peak)')
        ]
    nm, nd, _ = _grid(tables['V_SYS_LOAD_by_EndUse'], [], lambda *a: None)
    out = cal[~cal.month.between(1, nm) | ~cal.day_type.between(1, nd)]
    if len(out):
        issues.append(
            ('ERROR', name, f'{len(out)} rows outside the grid (months 1..{nm}, day types 1..{nd})')
        )
        return issues
    if issues:
        return issues
    if len(dates.dt.year.unique()) != 1:
        return [
            (
                'ERROR',
                name,
                f'dates span several years {sorted(dates.dt.year.unique().tolist())}; use one year',
            )
        ]
    year = int(dates.dt.year.iloc[0])
    expected = 366 if pd.Timestamp(year, 12, 31).dayofyear == 366 else 365
    if len(cal) != expected:
        issues.append(
            ('ERROR', name, f'{len(cal)} days for {year}, expected {expected} (one row per day)')
        )
    if (dates.dt.month != cal.month).any():
        bad = cal[dates.dt.month != cal.month].iloc[0]
        issues.append(
            (
                'ERROR',
                name,
                (
                    'month does not match the date, '
                    f'e.g. {pd.Timestamp(bad.date).date()} has month {int(bad.month)}'
                ),
            )
        )
    have = cal.groupby(['month', 'day_type']).size()
    for m in range(1, nm + 1):
        for d in range(1, nd + 1):
            if have.get((m, d), 0) == 0:
                issues.append(
                    (
                        'WARN',
                        name,
                        (
                            f'month {m} has no day of type {d}: that profile gets weight 0 and is '
                            'never used'
                        ),
                    )
                )
    return issues


def compare_calendar_to_nems(cal: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Show the days per (month, day type) of the calendar next to V_SYS_LOAD_WEIGHTS.

    For information: the two differ whenever the calendar is not the NEMS one (the NEMS weights
    fit 2018).

    Parameters
    ----------
    cal : pd.DataFrame
        Columns ``date, month, day_type``.
    tables : dict of str to pd.DataFrame
        The shape tables.

    Returns
    -------
    pd.DataFrame
        By month, the ``calendar`` and ``nems`` day counts and their ``difference`` for each day
        type.
    """
    w = tables['V_SYS_LOAD_WEIGHTS'].set_index(['Month', 'DAY']).WEIGHTS
    w.index.names = ['month', 'day_type']
    have = cal.groupby(['month', 'day_type']).size()
    t = pd.concat([have.rename('calendar'), w.rename('nems')], axis=1).fillna(0).astype(int)
    t['difference'] = t.calendar - t.nems
    wide = cast(pd.DataFrame, t.unstack('day_type'))
    return wide.swaplevel(axis=1).sort_index(axis=1)


def check_calendar(cal: pd.DataFrame, tables: dict[str, pd.DataFrame]) -> list[Issue]:
    """Run :func:`validate_calendar`, raising ``ValueError`` on any ERROR.

    Parameters
    ----------
    cal : pd.DataFrame
        Columns ``date, month, day_type``.
    tables : dict of str to pd.DataFrame
        The shape tables.

    Returns
    -------
    list of tuple
        The warnings.
    """
    return _raise_on_errors(validate_calendar(cal, tables))


def _raise_on_errors(issues: list[Issue]) -> list[Issue]:
    """Raise ``ValueError`` if any finding is an ERROR; otherwise return the findings."""
    if any(i[0] == 'ERROR' for i in issues):
        raise ValueError('input table is not usable:\n' + format_report(issues))
    return issues


def check_loads(loads: pd.DataFrame, tables: dict[str, pd.DataFrame] | None = None) -> list[Issue]:
    """Run :func:`validate_loads`, raising ``ValueError`` on any ERROR.

    Parameters
    ----------
    loads : pd.DataFrame
        Columns ``year, region, sector, euse, mwh``.
    tables : dict of str to pd.DataFrame, optional
        The shape tables.

    Returns
    -------
    list of tuple
        The warnings.
    """
    return _raise_on_errors(validate_loads(loads, tables))


def check_census_loads(
    census: pd.DataFrame, tables: dict[str, pd.DataFrame] | None = None
) -> list[Issue]:
    """Run :func:`validate_census_loads`, raising ``ValueError`` on any ERROR.

    Parameters
    ----------
    census : pd.DataFrame
        Columns ``year, sector, euse, census, mwh``.
    tables : dict of str to pd.DataFrame, optional
        The shape tables.

    Returns
    -------
    list of tuple
        The warnings.
    """
    return _raise_on_errors(validate_census_loads(census, tables))


def reconcile_census(
    census_mwh: pd.DataFrame,
    regional: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    rel_tol: float = 1e-9,
) -> tuple[pd.DataFrame, list[Issue]]:
    """Compare the input and output of ``census_to_region``, by sector.

    The map sends each census division's load to the 25 EMM regions and to Alaska and Hawaii (map
    rows 26 and 27, both in the Pacific division).  The model keeps regions 1-25 only, so::

        input MWh  =  output MWh  +  Alaska/Hawaii MWh        (exactly)

    is the identity that must hold; ``unexplained_mwh`` is what is left and should be zero.  The
    identity is checked by year, sector and end use.

    Parameters
    ----------
    census_mwh : pd.DataFrame
        The census-division loads, columns ``year, sector, euse, census, mwh``.
    regional : pd.DataFrame
        Their mapping to the EMM regions, from ``NemsLoad.census_to_region``.
    tables : dict of str to pd.DataFrame
        The shape tables.
    rel_tol : float
        Relative tolerance of the identity.

    Returns
    -------
    table : pd.DataFrame
        By sector: ``input_mwh``, ``output_mwh``, ``alaska_hawaii_mwh``, ``unexplained_mwh`` and
        ``output_share``.
    issues : list of tuple
        ``(level, table, message)``; an ERROR if the identity fails.
    """
    mp = tables['V_EMM_LDSMSTR_RGN_CEN']
    total = mp[mp.EMM_REG == EMM_REG_US_TOTAL].set_index(['SECTOR', 'CENSUS']).MappCtoN
    mp = mp[(mp.CENSUS <= 9) & (mp.EMM_REG < EMM_REG_US_TOTAL)]
    frac = (
        mp.MappCtoN.to_numpy()
        / total.reindex(list(zip(mp.SECTOR, mp.CENSUS, strict=True))).to_numpy()
    )
    mp = mp.assign(frac=np.where(np.isfinite(frac), frac, 0.0))
    fak = mp[mp.EMM_REG > N_REGIONS].groupby(['SECTOR', 'CENSUS']).frac.sum()

    c = census_mwh[census_mwh.census.between(1, 9)].copy()
    c['mwh_in'] = c.mwh.astype(float)
    key = list(zip(c.sector.astype(int), c.census.astype(int), strict=True))
    c['mwh_akhi'] = c.mwh_in * fak.reindex(key).fillna(0.0).to_numpy()
    per = c.groupby(['year', 'sector', 'euse'])[['mwh_in', 'mwh_akhi']].sum()
    out = regional.groupby(['year', 'sector', 'euse']).mwh.sum().rename('mwh_out')
    per = per.join(out, how='outer').fillna(0.0)
    per['unexplained_mwh'] = per.mwh_in - per.mwh_out - per.mwh_akhi
    issues = []
    bad = per[per.unexplained_mwh.abs() > rel_tol * per.mwh_in.abs().clip(lower=1.0)]
    if len(bad):
        issues.append(
            (
                'ERROR',
                'census_to_region',
                (
                    f'input != output + Alaska/Hawaii for {len(bad)} (year, sector, end use) rows; '
                    f'largest gap {bad.unexplained_mwh.abs().max():.6g} MWh'
                ),
            )
        )
    by_sector = per.groupby(level='sector').sum()
    by_sector['output_share'] = by_sector.mwh_out / by_sector.mwh_in
    by_sector = by_sector.rename(
        columns={'mwh_in': 'input_mwh', 'mwh_out': 'output_mwh', 'mwh_akhi': 'alaska_hawaii_mwh'}
    )
    return by_sector[
        ['input_mwh', 'output_mwh', 'alaska_hawaii_mwh', 'unexplained_mwh', 'output_share']
    ], issues


def format_report(issues: list[Issue]) -> str:
    """Format findings as text, errors first.

    Parameters
    ----------
    issues : list of tuple
        ``(level, table, message)``.

    Returns
    -------
    str
        One line per finding and a count of errors and warnings.
    """
    if not issues:
        return 'OK - no problems found.'
    n_err = sum(1 for lvl, _, _ in issues if lvl == 'ERROR')
    lines = [
        f'{lvl:5s} {t}: {m}' for lvl, t, m in sorted(issues, key=lambda i: (i[0] != 'ERROR', i[1]))
    ]
    lines.append(f'{n_err} error(s), {len(issues) - n_err} warning(s).')
    return '\n'.join(lines)


def check_tables(tables: dict[str, pd.DataFrame], **kw: float) -> list[Issue]:
    """Run :func:`validate_tables`, raising ``ValueError`` that lists every error.

    Parameters
    ----------
    tables : dict of str to pd.DataFrame
        The shape tables.
    **kw
        Passed to :func:`validate_tables`.

    Returns
    -------
    list of tuple
        The warnings.
    """
    issues = validate_tables(tables, **kw)
    errs = [i for i in issues if i[0] == 'ERROR']
    if errs:
        raise ValueError('input tables are not usable:\n' + format_report(issues))
    return issues


def check_energy_balance(
    profs: Mapping[int, dict],
    region_names: Mapping[int, str],
    year: int,
    method: str,
    hourly_rows: list[pd.DataFrame] | None = None,
    rtol: float = 1e-9,
) -> None:
    """Stop the run if a region's profile does not add up to the annual load that came in.

    For every region the weighted sum of the 864-point sales profile (``annual_sales_mwh``) and,
    when the 8760-hour rows are given, the sum of the hourly ``sales_mwh`` must equal the mapped
    annual load ``system_load`` (hydrogen included) to ``rtol``.

    Parameters
    ----------
    profs : dict
        Region id -> profile dictionary from :meth:`NemsLoad.build_all_regions`.
    region_names : mapping
        Region id -> name, for the message.
    year : int
        Year of the profiles, for the message.
    method : str
        Method name, for the message.
    hourly_rows : list of pd.DataFrame, optional
        Hourly rows of this year and method, one frame per region (``region_id``, ``sales_mwh``).
    rtol : float, default 1e-9
        Allowed relative difference.

    Raises
    ------
    ValueError
        If any region's sales differ from its annual load by more than ``rtol``.
    """
    hourly = {}
    for frame in hourly_rows or []:
        hourly[int(frame.region_id.iloc[0])] = float(frame.sales_mwh.sum())
    bad = []
    for r, p in profs.items():
        expected = p['system_load']
        found = {'864 points': p['annual_sales_mwh']}
        if r in hourly:
            found['hourly'] = hourly[r]
        for what, value in found.items():
            if abs(value - expected) > rtol * max(abs(expected), 1.0):
                bad.append(f'{region_names[r]} ({what}: {value:,.3f} vs {expected:,.3f} MWh)')
    if bad:
        raise ValueError(
            f'({method}, {year}) the profile does not add up to the annual load that came in for '
            f'{len(bad)} region(s): ' + '; '.join(bad[:10])
        )


def check_no_negative_load(long: pd.DataFrame, out: Path, method: str, show: int = 10) -> None:
    """Stop the run if any hourly load is negative, listing the offending rows.

    The delta formula can go below zero in a few hours (NEMS has no floor either); such a file is
    not written because the electricity model cannot use it.  All offending rows are saved to
    ``negative_load_rows.csv`` in the results folder.

    Parameters
    ----------
    long : pd.DataFrame
        Hourly loads with columns ``region_id, region, year, hour, load_mwh``.
    out : Path
        Results folder; receives ``negative_load_rows.csv`` when the check fails.
    method : str
        Method name, for the message.
    show : int, default 10
        Number of rows (the most negative first) printed in the message.

    Raises
    ------
    ValueError
        If any ``load_mwh`` is below zero.
    """
    bad = long[long.load_mwh < 0].sort_values('load_mwh')
    if bad.empty:
        return
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'negative_load_rows.csv'
    bad.to_csv(path, index=False, float_format='%.6f')
    raise ValueError(
        f'({method}) {len(bad)} hourly loads are negative (lowest {bad.load_mwh.min():,.0f} MWh) '
        f'in regions {sorted(set(bad.region))}, years {sorted(set(bad.year))}.  All rows are in '
        f'{path}; the most negative:\n'
        + bad.head(show).to_string(index=False)
        + '\nNo hourly file was written.'
    )
