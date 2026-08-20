"""Result extraction and reporting for the C-NGMM natural gas model.

Split out of ``ng_model.py``: everything here reads a *solved* :class:`NGModel`
and shapes it into tables, nothing here builds or solves the model.

The ``_extract_*`` helpers each return a tidy ``DataFrame`` for one result
dimension (production, pipeline flows, prices, storage, regional balance);
:func:`report` prints a summary of all five and optionally writes them as CSVs.

Equation references of the form "NGMM Eq 7" cite EIA's *Natural Gas Market Module
of NEMS: Model Documentation 2025*, not this code.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from pyomo.environ import value

from src.models.natural_gas.data import load_region_data

if TYPE_CHECKING:
    from src.models.natural_gas.ng_model import NGModel

logger = logging.getLogger(__name__)


def _extract_production(m: NGModel) -> pd.DataFrame:
    """Report per-step volume and the step's PBASE marginal price (NGMM Eq 1).

    ``cost_per_mmbtu`` is the midpoint price of the step, the average of PBASE_k and
    PBASE_{k+1}, rather than a single flat cost, because the step spans a price range.

    The ``supply_source`` column is not purely a step label: alongside ``step1``..``step5``
    it carries ``lng_import`` (backstop imports) and ``qmin_committed`` (the QMIN production
    floor, NGMM Eq 8). Those two are supply reaching the region without coming off an elastic
    step, which is why the column is named for the source rather than for the step.
    """
    rows = []
    for r in m.region_analyze:
        for t in m.steps:
            k_seg = int(t.replace('step', ''))
            for y in m.year:
                pb_k = value(m.PBASE[r, k_seg, y])
                pb_k1 = value(m.PBASE[r, k_seg + 1, y])
                rows.append(
                    {
                        'region': r,
                        'supply_source': t,
                        'year': y,
                        'production_bcf': value(m.sstep[r, t, y]),
                        'cost_per_mmbtu': 0.5 * (pb_k + pb_k1),
                    }
                )
        for y in m.year:
            lng = value(m.lng_import[r, y])
            if lng > 0.01:
                rows.append(
                    {
                        'region': r,
                        'supply_source': 'lng_import',
                        'year': y,
                        'production_bcf': lng,
                        'cost_per_mmbtu': value(m.lng_cost[r]),
                    }
                )
        # QMIN floor (committed production, NGMM Eq 8)
        for y in m.year:
            qmin = value(m.QMIN[r, y])
            if qmin > 0.01:
                rows.append(
                    {
                        'region': r,
                        'supply_source': 'qmin_committed',
                        'year': y,
                        'production_bcf': qmin,
                        'cost_per_mmbtu': value(m.PBASE[r, 1, y]),
                    }
                )
    return pd.DataFrame(rows)


def _extract_flows(m: NGModel) -> pd.DataFrame:
    """Extract per-arc pipeline flows and effective tariffs.

    Pipe_flow is an Expression (sum of tariff-curve segments).  We also report the effective average
    tariff = transport cost on this arc / volume, which captures the
    hurdle-rate behaviour of the QP tariff curve (NGMM Eq 6).
    """
    rows = []
    for o, d in m.arcs:
        for y in m.year:
            flow = value(m.pipe_flow[o, d, y])
            if flow > 0.1:
                # Effective average tariff = ∫ tariff curve / volume
                # = (Σ_k PTAR_k·tar_k + 0.5·tar_k²·slope_k) / flow
                num = 0.0
                for k_seg in m.tariff_segs:
                    q = value(m.tar_step[o, d, k_seg, y])
                    if q < 1e-9:
                        continue
                    pt_k = value(m.PTAR[o, d, k_seg, y])
                    pt_k1 = value(m.PTAR[o, d, k_seg + 1, y])
                    qt_k = value(m.QTAR[o, d, k_seg, y])
                    qt_k1 = value(m.QTAR[o, d, k_seg + 1, y])
                    width = qt_k1 - qt_k
                    if width < 1e-9:
                        continue
                    slope = (pt_k1 - pt_k) / width
                    num += pt_k * q + 0.5 * q * q * slope
                eff_tariff = num / flow if flow > 1e-6 else value(m.pipe_tariff[o, d])
                rows.append(
                    {
                        'origin': o,
                        'destination': d,
                        'year': y,
                        'flow_bcf': flow,
                        'capacity_bcf': value(m.pipe_capacity[o, d]),
                        'utilization': flow / value(m.pipe_capacity[o, d]),
                        'tariff_per_mmbtu': value(m.pipe_tariff[o, d]),
                        'effective_tariff_per_mmbtu': eff_tariff,
                    }
                )
    return pd.DataFrame(rows)


def _extract_prices(m: NGModel) -> pd.DataFrame:
    rows = []
    for r in m.region_analyze:
        for y in m.year:
            try:
                dual_val = m.dual[m.demand_balance[r, y]]
                price = abs(dual_val) / value(m.bcf_to_mmbtu)
            except KeyError:
                price = float('nan')
            rows.append({'region': r, 'year': y, 'gas_price_per_mmbtu': price})
    return pd.DataFrame(rows)


def _extract_storage(m: NGModel) -> pd.DataFrame:
    rows = []
    for r in m.region_analyze:
        for y in m.year:
            rows.append(
                {
                    'region': r,
                    'year': y,
                    'injection_bcf': value(m.stor_inject[r, y]),
                    'withdrawal_bcf': value(m.stor_withdraw[r, y]),
                    'working_cap_bcf': value(m.storage_working_cap[r]),
                }
            )
    return pd.DataFrame(rows)


def _extract_balance(m: NGModel) -> pd.DataFrame:
    """Regional supply/demand balance table (includes LNG export demand)."""
    # Precompute arc adjacency dicts to avoid O(arcs×regions)
    # scan inside the inner loop.  Each region scan was iterating all 26 arcs twice.
    # Original in-loop scan kept as comments inside the loop below.
    _inc: dict = defaultdict(list)  # region → [(origin, dest), ...]  arcs arriving at region
    _out: dict = defaultdict(list)  # region → [(origin, dest), ...]  arcs leaving region
    for o, d in m.arcs:
        _inc[d].append((o, d))
        _out[o].append((o, d))

    rows = []
    for r in m.region_analyze:
        for y in m.year:
            # Use the production_total Expression (QMIN floor + step sum, NGMM Eq 8) rather
            # than summing the input cost tiers, which are not a model quantity.
            prod_total = value(m.production_total[r, y])
            lng_imp = value(m.lng_import[r, y])
            # Use precomputed adjacency instead of full arc scan
            pipe_in = sum(value(m.pipe_flow[o, d, y]) for (o, d) in _inc[r])
            pipe_out = sum(value(m.pipe_flow[o, d, y]) for (o, d) in _out[r])
            stor_wd = value(m.stor_withdraw[r, y])
            stor_inj = value(m.stor_inject[r, y])
            total_dem = sum(value(m.demand[r, s, y]) for s in m.sectors)
            lng_exp = value(m.lng_export_demand[r, y])
            canada_sup = value(m.canada_supply[r, y])
            rows.append(
                {
                    'region': r,
                    'year': y,
                    'production_bcf': prod_total,
                    'canada_import_bcf': canada_sup,
                    'lng_import_bcf': lng_imp,
                    'pipe_inflow_bcf': pipe_in,
                    'pipe_outflow_bcf': pipe_out,
                    'stor_withdrawal': stor_wd,
                    'stor_injection': stor_inj,
                    'total_sector_demand_bcf': total_dem,
                    'lng_export_bcf': lng_exp,
                    'net_supply_bcf': prod_total
                    + canada_sup
                    + lng_imp
                    + pipe_in
                    - pipe_out
                    + stor_wd
                    - stor_inj,
                }
            )
    return pd.DataFrame(rows)


def report(m: NGModel, output_dir: Path | None = None) -> None:
    """Print summary and optionally write CSVs.

    Parameters
    ----------
    m : NGModel
        Solved model (solve() must have been called first).
    output_dir : Path | None
        If provided, write CSV files here.
    """
    # gather results objects
    results_production = _extract_production(m)
    results_flows = _extract_flows(m)
    results_prices = _extract_prices(m)
    results_storage = _extract_storage(m)
    results_balance = _extract_balance(m)

    sep = '-' * 70

    print(f'\n{sep}')
    print(' C-NGMM Results Summary')
    print(sep)

    # Aggregate production by year
    prod_yr = results_production.groupby('year')['production_bcf'].sum().reset_index()
    print('\n  Total US Production + LNG Imports [BCF/year]:')
    for _, row in prod_yr.iterrows():
        print(f'    {int(row["year"])}: {row["production_bcf"]:,.0f} BCF')

    # Prices by region and year
    print('\n  Regional Wellhead/Citygate Gas Price [$/MMBtu]:')
    pivot = results_prices.pivot(index='region', columns='year', values='gas_price_per_mmbtu')
    labels = load_region_data()['region_labels']
    pivot.index = [labels.get(r, r) for r in pivot.index]
    print(pivot.round(2).to_string())

    # LNG export demand by year (new)
    if 'lng_export_bcf' in results_balance.columns:
        lng_exp_yr = results_balance.groupby('year')['lng_export_bcf'].sum()
        print('\n  US LNG Export Demand [BCF/year]:')
        for yr, bcf in lng_exp_yr.items():
            print(f'    {int(yr)}: {bcf:,.0f} BCF  ({bcf / 365:.1f} BCF/day)')

    # Most congested pipelines
    if not results_flows.empty:
        top_pipes = results_flows.sort_values('utilization', ascending=False).head(5)[
            ['origin', 'destination', 'year', 'flow_bcf', 'utilization']
        ]
        print('\n  Top-5 Most Congested Pipeline Corridors:')
        print(top_pipes.to_string(index=False))

    print(f'\n{sep}\n')

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results_production.to_csv(output_dir / 'ng_production.csv', index=False)
        results_flows.to_csv(output_dir / 'ng_pipeline_flows.csv', index=False)
        results_prices.to_csv(output_dir / 'ng_prices.csv', index=False)
        results_storage.to_csv(output_dir / 'ng_storage.csv', index=False)
        results_balance.to_csv(output_dir / 'ng_regional_balance.csv', index=False)
        print(f'  Output CSVs written to: {output_dir}')
