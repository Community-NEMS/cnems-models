"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

One run of the load-profile model: tables + annual loads + calendar -> 864-point profiles (+ 8760
hours).

LoadSequencer (sequencer.py) calls it with the settings of the [load_config] section
(LoadConfig).  It can also be called directly; every setting is a keyword argument:

    run('input/load/tables', census_division_loads='loads.csv', year=2035, base_year=2023)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .calendars import (
    DAY_TYPE_NAME,
    display_path,
    get_calendar,
    load_calendar,
    read_holidays,
    read_peak_days,
)
from .data_validation import (
    check_calendar,
    check_census_loads,
    check_energy_balance,
    check_loads,
    check_no_negative_load,
    check_tables,
    check_ulossadj,
    compare_calendar_to_nems,
    format_report,
    reconcile_census,
)
from .demand_overrides import SECTOR_NAMES, apply_overrides, make_override_template, sector_code
from .hydrogen import annual_by_region, check_hydrogen_rows, even_profiles_from_nems
from .load_model import (
    H2_KEY,
    N_CENSUS,
    NemsLoad,
    load_tables,
    load_ulossadj,
    make_template,
)
from .nems_aeo_reader import (
    NEMS_RELEASE,
    ONSITE_CATEGORIES,
    extract_loads,
    extract_onsite_generation,
    scenario_name,
)

logger = logging.getLogger(__name__)


def run(
    tables_dir: str | Path,
    emm_region_loads: str | Path | None = None,
    year: int | list[int] | None = None,
    *,
    first_year: int | None = None,
    last_year: int | None = None,
    census_division_loads: str | Path | None = None,
    restart_file: str | Path | None = None,
    restart_values: str = 'mwh',
    scenario: str | None = None,
    demand_overrides: dict[str, str] | None = None,
    method: str = 'delta',
    base_year: int | None = None,
    base_freeze_year: int | None = None,
    ulossadj_file: str | Path | None = None,
    loss_adj: float | None = None,
    output_dir: str | Path = 'output',
    output_folder: str | Path | None = None,
    hourly8760: bool = True,
    save_profiles_864: bool = False,
    validate: bool = True,
    renormalize: bool = True,
    calendar_year: int | None = None,
    calendar_csv: str | Path | None = None,
    load_dir: str | Path | None = None,
    holidays: str | None = None,
    peak_days_csv: str | Path | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Build the profiles of all 25 regions for the projection years and write the results.

    Parameters
    ----------
    tables_dir : str or Path
        Folder with the shape / structure tables, one ``<table>.csv`` each (INPUT 1).
    emm_region_loads : str or Path, optional
        CSV ``year,region,sector,euse,mwh``: annual load by region (INPUT 2).
    year : int or list of int, optional
        Projection year to build, or a list of years: ``year = [2030, 2035, 2040]``.
    first_year, last_year : int, optional
        Alternative to ``year``: every year from ``first_year`` to ``last_year``.
    census_division_loads : str or Path, optional
        Alternative to ``emm_region_loads``: CSV ``year,sector,euse,census,mwh`` (INPUT 2).
    restart_file : str or Path, optional
        Alternative to ``emm_region_loads`` / ``census_division_loads``: a NEMS ``restart.rlx``;
        the census loads are read from it.  Next to a user file it supplies only the on-site
        generation.
    restart_values : {'mwh', 'mwh_nems'}
        ``"mwh"`` = benchmarked to sales, ``"mwh_nems"`` = as NEMS has them.
    scenario : str, optional
        Name of the NEMS scenario (for example cb2026); the results go to
        ``<output_dir>/<scenario>/``.  Default: the name stored in the restart file, else the name
        of the input file.
    demand_overrides : dict of str to str, optional
        ``{sector: csv}`` replacing whole sectors of the NEMS demand with your own annual forecast
        (sectors: residential, commercial, industrial, transport); needs ``restart_file`` or
        ``census_division_loads``.
    method : {'delta', 'basic', 'both'}
        The approach, see ``load_model``.
    base_year : int, optional
        Delta approach: one fixed base year of the loads (INPUT 3) ...
    base_freeze_year : int, optional
        ... or the NEMS rule: base year = ``min(year, base_freeze_year)``; NEMS uses 2026.  Give
        one of the two.
    ulossadj_file : str or Path, optional
        CSV ``year,ulossadj``: the value of the year multiplies the T&D loss factor (INPUT 5) ...
    loss_adj : float, optional
        ... or one number for the whole run.  Neither given: 1.0 (no adjustment).
    output_dir : str or Path
        Root folder of the results; with several years each year gets a sub-folder of the scenario
        folder.
    output_folder : str or Path, optional
        C-NEMS: the finished results folder (the project's ``<output_path>/<scenario_name>``).
        When given, the results are written straight into it and ``output_dir`` and the
        ``<scenario>`` sub-folder are not used.
    hourly8760 : bool
        Also write ``hourly_load_mwh`` (long: region_id, region, year, hour 1..8760, sales_mwh,
        load_mwh) and
        the 864 -> 8760 crosswalk; needs a calendar of 365 days.
    save_profiles_864 : bool
        Also write the 864-point profile of every region (``profile864_<nn>_<region>.csv``, with
        the sector curves) into ``<year>/[<method>/]``.  Off by default: these files are large and
        ``summary_by_year_region.csv`` is always written.
    validate : bool
        Check the tables, loads and calendar first (``data_validation.py``).
    renormalize : bool
        Divide every shape by its day-weighted sum so that each sums to exactly 1.
    calendar_year : int, optional
        None = default (2023 from the load file, or 2018 with a note); 2018 = NEMS; or another
        year.
    calendar_csv : str or Path, optional
        Your own calendar: CSV ``date,month,day_type`` (1/2/3 or weekday/weekend/peak), one row per
        day (INPUT 6).
    load_dir : str or Path, optional
        Folder with the hourly load file ``base_load_<year>.csv`` (or the file itself, or a name
        with ``{year}``).
    holidays : str, optional
        For another year: ``"federal"`` or a CSV ``month,day`` (or ``date``).
    peak_days_csv : str or Path, optional
        For another year: CSV ``month,day`` (12 rows).
    verbose : bool
        Log notes about the run at INFO level.

    Returns
    -------
    dict
        ``model`` (the NemsLoad), ``calendar`` (DataFrame), ``profiles``
        (``{year: {method: {region: profile}}}``), ``output_dir`` (str) and ``scenario`` (str).

    Raises
    ------
    ValueError
        If settings conflict or an input is unusable; the message names the problem.  Nothing is
        filled in silently.
    """

    def say(*parts: Any) -> None:
        if verbose:
            logger.info(' '.join(str(p) for p in parts))

    years = _years(year, first_year, last_year)
    if method not in ('basic', 'delta', 'both'):
        raise ValueError('method must be "basic", "delta" or "both"')
    if restart_values not in ('mwh', 'mwh_nems'):
        raise ValueError(
            'restart_values must be "mwh" (benchmarked to sales) or "mwh_nems" (as NEMS has them)'
        )
    if bool(emm_region_loads) + bool(census_division_loads) > 1 or not (
        emm_region_loads or census_division_loads or restart_file
    ):
        raise ValueError(
            'give exactly one source of annual loads: emm_region_loads, census_division_loads '
            'or a NEMS restart file'
        )
    # a restart file next to a user load file supplies only the on-site generation
    onsite_restart = None
    if restart_file and (emm_region_loads or census_division_loads):
        onsite_restart, restart_file = restart_file, None
    if demand_overrides and emm_region_loads:
        raise ValueError(
            'demand_overrides replace census-division sectors: use restart_file or '
            'census_division_loads, not emm_region_loads'
        )
    methods = ['basic', 'delta'] if method == 'both' else [method]
    if 'delta' in methods and (base_year is None) == (base_freeze_year is None):
        raise ValueError('the delta approach needs exactly one of base_year and base_freeze_year')
    base_years: set[int] = set()
    if 'delta' in methods:
        if base_year is not None:
            base_years = {base_year}
        elif base_freeze_year is not None:
            base_years = {min(y, base_freeze_year) for y in years}
    # scenario folder: <output_dir>/<scenario>/
    if not scenario:
        if restart_file:
            scenario = scenario_name(restart_file)
        if not scenario:
            scenario = Path(str(restart_file or census_division_loads or emm_region_loads)).stem
            say(
                f"note: no scenario name given or stored in the input; using '{scenario}' "
                '(set scenario = ... in the config)'
            )
    scenario = re.sub(r'[^A-Za-z0-9._-]+', '_', str(scenario).strip()) or 'scenario'
    results = Path(output_folder) if output_folder else Path(output_dir) / scenario
    output_dir = str(results)
    say(f'scenario {scenario}: results go to {display_path(output_dir)}')
    if restart_file:
        results.mkdir(parents=True, exist_ok=True)
        need = sorted(set(years) | base_years)
        tab, rep = extract_loads(restart_file, need)
        tab = tab.drop(columns=['mwh' if restart_values == 'mwh_nems' else 'mwh_nems'])
        tab = tab.rename(columns={'mwh_nems': 'mwh'})
        census_division_loads = results / f'census_loads_from_NEMS_{NEMS_RELEASE}.csv'
        tab.to_csv(census_division_loads, index=False)
        say(
            f'restart file read for {need[0]}-{need[-1]}: sector totals vs QBLK, '
            'max relative difference '
            + ', '.join(
                f's{int(r.sector)} {r.max_rel_diff:.1e}'
                for r in rep.groupby('sector', as_index=False).max_rel_diff.max().itertuples()
            )
        )
    if ulossadj_file and loss_adj is not None:
        raise ValueError('give ulossadj_file or loss_adj, not both')

    # INPUT 1: tables
    tables = load_tables(tables_dir)
    if validate:
        warnings = check_tables(tables)  # raises if an input table is unusable
        if warnings:
            say(format_report(warnings))

    # INPUT 6: calendar (built now, nothing is stored)
    if calendar_csv:
        cal, notes = (
            load_calendar(calendar_csv),
            [f'calendar read from {display_path(calendar_csv)}'],
        )
    else:
        cal, notes = get_calendar(
            calendar_year, load_dir, read_holidays(holidays), read_peak_days(peak_days_csv)
        )
    for n in notes:
        say(n)
    if hourly8760 and len(cal) != 365:
        raise ValueError(
            f'hourly8760 writes hours 1..8760, but the calendar has {len(cal)} days; use a '
            'calendar year with 365 days (for example calendar_year = 2023), or set '
            'hourly8760 = false'
        )
    if validate:
        for w in check_calendar(cal, tables):  # raises if the calendar is unusable
            logger.warning('%s: %s', w[1], w[2])
        say(
            'calendar day counts vs V_SYS_LOAD_WEIGHTS:\n'
            + compare_calendar_to_nems(cal, tables).to_string()
        )
    model = NemsLoad(tables, cal, renormalize=renormalize)
    results.mkdir(parents=True, exist_ok=True)
    write_onsite_generation(
        model, onsite_restart or restart_file, years, results, demand_overrides, bool(restart_file)
    )
    cal_year = int(pd.to_datetime(cal['date']).dt.year.iloc[0])
    cal.assign(day_type=cal.day_type.map(DAY_TYPE_NAME)).to_csv(
        results / f'calendar_used_{cal_year}.csv', index=False
    )

    # INPUT 5: T&D loss adjustment
    if ulossadj_file:
        ulossadj = load_ulossadj(ulossadj_file)
        if validate:
            check_ulossadj(ulossadj, years)
        adj = ulossadj
        say(f'ULOSSADJ for {years[0]}..{years[-1]} read from {display_path(ulossadj_file)}')
    elif loss_adj is not None:
        adj = float(loss_adj)
    else:
        adj = 1.0
        say(
            'note: ULOSSADJ = 1.0 (no adjustment); NEMS uses a year schedule, give ulossadj_file '
            'to apply it'
        )

    # INPUT 2: annual loads
    census = pd.DataFrame()
    if census_division_loads:
        census = pd.read_csv(census_division_loads)
        census, sources, compare = apply_overrides(
            census,
            demand_overrides,
            sorted(set(years) | base_years),
            nems_label=f'NEMS {NEMS_RELEASE}'
            if restart_file
            else f'file {Path(census_division_loads).name}',
        )
        sources.to_csv(results / 'demand_sources.csv', index=False)
        compare.to_csv(results / 'demand_comparison.csv', index=False)
        census.to_csv(results / 'census_loads_used.csv', index=False)
        for s in sources.itertuples():
            say(f'demand source, {s.sector}: {s.source} ({s.first_year}-{s.last_year})')
        if demand_overrides:
            replaced = {SECTOR_NAMES[sector_code(k)] for k in demand_overrides}
            show = compare[compare.sector.isin(replaced)]
            say(
                'replaced sectors, used / NEMS annual total:\n'
                + show.pivot(index='year', columns='sector', values='used_over_nems')
                .round(3)
                .to_string()
            )
        if validate:
            for w in check_census_loads(census, tables):
                logger.warning('%s: %s', w[1], w[2])
        regional = model.census_to_region(census)
        if validate:
            table, issues = reconcile_census(census, regional, tables)
            say(
                'census -> EMM regions, input vs output (MWh):\n'
                + table.to_string(float_format=lambda x: f'{x:,.4f}')
            )
            if issues:
                raise ValueError(issues[0][2])
    else:
        assert emm_region_loads is not None  # the source was checked at the start
        regional = pd.read_csv(emm_region_loads)
        if validate:
            for w in check_loads(regional, tables):
                logger.warning('%s: %s', w[1], w[2])
    regional.to_csv(results / 'region_loads_used.csv', index=False)
    absent = sorted((set(years) | base_years) - set(regional.year.astype(int)))
    if absent:
        raise ValueError(
            f'the annual loads have no rows for the years {absent} '
            f'(projection years {years[0]}..{years[-1]}'
            + (f', base years {sorted(base_years)}' if base_years else '')
            + ')'
        )

    # hydrogen: outside the decomposition, added to the profile as an hourly demand
    h2_profiles = None
    industry_replaced = any(sector_code(k) == H2_KEY[0] for k in demand_overrides or {})
    if restart_file and not industry_replaced:  # NEMS loads: the hydrogen module, by region
        h2_profiles = even_profiles_from_nems(model, restart_file, years)
        h2_year = annual_by_region(h2_profiles, model).groupby('year').mwh.sum()
        h2_note = 'NEMS hydrogen module (qelhm_hr), spread evenly over the year'
    else:  # the end use 4 rows of the loads, spread evenly over the year; they must be there
        if census_division_loads:
            check_hydrogen_rows(
                census, years, 'census', range(1, N_CENSUS + 1), Path(census_division_loads).name
            )
        else:
            check_hydrogen_rows(
                regional, years, 'region', model.regions, Path(str(emm_region_loads)).name
            )
        rows = regional[(regional.sector == H2_KEY[0]) & (regional.euse == H2_KEY[1])]
        h2_year = rows[rows.year.isin(years)].groupby('year').mwh.sum()
        h2_note = 'the end use 4 rows of the annual loads, spread evenly over the year'
    say(
        f'hydrogen (industrial end use 4, outside the load decomposition): {h2_note}; annual MWh '
        + ', '.join(f'{y}: {h2_year.get(y, 0.0):,.0f}' for y in years)
    )

    # profiles
    xw = model.crosswalk() if hourly8760 else None  # same map for every region, method and year
    if xw is not None:
        xw.to_csv(results / 'crosswalk_864_to_8760.csv', index=False)
    many = len(years) > 1
    result, all_summaries, hourly = {}, [], {}
    for y in years:
        for meth in methods:
            profs = model.build_all_regions(
                regional,
                y,
                meth,
                base_year,
                adj,
                h2_hourly_mw=h2_profiles[y] if h2_profiles else None,
                base_freeze_year=base_freeze_year,
            )
            result.setdefault(y, {})[meth] = profs
            out = results
            if many:
                out = out / str(y)
            if len(methods) > 1:
                out = out / meth
            if save_profiles_864:
                out.mkdir(parents=True, exist_ok=True)
            summary, rows = [], []
            for r, p in profs.items():
                name = model.regions[r]
                if save_profiles_864:
                    model.profile_frame(p).to_csv(
                        out / f'profile{model.n_load_points}_{r:02d}_{name}.csv', index=False
                    )
                if xw is not None:  # long format; the profile is in MW = MWh in each clock hour
                    rows.append(
                        pd.DataFrame(
                            {
                                'region_id': r,
                                'region': name,
                                'year': y,
                                'hour': xw.hour_index.to_numpy(),
                                'sales_mwh': np.asarray(p['sales'])[xw.point_index.to_numpy()],
                                'load_mwh': np.asarray(p['load'])[xw.point_index.to_numpy()],
                            }
                        )
                    )
                summary.append(
                    (
                        y,
                        r,
                        name,
                        meth,
                        p['system_load'],
                        p.get('slr', float('nan')),
                        p['td'],
                        p['annual_sales_mwh'],
                        p['annual_sales_mwh'] * p['td'],
                        float(p['load'].max()),
                    )
                )
            check_energy_balance(profs, model.regions, y, meth, rows if xw is not None else None)
            table = pd.DataFrame(
                summary,
                columns=[
                    'year',
                    'region_id',
                    'region',
                    'method',
                    'system_load_mwh',
                    'slr',
                    'td',
                    'annual_sales_mwh',
                    'annual_load_mwh',
                    'peak_load_mw',
                ],
            )
            all_summaries.append(table)
            if hourly8760:
                hourly.setdefault(meth, []).extend(rows)
            if save_profiles_864:
                say('wrote', display_path(out))
    # one file per method, all years: region_id, region, year, hour, sales_mwh, load_mwh
    for meth, frames in hourly.items():
        out = results / meth if len(methods) > 1 else results
        long = pd.concat(frames, ignore_index=True).sort_values(
            ['year', 'region_id', 'hour'], kind='stable'
        )
        check_no_negative_load(long, out, meth)
        out.mkdir(parents=True, exist_ok=True)
        path = out / 'hourly_load_mwh.csv'
        long.to_csv(path, index=False, float_format='%.6f')
        say('wrote', display_path(path), f'({len(long):,} rows)')
    summary_path = results / 'summary_by_year_region.csv'
    pd.concat(all_summaries).to_csv(summary_path, index=False)
    say('wrote', display_path(summary_path))
    return {
        'model': model,
        'calendar': cal,
        'profiles': result,
        'output_dir': output_dir,
        'scenario': scenario,
    }


def onsite_by_region(model: NemsLoad, gen: pd.DataFrame) -> pd.DataFrame:
    """Map on-site generation from census divisions to the 25 EMM regions, one row per year, region.

    Uses the same census -> EMM mapping as the loads, so Alaska and Hawaii drop out the same way.

    Parameters
    ----------
    model : NemsLoad
        The load model (supplies the mapping and region names).
    gen : pd.DataFrame
        Output of :func:`extract_onsite_generation`.

    Returns
    -------
    pd.DataFrame
        Columns ``year, region_id, region`` and ``<category>_own_use_mwh`` /
        ``<category>_sold_mwh`` for each of residential, commercial, industrial, refinery and
        transport.
    """
    names = {i: c for i, (c, _, _) in enumerate(ONSITE_CATEGORIES, 1)}
    parts = []
    for part in ('own_use', 'sold'):
        table = gen.assign(euse=gen.category.map({c: i for i, c in names.items()}))
        table = table.assign(mwh=table[f'{part}_mwh'])[['year', 'sector', 'euse', 'census', 'mwh']]
        mapped = model.census_to_region(table)
        mapped['column'] = mapped.euse.map(names) + f'_{part}_mwh'
        parts.append(mapped[['year', 'region', 'column', 'mwh']])
    wide = pd.concat(parts).pivot(index=['year', 'region'], columns='column', values='mwh')
    order = [f'{c}_{p}_mwh' for c, _, _ in ONSITE_CATEGORIES for p in ('own_use', 'sold')]
    wide = wide[order].rename_axis(columns=None).reset_index()
    wide = wide.rename(columns={'region': 'region_id'})
    wide.insert(2, 'region', wide.region_id.map(model.regions))
    return wide.sort_values(['year', 'region_id']).reset_index(drop=True)


def write_onsite_generation(
    model: NemsLoad,
    restart_file: str | Path | None,
    years: list[int],
    results: Path,
    demand_overrides: dict[str, str] | None,
    loads_from_nems: bool,
) -> None:
    """Write ``onsite_generation_by_year_region.csv`` from a NEMS restart file, if there is one.

    Parameters
    ----------
    model : NemsLoad
        The load model.
    restart_file : str or Path or None
        The NEMS restart file; ``None`` (loads from a user file only) writes nothing.
    years : list of int
        Projection years.
    results : Path
        Results folder.
    demand_overrides : dict of str to str, optional
        Replaced sectors; they get a warning and are named in ``annual_loads_source``.
    loads_from_nems : bool
        True if the annual loads also come from the restart file.
    """
    if restart_file is None:
        logger.info('no NEMS restart file: the on-site generation file is not written')
        return
    gen = extract_onsite_generation(restart_file, years)
    resid = gen[(gen.category == 'residential') & (gen.year == 2025)]
    if len(resid) and not 30_000e3 <= resid.own_use_mwh.sum() <= 80_000e3:
        logger.warning(
            'residential on-site generation used on site in 2025 is %s GWh, outside the usual '
            '30,000-80,000 GWh; check the restart file',
            f'{resid.own_use_mwh.sum() / 1e3:,.0f}',
        )
    out = onsite_by_region(model, gen)
    source = f'NEMS {NEMS_RELEASE}' if loads_from_nems else 'user file'
    replaced = sorted(SECTOR_NAMES[sector_code(k)] for k in (demand_overrides or {}))
    if replaced:
        source += ' with replaced ' + ', '.join(replaced)
    if source != f'NEMS {NEMS_RELEASE}':
        logger.warning(
            'on-site generation comes from the NEMS restart file but the demand is: %s', source
        )
    out['annual_loads_source'] = source
    path = results / 'onsite_generation_by_year_region.csv'
    out.to_csv(path, index=False, float_format='%.6f')
    logger.info('wrote %s (%d rows)', display_path(path), len(out))


def _years(year: int | Iterable[int] | None, first_year: int | None, last_year: int | None) -> list:
    """Return the projection years as a sorted list.

    Parameters
    ----------
    year : int or iterable of int, optional
        ``year = 2035`` or ``year = [2030, 2035]``.
    first_year, last_year : int, optional
        Alternative to ``year``: every year from the first to the last.

    Returns
    -------
    list of int
        The years, ascending.

    Raises
    ------
    ValueError
        If the years are missing, given in both ways, or not distinct whole numbers.
    """
    if year is not None and (first_year is not None or last_year is not None):
        raise ValueError('give year, or first_year and last_year, not both')
    if year is not None:
        years = [year] if isinstance(year, int) else list(year)
    elif first_year is not None and last_year is not None:
        if last_year < first_year:
            raise ValueError(f'last_year {last_year} is before first_year {first_year}')
        years = list(range(first_year, last_year + 1))
    else:
        raise ValueError(
            'projection years are needed: year = 2035, year = [2030, 2035], or first_year and '
            'last_year'
        )
    if (
        not years
        or any(isinstance(y, bool) or not isinstance(y, int) for y in years)
        or len(set(years)) != len(years)
    ):
        raise ValueError(f'the projection years must be distinct whole numbers, got {years}')
    return sorted(years)


def write_template(
    tables_dir: str | Path, path: str | Path, years: Iterable[int] = (2024, 2035)
) -> None:
    """Write a blank annual-loads template to fill in.

    Parameters
    ----------
    tables_dir : str or Path
        Folder with the shape tables.
    path : str or Path
        Where to write the template: one row per year x region x end use, ``mwh`` empty.
    years : iterable of int
        The years to list.
    """
    tables = load_tables(tables_dir)
    model = NemsLoad(tables, get_calendar(2018)[0])
    make_template(model, list(years)).to_csv(path, index=False)


def write_override_template(
    sector: str,
    path: str | Path,
    years: Iterable[int],
    *,
    restart_file: str | Path | None = None,
    census_division_loads: str | Path | None = None,
    tables_dir: str | Path | None = None,
) -> None:
    """Write the file you have to fill in to replace one sector of the NEMS demand.

    The rows (end uses, census divisions) are taken from the NEMS data, so the file always has the
    layout ``run()`` expects; the ``mwh`` column is blank.

    Parameters
    ----------
    sector : {'residential', 'commercial', 'industrial', 'transport'}
        The sector to replace.
    path : str or Path
        Where to write the file.
    years : iterable of int
        The years to forecast, for example ``range(2023, 2051)``; include the base year.
    restart_file : str or Path, optional
        A NEMS restart file with the end-use list ...
    census_division_loads : str or Path, optional
        ... or a census load table.  Give exactly one of the two.
    tables_dir : str or Path, optional
        The shape-table folder; if given, the end-use names are added.

    Raises
    ------
    ValueError
        If not exactly one of ``restart_file`` and ``census_division_loads`` is given.
    """
    if bool(restart_file) == bool(census_division_loads):
        raise ValueError(
            'give exactly one of restart_file and census_division_loads (a source with the NEMS '
            'end-use list)'
        )
    if restart_file:
        nems, _ = extract_loads(restart_file, list(years), check=False)
    else:
        assert census_division_loads is not None  # exactly one source, checked above
        nems = pd.read_csv(census_division_loads)
    make_override_template(nems, sector, list(years), path, tables_dir)
