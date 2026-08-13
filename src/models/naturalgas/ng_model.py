"""
C-NGMM: natural gas market model for the C-NEMS project
=======================================================
Natural gas market model aligned with EIA's Natural Gas Market Module (NGMM) for the
National Energy Modeling System (NEMS), AEO 2025 documentation.

Naming: C-NGMM is this model; NGMM is EIA's module in NEMS. References of the form
"NGMM Eq 7" cite the source documentation, not this code.

A QP that mirrors the NGMM mathematical formulation. The model still operates
on 9 EIA census divisions and annual time steps (the integrator wiring depends
on this), but the economic structure matches NGMM:

  * Quadratic objective: maximizes consumer + producer surplus minus transport
    cost (NGMM Eq 7).
  * Piecewise-linear elastic supply curve per region (NGMM Eq 1-5, Fig 3.4)
    with 5 segments built around an expected production point (Q0, P0) using
    step elasticities (ELAS, AEO footnote values 0.8/0.7/0.5/0.3/0.2)
    and segment volume adjustments (CRV).
  * Piecewise-linear LNG export demand curve (NGMM Eq 14, Fig 3.6): LNG export
    is a price-responsive *variable* on a downward-sloping demand curve anchored
    at the world LNG price.
  * Piecewise-linear pipeline tariff curve (NGMM Eq 6, Fig 3.5): tariff rises
    sharply approaching 100 % utilization, encoding the NGMM hurdle-rate
    behaviour without the separate capacity-expansion QP.
  * Pipeline fuel loss on hub-to-hub flow, distribution / intrastate / storage
    losses on the demand balance, plant-fuel and gathering charges (NGMM Eq
    10, 11 and the gathering term in Eq 7).

What is *not* implemented (Tier 2/3 items not in scope of this rewrite):
  * State-level hubs (NGMM uses 50 + 3 Texas hubs; this model keeps the
    9 census-division grouping because the unified/Gauss-Seidel integrators
    rely on it via load_ng_region_map).
  * Monthly time resolution (NGMM solves each month independently).
  * STEO benchmarking and the separate capacity-expansion QP run.
  * NA/AD supply-type separation (the model keeps a single supply curve per
    region; HSM updates that distinguish NA from AD are aggregated into Q0).

After solving, shadow prices on the regional demand-balance constraints
(self.demand_balance) serve as regional citygate gas prices, the GS integrator
extracts them via poll_gas_price().

Usage (standalone):
    python -m src.models.naturalgas.ng_model
    python -m src.models.naturalgas.ng_model --solver gurobi
    python -m src.models.naturalgas.ng_model --years 2025 2030 2035 2040 2045 2050

References:
    EIA Natural Gas Market Module of NEMS: Model Documentation 2025
        (NGMM_AEO2025.pdf alongside this file). Equation references in
        the code below cite this document.
    EIA Annual Energy Outlook 2025, Natural Gas Supply & Demand tables
    EIA Natural Gas Annual 2024, State & regional consumption tables
    EIA Underground Natural Gas Storage, Annual capacity data
"""

###############################################################################
# Setup
###############################################################################

from __future__ import annotations

import argparse
import logging
from collections import defaultdict, namedtuple
from pathlib import Path

import pandas as pd
from pyomo.environ import (
    ConcreteModel,
    Constraint,
    Expression,           # QP uses Expressions for derived production
    NonNegativeReals,
    Objective,
    Param,
    Set,
    SolverFactory,
    Suffix,
    Var,
    check_optimal_termination,
    maximize,             # Surplus is maximized (NGMM Eq 7)
    minimize,
    quicksum,             # Fast linear sums in QP construction
    value,
)

logger = logging.getLogger(__name__)

# Named index used when exchanging prices/quantities with other BlueSky models
GI = namedtuple('GI', ['region', 'year'])

# Load numerical parameters from CSV files
# via the data.py loader instead of hardcoded module-level dicts.
# Fallback constants are preserved inside data.py for offline / test use.
from src.models.naturalgas.data import load_all as _load_ng_data
_NG_DATA = _load_ng_data()

###############################################################################
# EIA NGMM Reference Data
# ---------------------------------------------------------------------------
# All quantities in BCF/year (billion cubic feet per year).
# All prices / costs in $/MMBtu.
# Conversion: 1 BCF ≈ 1.02 × 10^6 MMBtu  (used implicitly; costs reported
#   as $/MMBtu for interpretability and scaled internally in the objective).
#
# Data sources:
#   Production capacities: EIA AEO 2023 Natural Gas Supply Module
#   Demand: EIA Natural Gas Annual 2022, disaggregated to census divisions
#   Pipeline capacities: EIA Natural Gas Compendium of Interstate Pipelines
#   Storage: EIA Underground Natural Gas Storage (Form EIA-191)
###############################################################################

# ── Regions ─────────────────────────────────────────────────────────────────
# 9 EIA Census Divisions used throughout the NGMM
# Kept as code constants (definitional, not parameterizable)
REGIONS = [
    'new_england',        # CT, ME, MA, NH, RI, VT
    'middle_atlantic',    # NJ, NY, PA
    'east_north_central', # IL, IN, MI, OH, WI
    'west_north_central', # IA, KS, MN, MO, NE, ND, SD
    'south_atlantic',     # DC, DE, FL, GA, MD, NC, SC, VA, WV
    'east_south_central', # AL, KY, MS, TN
    'west_south_central', # AR, LA, OK, TX  ← Gulf Coast + Haynesville
    'mountain',           # AZ, CO, ID, MT, NV, NM, UT, WY ← Rockies + Permian
    'pacific',            # AK, CA, HI, OR, WA
]

REGION_LABELS = {
    'new_england':        'New England',
    'middle_atlantic':    'Middle Atlantic',
    'east_north_central': 'East North Central',
    'west_north_central': 'West North Central',
    'south_atlantic':     'South Atlantic',
    'east_south_central': 'East South Central',
    'west_south_central': 'West South Central (Gulf Coast)',
    'mountain':           'Mountain (Rockies / Permian)',
    'pacific':            'Pacific',
}

# ── Supply Curves ────────────────────────────────────────────────────────────
# Loaded from input/naturalgas/ng_supply_cost_tiers.csv
# via data.py.  Hardcoded values are preserved as fallbacks inside data.py.
# Hardcoded dict:
# SUPPLY_COST_TIERS = {
#     'new_england':        [(  60,  3.50), (  30,  5.00), (  10,  7.50)],
#     'middle_atlantic':    [(5000,  1.80), (5500,  2.30), (3000,  3.20)],
#     'east_north_central': [( 500,  2.40), ( 400,  3.50), ( 150,  5.00)],
#     'west_north_central': [( 900,  2.20), ( 700,  3.20), ( 300,  4.80)],
#     'south_atlantic':     [( 250,  3.00), ( 200,  4.50), ( 100,  6.50)],
#     'east_south_central': [( 450,  2.50), ( 350,  3.60), ( 150,  5.20)],
#     'west_south_central': [(8500,  1.40), (8000,  1.90), (4500,  2.80)],
#     'mountain':           [(4500,  1.80), (3500,  2.40), (2000,  3.40)],
#     'pacific':            [( 600,  2.80), ( 450,  3.80), ( 200,  5.80)],
# }
SUPPLY_COST_TIERS = _NG_DATA['supply_cost_tiers']

# Optional year-varying anchor path
# {(region, year): (q0_mult, p0_mult)}; empty dict -> static anchors (previous behaviour).
SUPPLY_ANCHORS = _NG_DATA.get('supply_anchors', {})

COST_TIER_LABELS = ['low_cost', 'medium_cost', 'high_cost']

# LNG import availability (coastal regions only), high-cost backstop supply
# Loaded from input/naturalgas/ng_lng_import.csv
# Hardcoded dict:
# LNG_IMPORT = {
#     'new_england':     (350, 8.00),
#     'south_atlantic':  (300, 7.50),
#     'pacific':         (200, 8.50),
# }
LNG_IMPORT = _NG_DATA['lng_import']

# ── US LNG Export Demand ──────────────────────────────────────────────────────
# US LNG exports are a major use of domestic gas supply (~14+ BCF/day in 2025).
# Treated as exogenous demand, export contracts are long-term obligations
# that tighten the domestic supply-demand balance and raise domestic prices.
#
# Sources:  EIA AEO 2025 LNG Export projections; DOE export authorisation data.
#   West South Central: Sabine Pass, Corpus Christi, Freeport, Calcasieu Pass,
#                       Cameron, Golden Pass (under construction), Plaquemines,
#                       Port Arthur (planned), Rio Grande (planned).
#   South Atlantic:     Cove Point (MD), Elba Island (GA).
# Pacific: Jordan Cove / Magnolia (proposed, Oregon / BC border) ,
#                       assumed to partially materialise after 2030.
#
# Units: BCF/yr.  Linearly interpolated for years between listed values.
# Loaded from input/naturalgas/ng_lng_export.csv
# Hardcoded dict:
# LNG_EXPORT_DEMAND_BCF: dict[str, dict[int, float]] = {
#     'west_south_central': {2025: 4300, 2030: 5100, 2035: 5700, 2040: 6200, 2045: 6700, 2050: 7200},
#     'south_atlantic':     {2025:  480, 2030:  560, 2035:  620, 2040:  650, 2045:  700, 2050:  730},
#     'pacific':            {2025:    0, 2030:   80, 2035:  200, 2040:  350, 2045:  500, 2050:  650},
# }
LNG_EXPORT_DEMAND_BCF: dict[str, dict[int, float]] = _NG_DATA['lng_export']

def _interp_lng_export(region: str, year: int) -> float:
    """Linearly interpolate LNG export demand for any year from table breakpoints."""
    table = LNG_EXPORT_DEMAND_BCF.get(region, {})
    if not table:
        return 0.0
    years_sorted = sorted(table)
    if year <= years_sorted[0]:
        return table[years_sorted[0]]
    if year >= years_sorted[-1]:
        return table[years_sorted[-1]]
    for k in range(len(years_sorted) - 1):
        y0, y1 = years_sorted[k], years_sorted[k + 1]
        if y0 <= year <= y1:
            t = (year - y0) / (y1 - y0)
            return table[y0] + t * (table[y1] - table[y0])
    return 0.0

# ── Demand Price Elasticities ─────────────────────────────────────────────────
# Own-price short-run elasticities for each end-use sector.
# Negative: demand falls when gas price rises.
#
# Used in NGModel.update_demand_from_price(), called by the GS/unified
# integrator each iteration to add price-responsive demand behaviour,
# matching NEMS NGMM's price-sensitive demand blocks.
#
# Sources: EIA NEMS NGMM documentation; EIA Short-Term Energy Outlook
#          econometric estimates; literature review (Brown & Yucel 2008).
# Loaded from input/naturalgas/ng_demand_elasticity.csv
# Hardcoded dict:
# DEMAND_PRICE_ELASTICITY: dict[str, float] = {
#     'electric_power':  -0.15,
#     'industrial':      -0.20,
#     'residential':     -0.10,
#     'commercial':      -0.10,
#     'transportation':  -0.05,
# }
DEMAND_PRICE_ELASTICITY: dict[str, float] = _NG_DATA['demand_elasticity']

# ── Demand Data ──────────────────────────────────────────────────────────────
# Base-year (2025) demand by census division and end-use sector [BCF/yr].
# Sectors:  electric_power | industrial | residential | commercial | transportation
#
# Source: EIA Natural Gas Annual 2022 Tables 1-7, scaled to 2025 using AEO 2023
#         reference-case projections. Values rounded to nearest 5 BCF.
#
DEMAND_SECTORS = ['electric_power', 'industrial', 'residential', 'commercial', 'transportation']

# Loaded from input/naturalgas/ng_base_demand.csv
# Hardcoded dict (45 region-sector pairs):
# BASE_DEMAND_2025: dict[str, dict[str, float]] = {
#     'new_england':        {'electric_power':  215, 'industrial':  290, 'residential':  395, 'commercial':  360, 'transportation':   55},
#     'middle_atlantic':    {'electric_power':  810, 'industrial': 1180, 'residential':  795, 'commercial':  620, 'transportation':  110},
#     'east_north_central': {'electric_power': 1250, 'industrial': 1820, 'residential':  710, 'commercial':  530, 'transportation':  160},
#     'west_north_central': {'electric_power':  820, 'industrial':  810, 'residential':  415, 'commercial':  305, 'transportation':  115},
#     'south_atlantic':     {'electric_power': 2050, 'industrial': 1010, 'residential':  620, 'commercial':  510, 'transportation':  155},
#     'east_south_central': {'electric_power':  830, 'industrial':  620, 'residential':  315, 'commercial':  210, 'transportation':   60},
#     'west_south_central': {'electric_power': 3520, 'industrial': 3050, 'residential':  510, 'commercial':  415, 'transportation':  260},
#     'mountain':           {'electric_power': 1430, 'industrial':  820, 'residential':  415, 'commercial':  305, 'transportation':  115},
#     'pacific':            {'electric_power': 1320, 'industrial':  510, 'residential':  415, 'commercial':  360, 'transportation':   60},
# }
BASE_DEMAND_2025: dict[str, dict[str, float]] = _NG_DATA['base_demand']

# Loaded from input/naturalgas/ng_demand_growth.csv
# Hardcoded dict:
# DEMAND_GROWTH_RATES: dict[str, float] = {
#     'electric_power':  0.004,
#     'industrial':      0.008,
#     'residential':    -0.005,
#     'commercial':     -0.003,
#     'transportation':  0.025,
# }
DEMAND_GROWTH_RATES: dict[str, float] = _NG_DATA['demand_growth']

# ── Pipeline Network ─────────────────────────────────────────────────────────
# Directed arcs, each physical pipe defined as two directed arcs (both dirs).
# capacity_bcf: maximum throughput [BCF/yr] per direction
# tariff: FERC-approved transportation tariff [$/MMBtu]
#
# Source: EIA Compendium of Interstate Natural Gas Pipelines (2022)
#         FERC Form 2 tariff filings; capacity = aggregate nameplate × 365 d
#
# Each tuple: (origin, destination, capacity_bcf, tariff_$/MMBtu)
# Loaded from input/naturalgas/ng_pipeline_arcs.csv
# Hardcoded list of 26 directed arcs (see ng_pipeline_arcs.csv for full values):
# PIPELINE_ARCS_RAW = [
#     ('new_england', 'middle_atlantic', 1400, 0.55),
#     ('middle_atlantic', 'new_england', 1400, 0.55),
#     ... (26 arcs total)
# ]
PIPELINE_ARCS_RAW = _NG_DATA['pipeline_arcs']

# ── Underground Storage ───────────────────────────────────────────────────────
# Working gas capacity [BCF] and maximum seasonal withdrawal rate [BCF/yr].
# For an annual model the net storage change is constrained to ≈ 0 (cyclical).
# Model doesn't currently use storage, just a placeholder for future work.
# Source: EIA Form EIA-191M, aggregate by census division (2022)
# Loaded from input/naturalgas/ng_storage.csv
# Hardcoded dict:
# STORAGE = {
#     'new_england':        {'working': 180,  'inject':  60,  'withdraw':  90},
#     'middle_atlantic':    {'working': 420,  'inject': 140,  'withdraw': 210},
#     'east_north_central': {'working': 620,  'inject': 210,  'withdraw': 310},
#     'west_north_central': {'working': 510,  'inject': 170,  'withdraw': 255},
#     'south_atlantic':     {'working': 340,  'inject': 115,  'withdraw': 170},
#     'east_south_central': {'working': 160,  'inject':  55,  'withdraw':  80},
#     'west_south_central': {'working': 850,  'inject': 285,  'withdraw': 425},
#     'mountain':           {'working': 360,  'inject': 120,  'withdraw': 180},
#     'pacific':            {'working': 160,  'inject':  55,  'withdraw':  80},
# }
STORAGE = _NG_DATA['storage']

# Loaded from input/naturalgas/ng_scalars.csv
# Hardcoded value: STORAGE_OPEX = 0.18
STORAGE_OPEX = _NG_DATA['storage_opex']

# ── NGMM AEO2025 QP parameters ────────────────────────────────────────────────
# New module-level constants for the
# quadratic-program rewrite. All loaded from CSV via data.py with hardcoded
# fallbacks defined there. References below cite NGMM_AEO2025.pdf.

# Supply-curve shape (NGMM Eq 1-5, Fig 3.4). Built around an expected (Q0, P0)
# anchor with per-step elasticities and CRV breakpoint adjustments.
SUPPLY_CURVE_SHAPE = _NG_DATA['supply_curve_shape']

# Pipeline tariff curve shape (NGMM Eq 6, Fig 3.5). Utilisation breakpoints and
# tariff multipliers on the base tariff per arc.
TARIFF_CURVE_SHAPE = _NG_DATA['tariff_curve_shape']

# LNG export demand curve shape (NGMM Eq 14, Fig 3.6). World LNG price and
# downward-sloping demand factors over fractional capacity.
LNG_DEMAND_CURVE_SHAPE = _NG_DATA['lng_demand_curve']

# Per-region losses (NGMM Eq 10, 11): distribution, intrastate, storage, and
# plant-fuel fraction. {region: {distribution_loss, intrastate_loss,
# storage_loss, plant_fuel_frac}}.
LOSSES = _NG_DATA['losses']

# Per-region gathering charges in $/MMBtu (NGMM Eq 7 term).
GATHERING_CHARGES = _NG_DATA['gathering']

# Per-arc pipeline fuel-loss fractions (NGMM Eq 11 f^pip). Sparse, arcs not
# listed use the QP scalar default (~0.005).
PIPE_LOSS_BY_ARC = _NG_DATA['pipe_loss']

# Other NGMM-QP scalars (default values, overridable via ng_scalars.csv).
QP_SCALARS = _NG_DATA['qp_scalars']

# Supply-curve breakpoint count (5 segments → 6 breakpoints, matches NGMM
# AEO 2022 default; see SUPPLY_CURVE_SHAPE for the elasticities). Kept as a
# module constant because constraint indexing depends on it.
# $/MMBtu penalty on unserved demand in region-subset runs
# (see NGModel.__init__). Set ~100x any plausible gas price so the backstop is never economic and
# only relieves a genuine shortfall created by dropping a subset's supplying neighbours.
UNSERVED_PENALTY = 1000.0

SUPPLY_BREAK_IDS = [1, 2, 3, 4, 5, 6]
SUPPLY_STEP_IDS = [1, 2, 3, 4, 5] # 5 segments between 6 breakpoints

# Tariff-curve segments (one fewer than the number of breakpoints in TARIFF_CURVE_SHAPE).
TARIFF_SEGMENTS = list(range(1, len(TARIFF_CURVE_SHAPE['util_break'])))

# LNG demand-curve segments.
LNG_SEGMENTS = list(range(1, len(LNG_DEMAND_CURVE_SHAPE['q_frac'])))

###############################################################################
# Helper: build demand projection
###############################################################################

# Region subsetting for standalone runs. The model
# only ever had a `years` knob; regions were the module constant REGIONS, so a "smoke" NG run was
# full-size. resolve_regions() is the single place that validates and canonicalizes a subset, so
# the CLI, the config path, and direct NGModel(...) construction cannot disagree.
def resolve_regions(regions: list[str] | None) -> list[str]:
    """Validate a region subset and return it in canonical REGIONS order.

    ``None`` (or the full set) returns all nine census divisions, so the default path is
    unchanged. Unknown names raise immediately with the valid list rather than failing later
    inside Pyomo with an opaque index error. Duplicates are collapsed; order is always canonical
    so two spellings of the same subset build identical models.
    """
    if regions is None:
        return list(REGIONS)
    requested = [str(r).strip() for r in regions if str(r).strip()]
    if not requested:
        raise ValueError('regions was empty, pass None for all nine, or at least one name.')
    unknown = [r for r in requested if r not in REGIONS]
    if unknown:
        raise ValueError(
            f'unknown NG region(s): {unknown}\nvalid regions are: {list(REGIONS)}'
        )
    return [r for r in REGIONS if r in set(requested)]


# Added the `regions` argument (default None = all nine, so
def project_demand(years: list[int],
                   regions: list[str] | None = None) -> dict[tuple[str, str, int], float]:
    """Project sector demand for each region and year using AEO growth rates.

    Parameters
    ----------
    years : list[int]
        Model years (e.g. [2025, 2030, 2035, 2040, 2045, 2050]).
    regions : list[str] | None
        Region subset; ``None`` projects all nine census divisions.

    Returns
    -------
    dict[(region, sector, year), float]
        Projected demand in BCF/year.
    """
    base_year = 2025
    demand: dict[tuple[str, str, int], float] = {}
    for region in resolve_regions(regions):
        for sector in DEMAND_SECTORS:
            base = BASE_DEMAND_2025[region][sector]
            g = DEMAND_GROWTH_RATES[sector]
            for year in years:
                dt = year - base_year
                demand[(region, sector, year)] = base * ((1 + g) ** dt)
    return demand


###############################################################################
# NGMM supply-curve breakpoint helpers (NGMM Eq 2-5)
# Used by NGModel.__init__ to build the
# elastic piecewise-linear supply curve around an expected (Q0, P0) anchor.
###############################################################################

def _supply_qbase(q0: float, k: int, crv_below: list, crv_above: list) -> float:
    """Compute QBASE_k for the NGMM supply curve (NGMM Eq 2 and 4).

    Note the two branches run their products in opposite directions. For k <= 3 the loop
    starts at index k-1 and runs to the end, so breakpoint 1 accumulates all three downward
    factors and sits furthest BELOW the anchor, while breakpoint 3 accumulates only the last
    one and sits just below it. For k > 3 the loop starts at 0 and runs k-3 times, so
    breakpoint 6 accumulates all three upward factors and sits furthest above.

    Reversing either direction produces a curve that is still monotonic and still spans a
    plausible range, so nothing downstream complains, the quantities are attached to
    the wrong prices.


    Breakpoints 1-3 sit below the anchor (Q0, P0); 4-6 sit above. Given:
        crv_below = [c1, c2, c3]  (volume drop fractions for steps 1, 2, 3)
        crv_above = [c1, c2, c3]  (volume rise fractions for steps 4, 5, 6)

    Returns the cumulative-product breakpoint:
        k = 1 -> Q0 × ∏_{i=1..3} (1 − crv_below[i])
        k = 2 -> Q0 × ∏_{i=2..3} (1 − crv_below[i])
        k = 3 -> Q0 × (1 − crv_below[3])
        k = 4 -> Q0 × (1 + crv_above[1])
        k = 5 -> Q0 × (1 + crv_above[1])(1 + crv_above[2])
        k = 6 -> Q0 × (1 + crv_above[1])(1 + crv_above[2])(1 + crv_above[3])
    """
    if k <= 3:
        f = 1.0
        for i in range(k - 1, 3):
            f *= (1.0 - crv_below[i])
        return q0 * f
    else:
        f = 1.0
        for i in range(0, k - 3):
            f *= (1.0 + crv_above[i])
        return q0 * f


def _supply_pbase(p0: float, k: int, crv_below: list, crv_above: list,
                  elas: list) -> float:
    """Compute PBASE_k for the NGMM supply curve.

    NGMM AEO2025 Eq 3 and Eq 5, as written
    in the PDF, give a non-monotonic price curve with the AEO 2022 default
    elasticities (0.2-0.8 < 1).  The literal formula is

        PBASE_step = P0 × ∏ (1 ± CRV_step) / ELAS_step

    which divides (1 ± CRV) by an elasticity < 1, exploding upward and
    producing prices that *fall* between adjacent steps below Q0 (verified
    with WSC test: PBASE_2 = 4.36 > PBASE_3 = 3.59, wrong direction).

    The economically-correct form, derived from the elasticity definition
    ε = (dQ/Q) / (dP/P) ⇒ dP/P = (dQ/Q)/ε ⇒ ΔPBASE/PBASE = ±CRV/ELAS, is

        PBASE_step = P0 × ∏ (1 ± CRV_step / ELAS_step)

    This is consistent with the in-step price formula (Eq 1)

        P(Q) = PBASE × (1 + (1/ELAS) × (Q - QBASE)/QBASE),

    which at Q = QBASE_{k+1} gives PBASE_{k+1} = PBASE_k × (1 + CRV/ELAS).
    We use the elasticity-correct form here; the literal-PDF form is
    preserved in the docstring above for documentation.

    REVIEW NOTE. This is a deliberate departure from the published NGMM specification, and it
    changes every price the model produces. The argument for it is that the published form is
    internally inconsistent, it contradicts NGMM's own in-step price formula (Eq 1) and is
    non-monotonic with NGMM's own default elasticities, but it is a departure nonetheless
    and should be flagged in any comparison against NGMM results.

    Note the elasticity indexing differs between branches: the k <= 3 branch reads elas[i] on
    the same index as crv_below[i], while the k > 3 branch reads elas[2 + i], continuing into
    the upper half of the five-element elasticity vector. Elasticities decline across the five
    segments (0.8 -> 0.2), so dividing by a smaller number above the anchor is what makes the
    curve steepen: supply gets progressively harder to expand.
    """
    if k <= 3:
        f = 1.0
        for i in range(k - 1, 3):
            f *= (1.0 - crv_below[i] / elas[i])
        return p0 * f
    else:
        f = 1.0
        for i in range(0, k - 3):
            f *= (1.0 + crv_above[i] / elas[2 + i])
        return p0 * f


###############################################################################
# Natural Gas Market Model
###############################################################################

class NGModel(ConcreteModel):
    """Pyomo model for the EIA-style Natural Gas Market Module.

    Minimizes total annual cost of supplying natural gas across 9 US census
    divisions subject to:
      - Step supply-curve capacity limits per region and NGMM step
      - Directed pipeline arc capacity limits
      - Underground storage balance (annual net injection = withdrawal)
      - Demand satisfaction in every region and year (with optional LNG backstop)

    Shadow prices on the demand-balance constraints are the regional gas prices
    returned to coupled models (electricity, hydrogen).

    Parameters
    ----------
    years : list[int]
        Planning years to include in the optimisation.
    mode : str
        'standard', standalone with built-in demand projections.
        'integrated', mutable demand/price params updated by the integrator.
    demand_override : dict[(region, sector, year), float] | None
        If provided, replaces internal demand projections.
    elec_demand_override : dict[GI, float] | None
        Regional annual electric-power gas demand from the electricity model.
    """

    # Added `regions` (default None = all nine, so every
    def __init__(
        self,
        years: list[int] | None = None,
        regions: list[str] | None = None,
        mode: str = 'standard',
        demand_override: dict | None = None,
        elec_demand_override: dict | None = None,
    ):
        """Full QP rewrite aligned with the
        NGMM AEO 2025 mathematical formulation. See module docstring for the list
        of NGMM features implemented (Tier 1) and the ones intentionally skipped
        (Tier 2/3). Equation numbers below cite NGMM_AEO2025.pdf §3.
        """
        super().__init__()

        # Region subsetting. `region_list` is the single
        # source of truth from here down; `is_region_subset` gates the unserved-demand backstop
        # so the full nine-region model is untouched (see the backstop block below).
        region_list = resolve_regions(regions)
        self.region_list = region_list
        self.is_region_subset = len(region_list) < len(REGIONS)

        if mode not in {'standard', 'integrated'}:
            raise ValueError("mode must be 'standard' or 'integrated'")
        self.ng_mode = mode

        if years is None:
            years = [2025, 2030, 2035, 2040, 2045, 2050]
        year_list = sorted(years)

        # ── build projected demand ────────────────────────────────────────────
        # Project only the active regions.
        projected = project_demand(year_list, regions=region_list)
        if demand_override:
            projected.update(demand_override)

        # if the electricity model passes updated elec-power gas demand, apply it
        if elec_demand_override:
            for gi, qty in elec_demand_override.items():
                projected[(gi.region, 'electric_power', gi.year)] = qty

        # ── build pipeline arc index ─────────────────────────────────────────
        # Keep only arcs INTERNAL to the active regions.
        # An arc with one endpoint outside the subset has no counterparty balance constraint, so
        # leaving it in would let gas appear from or vanish into a region the model no longer
        _active = set(region_list)
        _arcs_raw = [(o, d, cap, tar) for o, d, cap, tar in PIPELINE_ARCS_RAW
                     if o in _active and d in _active]
        arc_list   = [(o, d) for o, d, _, _ in _arcs_raw]
        arc_cap    = {(o, d): cap  for o, d, cap, _    in _arcs_raw}
        arc_tariff = {(o, d): tar  for o, d, _, tar    in _arcs_raw}

        # LNG export regions: only those listed in LNG_EXPORT_DEMAND_BCF carry an
        # endogenous LNG demand curve (NGMM Fig 3.6).  All other regions still
        # have lng_export[r, y] but it is bounded at zero.
        # Intersect with the active regions.
        lng_regions_list = [r for r in LNG_EXPORT_DEMAND_BCF if r in _active]

        # ── SETS ──────────────────────────────────────────────────────────────
        # Region subsetting. Every region-keyed Param below
        # is built from a rule function indexed off this Set, so subsetting here propagates
        # automatically; only arcs, LNG regions, and _base_demand needed explicit filtering.
        self.regions  = Set(initialize=region_list)
        # NGMM vocabulary, used consistently throughout this model:
        # STEP, an elastic piece of the supply curve that carries volume. NGMM's SSTEP
        # (Eq 7, Eq 8: PROD = sum_step SSTEP + QMIN). There are five.
        # BREAK, an endpoint bounding those steps; the index of NGMM's QBASE/PBASE. Six.
        # Six breaks bound five steps, so every loop over steps reads break k and break k+1.
#
        # These are NOT the three low/medium/high cost tiers in the input file. Those have no
        # NGMM counterpart at all (NGMM's supply dimension is (suptype, qps)) and are collapsed
        # into a single (Q0, P0) anchor below. update_supply_capacity() still accepts the legacy
        # three-key form from the HSM integrator and aggregates it into Q0.
        self.steps = Set(initialize=[f'step{k}' for k in SUPPLY_STEP_IDS]) # 5 NGMM steps
        self.supply_breaks = Set(initialize=SUPPLY_BREAK_IDS, ordered=True) # 6 breakpoints
        self.tariff_segs   = Set(initialize=TARIFF_SEGMENTS, ordered=True)
        self.tariff_breaks = Set(initialize=list(range(1, len(TARIFF_CURVE_SHAPE['util_break']) + 1)),
                                  ordered=True)
        self.lng_regions = Set(initialize=lng_regions_list)
        self.lng_segs    = Set(initialize=LNG_SEGMENTS, ordered=True)
        self.lng_breaks  = Set(initialize=list(range(1, len(LNG_DEMAND_CURVE_SHAPE['q_frac']) + 1)),
                                ordered=True)
        self.sectors  = Set(initialize=DEMAND_SECTORS)
        self.arcs     = Set(initialize=arc_list, dimen=2)
        self.year     = Set(initialize=year_list, ordered=True)

        # Suffix for dual (shadow price) extraction
        self.dual = Suffix(direction=Suffix.IMPORT)

        # ── PARAMETERS ────────────────────────────────────────────────────────
        #
        # NGMM-style elastic supply curve (Eq 1-5).  Each region/year has 6
        # breakpoints (Q, P) defining 5 linear segments.  Q0[r, y] = expected
        # production point, set initially from the legacy SUPPLY_COST_TIERS totals
        # and refreshed each iteration by update_supply_capacity() (HSM output).
        # The QBASE/PBASE breakpoints are mutable Params so the curve can be
        # rebuilt without rebuilding the model.

        # Initial Q0 from the aggregated input cost tiers (sum of capacities)
        # The (Q0, P0) anchors now follow the
        # optional YEAR-VARYING path in SUPPLY_ANCHORS (AEO production/supply-price paths, normalized
        # to 2025). The params were already (region, year)-indexed; only the initialization ignored y,
        # which froze the curve and made Henry Hub rise monotonically (+20% by 2050 vs AEO's hump
        # peaking ~2040). Missing entries multiply by 1.0 = the original static behaviour.
        def _q0_init(m, r, y):
            return (sum(cap for cap, _ in SUPPLY_COST_TIERS[r])
                    * SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[0])

        # Initial P0 from the quantity-weighted average of the input cost-tier costs. This is
        # where the three tiers stop existing: they become one price, and the five NGMM steps
        # are built around it.
        def _p0_init(m, r, y):
            cost_tiers = SUPPLY_COST_TIERS[r]
            tot_q = sum(c for c, _ in cost_tiers)
            if tot_q <= 0:
                return 3.0
            return (sum(c * p for c, p in cost_tiers) / tot_q
                    * SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[1])

        self.Q0 = Param(self.regions, self.year, initialize=_q0_init, mutable=True)
        self.P0 = Param(self.regions, self.year, initialize=_p0_init, mutable=True)

        # Supply-curve QBASE / PBASE breakpoints (NGMM Eq 2-5).
        # We compute initial breakpoint values from Q0/P0 and the SUPPLY_CURVE_SHAPE
        # constants here; update_supply_capacity() refreshes them whenever Q0 changes.
        crv_below = SUPPLY_CURVE_SHAPE['crv_below']  # [c1, c2, c3] for steps 1, 2, 3 below
        crv_above = SUPPLY_CURVE_SHAPE['crv_above']  # [c1, c2, c3] for steps 4, 5, 6 above
        elas      = SUPPLY_CURVE_SHAPE['elas']        # [e1..e5] for segments 1-5

        # Same year-varying anchor multipliers as
        # _q0_init/_p0_init above (these recompute q0/p0 inline). Originals had no SUPPLY_ANCHORS term.
        def _qbase_init(m, r, k, y):
            q0 = (sum(cap for cap, _ in SUPPLY_COST_TIERS[r])
                  * SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[0])
            return _supply_qbase(q0, k, crv_below, crv_above)

        def _pbase_init(m, r, k, y):
            tr = SUPPLY_COST_TIERS[r]
            tot_q = sum(c for c, _ in tr)
            p0 = sum(c * p for c, p in tr) / tot_q if tot_q > 0 else 3.0
            p0 *= SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[1]
            return _supply_pbase(p0, k, crv_below, crv_above, elas)

        self.QBASE = Param(self.regions, self.supply_breaks, self.year,
                           initialize=_qbase_init, mutable=True)
        self.PBASE = Param(self.regions, self.supply_breaks, self.year,
                           initialize=_pbase_init, mutable=True)

        # QMIN: committed production (NGMM Eq 8): the "wells already drilled"
        # floor.  Treated as a fraction of Q0 (NGMM uses an exogenous PEMEX /
        # historical-floor input; we use qmin_fraction × Q0 as a proxy).
        qmin_frac = QP_SCALARS.get('supply_curve_qmin_fraction', 0.20)
        self.QMIN = Param(self.regions, self.year,
                          initialize=lambda m, r, y: qmin_frac * value(m.Q0[r, y]),
                          mutable=True)

        # Gathering charge (NGMM Eq 7 P^gath term, $/MMBtu)
        self.gathering_charge = Param(
            self.regions,
            initialize=lambda m, r: GATHERING_CHARGES.get(r, QP_SCALARS['gathering_charge_avg']),
        )

        # LNG backstop import (existing 3-region exogenous capacity)
        self.lng_capacity = Param(
            self.regions,
            initialize=lambda m, r: LNG_IMPORT.get(r, (0, 0))[0],
        )
        self.lng_cost = Param(
            self.regions,
            initialize=lambda m, r: LNG_IMPORT.get(r, (0, 0))[1],
        )

        # Pipeline tariff curve (NGMM Eq 6, Fig 3.5).  PTAR[o, d, k] / QTAR[o, d, k]
        # are 7 breakpoint pairs per directed arc, computed from the base tariff
        # and capacity using TARIFF_CURVE_SHAPE multipliers.  Quadratic on each
        # segment between consecutive breakpoints (NGMM Fig 3.5 hurdle behaviour).
        util_breaks  = TARIFF_CURVE_SHAPE['util_break']
        tariff_mults = TARIFF_CURVE_SHAPE['tariff_mult']

        def _qtar_init(m, o, d, k, y):
            return arc_cap[(o, d)] * util_breaks[k - 1]

        def _ptar_init(m, o, d, k, y):
            return arc_tariff[(o, d)] * tariff_mults[k - 1]

        self.QTAR = Param(self.arcs, self.tariff_breaks, self.year,
                          initialize=_qtar_init, mutable=False)
        self.PTAR = Param(self.arcs, self.tariff_breaks, self.year,
                          initialize=_ptar_init, mutable=False)

        # Pipeline fuel-loss fraction per directed arc (NGMM Eq 11 f^pip)
        pipe_loss_default = QP_SCALARS.get('pipe_fuel_loss_default', 0.005)
        self.pipe_loss = Param(
            self.arcs,
            initialize=lambda m, o, d: PIPE_LOSS_BY_ARC.get((o, d), pipe_loss_default),
        )

        # Pipeline network base info (kept for reporting and capacity bounds)
        self.pipe_capacity = Param(self.arcs, initialize=lambda m, o, d: arc_cap[(o, d)])
        self.pipe_tariff   = Param(self.arcs, initialize=lambda m, o, d: arc_tariff[(o, d)])

        # LNG export demand curve (NGMM Fig 3.6).  Per LNG export region and year,
        # PLNG / QLNG breakpoints span a linear demand curve from world price up
        # to max_factor × world price at zero export volume.  The QLNG anchor is
        # the legacy LNG_EXPORT_DEMAND_BCF capacity for that (region, year).
        lng_q_frac   = LNG_DEMAND_CURVE_SHAPE['q_frac']
        lng_p_factor = LNG_DEMAND_CURVE_SHAPE['p_factor']
        lng_world_p  = LNG_DEMAND_CURVE_SHAPE['world_price']

        def _qlng_init(m, r, k, y):
            cap = _interp_lng_export(r, y)
            return cap * lng_q_frac[k - 1]

        def _plng_init(m, r, k, y):
            return lng_world_p * lng_p_factor[k - 1]

        self.QLNG = Param(self.lng_regions, self.lng_breaks, self.year,
                          initialize=_qlng_init, mutable=True)
        self.PLNG = Param(self.lng_regions, self.lng_breaks, self.year,
                          initialize=_plng_init, mutable=True)

        # Storage
        def _stor_working(m, r):
            return STORAGE[r]['working']

        def _stor_inject(m, r):
            return STORAGE[r]['inject']

        def _stor_withdraw(m, r):
            return STORAGE[r]['withdraw']

        self.storage_working_cap  = Param(self.regions, initialize=_stor_working)
        self.storage_inject_cap   = Param(self.regions, initialize=_stor_inject)
        self.storage_withdraw_cap = Param(self.regions, initialize=_stor_withdraw)
        self.storage_opex         = Param(initialize=STORAGE_OPEX)

        # NGMM losses (Eq 10, 11): distribution, intrastate, storage, plant fuel
        self.distribution_loss = Param(
            self.regions,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'distribution_loss', QP_SCALARS['distribution_loss_default']),
        )
        self.intrastate_loss = Param(
            self.regions,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'intrastate_loss', QP_SCALARS['intrastate_loss_default']),
        )
        self.storage_loss = Param(
            self.regions,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'storage_loss', QP_SCALARS['storage_loss_default']),
        )
        self.plant_fuel_frac = Param(
            self.regions,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'plant_fuel_frac', QP_SCALARS['plant_fuel_fraction_default']),
        )

        # Demand, mutable so the integrator can update it each iteration
        self.demand = Param(
            self.regions, self.sectors, self.year,
            initialize=lambda m, r, s, y: projected.get((r, s, y), 0.0),
            mutable=True,
        )

        # Conversion factor: BCF → MMBtu (objective scaling).  1 BCF = 1e6 MMBtu;
        # we divide by 1e3 so the objective reads in $-thousands per unit-step.
        # (Gurobi/HiGHS handle absolute scale fine; this is just for readability.)
        self.bcf_to_mmbtu = Param(initialize=1e3)

        # Canadian gas imports, mutable so the HSM integrator can update each iteration
        self.canada_supply = Param(
            self.regions, self.year,
            initialize=0.0,
            mutable=True,
        )

        # ── Base demand snapshot for price-elasticity updates ──────────────────
        # Stores the initial (no-price-adjustment) projected demand.
        # update_demand_from_price() modifies self.demand relative to this base.
        self._base_demand: dict[tuple[str, str, int], float] = {
            (r, s, y): projected.get((r, s, y), 0.0)
            # Active regions only, so the elasticity update
            # cannot reference a region this model does not carry.
            for r in region_list for s in DEMAND_SECTORS for y in year_list
        }
        # Reference gas prices, set after first GS solve via set_reference_prices().
        # Before being set, price-responsive demand has no effect (prices=reference).
        self._ref_prices: dict[tuple[str, int], float] = {}

        # ── VARIABLES ─────────────────────────────────────────────────────────
        # Per-segment supply volume (NGMM SSTEP decision variable, Eq 8).
        # Indexed by region × segment × year; bounded above by the segment
        # width via supply_step_cap_con.
        self.sstep = Var(self.regions, self.steps, self.year, within=NonNegativeReals)

        # The legacy attribute name
        # ``production`` is intentionally NOT defined.  Pyomo forbids aliasing
        # one Var component to two block attributes, and a grep of the
        # codebase (integrators, sensitivity/babymodel, hydrogen, hsm, comm)
        # confirms no caller indexes ng_model.production[r, t, y] directly.
        # Callers that previously wanted "production by cost tier" should now
        # read ``sstep`` (NGMM step volume) or ``production_total`` (per-region
        # year-total Expression, including the QMIN floor).

        # Total production per (region, year), Expression rather than Var so the
        # objective and demand-balance terms reference the step sum directly (NGMM Eq 8).
        # Equals QMIN (committed floor) + Σ_k sstep[r, k, y]  (NGMM Eq 8).
        def _prod_total_rule(m, r, y):
            return m.QMIN[r, y] + quicksum(m.sstep[r, t, y] for t in m.steps)
        self.production_total = Expression(self.regions, self.year, rule=_prod_total_rule)

        # LNG export per-step volume (NGMM Eq 14): price-responsive variable on
        # the LNG demand curve.  Indexed by LNG region × segment × year.
        self.lng_export_step = Var(
            self.lng_regions, self.lng_segs, self.year, within=NonNegativeReals,
        )

        # Total LNG export per (region, year), derived from segments.
        def _lng_export_total_rule(m, r, y):
            if r in m.lng_regions:
                return quicksum(m.lng_export_step[r, k, y] for k in m.lng_segs)
            return 0.0
        self.lng_export_demand = Expression(self.regions, self.year, rule=_lng_export_total_rule)

        # Pipeline tariff-curve per-step volume (NGMM Eq 15)
        self.tar_step = Var(self.arcs, self.tariff_segs, self.year, within=NonNegativeReals)

        # Pipeline flow per (arc, year), derived from tariff-curve segments
        # (NGMM Eq 15: FLOWH2H = Σ_step TAR_step).
        def _pipe_flow_rule(m, o, d, y):
            return quicksum(m.tar_step[o, d, k, y] for k in m.tariff_segs)
        self.pipe_flow = Expression(self.arcs, self.year, rule=_pipe_flow_rule)

        # LNG backstop import (non-negative; zero for landlocked regions via capacity=0)
        self.lng_import = Var(self.regions, self.year, within=NonNegativeReals)

        # Storage injection / withdrawal [BCF/yr seasonal cycle]
        self.stor_inject   = Var(self.regions, self.year, within=NonNegativeReals)
        self.stor_withdraw = Var(self.regions, self.year, within=NonNegativeReals)

        # Slack demand variable (for integration: allows other models to add load)
        self.var_demand = Var(self.regions, self.year, within=NonNegativeReals, initialize=0.0)

        # UNSERVED-DEMAND BACKSTOP, subset runs only.
        # `var_demand` is NonNegative and sits on the DEMAND side of demand_balance, so it can
        # only absorb surplus supply, never cover a shortfall. That makes any region subset that
        # is a net importer INFEASIBLE once its supplying neighbours are dropped, verified:
        # regions=['new_england'] has no production and no arcs and fails to solve at all.
        # Failing with an opaque Gurobi infeasibility would make this feature unusable in
        # practice, so subset runs get an explicit unserved-demand variable priced far above any
        # real gas price. It is only created when a strict subset is active, so the nine-region
        # model has no new variable and no new objective term (regression-safe by construction).
        # NB: `self.unserved` is only ever CREATED for a subset, do not pre-assign None here.
        # Assigning None first makes it a plain attribute on the Pyomo block, and attaching the
        # Var afterwards trips "Reassigning the non-component attribute unserved". Gate on
        # `is_region_subset` (a plain bool, never a component) instead.
        if self.is_region_subset:
            self.unserved = Var(self.regions, self.year, within=NonNegativeReals, initialize=0.0)

        # ── CONSTRAINTS ───────────────────────────────────────────────────────

        # (NGMM Eq 18) Supply-curve segment range: 0 ≤ SSTEP_k ≤ QBASE_{k+1} − QBASE_k
        def supply_step_cap_rule(m, r, t, y):
            k = int(t.replace('step', '')) # segment index 1..5
            seg_width = m.QBASE[r, k + 1, y] - m.QBASE[r, k, y]
            return m.sstep[r, t, y] <= seg_width
        self.supply_step_cap_con = Constraint(
            self.regions, self.steps, self.year, rule=supply_step_cap_rule,
        )

        # Backward-compat: total production must not exceed Σ_k segment widths
        # plus QMIN, automatically implied by supply_step_cap above, but kept
        # for explicit integrator-side capacity reads.  Implemented as a Param
        # rather than a constraint to avoid double-counting.
        self.supply_capacity = Param(
            self.regions, self.steps, self.year,
            initialize=lambda m, r, t, y:
                value(m.QBASE[r, int(t.replace('step', '')) + 1, y]
                      - m.QBASE[r, int(t.replace('step', '')), y]),
            mutable=True,
        )

        # (NGMM Eq 19) Tariff-curve segment range: 0 ≤ TAR_k ≤ QTAR_{k+1} − QTAR_k
        def tariff_step_cap_rule(m, o, d, k, y):
            return m.tar_step[o, d, k, y] <= m.QTAR[o, d, k + 1, y] - m.QTAR[o, d, k, y]
        self.tariff_step_cap_con = Constraint(
            self.arcs, self.tariff_segs, self.year, rule=tariff_step_cap_rule,
        )

        # (NGMM Eq 21) Total pipeline flow ≤ arc capacity.  Implied by tariff
        # step caps for the standard 6-segment curve (final breakpoint is at
        # 140 % capacity, allowing virtual capacity-expansion flow), but we keep
        # an explicit cap at 100 % when capacity expansion is disabled.  Skipped
        # by default; uncomment if you want hard capacity cuts.
        # def pipe_total_cap_rule(m, o, d, y):
        #     return m.pipe_flow[o, d, y] <= m.pipe_capacity[o, d]
        # self.pipe_cap_con = Constraint(self.arcs, self.year, rule=pipe_total_cap_rule)

        # (NGMM Eq 20) LNG demand-curve segment range
        def lng_step_cap_rule(m, r, k, y):
            return m.lng_export_step[r, k, y] <= m.QLNG[r, k + 1, y] - m.QLNG[r, k, y]
        self.lng_step_cap_con = Constraint(
            self.lng_regions, self.lng_segs, self.year, rule=lng_step_cap_rule,
        )

        # (legacy) LNG backstop capacity
        def lng_cap_rule(m, region, year):
            return m.lng_import[region, year] <= m.lng_capacity[region]
        self.lng_cap_con = Constraint(self.regions, self.year, rule=lng_cap_rule)

        # Storage injection / withdrawal capacities
        def inject_cap_rule(m, r, y):
            return m.stor_inject[r, y] <= m.storage_inject_cap[r]
        self.inject_cap_con = Constraint(self.regions, self.year, rule=inject_cap_rule)

        def withdraw_cap_rule(m, r, y):
            return m.stor_withdraw[r, y] <= m.storage_withdraw_cap[r]
        self.withdraw_cap_con = Constraint(self.regions, self.year, rule=withdraw_cap_rule)

        # Annual storage balance, net seasonal cycle closes within each year.
        def storage_balance_rule(m, r, y):
            return m.stor_inject[r, y] == m.stor_withdraw[r, y]
        self.storage_balance_con = Constraint(self.regions, self.year, rule=storage_balance_rule)

        # Precompute arc adjacency for the demand-balance closure (NGMM Eq 10, 11).
        _inc: dict = defaultdict(list)
        _out: dict = defaultdict(list)
        for (o, d) in arc_list:
            _inc[d].append((o, d))
            _out[o].append((o, d))

        # (NGMM Eq 10 + 11 combined into the regional demand balance, with the
        # 9-region structure replacing NGMM's separate hub + demand-node layers.
        # The dual of this constraint is the regional citygate gas price.)
        #
        # LHS (sources arriving in region r):
        #     production × (1 − intrastate_loss)        ← Eq 11 f^pip on intra-region
        #   + LNG backstop import
        #   + Canada supply
        #   + Σ pipe_in × (1 − pipe_loss)                ← Eq 11 fuel loss on inbound arcs
        #   + storage withdrawal × (1 − storage_loss)    ← Eq 10 Q^store on withdrawn gas
        #
        # RHS (uses):
        #     Σ_sector demand                           ← Eq 10 CONS_d
        #   + distribution_loss × (residential + commercial)  ← Eq 10 Q^dist_d
        #   + plant_fuel_frac × total demand            ← Eq 10 PLT_d
        #   + Σ pipe_out                                ← Eq 11 outbound to other hubs
        #   + storage injection                         ← Eq 11 hub-to-storage
        #   + Σ_k lng_export_step                       ← Eq 14 hub-to-LNG demand
        #   + var_demand                                 ← integration slack (kept)
        # THE PRICE-FORMING CONSTRAINT. Everything else in the model exists to give this one
        # equality something to balance. Three things to hold in mind reading it:
#
        # 1. It is an EQUALITY, and its dual is the regional gas price, the marginal cost of
        # one more unit delivered into region r in year y. poll_gas_price() reads exactly
        # these duals, and they are what crosses into the electricity model. Relaxing this
        # to an inequality would destroy the price signal, not merely loosen the model.
        # 2. Losses are applied ASYMMETRICALLY, and correctly: gas ARRIVING is derated
        # (inbound pipe flow, storage withdrawal, production leaving the field), gas
        # LEAVING is not (outbound flow, injection). The loss is on delivery, so charging
        # it at both ends would double-count.
        # 3. NGMM splits this across three constraints, supply mass balance (Eq 9), hub flow
        # balance (Eq 11), demand mass balance (Eq 10), because it models supply regions,
        # hubs, and demand regions as separate layers. Here each census division is all
        # three at once, so the three equations merge into this one. The consequence is
        # that there is no separate hub price: this dual does the job NGMM's hub-balance
        # dual does for Henry Hub.
        def demand_balance_rule(m, r, y):
            prod  = m.production_total[r, y]
            lng_b = m.lng_import[r, y]
            pipe_in_eff = quicksum(
                m.pipe_flow[o, d, y] * (1.0 - m.pipe_loss[o, d]) for (o, d) in _inc[r]
            )
            pipe_out = quicksum(m.pipe_flow[o, d, y] for (o, d) in _out[r])
            wd_eff = m.stor_withdraw[r, y] * (1.0 - m.storage_loss[r])
            inj    = m.stor_inject[r, y]

            sector_demand = quicksum(m.demand[r, s, y] for s in m.sectors)
            res_comm = m.demand[r, 'residential', y] + m.demand[r, 'commercial', y]
            dist_loss_term = m.distribution_loss[r] * res_comm
            plant_fuel = m.plant_fuel_frac[r] * sector_demand
            lng_export = m.lng_export_demand[r, y]

            # Unserved demand joins the SUPPLY side for
            # subset runs only (see the declaration above); zero for the full nine-region model.
            unserved = m.unserved[r, y] if m.is_region_subset else 0.0

            return (
                prod * (1.0 - m.intrastate_loss[r])
                + lng_b
                + m.canada_supply[r, y]
                + pipe_in_eff
                + wd_eff
                + unserved
                ==
                sector_demand
                + dist_loss_term
                + plant_fuel
                + pipe_out
                + inj
                + lng_export
                + m.var_demand[r, y]
            )
        self.demand_balance = Constraint(self.regions, self.year, rule=demand_balance_rule)

        # ── OBJECTIVE (NGMM Eq 7) ────────────────────────────────────────────
        # max  consumer_surplus(LNG export) + producer_surplus
        #         - gathering_cost - LNG_backstop_cost - transport_cost
        #         - storage_cost
        #
        # We express the QP as MINIMISE total_cost = (positive costs)
        # − (LNG consumer surplus) so that the integrator's existing
        # convergence checks (which expect a positive scalar) still work, and
        # so that any code that does `meta.obj = ... + ng_model.total_cost`
        # keeps the right sign.  Each piecewise segment contributes
        # PBASE_k · q_k + 0.5 · q_k² · slope_k  (the area under the segment
        # between (QBASE_k, PBASE_k) and (QBASE_{k+1}, PBASE_{k+1})), which is
        # quadratic in the segment volume, the source of the QP non-linearity.
        #
        # Note: the BCF×$/MMBtu factor is bcf_to_mmbtu = 1e3, applied uniformly.

        bcf = self.bcf_to_mmbtu

        # 1) Producer cost, area under the supply curve (subtracted from
        #    surplus = added to total_cost in minimisation form, NGMM Eq 7).
        # Evaluate widths/slopes as
        # numeric values from the breakpoint Params at construction time and
        # skip zero-width segments to avoid ZeroDivisionError on regions with
        # Q0 = 0 (none in current data, but defensive).
        # WHY THIS IS A QP, AND WHY THE SLOPES ARE PYTHON FLOATS.
        # Each segment contributes the trapezoid area under the supply curve between
        # (QBASE_k, PBASE_k) and (QBASE_k+1, PBASE_k+1), which integrates to
        # PBASE_k * q + 0.5 * slope * q^2
        #, linear plus quadratic in the segment volume q. That q^2 is the entire source of
        # the model's non-linearity, and it is why appsi_highs cannot carry this model.
#
        # Note value() on the breakpoints: the widths and slopes are evaluated NUMERICALLY at
        # construction, so they enter the expression as constants and only q stays symbolic.
        # That keeps the Hessian constant and the problem a genuine convex QP rather than a
        # general nonlinear program. Rewriting these to read the Params symbolically would
        # still "work" but would hand the solver a harder problem.
#
        # Note also the k and k+1 reads: SIX breakpoints bound FIVE segments, so every loop
        # here spans a pair. Mixing up breakpoint and segment indexing is the classic
        # off-by-one in this formulation.
        prod_cost = 0
        for r in self.regions:
            for y in self.year:
                for k_seg in range(1, len(SUPPLY_STEP_IDS) + 1):
                    tname = f'step{k_seg}'
                    qb_k_v  = value(self.QBASE[r, k_seg,     y])
                    qb_k1_v = value(self.QBASE[r, k_seg + 1, y])
                    width_v = qb_k1_v - qb_k_v
                    # Zero-width segments are skipped, not divided by. They arise wherever a
                    # region has no capacity of a given type (all breakpoints collapse onto
                    # the same value), and without this guard the slope below is a 0/0.
                    if width_v <= 1e-9:
                        continue
                    pb_k_v  = value(self.PBASE[r, k_seg,     y])
                    pb_k1_v = value(self.PBASE[r, k_seg + 1, y])
                    slope_v = (pb_k1_v - pb_k_v) / width_v
                    q = self.sstep[r, tname, y]
                    prod_cost = prod_cost + (pb_k_v * q + 0.5 * slope_v * q * q) * bcf

        # 2) Gathering charge (NGMM Eq 7, P^gath term)
        gathering_cost = quicksum(
            self.gathering_charge[r] * self.production_total[r, y] * bcf
            for r in self.regions for y in self.year
        )

        # 3) LNG backstop import cost (legacy exogenous import term)
        lng_backstop_cost = quicksum(
            self.lng_import[r, y] * self.lng_cost[r] * bcf
            for r in self.regions for y in self.year
        )

        # 4) Transport cost, area under the pipeline tariff curve (NGMM Eq 7)
        transport_cost = 0
        for (o, d) in arc_list:
            for y in self.year:
                for k_seg in self.tariff_segs:
                    qt_k_v  = value(self.QTAR[o, d, k_seg,     y])
                    qt_k1_v = value(self.QTAR[o, d, k_seg + 1, y])
                    width_v = qt_k1_v - qt_k_v
                    if width_v <= 1e-9:
                        continue
                    pt_k_v  = value(self.PTAR[o, d, k_seg,     y])
                    pt_k1_v = value(self.PTAR[o, d, k_seg + 1, y])
                    slope_v = (pt_k1_v - pt_k_v) / width_v
                    q = self.tar_step[o, d, k_seg, y]
                    transport_cost = transport_cost + (pt_k_v * q + 0.5 * slope_v * q * q) * bcf

        # 5) Storage opex (linear)
        storage_cost = quicksum(
            self.stor_inject[r, y] * self.storage_opex * bcf
            for r in self.regions for y in self.year
        )

        # 6) LNG consumer surplus, area under the LNG export demand curve
        #    (NGMM Eq 7, LNG block).  Demand-curve slope is negative
        #    (PLNG decreases with QLNG), so the quadratic term contributes a
        #    *concave* surplus that we ADD to consumer surplus and therefore
        #    SUBTRACT from total_cost.
        # Skip zero-width LNG segments
        # for (region, year) pairs with no LNG capacity (e.g. pacific 2025,
        # whose LNG_EXPORT_DEMAND_BCF entry is 0).  Without the guard, all
        # QLNG[k] = 0 → /0 in slope computation.
        lng_consumer_surplus = 0
        for r in self.lng_regions:
            for y in self.year:
                for k_seg in self.lng_segs:
                    ql_k_v  = value(self.QLNG[r, k_seg,     y])
                    ql_k1_v = value(self.QLNG[r, k_seg + 1, y])
                    width_v = ql_k1_v - ql_k_v
                    if width_v <= 1e-9:
                        continue
                    pl_k_v  = value(self.PLNG[r, k_seg,     y])
                    pl_k1_v = value(self.PLNG[r, k_seg + 1, y])
                    slope_v = (pl_k1_v - pl_k_v) / width_v
                    q = self.lng_export_step[r, k_seg, y]
                    lng_consumer_surplus = lng_consumer_surplus + (pl_k_v * q + 0.5 * slope_v * q * q) * bcf

        # Price the unserved-demand backstop for subset
        # runs. UNSERVED_PENALTY is ~100x any plausible gas price, so the solver uses it only
        # when the subset cannot source the gas, and the demand-balance dual in a
        # short region comes back at the penalty level, an unmistakable "this subset is
        # supply-short" signal instead of an opaque infeasibility. Zero for the full model.
        unserved_cost = 0
        if self.is_region_subset:
            unserved_cost = quicksum(
                self.unserved[r, y] * UNSERVED_PENALTY * bcf
                for r in self.regions for y in self.year
            )

        # Original objective preserved (unserved_cost is identically 0 for the full model):
        # self.total_cost = Objective(
        #     expr=(prod_cost + gathering_cost + lng_backstop_cost
        #           + transport_cost + storage_cost - lng_consumer_surplus),
        #     sense=minimize)
        # THE OBJECTIVE IS LEGITIMATELY NEGATIVE (about -371.8M at full resolution). The LNG
        # consumer-surplus term is subtracted and dominates the positive cost terms. This is
        # not a sign error: NGMM maximises surplus, and minimising the negative of it is the
        # same optimisation, chosen so an integrator that expects a scalar to minimise, and
        # code that writes `meta.obj = ... + ng_model.total_cost`, both keep working.
#
        # Only prod_cost, transport_cost and lng_consumer_surplus carry quadratic terms; the
        # other three blocks are linear.
        self.total_cost = Objective(
            expr=(prod_cost + gathering_cost + lng_backstop_cost
                  + transport_cost + storage_cost - lng_consumer_surplus
                  + unserved_cost),
            sense=minimize,
        )

    # ── Integration interface ─────────────────────────────────────────────────
#
    # The eight methods below are the ENTIRE surface a Gauss-Seidel loop drives. They work
    # because a handful of Params were declared mutable=True during construction, Q0, P0,
    # demand, and canada_supply. Everything else is fixed once the model is built.
#
    # Inbound: update_demand, update_demand_from_price, update_canada_supply,
    # update_supply_capacity, set_reference_prices
    # Outbound: poll_gas_price (the duals), poll_total_gas_demand
#
    # No method here re-solves. The caller owns the loop and decides when to solve, which is
    # what makes the ordering in docs/COUPLING.md the caller's responsibility rather than
    # something enforced here.

    def set_reference_prices(self, prices: 'dict[GI, float]') -> None:
        """Store solved gas prices as the reference baseline for demand elasticities.

        Call once after the first Gauss-Seidel solve so that subsequent calls
        to ``update_demand_from_price()`` compute demand adjustments relative
        to this equilibrium, not relative to an arbitrary initial price.

        Parameters
        ----------
        prices : dict[GI, float]
            Shadow prices from ``poll_gas_price()`` in $/MMBtu.
        """
        for gi, p in prices.items():
            self._ref_prices[(gi.region, gi.year)] = p
        logger.debug('C-NGMM: reference prices set for %d (region, year) pairs', len(prices))

    def update_demand_from_price(
        self,
        solved_prices: 'dict[GI, float]',
        alpha: float = 1.0,
    ) -> None:
        """Adjust sector demands using own-price elasticities (NEMS NGMM demand blocks).

        For each region, year, and sector:

            demand_new = base_demand × (price / ref_price) ^ elasticity

        where ``base_demand`` is the initial AEO-projected demand (stored at
        construction) and ``ref_price`` is the reference shadow price set by
        ``set_reference_prices()``.  Under-relaxation is applied when alpha < 1.

        Has no effect until ``set_reference_prices()`` has been called (reference
        prices default to empty → price ratio = 1 → no adjustment).

        Parameters
        ----------
        solved_prices : dict[GI, float]
            Current shadow prices from ``poll_gas_price()`` in $/MMBtu.
        alpha : float
            Under-relaxation factor (0 < alpha ≤ 1).
        """
        # SILENT NO-OP IF THE REFERENCE WAS NEVER SET. Deliberate, it lets the first
        # iteration run before any price exists, but it means forgetting the
        # set_reference_prices() call disables price-responsive demand for the whole run with
        # no warning. If the non-electric sectors are not moving between iterations, check
        # this first.
        if not self._ref_prices:
            return  # no reference set yet; skip silently

        n_updated = 0
        for r in self.regions:
            for y in self.year:
                price = solved_prices.get(GI(region=r, year=y))
                if price is None:
                    continue
                ref_p = self._ref_prices.get((r, y))
                if ref_p is None or ref_p < 1e-6:
                    continue
                price_ratio = max(price, 1e-6) / ref_p

                for sector in self.sectors:
                    elas = DEMAND_PRICE_ELASTICITY.get(sector, 0.0)
                    if abs(elas) < 1e-9:
                        continue
                    base_d = self._base_demand.get((r, sector, y), 0.0)
                    new_d = base_d * (price_ratio ** elas)
                    if alpha < 1.0:
                        current = value(self.demand[r, sector, y])
                        new_d = alpha * new_d + (1.0 - alpha) * current
                    self.demand[r, sector, y].set_value(max(new_d, 0.0))
                    n_updated += 1

        logger.debug('C-NGMM.update_demand_from_price: updated %d demand entries (alpha=%.2f)',
                     n_updated, alpha)

    def update_demand(
        self,
        new_demand: dict[GI, float],
        sector: str = 'electric_power',
        alpha: float = 1.0,
    ) -> None:
        """Update a single sector's demand from an external model.

        Called by the integrator to pass electricity-model gas-demand updates.
        When ``alpha < 1`` the update is blended with the current value to
        damp GS oscillations:  qty_new = alpha * qty + (1 - alpha) * qty_old.

        Parameters
        ----------
        new_demand : dict[GI, float]
            {GI(region, year): demand_BCF_per_year}
        sector : str
            Demand sector to update (default: 'electric_power').
        alpha : float
            Under-relaxation factor in (0, 1].  1.0 = full replacement.
        """
        valid_regions = set(self.regions)
        for gi, qty in new_demand.items():
            if gi.region not in valid_regions:
                logger.debug('C-NGMM.update_demand: unknown region %s, skipped', gi.region)
                continue
            if alpha < 1.0:
                current = value(self.demand[gi.region, sector, gi.year])
                qty = alpha * qty + (1.0 - alpha) * current
            self.demand[gi.region, sector, gi.year].set_value(qty)

    def update_canada_supply(self, supply: 'dict[GI, float]') -> None:
        """Update Canadian gas imports by region and year.

        Parameters
        ----------
        supply : dict[GI, float]
            {GI(region, year): supply_BCF_per_year}
        """
        valid_regions = set(self.regions)
        for gi, qty in supply.items():
            if gi.region not in valid_regions:
                logger.debug(
                    'C-NGMM.update_canada_supply: unknown region %s, skipped', gi.region
                )
                continue
            self.canada_supply[gi.region, gi.year].set_value(qty)

    def update_supply_capacity(
        self,
        capacity_updates: dict, # {(region_str, cost_tier_str, year_int): bcf_float}
        alpha: float = 1.0,
    ) -> None:
        """Rebuild the NGMM supply curve from externally supplied capacity.

        THE MIDDLE ELEMENT OF EACH KEY IS DISCARDED. The HSM integrator passes a three-key
        {(region, cost_tier, year): bcf} dict using the legacy low_cost / medium_cost /
        high_cost labels. The NGMM curve is built around a single expected production point
        Q0[r, y], not three independent caps, so this method sums across whatever cost tiers
        appear for a (region, year) and uses only the total. Passing one combined row per
        (region, year) gives an identical result to passing three.

        The tuple shape is retained deliberately, so existing callers keep working; it is not
        evidence that the model represents cost tiers. See the note on `self.steps` in
        __init__ for the NGMM vocabulary.

        Having aggregated to a new Q0, this alpha-blends it against the current Q0, rebuilds
        the breakpoints (QBASE, PBASE) via the NGMM Eq 2-5 helpers, and refreshes the
        step-width ``supply_capacity`` Param. The objective's quadratic supply-cost expression
        picks the new curve up on the next solve.

        Parameters
        ----------
        capacity_updates : dict
            {(region, cost_tier, year): capacity_BCF}. The cost_tier element is ignored;
            values are summed per (region, year).
        alpha : float
            Under-relaxation factor (0 < alpha <= 1.0)
        """
        # Step 1: aggregate to per-(region, year) totals. `_cost_tier` is unused by design --
        # see the docstring above; the underscore marks it as deliberately discarded.
        agg: dict[tuple[str, int], float] = defaultdict(float)
        for (region, _cost_tier, year), cap in capacity_updates.items():
            if region not in self.regions:
                continue
            agg[(region, int(year))] += float(cap)

        if not agg:
            return

        # Step 2: blend new Q0 with current Q0 (under-relaxation) and rebuild
        # the breakpoints for that (region, year) pair.
        crv_below = SUPPLY_CURVE_SHAPE['crv_below']
        crv_above = SUPPLY_CURVE_SHAPE['crv_above']
        elas      = SUPPLY_CURVE_SHAPE['elas']
        qmin_frac = QP_SCALARS.get('supply_curve_qmin_fraction', 0.20)
        rebuilt = 0
        for (region, year), new_q0 in agg.items():
            if alpha < 1.0:
                current_q0 = value(self.Q0[region, year])
                new_q0 = alpha * new_q0 + (1.0 - alpha) * current_q0
            new_q0 = max(new_q0, 1.0)  # numerical floor to keep breakpoints non-degenerate
            self.Q0[region, year].set_value(new_q0)
            self.QMIN[region, year].set_value(qmin_frac * new_q0)

            p0 = value(self.P0[region, year])
            for k in SUPPLY_BREAK_IDS: # 1..6
                self.QBASE[region, k, year].set_value(
                    _supply_qbase(new_q0, k, crv_below, crv_above)
                )
                self.PBASE[region, k, year].set_value(
                    _supply_pbase(p0, k, crv_below, crv_above, elas)
                )
            # Refresh the legacy-shape ``supply_capacity`` Param (segment widths)
            for k_seg in SUPPLY_STEP_IDS: # 1..5
                tname = f'step{k_seg}'
                width = value(self.QBASE[region, k_seg + 1, year]
                              - self.QBASE[region, k_seg, year])
                self.supply_capacity[region, tname, year].set_value(max(width, 0.0))
            rebuilt += 1

        logger.debug(
            'C-NGMM.update_supply_capacity: rebuilt curve for %d (region, year) pairs '
            '(alpha=%.2f)', rebuilt, alpha,
        )

    def poll_gas_price(self) -> dict[GI, float]:
        """Return solved regional gas prices (shadow prices of demand balance).

        Returns
        -------
        dict[GI, float]
            {GI(region, year): gas_price_$/MMBtu}
        """
        prices: dict[GI, float] = {}
        for r in self.regions:
            for y in self.year:
                try:
                    # Dual of the demand_balance constraint.
                    # The LP sign of the dual depends on which side of the
                    # equality is binding (supply-abundant vs demand-deficit
                    # regions get opposite signs in the degenerate dual
                    # solution).  The absolute value recovers the correct
                    # locational marginal price: price spreads between
                    # adjacent regions equal pipeline tariffs exactly.
                    p = self.dual[self.demand_balance[r, y]]
                    prices[GI(region=r, year=y)] = abs(p) / value(self.bcf_to_mmbtu)
                except KeyError:
                    prices[GI(region=r, year=y)] = 0.0
        return prices

    def poll_total_gas_demand(self) -> dict[GI, float]:
        """Return total gas consumption by region and year after solve.

        Returns
        -------
        dict[GI, float]
            {GI(region, year): total_gas_demand_BCF_per_year}
        """
        result: dict[GI, float] = defaultdict(float)
        for r in self.regions:
            for s in self.sectors:
                for y in self.year:
                    result[GI(region=r, year=y)] += value(self.demand[r, s, y])
        return dict(result)

    def attach_results(self) -> None:
        """Populate result DataFrames after an iterative solve.

        Called by the integrator after convergence to make result tables
        available for reporting without re-solving.  Equivalent to the
        table-attachment step inside the standalone ``solve()`` function.
        """
        self.results_production = _extract_production(self)
        self.results_flows      = _extract_flows(self)
        self.results_prices     = _extract_prices(self)
        self.results_storage    = _extract_storage(self)
        self.results_balance    = _extract_balance(self)


###############################################################################
# Solve & Report
###############################################################################

def solve(m: NGModel, solver_name: str | None = None) -> None:
    """Solve the Natural Gas Market Model.

    Switched to a Gurobi-first / HiGHS-fallback
    policy because the NGMM-aligned QP rewrite needs a convex-QP-capable solver,
    and Gurobi is already the standalone-via-meta default (see select_solver()
    in src/integrator/utilities.py).  HiGHS 1.5+ also handles convex QPs.

    Parameters
    ----------
    m : NGModel
        The instantiated (not yet solved) model.
    solver_name : str | None
        If supplied, force this specific Pyomo SolverFactory name.  If None
        (the default), tries 'appsi_gurobi' first and falls back to
        'appsi_highs' if Gurobi is unavailable.
    """
    if solver_name is None:
        # Note ordering:
        #   1. appsi_gurobi: Pyomo's APPSI interface to Gurobi (used by unified.py
        #      already; supports QP and warm starts).
        #   2. gurobi_direct: direct Pyomo→Gurobi interface, fall-through if
        #      APPSI is unavailable.
        # 3. highs: the standalone HiGHS interface, supports convex QP since
        #      HiGHS 1.5.  We use this NOT appsi_highs because Pyomo's APPSI
        #      HiGHS wrapper rejects degree-2 expressions (Pyomo bug, fix not
        #      backported as of v6.10).
        # The original list is all-unavailable
        # in the current `bsky` env (appsi_gurobi/gurobi_direct bindings absent; the ASL
        # 'highs' executable is not installed). Added the classic 'gurobi' interface first
        # (QP-capable and the only working Gurobi binding here) and 'appsi_highs' as a
        # Gurobipy 12.0.1 now installed; prefer in-memory
        # appsi_gurobi for the QP (no LP-file I/O, fast). Old gurobi-first order preserved:
        # candidates = ['gurobi', 'appsi_gurobi', 'gurobi_direct', 'highs', 'appsi_highs']
        # ORDERING MATTERS, do not reorder. The two Gurobi entries lead purely for
        # speed. The critical pair is the last two: 'highs' MUST precede 'appsi_highs'.
#
        # 'appsi_highs' calls generate_standard_repn(quadratic=False) internally, so it raises
        # DegreeError on any quadratic objective, still true in pyomo 6.10.1. 'highs' is the
        # new-generation interface (pyomo >= 6.10) that builds a Hessian and handles a convex
        # QP properly. With this ordering a Gurobi-free environment lands on the interface
        # that works rather than the one that raises, which is why 'appsi_highs' is kept at
        # the end as a last resort rather than removed outright.
#
        # Confirm which was chosen from the log line below, or from HiGHS's own output under
        # tee, which reports "1476 Hessian nonzeros" for the full model.
        candidates = ['appsi_gurobi', 'gurobi_direct', 'gurobi', 'highs', 'appsi_highs']
    else:
        candidates = [solver_name]

    opt = None
    chosen = None
    for cand in candidates:
        try:
            trial = SolverFactory(cand)
            if trial.available(exception_flag=False):
                opt = trial
                chosen = cand
                break
        except Exception as exc:
            logger.debug('C-NGMM: solver %s unavailable (%s)', cand, exc)

    if opt is None:
        raise RuntimeError(
            f'C-NGMM: none of the candidate solvers are available: {candidates}'
        )

    # Tighten solver options for the convex QP rewrite, Gurobi's barrier method
    # is the standard QP path; HiGHS auto-detects QP and uses an interior-point.
    # Apply the Gurobi QP options for the classic
    # 'gurobi' interface too (the available one here), not just appsi_gurobi.
    # Set QP options via the interface-appropriate API
    # (APPSI uses .gurobi_options; classic uses .options). Barrier is the QP path; duals requested.
    if chosen == 'appsi_gurobi':
        opt.gurobi_options['Method'] = 2
        opt.gurobi_options['QCPDual'] = 1
        opt.gurobi_options['BarConvTol'] = 1e-6
    elif chosen in ('gurobi', 'gurobi_direct'):
        opt.options['Method'] = 2          # barrier (default for QP, explicit for safety)
        opt.options['QCPDual'] = 1          # request meaningful duals on the QCP
        opt.options['BarConvTol'] = 1e-6

    logger.info('C-NGMM: solving with %s (QP) …', chosen)
    # No tee= here, so the solver's own output is not shown, and `results` is used for the
    # termination check below and then discarded rather than returned.
    results = opt.solve(m)

    if not check_optimal_termination(results):
        logger.error('C-NGMM: non-optimal solve! Results:\n%s', results)
        raise RuntimeError('NGModel solve did not reach an optimal solution.')

    logger.info('C-NGMM: solve complete, status %s', results.solver.termination_condition)

    # ── attach result tables to the model for reporting ──────────────────────
    m.results_production = _extract_production(m)
    m.results_flows      = _extract_flows(m)
    m.results_prices     = _extract_prices(m)
    m.results_storage    = _extract_storage(m)
    m.results_balance    = _extract_balance(m)


def _extract_production(m: NGModel) -> pd.DataFrame:
    """Report per-step volume and the step's PBASE marginal price (NGMM Eq 1).

    ``cost_per_mmbtu`` is the midpoint price of the step, the average of PBASE_k and
    PBASE_{k+1}, rather than a single flat cost, because the step spans a price range.

    The ``supply_source`` column is not purely a step label: alongside ``step1``..``step5``
    it carries ``lng_import`` (backstop imports) and ``qmin_committed`` (the QMIN production
    floor, NGMM Eq 8). Those two are supply reaching the region without coming off an elastic
    step, which is why the column is named for the source rather than for the step."""
    rows = []
    for r in m.regions:
        for t in m.steps:
            k_seg = int(t.replace('step', ''))
            for y in m.year:
                pb_k  = value(m.PBASE[r, k_seg,     y])
                pb_k1 = value(m.PBASE[r, k_seg + 1, y])
                rows.append({
                    'region':  r,
                    'supply_source':t,
                    'year':    y,
                    'production_bcf': value(m.sstep[r, t, y]),
                    'cost_per_mmbtu': 0.5 * (pb_k + pb_k1),
                })
        for y in m.year:
            lng = value(m.lng_import[r, y])
            if lng > 0.01:
                rows.append({
                    'region':  r,
                    'supply_source':'lng_import',
                    'year':    y,
                    'production_bcf': lng,
                    'cost_per_mmbtu': value(m.lng_cost[r]),
                })
        # QMIN floor (committed production, NGMM Eq 8)
        for y in m.year:
            qmin = value(m.QMIN[r, y])
            if qmin > 0.01:
                rows.append({
                    'region':  r,
                    'supply_source':'qmin_committed',
                    'year':    y,
                    'production_bcf': qmin,
                    'cost_per_mmbtu': value(m.PBASE[r, 1, y]),
                })
    return pd.DataFrame(rows)


def _extract_flows(m: NGModel) -> pd.DataFrame:
    """Pipe_flow is now an Expression
    (sum of tariff-curve segments).  We also report the effective average
    tariff = transport cost on this arc / volume, which captures the
    hurdle-rate behaviour of the QP tariff curve (NGMM Eq 6)."""
    rows = []
    for (o, d) in m.arcs:
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
                    pt_k  = value(m.PTAR[o, d, k_seg,     y])
                    pt_k1 = value(m.PTAR[o, d, k_seg + 1, y])
                    qt_k  = value(m.QTAR[o, d, k_seg,     y])
                    qt_k1 = value(m.QTAR[o, d, k_seg + 1, y])
                    width = qt_k1 - qt_k
                    if width < 1e-9:
                        continue
                    slope = (pt_k1 - pt_k) / width
                    num += pt_k * q + 0.5 * q * q * slope
                eff_tariff = num / flow if flow > 1e-6 else value(m.pipe_tariff[o, d])
                rows.append({
                    'origin':       o,
                    'destination':  d,
                    'year':         y,
                    'flow_bcf':     flow,
                    'capacity_bcf': value(m.pipe_capacity[o, d]),
                    'utilization':  flow / value(m.pipe_capacity[o, d]),
                    'tariff_per_mmbtu':           value(m.pipe_tariff[o, d]),
                    'effective_tariff_per_mmbtu': eff_tariff,
                })
    return pd.DataFrame(rows)


def _extract_prices(m: NGModel) -> pd.DataFrame:
    rows = []
    for r in m.regions:
        for y in m.year:
            try:
                dual_val = m.dual[m.demand_balance[r, y]]
                price    = abs(dual_val) / value(m.bcf_to_mmbtu)
            except KeyError:
                price = float('nan')
            rows.append({'region': r, 'year': y, 'gas_price_per_mmbtu': price})
    return pd.DataFrame(rows)


def _extract_storage(m: NGModel) -> pd.DataFrame:
    rows = []
    for r in m.regions:
        for y in m.year:
            rows.append({
                'region':           r,
                'year':             y,
                'injection_bcf':    value(m.stor_inject[r, y]),
                'withdrawal_bcf':   value(m.stor_withdraw[r, y]),
                'working_cap_bcf':  value(m.storage_working_cap[r]),
            })
    return pd.DataFrame(rows)


def _extract_balance(m: NGModel) -> pd.DataFrame:
    """Regional supply/demand balance table (includes LNG export demand)."""
    # Precompute arc adjacency dicts to avoid O(arcs×regions)
    # scan inside the inner loop.  Each region scan was iterating all 26 arcs twice.
    # Original in-loop scan kept as comments inside the loop below.
    from collections import defaultdict as _dd
    _inc: dict = _dd(list)   # region → [(origin, dest), ...]  arcs arriving at region
    _out: dict = _dd(list)   # region → [(origin, dest), ...]  arcs leaving region
    for (o, d) in m.arcs:
        _inc[d].append((o, d))
        _out[o].append((o, d))

    rows = []
    for r in m.regions:
        for y in m.year:
            # Use the production_total Expression (QMIN floor + step sum, NGMM Eq 8) rather
            # than summing the input cost tiers, which are not a model quantity.
            prod_total  = value(m.production_total[r, y])
            lng_imp     = value(m.lng_import[r, y])
            # Use precomputed adjacency instead of full arc scan
            pipe_in     = sum(value(m.pipe_flow[o, d, y]) for (o, d) in _inc[r])
            pipe_out    = sum(value(m.pipe_flow[o, d, y]) for (o, d) in _out[r])
            stor_wd     = value(m.stor_withdraw[r, y])
            stor_inj    = value(m.stor_inject[r, y])
            total_dem   = sum(value(m.demand[r, s, y]) for s in m.sectors)
            lng_exp     = value(m.lng_export_demand[r, y])
            canada_sup  = value(m.canada_supply[r, y])
            rows.append({
                'region':             r,
                'year':               y,
                'production_bcf':     prod_total,
                'canada_import_bcf':  canada_sup,
                'lng_import_bcf':     lng_imp,
                'pipe_inflow_bcf':    pipe_in,
                'pipe_outflow_bcf':   pipe_out,
                'stor_withdrawal':    stor_wd,
                'stor_injection':     stor_inj,
                'total_sector_demand_bcf': total_dem,
                'lng_export_bcf':     lng_exp,
                'net_supply_bcf':     prod_total + canada_sup + lng_imp + pipe_in
                                      - pipe_out + stor_wd - stor_inj,
            })
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
    sep = '-' * 70

    print(f'\n{sep}')
    print(' C-NGMM Results Summary')
    print(sep)

    # Aggregate production by year
    prod_yr = (
        m.results_production.groupby('year')['production_bcf'].sum().reset_index()
    )
    print('\n  Total US Production + LNG Imports [BCF/year]:')
    for _, row in prod_yr.iterrows():
        print(f"    {int(row['year'])}: {row['production_bcf']:,.0f} BCF")

    # Prices by region and year
    print('\n  Regional Wellhead/Citygate Gas Price [$/MMBtu]:')
    pivot = m.results_prices.pivot(index='region', columns='year', values='gas_price_per_mmbtu')
    pivot.index = [REGION_LABELS.get(r, r) for r in pivot.index]
    print(pivot.round(2).to_string())

    # LNG export demand by year (new)
    if 'lng_export_bcf' in m.results_balance.columns:
        lng_exp_yr = m.results_balance.groupby('year')['lng_export_bcf'].sum()
        print('\n  US LNG Export Demand [BCF/year]:')
        for yr, bcf in lng_exp_yr.items():
            print(f'    {int(yr)}: {bcf:,.0f} BCF  ({bcf/365:.1f} BCF/day)')

    # Most congested pipelines
    if not m.results_flows.empty:
        top_pipes = (
            m.results_flows.sort_values('utilization', ascending=False)
            .head(5)[['origin', 'destination', 'year', 'flow_bcf', 'utilization']]
        )
        print('\n  Top-5 Most Congested Pipeline Corridors:')
        print(top_pipes.to_string(index=False))

    print(f'\n{sep}\n')

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        m.results_production.to_csv(output_dir / 'ng_production.csv', index=False)
        m.results_flows.to_csv(output_dir / 'ng_pipeline_flows.csv', index=False)
        m.results_prices.to_csv(output_dir / 'ng_prices.csv', index=False)
        m.results_storage.to_csv(output_dir / 'ng_storage.csv', index=False)
        m.results_balance.to_csv(output_dir / 'ng_regional_balance.csv', index=False)
        print(f'  Output CSVs written to: {output_dir}')


###############################################################################
# CLI entry point
###############################################################################

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description='C-NGMM natural gas market model, standalone runner'
    )
    p.add_argument(
        '--years', nargs='+', type=int,
        default=[2025, 2030, 2035, 2040, 2045, 2050],
        help='Planning years to include in the optimisation.',
    )
    # Region subsetting for standalone runs, the
    # regional counterpart to --years. Default None = all nine census divisions (unchanged).
    p.add_argument(
        '--regions', nargs='+', type=str, default=None,
        metavar='REGION',
        help=('Census divisions to include (default: all nine). Valid: '
              + ', '.join(REGIONS) + '. Subsets keep only arcs internal to the selection and '
              'enable a penalized unserved-demand backstop, so a net-importing subset stays '
              'feasible; any unserved volume is reported.'),
    )
    p.add_argument(
        # None default lets solve() try Gurobi
        # first and fall back to HiGHS for the QP rewrite.
        '--solver', type=str, default=None,
        choices=['appsi_highs', 'appsi_gurobi', 'gurobi', 'highs'],
        help='Pyomo solver to use (default: auto, Gurobi first, fall back to HiGHS).',
    )
    p.add_argument(
        '--output', type=str, default=None,
        help='Directory for CSV output files (default: print only).',
    )
    p.add_argument(
        '--debug', action='store_true',
        help='Enable DEBUG-level logging.',
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s %(levelname)-8s %(name)s, %(message)s',
        datefmt='%H:%M:%S',
    )

    # logger.info('Building Natural Gas Market Model for years: %s', args.years)
    # m = NGModel(years=args.years, mode='standard')
    logger.info('Building Natural Gas Market Model for years: %s | regions: %s',
                args.years, args.regions or 'all 9')
    m = NGModel(years=args.years, regions=args.regions, mode='standard')

    logger.info('Model constructed, %d regions, %d arcs, %d variables, %d constraints',
                len(m.regions), len(m.arcs),
                sum(1 for _ in m.component_data_objects(Var)),
                sum(1 for _ in m.component_data_objects(Constraint)))
    if m.is_region_subset:
        logger.warning(
            'REGION SUBSET (%d of %d): results are NOT comparable to a full run, dropped '
            'regions take their production, demand, and trade with them. For mechanics and '
            'timing tests only.', len(m.regions), len(REGIONS))

    solve(m, solver_name=args.solver)

    # Surface any unserved demand loudly. A subset that
    # cannot source its own gas still solves (via the backstop), so without this the shortfall
    # would sit silently inside the objective and quietly distort the reported prices.
    if m.is_region_subset:
        tot_uns = sum(value(m.unserved[r, y]) for r in m.regions for y in m.year)
        if tot_uns > 1e-6:
            logger.warning('UNSERVED DEMAND: %.1f BCF total, this subset cannot source its own '
                           'gas; regional prices are set by the backstop penalty, not by the '
                           'market. Add the supplying region(s) for meaningful prices.', tot_uns)
            for r in m.regions:
                for y in m.year:
                    u = value(m.unserved[r, y])
                    if u > 1e-6:
                        logger.warning('    unserved %-20s %d: %10.1f BCF', r, y, u)
        else:
            logger.info('Unserved demand: none, this subset is self-sufficient.')

    output_dir = Path(args.output) if args.output else None
    report(m, output_dir=output_dir)

    # Quick demonstration of integration interface
    prices = m.poll_gas_price()
    logger.info(
        'Sample gas prices (2025): WSC=%.2f, MTN=%.2f, NE=%.2f $/MMBtu',
        prices.get(GI('west_south_central', 2025), float('nan')),
        prices.get(GI('mountain', 2025), float('nan')),
        prices.get(GI('new_england', 2025), float('nan')),
    )


if __name__ == '__main__':
    main()
