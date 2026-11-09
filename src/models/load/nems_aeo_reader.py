"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Turn a NEMS restart.rlx into the census-level load file used by the load model.

Output columns: year, sector, euse, census, mwh_nems, mwh
(annual MWh; NEMS stores TBtu, converted at 292,830 MWh per TBtu)
  sector/euse codes follow V_EMM_LDSMSTR_SECT_EU_DIM
  (1 residential 11 end uses, 2 commercial 116, 3 industrial 4, 4 transport 1)
  census = 1..9 divisions.

It repeats the part of uldsm.f (DSMARE, DSMACM, DSMAIN, DSMATR, DSMCADJ) that
builds LoadForec, but stops BEFORE the census -> EMM mapping (DSMEMMV), which
the load model does itself.  FORESIGHTadj is 1 when the forecast year equals the
base year, which is the case for the EMM run, so it is not applied.

Check: extracted sector totals equal QBLK sales plus behind-the-meter generation
(QELRS+CGRESGEN, QELCM+CGCOMMGEN, QELIN, QELTR).

Limits: the restart file holds one snapshot (final iteration, one scenario);
the historical regional factor (Histshr) and the high-demand multiplier
UQELFAC are not applied.
"""

import re
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from .load_model import MWH_PER_TBTU

# label used in file names and messages for data read from the NEMS restart file
NEMS_RELEASE = 'AEO2026'
FIRST_YEAR = 1990
NCEN = 9
# index parameters from includes/dsmdimen
ELINDEX, ELHEATX, ELWHEAX, ELCOOLX, ELSTOVX, ELDRYEX, ELSECHX = 1, 2, 2, 1, 3, 2, 2

# reads one variable of a restart file by its lower-case name
Getter = Callable[[str], np.ndarray]


def open_restart(path: str | Path) -> tuple[np.lib.npyio.NpzFile, dict[str, str]]:
    """Open a restart file.

    Parameters
    ----------
    path : str or Path
        The ``restart.rlx`` (a NumPy archive in the AEO2026 release).

    Returns
    -------
    z : NpzFile
        The opened archive.
    names : dict of str to str
        ``{lower-case variable name: array name in the archive}``.

    Raises
    ------
    ValueError
        If the file is a Git LFS pointer or not a NumPy archive.
    """
    p = Path(path)
    if p.exists() and p.stat().st_size < 1000:
        raise ValueError(f"{p} is a Git LFS pointer, not data; run 'git lfs pull' to download it")
    if p.exists() and not zipfile.is_zipfile(p):
        raise ValueError(
            f'{p} is not a NEMS AEO2026 restart.rlx (a NumPy archive in this release); an older '
            'restart.rlx may be Fortran unformatted binary, which this reader cannot open.'
        )
    z = np.load(path, allow_pickle=False)
    names = {n.split('/')[-1].lower(): n for n in z.files if n != '_variablelisting'}
    return z, names


def find(path: str | Path, pattern: str) -> list[tuple[str, tuple[int, ...]]]:
    """List the (array name, shape) of the variables whose name matches a regex.

    Parameters
    ----------
    path : str or Path
        The restart file.
    pattern : str
        A regular expression, matched case-insensitively against the variable names.

    Returns
    -------
    list of tuple
        ``(array name, shape)`` for each match.
    """
    z, names = open_restart(path)
    rx = re.compile(pattern, re.IGNORECASE)
    return [(n, z[n].shape) for k, n in sorted(names.items()) if rx.search(k)]


def _extract_year(get: Getter, iy: int) -> dict[tuple[int, int], np.ndarray]:
    """Return ``{(sector, euse): array(9) in TBtu}`` for year index ``iy`` (0 = 1990)."""
    out = {}
    c = slice(0, NCEN)
    mbtu = 1e-6  # UNCONFAR / UNCONFA
    # residential (arrays in MBtu, year first, census last)
    res = [
        get('htrcon')[iy, ELHEATX - 1, c],
        get('coolcn')[iy, ELCOOLX - 1, c],
        get('h2ocon')[iy, ELWHEAX - 1, c],
        get('ckcon')[iy, ELSTOVX - 1, c],
        get('drycon')[iy, ELDRYEX - 1, c],
        get('refcon')[iy, c],
        get('frzcon')[iy, c],
        get('ltcon')[iy, c],
        sum(get(n)[iy, c] for n in ('apcon', 'cswcon', 'dswcon', 'pcrcon', 'tvrcon', 'fancon')),
        get('shtcon')[iy, ELSECHX - 1, c],
    ]
    for e, a in enumerate(res, 1):
        out[(1, e)] = np.asarray(a, float) * mbtu
    trq = get('trq_elec')  # (10, census, year) already TBtu
    out[(1, 11)] = trq[0, c, iy].astype(float)

    # commercial: EndUseConsump(fuel, service, bldg, census, year) x adjustment
    euc = get('enduseconsump')
    qcm = get('qelcm')[c, iy].astype(float)
    dg = get('cgcommgen')[c, iy, :, 1].sum(axis=1) * 3412 / 1e6
    ev = trq[1:9, c, iy].astype(float).sum(axis=0)  # j = 2..9
    load = euc[ELINDEX - 1, :, :, c, iy].astype(float).sum(axis=(0, 1))
    other = euc[ELINDEX - 1, 9, :, c, iy].astype(float).sum(axis=0)  # service 10
    load = load + ev
    adj1 = np.zeros(NCEN)
    adj2 = np.zeros(NCEN)
    for k in range(NCEN):
        t = qcm[k] + dg[k]
        if other[k] > 0:
            adj1[k] = (t - load[k]) / other[k]
            if adj1[k] < -1:
                adj1[k] = -1.0
                adj2[k] = t / (load[k] - other[k]) - 1.0
        elif load[k] == 0:
            adj1[k] = -1.0
        else:
            adj1[k] = -1.0
            adj2[k] = t / (load[k] - other[k]) - 1.0
    e = 0
    for b in range(11):
        for s in range(10):
            e += 1
            a = adj1 if s == 9 else adj2
            out[(2, e)] = euc[ELINDEX - 1, s, b, c, iy].astype(float) * (1 + a)
    ev_types = [
        trq[1, c, iy],
        trq[2, c, iy],
        trq[3, c, iy],
        trq[4, c, iy],
        trq[5, c, iy] + trq[6, c, iy] + trq[7, c, iy],
        trq[8, c, iy],
    ]
    for x in ev_types:
        e += 1
        out[(2, e)] = x.astype(float)

    # industrial: QELINP, QELINS, QELINM, hydrogen
    for e, n in enumerate(('qelinp', 'qelins', 'qelinm', 'qelinh2e'), 1):
        out[(3, e)] = get(n)[c, iy].astype(float)
    # transport: electric rail
    out[(4, 1)] = trq[9, c, iy].astype(float)
    return out


def sector_controls(get: Getter, iy: int) -> dict[int, np.ndarray]:
    """Return the QBLK sector sales per division (gross of behind-the-meter generation).

    Parameters
    ----------
    get : callable
        Reads one restart variable by its lower-case name.
    iy : int
        Year index (0 = 1990).

    Returns
    -------
    dict of int to ndarray
        ``{sector: array(9)}`` in TBtu.
    """
    c = slice(0, NCEN)

    def dg(n: str) -> np.ndarray:
        return get(n)[c, iy, :, 1].sum(axis=1) * 3412 / 1e6

    return {
        1: get('qelrs')[c, iy].astype(float) + dg('cgresgen'),
        2: get('qelcm')[c, iy].astype(float) + dg('cgcommgen'),
        3: get('qelin')[c, iy].astype(float),
        4: get('qeltr')[c, iy].astype(float),
    }


def extract_loads(
    restart_path: str | Path,
    years: int | Iterable[int],
    out_csv: str | Path | None = None,
    check: bool = True,
    tol: float = 1e-3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the year/sector/euse/census table for the given years.

    Parameters
    ----------
    restart_path : str or Path
        The restart file.
    years : int or iterable of int
        The years to extract.
    out_csv : str or Path, optional
        If given, the load table is also written here.
    check : bool, default True
        Compare the extracted sector totals with the QBLK sales.
    tol : float
        Unused.

    Returns
    -------
    table : pd.DataFrame
        Columns ``year, sector, euse, census, mwh_nems, mwh``.  ``mwh_nems`` is the end-use value
        exactly as uldsm.f would use it.  ``mwh`` is the same, rescaled within each sector and
        division so the end uses add up to the QBLK sales (plus behind-the-meter generation).  The
        two agree wherever NEMS is consistent.
    report : pd.DataFrame
        The check of the sector totals against QBLK; empty if ``check`` is false.
    """
    z, names = open_restart(restart_path)

    def get(n: str) -> np.ndarray:
        return z[names[n]]

    rows, report = [], []
    for y in [years] if isinstance(years, int) else years:
        iy = y - FIRST_YEAR
        d = _extract_year(get, iy)
        ctrl = sector_controls(get, iy)
        tot = {s: np.sum([a for (ss, _), a in d.items() if ss == s], axis=0) for s in ctrl}
        for (s, e), a in d.items():
            for k in range(NCEN):
                t = float(tot[s][k])
                if t > 0:
                    b = float(a[k]) * ctrl[s][k] / t
                elif s == 4 and e == 1:  # transport has one end use
                    b = float(ctrl[s][k])
                else:
                    b = 0.0
                rows.append((y, s, e, k + 1, float(a[k]) * MWH_PER_TBTU, b * MWH_PER_TBTU))
        if check:
            ref = {1: 'qelrs', 2: 'qelcm', 3: 'qelin', 4: 'qeltr'}
            for s, n in ref.items():
                mine = tot[s]
                theirs = get(n)[:NCEN, iy].astype(float)
                # QELRS / QELCM are net of distributed generation
                if s == 1:
                    theirs = theirs + get('cgresgen')[:NCEN, iy, :, 1].sum(axis=1) * 3412 / 1e6
                if s == 2:
                    theirs = theirs + get('cgcommgen')[:NCEN, iy, :, 1].sum(axis=1) * 3412 / 1e6
                with np.errstate(divide='ignore', invalid='ignore'):
                    diff = np.where(theirs != 0, mine / theirs - 1, 0.0)
                report.append(
                    (y, s, float(np.abs(diff).max()), float(mine.sum()), float(theirs.sum()))
                )
    df = pd.DataFrame(rows, columns=['year', 'sector', 'euse', 'census', 'mwh_nems', 'mwh'])
    rep = pd.DataFrame(
        report, columns=['year', 'sector', 'max_rel_diff', 'sum_extracted_mwh', 'sum_QBLK_mwh']
    )
    rep[['sum_extracted_mwh', 'sum_QBLK_mwh']] *= MWH_PER_TBTU
    if out_csv:
        df.to_csv(out_csv, index=False)
    return df, rep


# On-site generation: (category, restart array, sector whose census -> EMM mapping applies).
# Refinery cogeneration is reported apart from the rest of industry; transport has no array.
ONSITE_CATEGORIES: tuple[tuple[str, str | None, int], ...] = (
    ('residential', 'cgresgen', 1),
    ('commercial', 'cgcommgen', 2),
    ('industrial', 'cgindlgen', 3),
    ('refinery', 'cgrefgen', 3),
    ('transport', None, 4),
)
GWH_TO_MWH = 1000.0
# last axis of the COGEN arrays: Python index 0 = sold to the grid, 1 = used on site
SOLD_SLOT, OWN_USE_SLOT = 0, 1


def extract_onsite_generation(restart_path: str | Path, years: int | Iterable[int]) -> pd.DataFrame:
    """Read the on-site generation by census division from a restart file.

    The ``COGEN`` arrays (``cgresgen``, ``cgcommgen``, ``cgindlgen``, ``cgrefgen``) have shape
    ``(census 11, year 61, technology 13, 2)`` in GWh.  Positions 0-8 of the census axis are the
    nine divisions (10 is empty, 11 is the US total and is not used); the technologies are summed;
    the last axis is 0 = sold to the grid, 1 = used on site.  Transport has no array: its rows
    are 0.

    Parameters
    ----------
    restart_path : str or Path
        The restart file.
    years : int or iterable of int
        The years to extract.

    Returns
    -------
    pd.DataFrame
        Columns ``year, category, sector, census, own_use_mwh, sold_mwh`` (MWh; ``category`` is
        residential, commercial, industrial, refinery or transport; ``sector`` is the 1-4 code
        whose census -> EMM mapping applies; ``census`` 1-9).

    Raises
    ------
    ValueError
        If an array is missing from the file, has an unexpected shape, does not cover a year, or
        has negative or non-finite values.
    """
    z, names = open_restart(restart_path)
    year_list = [years] if isinstance(years, int) else list(years)
    rows = []
    for category, var, sector in ONSITE_CATEGORIES:
        if var is None:
            rows += [(y, category, sector, k + 1, 0.0, 0.0) for y in year_list for k in range(NCEN)]
            continue
        if var not in names:
            raise ValueError(f'{restart_path}: variable {var!r} (on-site generation) is missing')
        a = np.asarray(z[names[var]], dtype=float)
        if a.ndim != 4 or a.shape[0] < NCEN or a.shape[3] != 2:
            raise ValueError(
                f'{var}: expected shape (census >= 9, year, technology, 2), got {a.shape}'
            )
        a = a[:NCEN]
        for y in year_list:
            iy = y - FIRST_YEAR
            if not 0 <= iy < a.shape[1]:
                raise ValueError(f'{var}: year {y} is outside the restart file')
            own = a[:, iy, :, OWN_USE_SLOT].sum(axis=1) * GWH_TO_MWH
            sold = a[:, iy, :, SOLD_SLOT].sum(axis=1) * GWH_TO_MWH
            # single technologies can be slightly negative in old years; the sums must not be
            if (
                not (np.isfinite(own).all() and np.isfinite(sold).all())
                or min(own.min(), sold.min()) < 0
            ):
                raise ValueError(f'{var}, {y}: on-site generation must be finite and not negative')
            rows += [(y, category, sector, k + 1, own[k], sold[k]) for k in range(NCEN)]
    return pd.DataFrame(
        rows, columns=['year', 'category', 'sector', 'census', 'own_use_mwh', 'sold_mwh']
    )


HYDROGEN_VARIABLE = 'qelhm_hr'
N_SEASONS, N_DAY_HOURS = 4, 24
N_EMM = 25


def extract_hydrogen_qelhm_hr(restart_path: str | Path, years: int | Iterable[int]) -> pd.DataFrame:
    """Read the hourly electrolyzer demand of the NEMS hydrogen module from a restart file.

    ``HMMBLK/qelhm_hr`` has shape ``(region 28, season 4, hour of day 24, year 61)`` in MWh.
    Positions 0-24 of the region axis are the 25 EMM regions (25-27 are Alaska, Hawaii and the US
    total and are not used).  Each value is the energy of one hour of the day, summed over all
    the days of the season; it is not a power.

    Parameters
    ----------
    restart_path : str or Path
        The restart file.
    years : int or iterable of int
        The years to extract.

    Returns
    -------
    pd.DataFrame
        Columns ``year, region_id`` (1-25), ``season`` (1-4), ``hour_of_day`` (1-24), ``mwh``.

    Raises
    ------
    ValueError
        If the variable is missing, has an unexpected shape, does not cover a year, or has
        negative or non-finite values in a requested year.
    """
    z, names = open_restart(restart_path)
    if HYDROGEN_VARIABLE not in names:
        raise ValueError(
            f'{restart_path}: variable {HYDROGEN_VARIABLE!r} (hydrogen module demand) is missing'
        )
    a = np.asarray(z[names[HYDROGEN_VARIABLE]], dtype=float)
    if a.ndim != 4 or a.shape[:3] != (28, N_SEASONS, N_DAY_HOURS):
        raise ValueError(
            f'{HYDROGEN_VARIABLE}: expected shape (28, {N_SEASONS}, {N_DAY_HOURS}, year), '
            f'got {a.shape}'
        )
    year_list = [years] if isinstance(years, int) else list(years)
    frames = []
    for y in year_list:
        iy = y - FIRST_YEAR
        if not 0 <= iy < a.shape[3]:
            raise ValueError(f'{HYDROGEN_VARIABLE}: year {y} is outside the restart file')
        v = a[:N_EMM, :, :, iy]
        if not np.isfinite(v).all() or (v < 0).any():
            raise ValueError(
                f'{HYDROGEN_VARIABLE}, {y}: hydrogen demand must be finite and not negative'
            )
        r, s, h = np.meshgrid(
            np.arange(1, N_EMM + 1),
            np.arange(1, N_SEASONS + 1),
            np.arange(1, N_DAY_HOURS + 1),
            indexing='ij',
        )
        frames.append(
            pd.DataFrame(
                {
                    'year': y,
                    'region_id': r.ravel(),
                    'season': s.ravel(),
                    'hour_of_day': h.ravel(),
                    'mwh': v.ravel(),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def find_restart(root: str | Path, scenario: str) -> Path:
    """Find the newest ``restart.rlx`` of a scenario: ``root/<scenario>/<run>/restart.rlx``.

    Parameters
    ----------
    root : str or Path
        The folder with the NEMS outputs by scenario.
    scenario : str
        The NEMS scenario folder name, e.g. ``cb2026``.

    Returns
    -------
    Path
        The restart file of the last run folder (in name order) that has one.

    Raises
    ------
    FileNotFoundError
        If the scenario folder or a restart file is missing, or the file is a Git LFS pointer
        (about 130 bytes), not data: run ``git lfs pull`` first.
    """
    folder = Path(root) / scenario
    if not folder.is_dir():
        have = (
            sorted(p.name for p in Path(root).iterdir() if p.is_dir())
            if Path(root).is_dir()
            else []
        )
        raise FileNotFoundError(f'no scenario folder {folder}; scenarios found in {root}: {have}')
    # run folders, or the files directly
    runs = sorted(p for p in folder.iterdir() if p.is_dir()) or [folder]
    for run in reversed(runs):
        for name in ('restart.rlx',):
            f = run / name
            if f.exists():
                if f.stat().st_size < 1000:
                    raise FileNotFoundError(
                        f"{f} is a Git LFS pointer, not data; run 'git lfs pull'"
                    )
                return f
    raise FileNotFoundError(f'no restart file under {folder}')


def extract_scenarios(
    root: str | Path,
    scenarios: Iterable[str],
    years: int | Iterable[int],
    out_csv: str | Path | None = None,
) -> pd.DataFrame:
    """Stack the census load tables of several scenarios.

    Parameters
    ----------
    root : str or Path
        The folder with the NEMS outputs by scenario.
    scenarios : iterable of str
        NEMS scenario folder names.
    years : int or iterable of int
        The years to extract.
    out_csv : str or Path, optional
        If given, the stacked table is also written here.

    Returns
    -------
    pd.DataFrame
        The tables of :func:`extract_loads` with a leading ``scenario`` column.
    """
    frames = []
    for s in scenarios:
        df, _ = extract_loads(find_restart(root, s), years)
        df.insert(0, 'scenario', s)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    if out_csv:
        out.to_csv(out_csv, index=False)
    return out


def scenario_name(path: str | Path) -> str:
    """Return the scenario key stored in a restart file, e.g. ``'cb2026'``.

    Parameters
    ----------
    path : str or Path
        The restart file.

    Returns
    -------
    str
        The scenario key, or ``''`` if the file does not carry one.
    """
    z, n = open_restart(path)
    if 'scen' not in n:
        return ''
    v = z[n['scen']].ravel()[0]
    return (v.decode() if isinstance(v, bytes) else str(v)).strip()
