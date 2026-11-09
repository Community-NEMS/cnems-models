"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Choose where each sector's annual end-use load comes from: NEMS (default) or the user's forecast.

The default table (NEMS) has one row per year x sector x end use x census division, in MWh:
    year, sector, euse, census, mwh
A sector can be replaced by a file with the same layout.  Nothing is guessed: a replacement file
must cover every end use, every census division 1-9 and every year the run needs (projection years
and base year), otherwise the run stops with a message.  No NEMS-style adjustment is applied to a
replacement (see README): the numbers are used as given.
"""

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

SECTORS = {'residential': 1, 'commercial': 2, 'industrial': 3, 'transport': 4}
SECTOR_NAMES = {v: k for k, v in SECTORS.items()}
KEYS = ['year', 'sector', 'euse', 'census']


def sector_code(name: str | int) -> int:
    """Convert a sector name to its code, e.g. ``'commercial'`` (or 2) -> 2.

    Parameters
    ----------
    name : str or int
        A sector name (case and surrounding blanks ignored) or a sector code.

    Returns
    -------
    int
        The sector code, 1 to 4.

    Raises
    ------
    ValueError
        If ``name`` is not a sector.
    """
    if isinstance(name, str) and name.strip().lower() in SECTORS:
        return SECTORS[name.strip().lower()]
    if isinstance(name, (int, np.integer)) and int(name) in SECTOR_NAMES:
        return int(name)
    raise ValueError(f'unknown sector {name!r}: use one of {sorted(SECTORS)}')


def _grid(nems: pd.DataFrame, sector: int, years: Iterable[int]) -> pd.DataFrame:
    """Return all (year, euse, census) rows a replacement for ``sector`` must contain."""
    e = nems.loc[nems.sector == sector, 'euse'].drop_duplicates().sort_values()
    return pd.MultiIndex.from_product(
        [sorted(years), e, range(1, 10)], names=['year', 'euse', 'census']
    ).to_frame(index=False)


def read_override(
    path: str | Path, sector: str | int, nems: pd.DataFrame, years: Iterable[int]
) -> pd.DataFrame:
    """Read and check one replacement file.

    Parameters
    ----------
    path : str or Path
        The replacement file, columns ``year, euse, census, mwh`` (``sector`` optional).
    sector : str or int
        The sector the file replaces.
    nems : pd.DataFrame
        The NEMS table; supplies the end uses each sector must have.
    years : iterable of int
        The years the run needs (projection years and base year).

    Returns
    -------
    pd.DataFrame
        Columns ``year, sector, euse, census, mwh`` for the years needed.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file has the wrong columns or units, other sectors, duplicate or missing rows,
        unknown end uses or census divisions, or values that are not finite and non-negative.
    """
    path = Path(path)
    s = sector_code(sector)
    where = f'{path} (sector {SECTOR_NAMES[s]})'
    if not path.exists():
        raise FileNotFoundError(
            f'override file not found: {where}\n'
            f'Create the empty file first (this is a separate step, the run never creates it):\n'
            f'  from src.models.load.run import write_override_template\n'
            f"  write_override_template('{SECTOR_NAMES[s]}', '<path>', "
            f"range(<first year>, <last year> + 1), restart_file='<NEMS restart file>', "
            f"tables_dir='<tables folder>')\n"
            f'then fill the mwh column and point [load_config.demand_overrides] to it.'
        )
    df = pd.read_csv(path)
    if 'tbtu' in df.columns and 'mwh' not in df.columns:
        raise ValueError(
            f"{where}: this file has a 'tbtu' column; the module now takes annual energy in MWh "
            "(1 TBtu = 292,830 MWh): rename the column to 'mwh' and convert the values"
        )
    need_cols = {'year', 'euse', 'census', 'mwh'}
    if need_cols - set(df.columns):
        raise ValueError(
            f'{where}: missing columns {sorted(need_cols - set(df.columns))}; '
            f'required: year, euse, census, mwh (sector optional)'
        )
    if 'sector' in df.columns:
        other = pd.Series([_safe_code(x) for x in df['sector']])
        if (other != s).any():
            found = sorted(set(df['sector'].astype(str)[other != s]))[:5]
            raise ValueError(
                f'{where}: the file contains other sectors ({found}); give one file per sector'
            )
    df = df[['year', 'euse', 'census', 'mwh']].copy()
    for c in ('year', 'euse', 'census'):
        if not pd.api.types.is_integer_dtype(df[c]):
            if not (pd.to_numeric(df[c], errors='coerce') % 1 == 0).all():
                raise ValueError(f'{where}: column {c} must hold whole numbers')
            df[c] = pd.to_numeric(df[c]).astype(int)
    if not pd.api.types.is_numeric_dtype(df['mwh']):
        df['mwh'] = pd.to_numeric(df['mwh'], errors='coerce')
    if df.duplicated(['year', 'euse', 'census']).any():
        d = df[df.duplicated(['year', 'euse', 'census'], keep=False)].head(3)
        raise ValueError(f'{where}: duplicated rows, e.g. {d.to_numpy().tolist()}')
    bad = df[~df.census.between(1, 9)]
    if len(bad):
        raise ValueError(f'{where}: census must be 1-9, found {sorted(bad.census.unique())[:5]}')
    unknown = sorted(set(df.euse) - set(nems.loc[nems.sector == s, 'euse']))
    if unknown:
        raise ValueError(
            f'{where}: end-use codes {unknown[:8]} do not exist in NEMS for this sector'
        )
    df = df[df.year.isin(list(years))]
    grid = _grid(nems, s, years)
    m = grid.merge(df, on=['year', 'euse', 'census'], how='left')
    missing = m[m.mwh.isna()]
    if len(missing):
        yrs = [int(y) for y in sorted(missing.year.unique())]
        raise ValueError(
            f'{where}: {len(missing)} of {len(m)} required rows are missing or empty '
            f'(years {yrs[:6]}{"..." if len(yrs) > 6 else ""}, '
            f'e.g. euse {int(missing.euse.iloc[0])} census {int(missing.census.iloc[0])}). '
            f'Every end use x census division x year is required, including the base year.'
        )
    if not np.isfinite(m.mwh).all() or (m.mwh < 0).any():
        raise ValueError(f'{where}: mwh must be finite and not negative')
    m.insert(1, 'sector', s)
    return m


def _safe_code(x) -> int:
    """Return the sector code of ``x``, or -1 if it is not a sector."""
    try:
        return sector_code(x if isinstance(x, str) else int(x))
    except Exception:  # noqa: BLE001 - any failure to read the cell means "not a sector"
        return -1


def apply_overrides(
    nems: pd.DataFrame,
    overrides: dict[str, str] | None,
    years: Iterable[int],
    nems_label: str = 'NEMS',
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Replace whole sectors of the NEMS table.

    Parameters
    ----------
    nems : pd.DataFrame
        The NEMS table, columns ``year, sector, euse, census, mwh``.
    overrides : dict of str to str, optional
        ``{sector: replacement file}``.
    years : iterable of int
        The years to keep (projection years and base year).
    nems_label : str, default 'NEMS'
        How the unreplaced sectors are named in the sources table.

    Returns
    -------
    table : pd.DataFrame
        The table used, same columns as ``nems``.
    sources : pd.DataFrame
        One row per sector: where it came from, its years, and its total in the first year.
    comparison : pd.DataFrame
        Annual total by sector and year, NEMS versus used, and the ratio (equal when not replaced).

    Raises
    ------
    ValueError
        If a sector is listed twice, or a replacement file is not usable (see
        :func:`read_override`).
    """
    overrides = dict(overrides or {})
    codes = {sector_code(k): v for k, v in overrides.items()}
    if len(codes) != len(overrides):
        raise ValueError('a sector is listed twice in the overrides')
    years = sorted(set(years))
    out = nems[nems.year.isin(years)].copy()
    for s, path in codes.items():
        new = read_override(path, s, nems, years)
        out = pd.concat([out[out.sector != s], new], ignore_index=True)
    out = out.sort_values(KEYS).reset_index(drop=True)

    rows = []
    for s in sorted(SECTOR_NAMES):
        sub = out[out.sector == s]
        if sub.empty:
            continue
        rows.append(
            {
                'sector': SECTOR_NAMES[s],
                'source': ('REPLACED: ' + Path(codes[s]).name) if s in codes else nems_label,
                'first_year': int(sub.year.min()),
                'last_year': int(sub.year.max()),
                'rows': len(sub),
                'total_mwh_first_year': float(sub[sub.year == sub.year.min()].mwh.sum()),
            }
        )
    sources = pd.DataFrame(rows)

    a = nems[nems.year.isin(years)].groupby(['year', 'sector']).mwh.sum().rename('nems_mwh')
    b = out.groupby(['year', 'sector']).mwh.sum().rename('used_mwh')
    comp = pd.concat([a, b], axis=1).reset_index()
    comp['used_over_nems'] = comp.used_mwh / comp.nems_mwh.where(comp.nems_mwh != 0)
    comp['sector'] = comp.sector.map(SECTOR_NAMES)
    return out, sources, comp


def make_override_template(
    nems: pd.DataFrame,
    sector: str | int,
    years: Iterable[int],
    path: str | Path,
    tables_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Write an empty replacement file for one sector: every required row, ``mwh`` blank.

    Parameters
    ----------
    nems : pd.DataFrame
        The NEMS table; supplies the end uses and the reference values.
    sector : str or int
        The sector to replace.
    years : iterable of int
        The years the file must cover.
    path : str or Path
        Where to write the file.
    tables_dir : str or Path, optional
        The shape-table folder; if given, the end-use names are added.

    Returns
    -------
    pd.DataFrame
        What was written.  Columns: ``year``, ``sector`` (code), ``sector_name``, ``euse``,
        ``end_use`` (name, informational), ``census``, ``mwh`` (to fill in) and
        ``nems_reference_mwh`` (what NEMS has; informational, ignored when the file is read).
    """
    s = sector_code(sector)
    g = _grid(nems, s, years)
    g.insert(1, 'sector', s)
    g.insert(2, 'sector_name', SECTOR_NAMES[s])
    names = None
    if tables_dir:
        f = Path(tables_dir) / 'V_EMM_LDSMSTR_SECT_EU_DIM.csv'
        if f.exists():
            d = pd.read_csv(f)
            names = d[d.SECTOR == s].set_index('EUSE').EUNAM.str.strip()
    g.insert(4, 'end_use', g.euse.map(names) if names is not None else '')
    g['mwh'] = np.nan
    ref = (
        nems[nems.sector == s]
        .set_index(['year', 'euse', 'census'])
        .mwh.rename('nems_reference_mwh')
    )
    g = g.merge(ref.reset_index(), on=['year', 'euse', 'census'], how='left')
    g.to_csv(path, index=False)
    return g
