"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Python port of the NEMS Electricity Market Module (EMM) load-and-demand (LDSM / "ELD") hourly-load
algorithm, as implemented in source/uldsm.f of https://github.com/EIAgov/NEMS .  It builds the
hourly load profile of each of the 25 EMM regions from

    * the shape and structure tables of NEMS_INPUT.db, provided as CSV files (one per table), and
    * annual electricity load by end use (the output of the NEMS demand modules).

What is ported (Fortran routine -> Python)
------------------------------------------
    CALLSYSLOAD / CALLSYSLOAD_EU   -> NemsLoad._build             (shape tables, 864 points)
    LOAD_LDSM_CAL                  -> NemsLoad._build             (day weights = IDAYTQ)
    DSMRST  (LSR name matching)    -> NemsLoad.shape_name         (EUrecNUM lookup)
    DSMEMMV + DSMRST normalising   -> NemsLoad.census_to_region   (census -> EMM region)
    DSMDLT  (delta approach)       -> NemsLoad.delta_profile      (load2 = load1 - slr*BaseYrLd)
    DSMNWS  (annual -> hourly)     -> NemsLoad.delta_profile / basic_profile (SYLOAD += DistLo*load)
    DSMHLM  (T&D losses, H2)       -> NemsLoad._finish

Not ported: everything that happens after the hourly load exists (load duration curves, dispatch,
capacity planning), and the NEMS demand modules that produce the annual loads.  Those annual loads
are an INPUT here.

Two ways to build the hourly load (section "Developing system load shapes" of the EMM report)
-----------------------------------------------------------------------------------------------
BASIC  (Eq. 2-2, method="basic")
    SYLOAD[h] = sum_e DistLo_e[h] * load1_e            bottom-up sum of end-use shapes
    Needs: end-use shapes, annual load by end use of the projection year.  No base year.
    (The sums leave out hydrogen, industrial end use 4: it is added to SYLOAD afterwards.)
    In uldsm.f this is the formula behind the SECTOR curves (SectorLoad); the system load that goes
    to planning/dispatch uses the delta approach.  The report says the system result is then
    multiplied by the T&D loss factor, which is what method="basic" does.
DELTA  (Eqs. 2-3, 2-4, method="delta")
    SYLOAD[h] = DistLo_system[h]*SystemLoad + sum_e DistLo_e[h]*(load1_e - slr*BaseYrLd_e)
    Needs: everything BASIC needs, plus the historical system shape and the base-year load by end
    use.

WHERE TO FEED YOUR INPUT FILES   (a run is described in [load_config], see run.py and the README)
------------------------------
    INPUT 1  Shape and structure tables, one CSV per table, named <table>.csv, all in one folder
             config: tables_dir
             Python: tables = load_tables(folder); check_tables(tables);
                     model = NemsLoad(tables, calendar)
             tables used (all required): V_SYS_LOAD_by_EndUse, V_SYS_LOAD_by_EMM_REGION,
             V_SYS_LOAD_WEIGHTS, V_EMM_LDSMSTR_EU_SECT, V_EMM_LDSMSTR_SECT_EU_DIM,
             V_EMM_LDSMSTR_SECT_DIM, V_EMM_LDSMSTR_RGN, V_EMM_LDSMSTR_RGN_CEN
    INPUT 2  Annual load, projection year (BASIC and DELTA)
             CSV  year,region,sector,euse,mwh                   config: emm_region_loads
             or by census division: year,sector,euse,census,mwh   config: census_division_loads
    INPUT 3  Annual load, base year (DELTA only; same format as INPUT 2, same file is fine)
             selected with base_year (fixed) or base_freeze_year (NEMS rule: min(year, freeze year))
    INPUT 4  Hydrogen (electrolyzer) demand: industrial end use 4 of INPUT 2 (an annual row)
             or, in Python, one value per point (864) in MW per region (argument h2_hourly_mw).
             Hydrogen has no shape, so the sums above leave it out; its demand is added to the
             sales profile afterwards and goes through the T&D factor with the rest (hydrogen.py)
    INPUT 5  ULOSSADJ, optional: multiplier on the T&D loss factor.  A CSV year,ulossadj
             config: ulossadj_file, or one number for the whole run (loss_adj); default 1.0 = no
             adjustment.
             Python: build_all_regions(..., loss_adj=load_ulossadj(path))   a missing year is an
             error.  NEMS sets it per year in prepett.f (1.0 to 1998, 0.97-0.91 for 1999-2006, 1.0
             for 2007-2010, 1.05 for 2011-2013, 1.02 for 2014, 1.0 for 2015-2019, 0.8 from 2020):
             the file ulossadj_by_year.csv holds this schedule
    INPUT 6  Day-type calendar (calendars.py; default 2023, built at each run from the hourly load
             file)   config: calendar_year, calendar_csv, load_dir, holidays, peak_days_csv
             Python: NemsLoad(tables, calendar_df=get_calendar(load_csv=...)[0])
             The day counts of the calendar are the weights (replacing V_SYS_LOAD_WEIGHTS, which
             is used only for checks), and every system and end-use shape is divided by its own
             day-weighted sum, so that each sums to exactly 1 with the weights in use (the NEMS
             shapes sum to 1 only with the NEMS day counts, which fit 2018; with the counts of
             another year they are off by about 1%).  Annual energy is then conserved exactly, and
             the same calendar drives the 8760-hour expansion.  renormalize = false turns the
             scaling off.  2018 = NEMS (counts equal the NEMS weights), 2023 = built from an
             hourly load file (default; if it cannot be built and you did not name a year, 2018 is
             used and a note says so).  Any other year needs holidays and peak days (or a load
             file); see calendars.py.

Units
-----
Annual loads are in MWh.  A profile value is MWh in one representative hour, which equals average
MW.  The shipped tables give 864 points = 12 months x 3 day types x 24 hours (day type 1 weekday,
2 weekend, 3 peak day); the model reads these three sizes from the tables.  Each point stands for
`weight` days (V_SYS_LOAD_WEIGHTS = IDAYTQ in the Fortran).  The annual energy of a profile is
sum(value * weight).

The index of a point is  q = ((month-1)*n_daytypes + (day_type-1))*hours_per_day + hour - 1
(0-based; n_daytypes = 3 and hours_per_day = 24 in the shipped data), exactly as in uldsm.f.

Status: this is a re-implementation from reading the source.  It was NOT checked against numbers
from a real NEMS run (that needs the whole of NEMS).  Internal consistency (energy
conservation etc.) is checked by tests/load/test_algorithm.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from .calendars import to_model_calendar  # day-type calendars live in calendars.py

# MWh per TBtu (NEMS UNCONFA = 292.83 GWh per TBtu, includes/dsmdimen); used when NEMS data are read
MWH_PER_TBTU = 292_830.0
N_REGIONS = 25  # EMM regions modelled
N_CENSUS = 9  # census divisions used in the mapping (nCENSUSreg = MNUMCR-2)
# EMM_REG code 28 in V_EMM_LDSMSTR_RGN_CEN = contiguous-US total (denominator of the census map)
EMM_REG_US_TOTAL = 28
# industrial sector, end use 4 = hydrogen: outside the decomposition, added as an hourly profile
H2_KEY = (3, 4)


def point_index(
    month: Any, day_type: Any, hour: Any, n_daytypes: int = 3, hours_per_day: int = 24
) -> np.ndarray:
    """Return the 0-based index into the point arrays.

    The formula of uldsm.f, which has 3 day types and 24 hours fixed.  No range check here:
    ``NemsLoad.point()`` adds it, using the sizes read from the tables.

    Parameters
    ----------
    month, day_type, hour : int or array_like
        Month 1-12, day type 1-3 and hour of the day 1-24.
    n_daytypes, hours_per_day : int
        The sizes of the grid.

    Returns
    -------
    ndarray
        The index of each point.
    """
    return (
        ((np.asarray(month) - 1) * n_daytypes + (np.asarray(day_type) - 1)) * hours_per_day
        + np.asarray(hour)
        - 1
    )


# --------------------------------------------------------------------------- #
# database access
# --------------------------------------------------------------------------- #
TABLES = [
    'V_SYS_LOAD_WEIGHTS',
    'V_SYS_LOAD_by_EMM_REGION',
    'V_SYS_LOAD_by_EndUse',
    'V_EMM_LDSMSTR_EU_SECT',
    'V_EMM_LDSMSTR_SECT_EU_DIM',
    'V_EMM_LDSMSTR_SECT_DIM',
    'V_EMM_LDSMSTR_RGN',
    'V_EMM_LDSMSTR_RGN_CEN',
]


# >>> INPUT 1: the shape / structure tables (CSV files) -------------------------
def load_tables(csv_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Read the input tables: one CSV per table, named ``<table>.csv``.

    All the tables in ``TABLES`` are required, for both the basic and the delta approach.  Only
    reads files; no calculation happens here.

    Parameters
    ----------
    csv_dir : str or Path
        The folder with the tables.

    Returns
    -------
    dict of str to pd.DataFrame
        ``{table name: table}``.

    Raises
    ------
    FileNotFoundError
        If the folder or a table is missing.
    """
    csv_dir = Path(csv_dir)
    if not csv_dir.is_dir():
        raise FileNotFoundError(f'folder not found: {csv_dir}')
    tables = {}
    for t in TABLES:
        p = csv_dir / (t + '.csv')
        if p.exists():
            tables[t] = pd.read_csv(p)
        else:
            raise FileNotFoundError(f'missing input file: {p}')
    return tables


# >>> INPUT 5: ULOSSADJ by year (CSV with columns year, ulossadj) ----------------
def load_ulossadj(path: str | Path) -> pd.Series:
    """Read the year -> ULOSSADJ file.  Lines starting with # are comments.

    Only reads; use ``data_validation.check_ulossadj`` to check it.

    Parameters
    ----------
    path : str or Path
        CSV with the columns ``year`` and ``ulossadj``.

    Returns
    -------
    pd.Series
        ULOSSADJ indexed by calendar year.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f'missing input file: {path}')
    df = pd.read_csv(path, comment='#')
    return pd.Series(
        df['ulossadj'].to_numpy(), index=df['year'].astype(int).to_numpy(), name='ulossadj'
    )


class NemsLoad:
    """Loads the shape and structure tables once; builds profiles on request."""

    def __init__(
        self,
        tables: dict[str, pd.DataFrame],
        calendar_df: pd.DataFrame,
        renormalize: bool = True,
    ) -> None:
        """Index the tables and the calendar; reads no files and does NOT check the data.

        Run ``data_validation.check_tables(tables)`` first.

        Parameters
        ----------
        tables : dict of str to pd.DataFrame
            The dictionary returned by :func:`load_tables`; it can also be built by hand (for
            example in tests).
        calendar_df : pd.DataFrame
            The day-type calendar (columns ``date``, ``month``, ``day_type``; see
            ``calendars.get_calendar``), INPUT 6.  The days represented by each (month, day type)
            are COUNTED from it: they are the weights (IDAYTQ), and the same calendar lays the 864
            points on the 8760 hours.  V_SYS_LOAD_WEIGHTS is not used here.
        renormalize : bool, default True
            Divide every shape by its own day-weighted sum, so that each shape sums to exactly 1
            with these weights (the NEMS shapes sum to 1 only with the NEMS day counts, which fit
            the 2018 calendar).
        """
        # words -> numbers if needed
        self.calendar = to_model_calendar(calendar_df).sort_values('date').reset_index(drop=True)
        self.renormalize = bool(renormalize)
        self._build(tables)

    # ---- building the lookups ---------------------------------------------- #
    def _build(self, T: dict[str, pd.DataFrame]) -> None:
        """Build the point grid, day weights, shapes and structure lookups from the tables."""
        # size of the grid: months x day types x hours, read from the end-use shape table ---
        enduse_shape_tbl = T['V_SYS_LOAD_by_EndUse']
        self.n_months, self.n_daytypes, self.hours_per_day = (
            int(enduse_shape_tbl.Month.max()),
            int(enduse_shape_tbl.DAY.max()),
            int(enduse_shape_tbl.HOUR.max()),
        )
        # 864 in the shipped data
        self.n_load_points = self.n_months * self.n_daytypes * self.hours_per_day

        # day weights (IDAYTQ / WEIGHT): days represented by each (month, day type), counted from
        # the calendar -----
        counts = self.calendar.groupby(['month', 'day_type']).size().rename('days').reset_index()
        self.day_weights = {(int(m), int(d)): int(x) for m, d, x in counts.itertuples(index=False)}
        for m in range(1, self.n_months + 1):  # (month, day type) with no day counts 0
            for d in range(1, self.n_daytypes + 1):
                self.day_weights.setdefault((m, d), 0)
        # the same numbers, one per point (days represented by the point)
        self.weights = np.zeros(self.n_load_points)
        hours = np.arange(1, self.hours_per_day + 1)
        for (m, d), x in self.day_weights.items():
            self.weights[point_index(m, d, hours, self.n_daytypes, self.hours_per_day)] = x
        self.n_days = sum(self.day_weights.values())  # 365, or 366 in a leap year
        # 8760, or 8784 in a leap year (if 24 h/day)
        self.n_hours = self.n_days * self.hours_per_day

        # system load shape DistLo_s(region, 864)  (delta approach only) ---------------
        self.dist_sys = {}
        for r, g in T['V_SYS_LOAD_by_EMM_REGION'].groupby('EMM_REG'):
            arr = np.full(self.n_load_points, np.nan)
            arr[point_index(g.Month, g.DAY, g.HOUR, self.n_daytypes, self.hours_per_day)] = (
                g.PCT.to_numpy()
            )
            self.dist_sys[int(cast(int, r))] = arr

        # end-use load shapes DistLo_eu_s(name, 864) -------------------------------
        self.dist_eu = {}
        for name, g in T['V_SYS_LOAD_by_EndUse'].groupby('LS_Name'):
            arr = np.full(self.n_load_points, np.nan)
            arr[point_index(g.Month, g.DAY, g.HOUR, self.n_daytypes, self.hours_per_day)] = (
                g.PCT.to_numpy()
            )
            self.dist_eu[str(name).strip()] = arr

        # day-weighted sum of every shape, and (optionally) renormalisation to exactly 1 --------
        # sum = sum_q shape_q * weight_q is the share of annual energy the 864 points add up to.
        # It is 1 only with the weights the shapes were built with; with other day counts it is
        # not, so divide by it.
        self.shape_sum = {
            ('system', r): float((a * self.weights).sum()) for r, a in self.dist_sys.items()
        }
        self.shape_sum.update(
            {('end use', n): float((a * self.weights).sum()) for n, a in self.dist_eu.items()}
        )
        if self.renormalize:
            for r in self.dist_sys:
                self.dist_sys[r] = self.dist_sys[r] / self.shape_sum[('system', r)]
            for n in self.dist_eu:
                self.dist_eu[n] = self.dist_eu[n] / self.shape_sum[('end use', n)]

        # structure: end use -> shape name per region (LSRnam) ----------------------
        eu_sect_tbl = T['V_EMM_LDSMSTR_EU_SECT']
        eu_sect_tbl = eu_sect_tbl[eu_sect_tbl.EMM_REG <= N_REGIONS]
        self._shape = {
            (int(r), int(s_), int(e_)): str(n).strip()
            for r, s_, e_, n in eu_sect_tbl[['EMM_REG', 'SECTOR', 'EUSE', 'LSRnam']].itertuples(
                index=False
            )
        }
        self.end_uses = sorted({(s_, e_) for (_, s_, e_) in self._shape})  # (sector, end use)

        eu_dim_tbl = T['V_EMM_LDSMSTR_SECT_EU_DIM']
        self.eu_name = {
            (int(a), int(b)): str(n).strip()
            for a, b, n in eu_dim_tbl[['SECTOR', 'EUSE', 'EUNAM']].itertuples(index=False)
        }
        sect_dim_tbl = T['V_EMM_LDSMSTR_SECT_DIM']
        self.sector_name = {
            int(a): str(n).strip()
            for a, n in sect_dim_tbl[['SECTOR_NUM', 'SECTOR_NM']].itertuples(index=False)
        }

        # regions and T&D loss factor UQTDLS -------------------------------------
        rgn_tbl = T['V_EMM_LDSMSTR_RGN']
        rgn_tbl = rgn_tbl[rgn_tbl.EMM_REG <= N_REGIONS]
        self.regions = {
            int(r): str(n).strip()
            for r, n in rgn_tbl[['EMM_REG', 'EMM_REG_NM']].itertuples(index=False)
        }
        self.tdloss = {
            int(r): float(t) for r, t in rgn_tbl[['EMM_REG', 'UQTDLS']].itertuples(index=False)
        }

        # census -> EMM region mapping (MappCtoN), normalised like uldsm.f l.4195 ----
        rgn_cen_tbl = T['V_EMM_LDSMSTR_RGN_CEN']
        raw = {
            (int(r), int(s_), int(c)): float(v)
            for r, s_, c, v in rgn_cen_tbl[['EMM_REG', 'SECTOR', 'CENSUS', 'MappCtoN']].itertuples(
                index=False
            )
        }
        self.map_c2n = {}
        for (r, s_, c), v in raw.items():
            if r <= N_REGIONS and c <= N_CENSUS:
                denom = raw.get((EMM_REG_US_TOTAL, s_, c), 0.0)
                self.map_c2n[(r, s_, c)] = v / denom if denom > 0 else 0.0

    # ---- helpers -------------------------------------------------------------- #
    def shape_name(self, region: int, sector: int, euse: int) -> str:
        """Return the name of the load shape of an end use in a region.

        Parameters
        ----------
        region, sector, euse : int
            EMM region, sector and end-use codes.

        Returns
        -------
        str
            The shape name (LSRnam).
        """
        return self._shape[(region, sector, euse)]

    def census_to_region(self, census_mwh: pd.DataFrame) -> pd.DataFrame:
        """Map annual loads from census divisions to EMM regions (DSMEMMV).

        ``NERCval = sum_k CENSUSvalue(k) * MappCtoN(region, k, sector)``; the unit is the same in
        and out (MWh).

        Parameters
        ----------
        census_mwh : pd.DataFrame
            Columns ``year, sector, euse, census`` (1-9), ``mwh``.

        Returns
        -------
        pd.DataFrame
            Columns ``year, region, sector, euse, mwh`` (regions 1-25).
        """
        rows = []
        for (y, s_, e_), g in census_mwh.groupby(['year', 'sector', 'euse']):
            vals = dict(zip(g.census.astype(int), g.mwh.astype(float), strict=True))
            for r in self.regions:
                tot = sum(v * self.map_c2n.get((r, int(s_), c), 0.0) for c, v in vals.items())
                rows.append((int(y), r, int(s_), int(e_), tot))
        return pd.DataFrame(rows, columns=['year', 'region', 'sector', 'euse', 'mwh'])

    # ---- the algorithms ---------------------------------------------------------- #
    def _hydrogen(self, annual_mwh: float, h2_hourly_mw: np.ndarray | None) -> np.ndarray | None:
        """Return the hydrogen demand (MW at each point) to add to the profile, or ``None``.

        Hydrogen is outside the load decomposition.  A given profile is used as it is; without
        one the annual hydrogen load is spread evenly over every hour of the calendar.

        Parameters
        ----------
        annual_mwh : float
            The annual hydrogen load of the region (the industrial end use 4 row), MWh.
        h2_hourly_mw : ndarray, optional
            Hydrogen demand in MW at each point (864); replaces ``annual_mwh`` when given.

        Returns
        -------
        ndarray or None
            ``None`` if there is no hydrogen.

        Raises
        ------
        ValueError
            If a profile has the wrong length or negative values.
        """
        if h2_hourly_mw is None:
            if annual_mwh == 0.0:
                return None
            return np.full(self.n_load_points, annual_mwh / self.n_hours)
        h2 = np.asarray(h2_hourly_mw, dtype=float)
        if h2.shape != (self.n_load_points,):
            raise ValueError(f'h2_hourly_mw must have {self.n_load_points} values')
        if (h2 < 0).any() or not np.isfinite(h2).all():
            raise ValueError('h2_hourly_mw must be finite and not negative')
        return h2

    def _finish(
        self,
        region: int,
        sales: np.ndarray,
        sector_sales: dict[int, np.ndarray],
        h2: np.ndarray | None,
        loss_adj: float,
        **extra: Any,
    ) -> dict[str, Any]:
        """Add the hydrogen demand and the T&D losses to a profile and return the result.

        ``extra`` carries ``system_load`` as the sum of the non-hydrogen end uses; the hydrogen
        energy is added to it here.
        """
        hydrogen_mwh = 0.0
        if h2 is not None:  # hydrogen is outside the decomposition: added to the sales profile
            sales = sales + h2
            sector_sales[H2_KEY[0]] = (
                sector_sales.setdefault(H2_KEY[0], np.zeros(self.n_load_points)) + h2
            )
            hydrogen_mwh = float((h2 * self.weights).sum())
        td = 1.0 + self.tdloss[region] * loss_adj  # T&D loss expansion (DSMHLM)
        extra['system_load_ex_h2_mwh'] = extra['system_load']
        extra['system_load'] = extra['system_load'] + hydrogen_mwh
        return {
            'sales': sales,
            'load': sales * td,
            'sector_sales': sector_sales,
            'td': td,
            'annual_sales_mwh': float((sales * self.weights).sum()),
            'hydrogen_mwh': hydrogen_mwh,
            **extra,
        }

    def basic_profile(
        self,
        region: int,
        load1: pd.Series,
        loss_adj: float = 1.0,
        h2_hourly_mw: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Build the BASIC profile (Eq. 2-2; DSMNWS + DSMHLM) of one region and year.

        ``SYLOAD[h] = sum_e DistLo_e[h] * load1_e`` over the end uses except hydrogen, plus the
        hydrogen demand, then ``x (1 + UQTDLS*ULOSSADJ)``.  No base year (INPUT 3) and no
        historical system shape (V_SYS_LOAD_by_EMM_REGION) are used.  In uldsm.f the sum is
        computed per sector; adding the sectors gives the system sales, and ``load`` applies the
        T&D factor as the report describes.

        Parameters
        ----------
        region : int
            EMM region number 1-25.
        load1 : pd.Series
            Annual load by (sector, euse), MWh, projection year (LoadForec(:,1)), INPUT 2.  A
            (sector, euse) that is absent counts as 0; one that is not in the structure table is
            dropped without a message (``data_validation.check_loads`` catches both).  The
            hydrogen row (industrial end use 4) is not part of the decomposition; it is the
            annual hydrogen load unless ``h2_hourly_mw`` is given.
        loss_adj : float, default 1.0
            ULOSSADJ(year), multiplies the T&D loss factor UQTDLS, INPUT 5; the NEMS value depends
            on the year (0.8 from 2020 on, see the module docstring); 1.0 means "no adjustment".
        h2_hourly_mw : ndarray, optional
            Hydrogen demand in MW, one value per point (864); replaces the hydrogen row.  Without
            it the row is spread evenly over the year.

        Returns
        -------
        dict
            Arrays of length ``n_load_points`` (MW): ``sales`` (SYLOAD before T&D losses: the sum
            over the end uses except hydrogen of DistLo_e * load1_e, plus hydrogen), ``load``
            (SYLOAD after T&D losses, what goes on to dispatch/planning), ``sector_sales``
            (``{sector: array}``, DistLo*load1 per sector plus hydrogen in industry, no T&D as in
            the Fortran SectorLoad); and the scalars ``system_load`` (MWh, all end uses including
            hydrogen), ``system_load_ex_h2_mwh``, ``hydrogen_mwh``, ``td``, ``annual_sales_mwh``
            and ``method`` ("basic").
        """
        L1 = load1.reindex(self.end_uses).astype(float).fillna(0.0)
        h2_annual = float(L1.get(H2_KEY, 0.0))
        sales = np.zeros(self.n_load_points)
        sector_sales: dict[int, np.ndarray] = {}
        for s_, e_ in self.end_uses:
            sector_sales.setdefault(s_, np.zeros(self.n_load_points))
            if (s_, e_) == H2_KEY:  # hydrogen has no shape; added after the decomposition
                continue
            part = self.dist_eu[self.shape_name(region, s_, e_)] * L1[(s_, e_)]
            sector_sales[s_] += part
            sales += part
        return self._finish(
            region,
            sales,
            sector_sales,
            self._hydrogen(h2_annual, h2_hourly_mw),
            loss_adj,
            system_load=float(L1.drop(H2_KEY, errors='ignore').sum()),
            method='basic',
        )

    def delta_profile(
        self,
        region: int,
        load1: pd.Series,
        base: pd.Series,
        loss_adj: float = 1.0,
        h2_hourly_mw: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Build the DELTA profile (Eqs. 2-3, 2-4; DSMDLT + DSMNWS + DSMHLM) of one region and year.

        ``SYLOAD[h] = DistLo_sys[h]*SystemLoad + sum_e DistLo_e[h]*(load1_e - slr*BaseYrLd_e)``
        with ``slr = SystemLoad / sum_e BaseYrLd_e``, then the hydrogen demand is added and
        everything is multiplied by ``(1 + UQTDLS*ULOSSADJ)``.  The sums run over the end uses
        except hydrogen, which is outside the decomposition (in NEMS it is inside ``SystemLoad``
        and the base-year sum, which counts ``slr * BaseYrLd_hydrogen`` extra unless the base-year
        hydrogen is zero).  Also uses the historical system shape of the region
        (V_SYS_LOAD_by_EMM_REGION).

        Parameters
        ----------
        region : int
            EMM region number 1-25.
        load1 : pd.Series
            Annual load by (sector, euse), MWh, projection year (LoadForec(:,1)), INPUT 2.
        base : pd.Series
            Annual load by (sector, euse), MWh, base year (BaseYrLd), INPUT 3.  Its total
            without hydrogen must be positive; absent categories count as 0.  The hydrogen row
            is not used.
        loss_adj : float, default 1.0
            ULOSSADJ(year), multiplies the T&D loss factor UQTDLS, INPUT 5; the NEMS value depends
            on the year (0.8 from 2020 on, see the module docstring); 1.0 means "no adjustment".
        h2_hourly_mw : ndarray, optional
            Hydrogen demand in MW, one value per point (864); replaces the hydrogen row of
            ``load1``.  Without it the row is spread evenly over the year.

        Returns
        -------
        dict
            Arrays of length ``n_load_points`` (MW): ``sales`` (SYLOAD before T&D losses,
            including hydrogen), ``load`` (SYLOAD after T&D losses, what goes on to
            dispatch/planning), ``sector_sales`` (``{sector: array}``, DistLo*load1 per sector,
            plus hydrogen in industry, no T&D as in the Fortran SectorLoad); and the scalars
            ``system_load`` (MWh, all end uses including hydrogen), ``system_load_ex_h2_mwh``
            (the ``SystemLoad`` of the decomposition), ``hydrogen_mwh``, ``base_system_load``
            (MWh, sum of base without hydrogen), ``slr``, ``td``, ``annual_sales_mwh`` and
            ``method`` ("delta").

        Raises
        ------
        ValueError
            If the base-year system load without hydrogen is zero.
        """
        keys = self.end_uses
        L1 = load1.reindex(keys).astype(float).fillna(0.0)  # NaN -> 0 as in DSMNWS
        B = base.reindex(keys).astype(float).fillna(0.0)
        h2_annual = float(L1.get(H2_KEY, 0.0))
        L1 = L1.drop(H2_KEY, errors='ignore')  # hydrogen is outside the decomposition
        B = B.drop(H2_KEY, errors='ignore')
        keys = list(L1.index)

        system_load = L1.sum()  # DSMDLT
        base_sys = B.sum()
        if base_sys <= 0:
            raise ValueError(
                'base-year system load is zero: cannot compute slr = SystemLoad/BaseYrSysLd'
            )
        slr = system_load / base_sys
        L2 = L1 - slr * B  # Eq. 2-4

        sales = self.dist_sys[region] * system_load  # DSMNWS(0,0,0): system shape * SystemLoad
        sector_sales: dict[int, np.ndarray] = {}
        for s_, e_ in keys:
            sector_sales.setdefault(s_, np.zeros(self.n_load_points))
            shp = self.dist_eu[self.shape_name(region, s_, e_)]
            if L2[(s_, e_)] != 0.0:
                sales += shp * L2[(s_, e_)]  # Eq. 2-3: DistLo_e * load2_e
            sector_sales[s_] += shp * L1[(s_, e_)]  # SectorLoad uses load1
        return self._finish(
            region,
            sales,
            sector_sales,
            self._hydrogen(h2_annual, h2_hourly_mw),
            loss_adj,
            system_load=float(system_load),
            base_system_load=float(base_sys),
            slr=float(slr),
            method='delta',
        )

    def build_all_regions(
        self,
        loads: pd.DataFrame,
        year: int,
        method: str = 'delta',
        base_year: int | None = None,
        loss_adj: float | pd.Series | dict = 1.0,
        h2_hourly_mw: dict | None = None,
        base_freeze_year: int | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Build the profiles of all 25 regions for one year.

        Parameters
        ----------
        loads : pd.DataFrame
            Columns ``year, region, sector, euse, mwh``.
        year : int
            The projection year.
        method : {'delta', 'basic'}
            ``"basic"`` needs only ``year``; ``"delta"`` also needs a base year, given in one of
            the two ways below.
        base_year : int, optional
            A fixed base year, the same for every projection year.
        base_freeze_year : int, optional
            The NEMS rule.  The base year of projection year y is ``min(y, base_freeze_year)``: up
            to the freeze year each year is its own base (the delta term is zero), later years use
            the freeze year's loads.  In NEMS this is year index MSEDYR+3 = 2026 (DSMFOR).  Give
            one of ``base_year`` and ``base_freeze_year``, not both.
        loss_adj : float, Series or dict
            A number, or a year -> ULOSSADJ table (see :func:`load_ulossadj`); with a table the
            value for ``year`` is used and a missing year raises ``KeyError``.
        h2_hourly_mw : dict, optional
            ``{region: hydrogen demand in MW per point}``.  A region without an entry takes its
            hydrogen from the industrial end use 4 row of ``loads``, spread evenly over the year.

        Returns
        -------
        dict of int to dict
            ``{region: profile}``, each as returned by :meth:`basic_profile` or
            :meth:`delta_profile`.

        Raises
        ------
        ValueError
            If the method or the base-year settings are not valid, or a region has no loads for
            the year or the base year.
        """
        if isinstance(loss_adj, (pd.Series, dict)):
            loss_adj = float(loss_adj[year])
        if method not in ('basic', 'delta'):
            raise ValueError("method must be 'basic' or 'delta'")
        if base_year is not None and base_freeze_year is not None:
            raise ValueError('give base_year or base_freeze_year, not both')
        if base_freeze_year is not None:
            base_year = min(year, base_freeze_year)
        if method == 'delta' and base_year is None:
            raise ValueError('the delta approach needs base_year or base_freeze_year')
        out = {}
        for r in self.regions:
            sub = loads[loads.region == r]

            def series(y: int, sub: pd.DataFrame = sub) -> pd.Series:
                return sub[sub.year == y].set_index(['sector', 'euse']).mwh

            L1 = series(year)
            if L1.empty:
                raise ValueError(f'region {r}: no loads for year {year}')
            h2 = (h2_hourly_mw or {}).get(r)
            if method == 'basic':
                out[r] = self.basic_profile(r, L1, loss_adj, h2)
            else:
                assert base_year is not None  # checked above
                B = series(base_year)
                if B.empty:
                    raise ValueError(f'region {r}: no loads for base year {base_year}')
                out[r] = self.delta_profile(r, L1, B, loss_adj, h2)
        return out

    # ---- 864 -> 8760 ------------------------------------------------------------- #
    def profile_frame(self, prof: dict[str, Any]) -> pd.DataFrame:
        """Return an 864-point profile as a table (one row per month/day type/hour).

        Parameters
        ----------
        prof : dict
            A profile from :meth:`basic_profile` or :meth:`delta_profile`.

        Returns
        -------
        pd.DataFrame
            Columns ``month, day_type, hour, weight_days, sales_mw, load_mw`` and one
            ``sales_mw_<sector>`` per sector.
        """
        nd, hp = self.n_daytypes, self.hours_per_day
        m = np.repeat(np.arange(1, self.n_months + 1), nd * hp)
        d = np.tile(np.repeat(np.arange(1, nd + 1), hp), self.n_months)
        h = np.tile(np.arange(1, hp + 1), self.n_months * nd)
        df = pd.DataFrame(
            {
                'month': m,
                'day_type': d,
                'hour': h,
                'weight_days': self.weights,
                'sales_mw': prof['sales'],
                'load_mw': prof['load'],
            }
        )
        for s_, arr in prof['sector_sales'].items():
            df[f'sales_mw_{self.sector_name.get(s_, s_).lower()}'] = arr
        return df

    def crosswalk(self) -> pd.DataFrame:
        """Return the map from the 864 points to the 8760 hours: one row per hour of the calendar.

        It depends only on the calendar, not on the projection year: every projected year is laid
        out in the same order of days as the calendar's year, so the table is built once and
        reused (written as ``crosswalk_864_to_8760.csv``).

        Returns
        -------
        pd.DataFrame
            Columns ``hour_index`` (1..n_hours, in date order: 1 = 00:00-01:00 on the calendar's
            1 January), ``datetime``, ``month``, ``day``, ``hour_of_day`` (1..24), ``day_type``
            and ``point_index`` (0..863, the representative point that supplies this hour; same
            index as in the profile arrays).

        Raises
        ------
        ValueError
            If the tables do not have 24 hours per day.
        """
        if self.hours_per_day != 24:
            raise ValueError(
                'the crosswalk writes clock times and needs 24 hours per day; the tables have '
                f'{self.hours_per_day}'
            )
        cal, hp = self.calendar, self.hours_per_day
        hours = np.tile(np.arange(hp), len(cal))  # 0..23 for every day
        first_point = point_index(
            cal.month.to_numpy(), cal.day_type.to_numpy(), 1, self.n_daytypes, hp
        )
        day_of = np.repeat(np.arange(len(cal)), hp)  # which calendar day each hour belongs to
        return pd.DataFrame(
            {
                'hour_index': np.arange(1, len(cal) * hp + 1),
                'datetime': pd.to_datetime(cal.date.to_numpy()[day_of])
                + pd.to_timedelta(hours, unit='h'),
                'month': cal.month.to_numpy()[day_of],
                'day': pd.to_datetime(cal.date).dt.day.to_numpy()[day_of],
                'hour_of_day': hours + 1,
                'day_type': cal.day_type.to_numpy()[day_of],
                'point_index': first_point[day_of] + hours,
            }
        )

    def expand_8760(
        self, prof: dict[str, Any], column: str = 'load', xwalk: pd.DataFrame | None = None
    ) -> pd.DataFrame:
        """Return hourly values (MW) for every hour of the calendar.

        ``value[h] = profile[point_index[h]]`` through the crosswalk.  The rows carry the dates of
        the CALENDAR (its year), whatever projection year the profile is for: a projected year
        repeats the calendar's order of days.

        Parameters
        ----------
        prof : dict
            A profile from :meth:`basic_profile` or :meth:`delta_profile`.
        column : str, default 'load'
            Which array of the profile to expand.
        xwalk : pd.DataFrame, optional
            The result of :meth:`crosswalk`, to avoid rebuilding the map for every region.

        Returns
        -------
        pd.DataFrame
            8760 rows (8784 if the calendar has 366 days): the crosswalk without ``point_index``
            plus the column ``<column>_mw``.
        """
        xw = xwalk if xwalk is not None else self.crosswalk()
        out = xw.drop(columns='point_index').copy()
        out[f'{column}_mw'] = np.asarray(prof[column])[xw.point_index.to_numpy()]
        return out


# --------------------------------------------------------------------------- #
# input templates
# --------------------------------------------------------------------------- #
def make_template(model: NemsLoad, years: list[int] | tuple[int, ...] | range) -> pd.DataFrame:
    """Build a blank annual-loads table: one row per year x region x (sector, end use).

    Parameters
    ----------
    model : NemsLoad
        The model whose structure tables define the rows.
    years : iterable of int
        The years to list.

    Returns
    -------
    pd.DataFrame
        Columns ``year, region, region_name, sector, sector_name, euse, euse_name, shape,
        shape_status`` and an empty ``mwh`` to fill in.
    """
    rows = []
    for y in years:
        for r, rn in model.regions.items():
            for s_, e_ in model.end_uses:
                shp = model.shape_name(r, s_, e_)
                rows.append(
                    (
                        y,
                        r,
                        rn,
                        s_,
                        model.sector_name.get(s_, ''),
                        e_,
                        model.eu_name.get((s_, e_), ''),
                        shp,
                        'none (hydrogen: even profile)'
                        if (s_, e_) == H2_KEY
                        else ('ok' if shp in model.dist_eu else 'MISSING SHAPE'),
                        np.nan,
                    )
                )
    return pd.DataFrame(
        rows,
        columns=[
            'year',
            'region',
            'region_name',
            'sector',
            'sector_name',
            'euse',
            'euse_name',
            'shape',
            'shape_status',
            'mwh',
        ],
    )
