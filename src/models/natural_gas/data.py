"""CSV-backed parameter loader for the C-NGMM.

Reads all numerical parameters from ``input/natural_gas/`` CSV files.
If a file is missing or malformed, a warning is logged and the hardcoded
fallback constants defined here are used transparently.

Extracted from ng_model.py module-level
constants so that supply curves, demand tables, pipelines, storage, and LNG
assumptions are traceable to versioned data files rather than embedded code.

CSV files (all in ``input/natural_gas/``):
    ng_supply_cost_tiers.csv → SUPPLY_COST_TIERS
    ng_lng_import.csv        → LNG_IMPORT
    ng_lng_export.csv        → LNG_EXPORT_DEMAND_BCF
    ng_demand_elasticity.csv → DEMAND_PRICE_ELASTICITY
    ng_base_demand.csv       → BASE_DEMAND_2025
    ng_demand_growth.csv     → DEMAND_GROWTH_RATES
    ng_pipeline_arcs.csv     → PIPELINE_ARCS_RAW
    ng_storage.csv           → STORAGE
    ng_scalars.csv           → STORAGE_OPEX, gathering_charge_avg,
                                lng_world_price_per_mmbtu, lng_max_price_factor,
                                pipe_fuel_loss_default,
                                distribution_loss_default, intrastate_loss_default,
                                plant_fuel_fraction_default, storage_loss_default,
                                supply_curve_qmin_fraction (scalars)

Added NGMM AEO2025-aligned parameters for
the QP rewrite of ng_model.py:
    ng_supply_curve_shape.csv → SUPPLY_CURVE_SHAPE (ELAS, CRV per step; AEO 2022
                                 footnote: ELAS = [0.8, 0.7, 0.5, 0.3, 0.2])
    ng_tariff_curve_shape.csv → TARIFF_CURVE_SHAPE (utilisation breakpoints and
                                 tariff multipliers; NGMM Fig 3.5 hurdle-rate)
    ng_lng_demand_curve.csv   → LNG_DEMAND_CURVE_SHAPE (NGMM Fig 3.6 linear
                                 demand curve down from LNG_MAX to zero at 0)
    ng_losses.csv             → LOSSES (per-region distribution, intrastate,
                                 plant-fuel, storage-loss; NGMM Eq 10, 11)
    ng_gathering.csv          → GATHERING_CHARGES (per-region $/MMBtu;
                                 NGMM Eq 7 P^gath term)
    ng_pipe_loss.csv          → PIPE_LOSS (per-arc fraction; NGMM Eq 11 f^pip)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default data directory
# ---------------------------------------------------------------------------

# DEPTH-SENSITIVE. This file sits at src/models/natural_gas/data.py, so parents[3] walks up
# natural_gas -> models -> src -> <repo root>, giving <repo root>/input/natural_gas.
#
# Move this file to a different directory depth and the path still resolves, to somewhere
# that does not exist. Every load then falls back, no exception is raised, and the model
# solves on fallback constants throughout: plausible numbers, silently wrong provenance. If
# you relocate the module, this line must be updated in the same commit.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / 'input' / 'natural_gas'

# ---------------------------------------------------------------------------
# Hardcoded fallback constants (original values from ng_model.py)
# These are used verbatim if the corresponding CSV file cannot be read.
#
# Two reasons they exist rather than letting a missing file raise:
# 1. the model stays runnable with an incomplete input set, so you can bisect what is
# missing instead of being blocked at the first absent file;
# 2. six of these groups ship with NO CSV at all in this distribution, so the fallbacks
# are the live values, not merely emergency defaults, see the module docstring.
#
# The cost of that design is that a typo'd filename is indistinguishable from a file that is
# absent on purpose. Both log the same warning. The INFO lines emitted on the success path
# ("SUPPLY_COST_TIERS loaded from CSV (9 regions)") are the more reliable signal: ten of those
# should appear on a healthy run.
# ---------------------------------------------------------------------------

_REGIONS_FALLBACK = [
    'new_england', 'middle_atlantic', 'east_north_central', 'west_north_central',
    'south_atlantic', 'east_south_central', 'west_south_central', 'mountain', 'pacific',
]

_SUPPLY_COST_TIERS_FALLBACK = {
    'new_england':        [(  60,  3.50), (  30,  5.00), (  10,  7.50)],
    'middle_atlantic':    [(5000,  1.80), (5500,  2.30), (3000,  3.20)],
    'east_north_central': [( 500,  2.40), ( 400,  3.50), ( 150,  5.00)],
    'west_north_central': [( 900,  2.20), ( 700,  3.20), ( 300,  4.80)],
    'south_atlantic':     [( 250,  3.00), ( 200,  4.50), ( 100,  6.50)],
    'east_south_central': [( 450,  2.50), ( 350,  3.60), ( 150,  5.20)],
    'west_south_central': [(8500,  1.40), (8000,  1.90), (4500,  2.80)],
    'mountain':           [(4500,  1.80), (3500,  2.40), (2000,  3.40)],
    'pacific':            [( 600,  2.80), ( 450,  3.80), ( 200,  5.80)],
}

_LNG_IMPORT_FALLBACK = {
    'new_england':    (350, 8.00),
    'south_atlantic': (300, 7.50),
    'pacific':        (200, 8.50),
}

_LNG_EXPORT_DEMAND_BCF_FALLBACK: dict[str, dict[int, float]] = {
    'west_south_central': {2025: 4300, 2030: 5100, 2035: 5700, 2040: 6200, 2045: 6700, 2050: 7200},
    'south_atlantic':     {2025:  480, 2030:  560, 2035:  620, 2040:  650, 2045:  700, 2050:  730},
    'pacific':            {2025:    0, 2030:   80, 2035:  200, 2040:  350, 2045:  500, 2050:  650},
}

_DEMAND_PRICE_ELASTICITY_FALLBACK = {
    'electric_power': -0.15,
    'industrial':     -0.20,
    'residential':    -0.10,
    'commercial':     -0.10,
    'transportation': -0.05,
}

_BASE_DEMAND_2025_FALLBACK: dict[str, dict[str, float]] = {
    'new_england':        {'electric_power':  215, 'industrial':  290, 'residential':  395, 'commercial':  360, 'transportation':   55},
    'middle_atlantic':    {'electric_power':  810, 'industrial': 1180, 'residential':  795, 'commercial':  620, 'transportation':  110},
    'east_north_central': {'electric_power': 1250, 'industrial': 1820, 'residential':  710, 'commercial':  530, 'transportation':  160},
    'west_north_central': {'electric_power':  820, 'industrial':  810, 'residential':  415, 'commercial':  305, 'transportation':  115},
    'south_atlantic':     {'electric_power': 2050, 'industrial': 1010, 'residential':  620, 'commercial':  510, 'transportation':  155},
    'east_south_central': {'electric_power':  830, 'industrial':  620, 'residential':  315, 'commercial':  210, 'transportation':   60},
    'west_south_central': {'electric_power': 3520, 'industrial': 3050, 'residential':  510, 'commercial':  415, 'transportation':  260},
    'mountain':           {'electric_power': 1430, 'industrial':  820, 'residential':  415, 'commercial':  305, 'transportation':  115},
    'pacific':            {'electric_power': 1320, 'industrial':  510, 'residential':  415, 'commercial':  360, 'transportation':   60},
}

_DEMAND_GROWTH_RATES_FALLBACK = {
    'electric_power':  0.004,
    'industrial':      0.008,
    'residential':    -0.005,
    'commercial':     -0.003,
    'transportation':  0.025,
}

_PIPELINE_ARCS_RAW_FALLBACK = [
    ('new_england',        'middle_atlantic',    1400, 0.55),
    ('middle_atlantic',    'new_england',        1400, 0.55),
    ('middle_atlantic',    'east_north_central', 2200, 0.40),
    ('east_north_central', 'middle_atlantic',    2200, 0.40),
    ('middle_atlantic',    'south_atlantic',     3000, 0.35),
    ('south_atlantic',     'middle_atlantic',    3000, 0.35),
    ('middle_atlantic',    'east_south_central',  800, 0.45),
    ('east_south_central', 'middle_atlantic',     800, 0.45),
    ('east_north_central', 'west_north_central', 1600, 0.30),
    ('west_north_central', 'east_north_central', 1600, 0.30),
    ('east_north_central', 'south_atlantic',      600, 0.40),
    ('south_atlantic',     'east_north_central',  600, 0.40),
    ('west_north_central', 'west_south_central', 2100, 0.35),
    ('west_south_central', 'west_north_central', 2100, 0.35),
    ('west_north_central', 'mountain',           1100, 0.30),
    ('mountain',           'west_north_central', 1100, 0.30),
    ('south_atlantic',     'east_south_central', 2200, 0.25),
    ('east_south_central', 'south_atlantic',     2200, 0.25),
    ('east_south_central', 'west_south_central', 3200, 0.20),
    ('west_south_central', 'east_south_central', 3200, 0.20),
    ('west_south_central', 'mountain',           3800, 0.30),
    ('mountain',           'west_south_central', 3800, 0.30),
    ('mountain',           'pacific',            4200, 0.40),
    ('pacific',            'mountain',           4200, 0.40),
    ('west_south_central', 'south_atlantic',     1500, 0.40),
    ('south_atlantic',     'west_south_central', 1500, 0.40),
]

_STORAGE_FALLBACK = {
    'new_england':        {'working': 180,  'inject':  60,  'withdraw':  90},
    'middle_atlantic':    {'working': 420,  'inject': 140,  'withdraw': 210},
    'east_north_central': {'working': 620,  'inject': 210,  'withdraw': 310},
    'west_north_central': {'working': 510,  'inject': 170,  'withdraw': 255},
    'south_atlantic':     {'working': 340,  'inject': 115,  'withdraw': 170},
    'east_south_central': {'working': 160,  'inject':  55,  'withdraw':  80},
    'west_south_central': {'working': 850,  'inject': 285,  'withdraw': 425},
    'mountain':           {'working': 360,  'inject': 120,  'withdraw': 180},
    'pacific':            {'working': 160,  'inject':  55,  'withdraw':  80},
}

_STORAGE_OPEX_FALLBACK: float = 0.18  # $/MMBtu

# ---------------------------------------------------------------------------
# NGMM AEO2025-aligned fallbacks
# ---------------------------------------------------------------------------

# Supply-curve shape parameters (NGMM Eq 1-5, Fig 3.4).
# Five elastic segments built around an expected (Q0, P0) anchor point.
# crv_below[k]: percentage drop in QBASE from the anchor for the k-th step below Q0
# crv_above[k]: percentage rise in QBASE from the anchor for the k-th step above Q0
# elas[k]:      step elasticity of supply (AEO 2022 footnote values, segments 1-5)
# A 6-breakpoint curve forms 5 segments. Breakpoints are:
#   QBASE_1 = Q0 * (1 - crv_below[3]) * (1 - crv_below[2]) * (1 - crv_below[1])
#   QBASE_2 = Q0 * (1 - crv_below[3]) * (1 - crv_below[2])
#   QBASE_3 = Q0 * (1 - crv_below[3])
#   QBASE_4 = Q0 * (1 + crv_above[1])
#   QBASE_5 = Q0 * (1 + crv_above[1]) * (1 + crv_above[2])
#   QBASE_6 = Q0 * (1 + crv_above[1]) * (1 + crv_above[2]) * (1 + crv_above[3])
# PBASE breakpoints use the same products of (1 +/- crv)/elas (NGMM Eq 3, 5).
_SUPPLY_CURVE_SHAPE_FALLBACK: dict = {
    'crv_below': [0.30, 0.15, 0.05],   # steps 1, 2, 3 below Q0
    'crv_above': [0.05, 0.15, 0.30],   # steps 4, 5, 6 above Q0
    'elas':      [0.8, 0.7, 0.5, 0.3, 0.2],  # 5 segment elasticities (AEO 2022)
}

# Pipeline tariff curve (NGMM Fig 3.5). Six breakpoints in capacity-utilisation
# space define five segments; multipliers are relative to the flat base tariff.
# The tariff rises slowly up to ~80 % utilisation, then sharply approaching 100 %
# (hurdle-rate behavior), and the >100 % step represents capacity that could be
# built in a capacity-expansion run.
_TARIFF_CURVE_SHAPE_FALLBACK: dict = {
    'util_break':     [0.00, 0.20, 0.60, 0.80, 0.95, 1.00, 1.40],  # 7 breakpoints, 6 segments
    'tariff_mult':    [0.40, 0.55, 0.75, 0.95, 1.50, 3.00, 3.50],  # multiplier on base tariff
}

# LNG export demand curve (NGMM Fig 3.6). A linear demand curve in (Q, P) space:
# at Q=Q_capacity, P = world LNG price; at Q=0, P = lng_max_price_factor × world price.
# Three steps give a coarse PL approximation of the linear curve.
_LNG_DEMAND_CURVE_SHAPE_FALLBACK: dict = {
    'q_frac':         [0.00, 0.50, 0.85, 1.00],  # fraction of capacity at each breakpoint
    'p_factor':       [2.00, 1.50, 1.10, 1.00],  # factor on world LNG price
    'world_price':    7.00,                       # $/MMBtu, AEO 2025 reference
    'max_factor':     2.00,                       # P at Q=0 = max_factor × world_price
}

# Per-region loss fractions and plant-fuel use (NGMM Eq 10, 11). Distribution
# loss applies to residential + commercial volumes at delivery (~0.8 % typical
# LDC unaccounted-for-gas). Intrastate loss represents short-haul pipeline
# losses on volumes produced and consumed within the same census division
# (~0.3 %). Storage loss is fraction of cycled storage volume (~0.5 %).
# Plant fuel is the fixed BCF/yr consumed in lease + processing-plant operations
# (~3 % of throughput, applied as a fraction of total sector demand).
_LOSSES_FALLBACK: dict[str, dict[str, float]] = {
    r: {
        'distribution_loss': 0.008,
        'intrastate_loss':   0.003,
        'storage_loss':      0.005,
        'plant_fuel_frac':   0.030,
    }
    for r in _REGIONS_FALLBACK
}

# Gathering charge ($/MMBtu) per supply region, first-mile cost of moving gas
# from wellhead to the regional hub (NGMM Eq 7 P^gath term). Higher in the
# remote / steep-terrain regions (Mountain), lower in the Gulf Coast.
_GATHERING_CHARGES_FALLBACK: dict[str, float] = {
    'new_england':        0.30,
    'middle_atlantic':    0.25,
    'east_north_central': 0.20,
    'west_north_central': 0.20,
    'south_atlantic':     0.25,
    'east_south_central': 0.22,
    'west_south_central': 0.15,
    'mountain':           0.35,
    'pacific':            0.30,
}

# Pipeline fuel-loss fraction per directed arc (NGMM Eq 11 f^pip). Default
# value (~0.5 % per long-haul corridor) applies unless an arc-specific value
# is supplied via ng_pipe_loss.csv.
_PIPE_LOSS_DEFAULT_FALLBACK: float = 0.005

# Other QP scalars used by the rewrite. Surfaced through ng_scalars.csv keys.
_QP_SCALARS_FALLBACK: dict[str, float] = {
    'gathering_charge_avg':       0.25,
    'lng_world_price_per_mmbtu':  7.00,
    'lng_max_price_factor':       2.00,
    'pipe_fuel_loss_default':     0.005,
    'distribution_loss_default':  0.008,
    'intrastate_loss_default':    0.003,
    'plant_fuel_fraction_default':0.030,
    'storage_loss_default':       0.005,
    'supply_curve_qmin_fraction': 0.20,   # QMIN = qmin_fraction × Q0 (committed wells)
}

# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------

def _csv(filename: str, data_dir: Path) -> pd.DataFrame | None:
    """Read a CSV from data_dir, skipping comment lines starting with '#'.
    Returns None and logs a warning on any failure.

    The single I/O primitive every loader below goes through, so the missing-file policy is
    defined in exactly one place. Two behaviours worth knowing:

    * ``comment='#'`` drops the provenance header each input carries (source, units, vintage),
      so those headers can be edited freely without affecting parsing.
    * The bare ``except Exception`` is deliberate: a malformed CSV must
      degrade to the fallback exactly as an absent one does. Narrowing it to ParserError would
      let an encoding error or a permissions failure propagate, which is precisely the
      behaviour this loader is designed to avoid.

    Note the asymmetry that follows from that: this returns None for BOTH "no such file" and
    "file exists but is broken". The log message distinguishes them; the return value does not.
"""
    path = data_dir / filename
    try:
        df = pd.read_csv(path, comment='#')
        # Strip header whitespace so a hand-edited ' region' still matches lookups on 'region'.
        df.columns = df.columns.str.strip()
        return df
    except FileNotFoundError:
        logger.warning('NG data file not found: %s, using fallback', path)
        return None
    except Exception as exc:
        logger.warning('Could not read %s (%s), using fallback', path, exc)
        return None


def load_supply_cost_tiers(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, list[tuple[float, float]]]:
    """Load SUPPLY_COST_TIERS from ng_supply_cost_tiers.csv.

    Returns {region: [(capacity_bcf, cost_per_mmbtu), ...]} in low/medium/high cost order.

    These three cost tiers have NO counterpart in NGMM. NGMM's supply dimension is
    ``(suptype, qps)``, supply type by supply region, where suptype separates
    associated-dissolved from nonassociated gas, and its elastic pieces are *steps*
    (``SSTEP``, NGMM Eq 8). The tiers here are purely an artifact of this input format:
    ng_model.NGModel.__init__ collapses them immediately into a single anchor point,

        Q0 = sum of capacity P0 = capacity-weighted mean cost

    and then builds five NGMM *steps* around that anchor. So ``sstep[r, 'step3', y]`` has no
    relationship to ``high_cost``; the tiers survive only as a way of expressing the anchor.
"""
    df = _csv('ng_supply_cost_tiers.csv', data_dir)
    if df is None:
        return dict(_SUPPLY_COST_TIERS_FALLBACK)
    # Fixed order, not the CSV's row order: the anchor is a weighted mean, so ordering does not
    # affect Q0/P0, but a stable order keeps the loaded structure comparable run to run.
    cost_tier_order = ['low_cost', 'medium_cost', 'high_cost']
    result: dict[str, list] = {}
    for region, grp in df.groupby('region'):
        grp = grp.set_index('cost_tier')
        row = []
        for t in cost_tier_order:
            if t in grp.index:
                row.append((float(grp.at[t, 'capacity_bcf']), float(grp.at[t, 'cost_per_mmbtu'])))
        if row:
            result[str(region)] = row
    if not result:
        logger.warning('ng_supply_cost_tiers.csv yielded empty result, using fallback')
        return dict(_SUPPLY_COST_TIERS_FALLBACK)
    logger.info('SUPPLY_COST_TIERS loaded from CSV (%d regions)', len(result))
    return result


def load_supply_anchors(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[tuple[str, int], tuple[float, float]]:
    """Optional year-varying supply-curve anchor
    path from ng_supply_anchors.csv: {(region, year): (q0_mult, p0_mult)}, multipliers on the static
    cost-tier-derived (Q0, P0) anchors (harness/build_ng_anchor_path.py; AEO Table 59 production +
    regional supply-price paths, normalized to 2025). Missing file or row -> {} / no entry, and the
    model falls back to multiplier 1.0 = the previous static-curve behaviour."""
    df = _csv('ng_supply_anchors.csv', data_dir)
    if df is None:
        return {}
    try:
        out = {(str(r.region), int(r.year)): (float(r.q0_mult), float(r.p0_mult))
               for r in df.itertuples()}
        logger.info('SUPPLY_ANCHORS loaded from CSV (%d region-years)', len(out))
        return out
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning('ng_supply_anchors.csv malformed (%s), ignoring (static anchors)', exc)
        return {}


def load_lng_import(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, tuple[float, float]]:
    """Load LNG_IMPORT from ng_lng_import.csv."""
    df = _csv('ng_lng_import.csv', data_dir)
    if df is None:
        return dict(_LNG_IMPORT_FALLBACK)
    result = {
        str(row['region']): (float(row['capacity_bcf']), float(row['cost_per_mmbtu']))
        for _, row in df.iterrows()
    }
    if not result:
        return dict(_LNG_IMPORT_FALLBACK)
    logger.info('LNG_IMPORT loaded from CSV (%d terminals)', len(result))
    return result


def load_lng_export(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[int, float]]:
    """Load LNG_EXPORT_DEMAND_BCF from ng_lng_export.csv."""
    df = _csv('ng_lng_export.csv', data_dir)
    if df is None:
        return {k: dict(v) for k, v in _LNG_EXPORT_DEMAND_BCF_FALLBACK.items()}
    result: dict[str, dict[int, float]] = {}
    for _, row in df.iterrows():
        region = str(row['region'])
        result.setdefault(region, {})[int(row['year'])] = float(row['demand_bcf'])
    if not result:
        return {k: dict(v) for k, v in _LNG_EXPORT_DEMAND_BCF_FALLBACK.items()}
    logger.info('LNG_EXPORT_DEMAND_BCF loaded from CSV (%d region-year pairs)',
                sum(len(v) for v in result.values()))
    return result


def load_demand_elasticity(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load DEMAND_PRICE_ELASTICITY from ng_demand_elasticity.csv."""
    df = _csv('ng_demand_elasticity.csv', data_dir)
    if df is None:
        return dict(_DEMAND_PRICE_ELASTICITY_FALLBACK)
    result = {str(row['sector']): float(row['own_price_elasticity']) for _, row in df.iterrows()}
    if not result:
        return dict(_DEMAND_PRICE_ELASTICITY_FALLBACK)
    logger.info('DEMAND_PRICE_ELASTICITY loaded from CSV')
    return result


def load_base_demand(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[str, float]]:
    """Load BASE_DEMAND_2025 from ng_base_demand.csv."""
    df = _csv('ng_base_demand.csv', data_dir)
    if df is None:
        return {k: dict(v) for k, v in _BASE_DEMAND_2025_FALLBACK.items()}
    result: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        region = str(row['region'])
        result.setdefault(region, {})[str(row['sector'])] = float(row['demand_bcf_2025'])
    if not result:
        return {k: dict(v) for k, v in _BASE_DEMAND_2025_FALLBACK.items()}
    logger.info('BASE_DEMAND_2025 loaded from CSV (%d region-sector pairs)',
                sum(len(v) for v in result.values()))
    return result


def load_demand_growth(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load DEMAND_GROWTH_RATES from ng_demand_growth.csv."""
    df = _csv('ng_demand_growth.csv', data_dir)
    if df is None:
        return dict(_DEMAND_GROWTH_RATES_FALLBACK)
    result = {str(row['sector']): float(row['annual_growth_rate']) for _, row in df.iterrows()}
    if not result:
        return dict(_DEMAND_GROWTH_RATES_FALLBACK)
    logger.info('DEMAND_GROWTH_RATES loaded from CSV')
    return result


def load_pipeline_arcs(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> list[tuple[str, str, float, float]]:
    """Load PIPELINE_ARCS_RAW from ng_pipeline_arcs.csv."""
    df = _csv('ng_pipeline_arcs.csv', data_dir)
    if df is None:
        return list(_PIPELINE_ARCS_RAW_FALLBACK)
    result = [
        (str(row['origin']), str(row['destination']),
         float(row['capacity_bcf']), float(row['tariff_per_mmbtu']))
        for _, row in df.iterrows()
    ]
    if not result:
        return list(_PIPELINE_ARCS_RAW_FALLBACK)
    logger.info('PIPELINE_ARCS_RAW loaded from CSV (%d directed arcs)', len(result))
    return result


def load_storage(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[str, float]]:
    """Load STORAGE from ng_storage.csv."""
    df = _csv('ng_storage.csv', data_dir)
    if df is None:
        return {k: dict(v) for k, v in _STORAGE_FALLBACK.items()}
    result = {
        str(row['region']): {
            'working':  float(row['working_cap_bcf']),
            'inject':   float(row['inject_cap_bcf_yr']),
            'withdraw': float(row['withdraw_cap_bcf_yr']),
        }
        for _, row in df.iterrows()
    }
    if not result:
        return {k: dict(v) for k, v in _STORAGE_FALLBACK.items()}
    logger.info('STORAGE loaded from CSV (%d regions)', len(result))
    return result


def load_storage_opex(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> float:
    """Load STORAGE_OPEX scalar from ng_scalars.csv."""
    df = _csv('ng_scalars.csv', data_dir)
    if df is None:
        return _STORAGE_OPEX_FALLBACK
    try:
        df = df.set_index('parameter')
        val = float(df.at['storage_opex', 'value'])
        logger.info('STORAGE_OPEX loaded from CSV: %.4f', val)
        return val
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning('Could not read storage_opex from ng_scalars.csv (%s), using fallback', exc)
        return _STORAGE_OPEX_FALLBACK


# ---------------------------------------------------------------------------
# NGMM AEO2025 parameter loaders
# ---------------------------------------------------------------------------

def load_supply_curve_shape(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict:
    """Load NGMM-style supply-curve shape (ELAS per segment, CRV per breakpoint).

    CSV format (ng_supply_curve_shape.csv), one row per step:
        side, step, crv, elas
    where side in {'below', 'above'} and step in {1, 2, 3}. The 'elas' column
    gives the segment elasticity (5 values: segments 1-5 left-to-right).
    """
    df = _csv('ng_supply_curve_shape.csv', data_dir)
    if df is None:
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _SUPPLY_CURVE_SHAPE_FALLBACK.items()}
    try:
        crv_below = sorted(
            [(int(r['step']), float(r['crv'])) for _, r in df.iterrows() if r['side'] == 'below'],
            key=lambda t: t[0],
        )
        crv_above = sorted(
            [(int(r['step']), float(r['crv'])) for _, r in df.iterrows() if r['side'] == 'above'],
            key=lambda t: t[0],
        )
        elas_rows = sorted(
            [(int(r['step']), float(r['elas'])) for _, r in df.iterrows()
             if pd.notna(r.get('elas', None))],
            key=lambda t: t[0],
        )
        result = {
            'crv_below': [v for _, v in crv_below[:3]],
            'crv_above': [v for _, v in crv_above[:3]],
            'elas':      [v for _, v in elas_rows[:5]],
        }
        if len(result['crv_below']) != 3 or len(result['crv_above']) != 3 or len(result['elas']) != 5:
            raise ValueError('supply-curve shape CSV did not yield 3/3/5 entries')
        logger.info('SUPPLY_CURVE_SHAPE loaded from CSV')
        return result
    except (KeyError, ValueError) as exc:
        logger.warning('Could not parse ng_supply_curve_shape.csv (%s), using fallback', exc)
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _SUPPLY_CURVE_SHAPE_FALLBACK.items()}


def load_tariff_curve_shape(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict:
    """Load NGMM pipeline-tariff curve shape (NGMM Fig 3.5)."""
    df = _csv('ng_tariff_curve_shape.csv', data_dir)
    if df is None:
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _TARIFF_CURVE_SHAPE_FALLBACK.items()}
    try:
        rows = sorted(
            [(float(r['util_break']), float(r['tariff_mult'])) for _, r in df.iterrows()],
            key=lambda t: t[0],
        )
        result = {
            'util_break':  [u for u, _ in rows],
            'tariff_mult': [m for _, m in rows],
        }
        if len(result['util_break']) < 3:
            raise ValueError('need at least 3 breakpoints in tariff curve')
        logger.info('TARIFF_CURVE_SHAPE loaded from CSV (%d breakpoints)', len(rows))
        return result
    except (KeyError, ValueError) as exc:
        logger.warning('Could not parse ng_tariff_curve_shape.csv (%s), using fallback', exc)
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _TARIFF_CURVE_SHAPE_FALLBACK.items()}


def load_lng_demand_curve(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict:
    """Load NGMM LNG export demand curve shape (NGMM Fig 3.6).

    CSV format (ng_lng_demand_curve.csv), one breakpoint per row:
        q_frac, p_factor
    plus optional scalar rows in ng_scalars.csv for world_price / max_factor.

    KNOWN ISSUE, the calibrated world price is not reaching the model
    -----------------------------------------------------------------

    """
    df = _csv('ng_lng_demand_curve.csv', data_dir)
    if df is None:
        # <-- the early return. Returns before load_qp_scalars() is ever called, so
        # 'world_price' comes from the fallback dict (7.00), not ng_scalars.csv (5.30).
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _LNG_DEMAND_CURVE_SHAPE_FALLBACK.items()}
    try:
        rows = sorted(
            [(float(r['q_frac']), float(r['p_factor'])) for _, r in df.iterrows()],
            key=lambda t: t[0],
        )
        scalars = load_qp_scalars(data_dir)
        result = {
            'q_frac':       [q for q, _ in rows],
            'p_factor':     [p for _, p in rows],
            'world_price':  scalars.get('lng_world_price_per_mmbtu',
                                        _LNG_DEMAND_CURVE_SHAPE_FALLBACK['world_price']),
            'max_factor':   scalars.get('lng_max_price_factor',
                                        _LNG_DEMAND_CURVE_SHAPE_FALLBACK['max_factor']),
        }
        if len(result['q_frac']) < 2:
            raise ValueError('need at least 2 breakpoints in LNG demand curve')
        logger.info('LNG_DEMAND_CURVE_SHAPE loaded from CSV (%d breakpoints)', len(rows))
        return result
    except (KeyError, ValueError) as exc:
        logger.warning('Could not parse ng_lng_demand_curve.csv (%s), using fallback', exc)
        return {k: list(v) if isinstance(v, list) else v
                for k, v in _LNG_DEMAND_CURVE_SHAPE_FALLBACK.items()}


def load_losses(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[str, float]]:
    """Load per-region loss fractions and plant-fuel fraction (NGMM Eq 10, 11).

    CSV format (ng_losses.csv), one row per region:
        region, distribution_loss, intrastate_loss, storage_loss, plant_fuel_frac
    """
    df = _csv('ng_losses.csv', data_dir)
    if df is None:
        return {k: dict(v) for k, v in _LOSSES_FALLBACK.items()}
    try:
        result: dict[str, dict[str, float]] = {}
        for _, row in df.iterrows():
            result[str(row['region'])] = {
                'distribution_loss': float(row.get('distribution_loss', 0.008)),
                'intrastate_loss':   float(row.get('intrastate_loss',   0.003)),
                'storage_loss':      float(row.get('storage_loss',      0.005)),
                'plant_fuel_frac':   float(row.get('plant_fuel_frac',   0.030)),
            }
        if not result:
            return {k: dict(v) for k, v in _LOSSES_FALLBACK.items()}
        logger.info('LOSSES loaded from CSV (%d regions)', len(result))
        return result
    except (KeyError, ValueError) as exc:
        logger.warning('Could not parse ng_losses.csv (%s), using fallback', exc)
        return {k: dict(v) for k, v in _LOSSES_FALLBACK.items()}


def load_gathering_charges(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load per-region gathering charges in $/MMBtu (NGMM Eq 7 P^gath term).

    CSV format (ng_gathering.csv), one row per region:
        region, gathering_charge_per_mmbtu
    """
    df = _csv('ng_gathering.csv', data_dir)
    if df is None:
        return dict(_GATHERING_CHARGES_FALLBACK)
    try:
        result = {
            str(row['region']): float(row['gathering_charge_per_mmbtu'])
            for _, row in df.iterrows()
        }
        if not result:
            return dict(_GATHERING_CHARGES_FALLBACK)
        logger.info('GATHERING_CHARGES loaded from CSV (%d regions)', len(result))
        return result
    except (KeyError, ValueError) as exc:
        logger.warning('Could not parse ng_gathering.csv (%s), using fallback', exc)
        return dict(_GATHERING_CHARGES_FALLBACK)


def load_pipe_loss(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[tuple[str, str], float]:
    """Load per-arc pipeline fuel-loss fraction (NGMM Eq 11 f^pip).

    CSV format (ng_pipe_loss.csv), one row per directed arc:
        origin, destination, loss_fraction
    Arcs not listed default to ``pipe_fuel_loss_default`` from ng_scalars.csv.
    """
    df = _csv('ng_pipe_loss.csv', data_dir)
    if df is None:
        return {}  # empty → falls back to default scalar at construction
    try:
        result = {
            (str(row['origin']), str(row['destination'])): float(row['loss_fraction'])
            for _, row in df.iterrows()
        }
        logger.info('PIPE_LOSS loaded from CSV (%d arcs)', len(result))
        return result
    except (KeyError, ValueError) as exc:
        logger.warning('Could not parse ng_pipe_loss.csv (%s), using fallback', exc)
        return {}


def load_qp_scalars(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load NGMM-QP scalars (default values for parameters not separately loaded).

    Reads the same ng_scalars.csv used by load_storage_opex(); returns all
    parameter/value rows whose key matches a recognised scalar name. Missing
    keys fall back to _QP_SCALARS_FALLBACK.
    """
    df = _csv('ng_scalars.csv', data_dir)
    result = dict(_QP_SCALARS_FALLBACK)
    if df is None:
        return result
    try:
        df = df.set_index('parameter')
        for key in _QP_SCALARS_FALLBACK:
            if key in df.index:
                result[key] = float(df.at[key, 'value'])
        return result
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning('Could not parse ng_scalars.csv QP keys (%s), using fallbacks', exc)
        return result


# ---------------------------------------------------------------------------
# Convenience: load everything at once
# ---------------------------------------------------------------------------

def load_all(data_dir: str | Path | None = None) -> dict:
    """Load all NG model parameters from CSV files.

    Parameters
    ----------
    data_dir : str or Path, optional
        Directory containing the NG input CSV files.
        Defaults to ``input/natural_gas/`` relative to the repository root.

    Returns
    -------
    dict with keys:
        supply_cost_tiers, lng_import, lng_export, demand_elasticity,
        base_demand, demand_growth, pipeline_arcs, storage, storage_opex
    """
    d = Path(data_dir) if data_dir is not None else _DEFAULT_DATA_DIR
    return {
        'supply_cost_tiers': load_supply_cost_tiers(d),
        # Optional year-varying anchor path
        'supply_anchors':     load_supply_anchors(d),
        'lng_import':         load_lng_import(d),
        'lng_export':         load_lng_export(d),
        'demand_elasticity':  load_demand_elasticity(d),
        'base_demand':        load_base_demand(d),
        'demand_growth':      load_demand_growth(d),
        'pipeline_arcs':      load_pipeline_arcs(d),
        'storage':            load_storage(d),
        'storage_opex':       load_storage_opex(d),
        # NGMM AEO2025 QP parameters
        'supply_curve_shape': load_supply_curve_shape(d),
        'tariff_curve_shape': load_tariff_curve_shape(d),
        'lng_demand_curve':   load_lng_demand_curve(d),
        'losses':             load_losses(d),
        'gathering':          load_gathering_charges(d),
        'pipe_loss':          load_pipe_loss(d),
        'qp_scalars':         load_qp_scalars(d),
    }
