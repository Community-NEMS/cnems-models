"""CSV-backed parameter loader for the C-NGMM.

Reads all numerical parameters from ``input/natural_gas/`` CSV files. Every file listed below
is required: a missing or malformed input raises ``ValueError`` rather than substituting a
built-in default, so the values a run solves on are always the values on disk.

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
    ng_scalars.csv           → STORAGE_OPEX, lng_world_price_per_mmbtu,
                                lng_max_price_factor, pipe_fuel_loss,
                                distribution_loss, intrastate_loss,
                                plant_fuel_frac, storage_loss,
                                supply_curve_qmin_fraction (scalars)

Added NGMM AEO2025-aligned parameters for
the QP rewrite of ng_model.py:
    ng_supply_curve_shape.csv → SUPPLY_CURVE_SHAPE (ELAS, CRV per step; AEO 2022
                                 footnote: ELAS = [0.8, 0.7, 0.5, 0.3, 0.2])
    ng_tariff_curve_shape.csv → TARIFF_CURVE_SHAPE (utilisation breakpoints and
                                 tariff multipliers; NGMM Fig 3.5 hurdle-rate)
    ng_lng_demand_curve.csv   → LNG_DEMAND_CURVE_SHAPE (NGMM Fig 3.6 linear
                                 demand curve down from LNG_MAX to zero at 0)
    ng_gathering.csv          → GATHERING_CHARGES (per-region $/MMBtu;
                                 NGMM Eq 7 P^gath term)
    ng_supply_anchors.csv     → SUPPLY_ANCHORS (per-(region,year) multipliers on
                                 the static Q0/P0 anchors)

OPTIONAL override files, the only inputs whose absence is not an error. Their values live
in ng_scalars.csv under the same names, so an absent file means "nothing departs from the
scalar" rather than "the value is hiding in Python". Neither ships in this repo:
    ng_losses.csv             → LOSSES (per-region, per-column override of
                                 distribution_loss, intrastate_loss, storage_loss,
                                 plant_fuel_frac; NGMM Eq 10, 11)
    ng_pipe_loss.csv          → PIPE_LOSS (per-arc override of pipe_fuel_loss;
                                 NGMM Eq 11 f^pip)
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TypedDict

import pandas as pd

from src.common.common_config import CommonConfig
from src.models.natural_gas.ng_config import NGConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default data directory
# ---------------------------------------------------------------------------

# parents[3] walks natural_gas -> models -> src -> <repo root>, giving
# <repo root>/input/natural_gas.
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / 'input' / 'natural_gas'


# ---------------------------------------------------------------------------
# Required inputs
# ---------------------------------------------------------------------------

# There are no hardcoded fallback constants. Every CSV named in the module docstring is a hard
# requirement: a missing or malformed file raises rather than substituting built-in values.
#
# The previous design kept a fallback dict per input so the model stayed runnable with an
# incomplete input set. The cost was that six of those groups shipped with no CSV at all, so
# their fallbacks were the live values rather than emergency defaults, and a typo'd filename was
# indistinguishable from a file absent on purpose. Both merely logged a warning.

# Scalars that ng_scalars.csv must define. Names only; the values live in the CSV.
#
# The four loss names below deliberately match the column names of the optional ng_losses.csv
# override file, and pipe_fuel_loss matches what ng_pipe_loss.csv overrides. Where a name can
# appear in two places, it is the same name in both.
_REQUIRED_QP_SCALARS: tuple[str, ...] = (
    'lng_world_price_per_mmbtu',
    'lng_max_price_factor',
    'pipe_fuel_loss',
    'distribution_loss',
    'intrastate_loss',
    'plant_fuel_frac',
    'storage_loss',
    'supply_curve_qmin_fraction',
)
# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------


def _csv(filename: str, data_dir: Path) -> pd.DataFrame | None:
    """Read a CSV from data_dir, skipping comment lines starting with '#'.

    Returns None and logs a warning on any failure. **Every caller turns that None into a
    ``ValueError``** - this primitive reports the failure, callers decide it is fatal, and
    naming the file in the caller's message is what makes the error actionable.

    The single I/O primitive every loader below goes through, so the read policy is defined in
    exactly one place. Two behaviours worth knowing:

    * ``comment='#'`` drops any provenance header an input carries (source, units, vintage),
      so those headers can be edited freely without affecting parsing.
    * The bare ``except Exception`` is deliberate: a malformed CSV must be reported the same
      way an absent one is. Narrowing it to ParserError would let an encoding error or a
      permissions failure propagate as a different exception type from a different place.

    Note the asymmetry that follows: this returns None for BOTH "no such file" and "file
    exists but is broken". The log message distinguishes them; the return value does not.
    """
    path = data_dir / filename
    try:
        df = pd.read_csv(path, comment='#')
        # Strip header whitespace so a hand-edited ' region' still matches lookups on 'region'.
        df.columns = df.columns.str.strip()
        return df
    except FileNotFoundError:
        logger.warning('NG data file not found: %s', path)
        return None
    except Exception as exc:  # noqa: BLE001 - broad on purpose, see docstring
        logger.warning('Could not read %s (%s)', path, exc)
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
        raise ValueError(
            f'ng_supply_cost_tiers.csv could not be read from {data_dir}; '
            'this input has no fallback'
        )
    # Fixed order, not the CSV's row order: the anchor is a weighted mean, so ordering does not
    # affect Q0/P0, but a stable order keeps the loaded structure comparable run to run.
    cost_tier_order = ['low_cost', 'medium_cost', 'high_cost']
    result: dict[str, list[tuple[float, float]]] = {}
    for region, grp in df.groupby('region'):
        grp = grp.set_index('cost_tier')
        row: list[tuple[float, float]] = []
        for t in cost_tier_order:
            if t in grp.index:
                # .at, not .loc: both are scalar lookups on a unique index, .at is cheaper.
                # TODO:  Investigate the source df and determine if/why column is not typed
                # pyrefly: ignore[bad-argument-type]  - these pulled values will be floats
                cap = float(grp.at[t, 'capacity_bcf'])  # noqa: PD008
                # pyrefly: ignore[bad-argument-type]  - these pulled values will be floats
                cost = float(grp.at[t, 'cost_per_mmbtu'])  # noqa: PD008
                row.append((cap, cost))
        if row:
            result[str(region)] = row
    if not result:
        raise ValueError(f'ng_supply_cost_tiers.csv in {data_dir} yielded no rows')
    logger.info('SUPPLY_COST_TIERS loaded from CSV (%d regions)', len(result))
    return result


def load_supply_anchors(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[tuple[str, int], tuple[float, float]]:
    """Year-varying supply-curve anchor path.

    Read from ng_supply_anchors.csv: {(region, year): (q0_mult, p0_mult)}, multipliers on the static
    cost-tier-derived (Q0, P0) anchors (harness/build_ng_anchor_path.py; AEO Table 59 production +
    regional supply-price paths, normalized to 2025).

    The file is required. An *absent row* still means multiplier 1.0 for that region-year, which is
    the static-curve behaviour, but an absent *file* is an error: it used to return {} silently,
    which is indistinguishable from a file listing no anchors and quietly reverts every region to
    the static curve.

    Raises
    ------
    ValueError
        If the file is missing or unreadable.
    """
    df = _csv('ng_supply_anchors.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_supply_anchors.csv could not be read from {data_dir}; this input has no fallback'
        )
    try:
        out = {
            # itertuples() attributes are typed as the union of every pandas scalar type, so
            # int()/float() on them do not check. The CSV columns are numeric; a non-numeric
            # value raises and is caught below.
            # TODO:  Investigate the source df and determine if/why columns are not typed
            # pyrefly: ignore[bad-argument-type]  - these pulled values will be numeric
            (str(r.region), int(r.year)): (float(r.q0_mult), float(r.p0_mult))
            for r in df.itertuples()
        }
        logger.info('SUPPLY_ANCHORS loaded from CSV (%d region-years)', len(out))
        return out
    except Exception as exc:
        raise ValueError(f'Could not parse ng_supply_anchors.csv in {data_dir}') from exc


def load_lng_import(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, tuple[float, float]]:
    """Load LNG_IMPORT from ng_lng_import.csv."""
    df = _csv('ng_lng_import.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_lng_import.csv could not be read from {data_dir}; this input has no fallback'
        )
    result = {
        str(row['region']): (float(row['capacity_bcf']), float(row['cost_per_mmbtu']))
        for _, row in df.iterrows()
    }
    if not result:
        raise ValueError(f'ng_lng_import.csv in {data_dir} yielded no rows')
    logger.info('LNG_IMPORT loaded from CSV (%d terminals)', len(result))
    return result


def load_lng_export(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[int, float]]:
    """Load LNG_EXPORT_DEMAND_BCF from ng_lng_export.csv."""
    df = _csv('ng_lng_export.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_lng_export.csv could not be read from {data_dir}; this input has no fallback'
        )
    result: dict[str, dict[int, float]] = {}
    for _, row in df.iterrows():
        region = str(row['region'])
        result.setdefault(region, {})[int(row['year'])] = float(row['demand_bcf'])
    if not result:
        raise ValueError(f'ng_lng_export.csv in {data_dir} yielded no rows')
    logger.info(
        'LNG_EXPORT_DEMAND_BCF loaded from CSV (%d region-year pairs)',
        sum(len(v) for v in result.values()),
    )
    return result


def load_demand_elasticity(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load DEMAND_PRICE_ELASTICITY from ng_demand_elasticity.csv."""
    df = _csv('ng_demand_elasticity.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_demand_elasticity.csv could not be read from {data_dir}; '
            'this input has no fallback'
        )
    result = {str(row['sector']): float(row['own_price_elasticity']) for _, row in df.iterrows()}
    if not result:
        raise ValueError(f'ng_demand_elasticity.csv in {data_dir} yielded no rows')
    logger.info('DEMAND_PRICE_ELASTICITY loaded from CSV')
    return result


def load_base_demand(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[str, float]]:
    """Load BASE_DEMAND_2025 from ng_base_demand.csv."""
    df = _csv('ng_base_demand.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_base_demand.csv could not be read from {data_dir}; this input has no fallback'
        )
    result: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        region = str(row['region'])
        result.setdefault(region, {})[str(row['sector'])] = float(row['demand_bcf_2025'])
    if not result:
        raise ValueError(f'ng_base_demand.csv in {data_dir} yielded no rows')
    logger.info(
        'BASE_DEMAND_2025 loaded from CSV (%d region-sector pairs)',
        sum(len(v) for v in result.values()),
    )
    return result


def load_demand_growth(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load DEMAND_GROWTH_RATES from ng_demand_growth.csv."""
    df = _csv('ng_demand_growth.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_demand_growth.csv could not be read from {data_dir}; this input has no fallback'
        )
    result = {str(row['sector']): float(row['annual_growth_rate']) for _, row in df.iterrows()}
    if not result:
        raise ValueError(f'ng_demand_growth.csv in {data_dir} yielded no rows')
    logger.info('DEMAND_GROWTH_RATES loaded from CSV')
    return result


def load_pipeline_arcs(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> list[tuple[str, str, float, float]]:
    """Load PIPELINE_ARCS_RAW from ng_pipeline_arcs.csv."""
    df = _csv('ng_pipeline_arcs.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_pipeline_arcs.csv could not be read from {data_dir}; this input has no fallback'
        )
    result = [
        (
            str(row['origin']),
            str(row['destination']),
            float(row['capacity_bcf']),
            float(row['tariff_per_mmbtu']),
        )
        for _, row in df.iterrows()
    ]
    if not result:
        raise ValueError(f'ng_pipeline_arcs.csv in {data_dir} yielded no rows')
    logger.info('PIPELINE_ARCS_RAW loaded from CSV (%d directed arcs)', len(result))
    return result


def load_storage(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[str, float]]:
    """Load STORAGE from ng_storage.csv."""
    df = _csv('ng_storage.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_storage.csv could not be read from {data_dir}; this input has no fallback'
        )
    result = {
        str(row['region']): {
            'working': float(row['working_cap_bcf']),
            'inject': float(row['inject_cap_bcf_yr']),
            'withdraw': float(row['withdraw_cap_bcf_yr']),
        }
        for _, row in df.iterrows()
    }
    if not result:
        raise ValueError(f'ng_storage.csv in {data_dir} yielded no rows')
    logger.info('STORAGE loaded from CSV (%d regions)', len(result))
    return result


def load_storage_opex(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> float:
    """Load STORAGE_OPEX scalar from ng_scalars.csv."""
    df = _csv('ng_scalars.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_scalars.csv could not be read from {data_dir}; this input has no fallback'
        )
    try:
        df = df.set_index('parameter')
        # TODO:  Investigate the source df and determine if/why column is not typed
        # pyrefly: ignore[bad-argument-type]  - this pulled value will be a float
        val = float(df.at['storage_opex', 'value'])  # noqa: PD008 - scalar lookup
        logger.info('STORAGE_OPEX loaded from CSV: %.4f', val)
        return val
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f'Could not read storage_opex from ng_scalars.csv in {data_dir}') from exc


# ---------------------------------------------------------------------------
# NGMM AEO2025 parameter loaders
# ---------------------------------------------------------------------------


def load_supply_curve_shape(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, list[float]]:
    """Load NGMM-style supply-curve shape (ELAS per segment, CRV per breakpoint).

    CSV format (ng_supply_curve_shape.csv), one row per step:
        side, step, crv, elas
    where side in {'below', 'above'} and step in {1, 2, 3}. The 'elas' column
    gives the segment elasticity (5 values: segments 1-5 left-to-right).
    """
    df = _csv('ng_supply_curve_shape.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_supply_curve_shape.csv could not be read from {data_dir}; '
            'this input has no fallback'
        )
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
            [
                (int(r['step']), float(r['elas']))
                for _, r in df.iterrows()
                if pd.notna(r.get('elas', None))
            ],
            key=lambda t: t[0],
        )
        result = {
            'crv_below': [v for _, v in crv_below[:3]],
            'crv_above': [v for _, v in crv_above[:3]],
            'elas': [v for _, v in elas_rows[:5]],
        }
        if (
            len(result['crv_below']) != 3
            or len(result['crv_above']) != 3
            or len(result['elas']) != 5
        ):
            raise ValueError('supply-curve shape CSV did not yield 3/3/5 entries')
        logger.info('SUPPLY_CURVE_SHAPE loaded from CSV')
        return result
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Could not parse ng_supply_curve_shape.csv in {data_dir}') from exc


def load_tariff_curve_shape(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, list[float]]:
    """Load NGMM pipeline-tariff curve shape (NGMM Fig 3.5)."""
    df = _csv('ng_tariff_curve_shape.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_tariff_curve_shape.csv could not be read from {data_dir}; '
            'this input has no fallback'
        )
    try:
        rows = sorted(
            [(float(r['util_break']), float(r['tariff_mult'])) for _, r in df.iterrows()],
            key=lambda t: t[0],
        )
        result = {
            'util_break': [u for u, _ in rows],
            'tariff_mult': [m for _, m in rows],
        }
        if len(result['util_break']) < 3:
            raise ValueError('need at least 3 breakpoints in tariff curve')
        logger.info('TARIFF_CURVE_SHAPE loaded from CSV (%d breakpoints)', len(rows))
        return result
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Could not parse ng_tariff_curve_shape.csv in {data_dir}') from exc


def load_lng_demand_curve(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, list[float] | float]:
    """Load NGMM LNG export demand curve shape (NGMM Fig 3.6).

    CSV format (ng_lng_demand_curve.csv), one breakpoint per row:
        q_frac, p_factor
    The curve is anchored by ``lng_world_price_per_mmbtu`` and ``lng_max_price_factor``, both
    read from ng_scalars.csv via :func:`load_qp_scalars`.
    """
    df = _csv('ng_lng_demand_curve.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_lng_demand_curve.csv could not be read from {data_dir}; this input has no fallback'
        )
    try:
        rows = sorted(
            [(float(r['q_frac']), float(r['p_factor'])) for _, r in df.iterrows()],
            key=lambda t: t[0],
        )
        scalars = load_qp_scalars(data_dir)
        # Bound to locals rather than checked through result[...]: this dict is heterogeneous
        # (list values and scalar values), so len() on a looked-up value is not well typed.
        q_frac = [q for q, _ in rows]
        p_factor = [p for _, p in rows]
        if len(q_frac) < 2:
            raise ValueError('need at least 2 breakpoints in LNG demand curve')
        result: dict[str, list[float] | float] = {
            'q_frac': q_frac,
            'p_factor': p_factor,
            # Direct indexing, not .get(): load_qp_scalars guarantees both keys or raises, so a
            # default here would be unreachable code masking a contract change.
            'world_price': scalars['lng_world_price_per_mmbtu'],
            'max_factor': scalars['lng_max_price_factor'],
        }
        logger.info('LNG_DEMAND_CURVE_SHAPE loaded from CSV (%d breakpoints)', len(rows))
        return result
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Could not parse ng_lng_demand_curve.csv in {data_dir}') from exc


def load_losses(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, dict[str, float]]:
    """Load per-region overrides of the loss fractions and plant-fuel fraction (NGMM Eq 10, 11).

    CSV format (ng_losses.csv), one row per region:
        region, and any of distribution_loss, intrastate_loss, storage_loss, plant_fuel_frac

    **This file is optional.** The four values are single numbers applying to every region, so
    they live in ng_scalars.csv as ``distribution_loss``, ``intrastate_loss``,
    ``storage_loss`` and ``plant_fuel_frac``, all of which ARE required. The column names
    here match those scalar names exactly.
    This file exists only to override them where a region is known to differ.

    Overriding is per region AND per column: ng_model resolves each value as
    ``losses.get(region, {}).get(column, <scalar default>)``, so a file may list one region and
    one column and everything else still takes its scalar. Only the columns actually present
    are returned, which is what makes that partial override work.

    Contrast with the required inputs in this module, where an absent file would mean the value
    existed nowhere on disk. Here it is in ng_scalars.csv either way.

    Returns
    -------
    dict[str, dict[str, float]]
        {region: {column: value}} for overridden entries; empty if the file is absent.

    Raises
    ------
    ValueError
        If the file is present but cannot be parsed, or lacks a ``region`` column. Only its
        absence is benign.
    """
    df = _csv('ng_losses.csv', data_dir)
    if df is None:
        logger.info('No ng_losses.csv; every region uses the loss defaults from ng_scalars')
        return {}

    if 'region' not in df.columns:
        raise ValueError(f'ng_losses.csv in {data_dir} has no "region" column')

    known = ('distribution_loss', 'intrastate_loss', 'storage_loss', 'plant_fuel_frac')
    present = [col for col in known if col in df.columns]
    if not present:
        raise ValueError(
            f'ng_losses.csv in {data_dir} overrides nothing: expected at least one of {known}'
        )

    try:
        result: dict[str, dict[str, float]] = {
            str(row['region']): {col: float(row[col]) for col in present}
            for _, row in df.iterrows()
        }
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Could not parse ng_losses.csv in {data_dir}') from exc

    if not result:
        raise ValueError(f'ng_losses.csv in {data_dir} yielded no rows')
    logger.info('LOSSES overrides loaded from CSV (%d regions, columns %s)', len(result), present)
    return result


def load_gathering_charges(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load per-region gathering charges in $/MMBtu (NGMM Eq 7 P^gath term).

    CSV format (ng_gathering.csv), one row per region:
        region, gathering_charge_per_mmbtu
    """
    df = _csv('ng_gathering.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_gathering.csv could not be read from {data_dir}; this input has no fallback'
        )
    try:
        result = {
            str(row['region']): float(row['gathering_charge_per_mmbtu']) for _, row in df.iterrows()
        }
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Could not parse ng_gathering.csv in {data_dir}') from exc

    if not result:
        raise ValueError(f'ng_gathering.csv in {data_dir} yielded no rows')
    logger.info('GATHERING_CHARGES loaded from CSV (%d regions)', len(result))
    return result


def load_pipe_loss(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[tuple[str, str], float]:
    """Load per-arc overrides of the pipeline fuel-loss fraction (NGMM Eq 11 f^pip).

    CSV format (ng_pipe_loss.csv), one row per directed arc:
        origin, destination, loss_fraction

    **This file is optional, and is the only optional input in this module.** Loss is a single
    number that applies to every corridor unless a corridor is known to differ, so the value
    itself lives in ng_scalars.csv as ``pipe_fuel_loss``, which IS required. This file
    exists only to override that scalar for specific arcs; an absent file means no overrides,
    and ng_model applies the scalar to all of them.

    That is not the fallback pattern removed from the rest of this module. There, an absent file
    meant the value came from a constant in Python and nothing on disk recorded it. Here the
    value is on disk either way -- only the arc-level exceptions are missing, and there are
    currently none.

    Returns
    -------
    dict[tuple[str, str], float]
        {(origin, destination): loss_fraction} for overridden arcs; empty if the file is absent.

    Raises
    ------
    ValueError
        If the file is present but cannot be parsed. Only its absence is benign.
    """
    df = _csv('ng_pipe_loss.csv', data_dir)
    if df is None:
        logger.info('No ng_pipe_loss.csv; every arc uses pipe_fuel_loss from ng_scalars')
        return {}
    try:
        result = {
            (str(row['origin']), str(row['destination'])): float(row['loss_fraction'])
            for _, row in df.iterrows()
        }
    except (KeyError, ValueError) as exc:
        raise ValueError(f'Could not parse ng_pipe_loss.csv in {data_dir}') from exc

    logger.info('PIPE_LOSS overrides loaded from CSV (%d arcs)', len(result))
    return result


def load_qp_scalars(
    data_dir: Path = _DEFAULT_DATA_DIR,
) -> dict[str, float]:
    """Load NGMM-QP scalars (default values for parameters not separately loaded).

    Reads the same ng_scalars.csv used by load_storage_opex(). Every name in
    ``_REQUIRED_QP_SCALARS`` must be present; there are no per-key defaults, so a scalar
    omitted from the CSV is an error rather than a silent substitution.

    Raises
    ------
    ValueError
        If ng_scalars.csv is missing or unreadable, omits a required scalar, or holds a
        non-numeric value for one.
    """
    df = _csv('ng_scalars.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_scalars.csv could not be read from {data_dir}; this input has no fallback'
        )
    if 'parameter' not in df.columns:
        raise ValueError(f'ng_scalars.csv in {data_dir} has no "parameter" column')
    df = df.set_index('parameter')

    absent = [key for key in _REQUIRED_QP_SCALARS if key not in df.index]
    if absent:
        raise ValueError(f'ng_scalars.csv in {data_dir} is missing required scalar(s): {absent}')

    try:
        # TODO:  Investigate the source df and determine if/why column is not typed
        result = {
            # pyrefly: ignore[bad-argument-type]  - these pulled values will be floats
            key: float(df.at[key, 'value'])  # noqa: PD008 - scalar lookup
            for key in _REQUIRED_QP_SCALARS
        }
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f'Could not parse QP scalars from ng_scalars.csv in {data_dir}') from exc

    logger.info('QP_SCALARS loaded from CSV (%d keys)', len(result))
    return result


class RegionData(TypedDict):
    """Return shape of :func:`load_region_data`.

    ``regions`` is the master list in file order; ``regions_domestic`` and
    ``regions_international`` partition it; ``region_labels`` covers every region in either.
    """

    regions: list[str]
    regions_domestic: list[str]
    regions_analyze: list[str]
    regions_international: list[str]
    region_labels: dict[str, str]


def load_sector_data(ng_config: NGConfig) -> list[str]:
    """Load sector data."""
    f_name = 'ng_sector_data.csv'
    path = ng_config.input_path / f_name
    if not path.exists():
        logger.error('Could not find sector data file (%s)', path)
        raise FileNotFoundError(path)
    res = []
    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                res.append(row['name'])
    except KeyError, ValueError:
        logger.warning('Could not parse sector_data.csv (%s)', path)
        raise
    return res


def load_region_data(ng_config: NGConfig) -> RegionData:
    """Load the model regions and their display labels from ng_region_data.csv.

    Returns the master region list in file order plus the two subsets it is partitioned into,
    mirroring the electricity model's region / region_domestic / region_international split,
    and a label for every region in the file.

    The ``covered_areas`` column (the states each census division spans) is documentation for
    the reader of the CSV; it is not returned. Flags follow the electricity convention: a
    case-insensitive 'true' means the region belongs to that group, anything else ('-') means
    it does not.

    Unlike every other loader here, this one has NO fallback: regions are definitional, and a
    model built on a region set that disagrees with the rest of the input files is worse than a
    model that refuses to build.

    Raises
    ------
    ValueError
        If the file is missing or unreadable, lacks a required column, or declares no domestic
        regions.
    """
    data_dir = ng_config.input_path
    df = _csv('ng_region_data.csv', data_dir)
    if df is None:
        raise ValueError(
            f'ng_region_data.csv could not be read from {data_dir}; regions have no fallback'
        )
    try:
        regions = {str(row['region']).strip() for _, row in df.iterrows()}
        domestic = {
            str(row['region']).strip()
            for _, row in df.iterrows()
            if str(row['domestic']).strip().lower() == 'true'
        }
        # apply the filter from the config
        if ng_config.region_filter:
            r_filter = set(ng_config.region_filter)

            # look for erroneous filter entries first
            bogus_filters = r_filter - domestic
            if bogus_filters:
                logger.error('NG Config region filter has unknown regions: %s', bogus_filters)
                raise ValueError(f'Unrecognized region filter(s): {sorted(bogus_filters)}')

            filtered_domestic_regions = domestic.intersection(r_filter)
            if len(filtered_domestic_regions) < len(domestic):
                dropped_regions = domestic - filtered_domestic_regions
                logger.info('Dropped domestic regions: %s', dropped_regions)
                logger.warning(
                    'Domestic region subset (%d of %d): results are NOT comparable '
                    'to a full run, dropped '
                    'regions take their production, demand, and trade with them. For mechanics and '
                    'timing tests only.',
                    len(filtered_domestic_regions),
                    len(domestic),
                )
        else:
            filtered_domestic_regions = domestic
        international = [
            str(row['region']).strip()
            for _, row in df.iterrows()
            if str(row['international']).strip().lower() == 'true'
        ]
        labels = {str(row['region']).strip(): str(row['label']).strip() for _, row in df.iterrows()}

    except KeyError as exc:
        raise ValueError(f'ng_region_data.csv is missing required column {exc}') from exc
    if not domestic:
        raise ValueError('ng_region_data.csv declares no domestic regions')
    logger.info(
        'Regions loaded from CSV (%d total: %d domestic (%d for analysis), %d international)',
        len(regions),
        len(domestic),
        len(filtered_domestic_regions),
        len(international),
    )
    return {
        'regions': sorted(regions),
        'regions_domestic': sorted(domestic),
        'regions_analyze': sorted(filtered_domestic_regions),
        'regions_international': international,
        'region_labels': labels,
    }


###############################################################################
# Helper: build demand projection
###############################################################################


# Added the `regions` argument (default None = all nine, so
def project_demand(
    demand_table: dict,
    growth_rate_table: dict,
    years: list[int],
    regions: list[str],
    sectors: list[str],
) -> dict[tuple[str, str, int], float]:
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
    for region in regions:
        for sector in sectors:
            base = demand_table[region][sector]
            g = growth_rate_table[sector]
            for year in years:
                dt = year - base_year
                demand[(region, sector, year)] = base * ((1 + g) ** dt)
    return demand


# ---------------------------------------------------------------------------
# Convenience: load everything at once
# ---------------------------------------------------------------------------


class NGData(TypedDict):
    """Return shape of :func:`load_all`: one key per loader, in the same order.

    A TypedDict rather than ``dict[str, Any]`` so that a consumer indexing
    ``_NG_DATA['supply_curve_shape']`` gets the loader's own return type instead of ``Any``,
    and so a typo'd key is a type error rather than a runtime KeyError.
    """

    regions: list[str]
    regions_domestic: list[str]
    regions_analyze: list[str]
    regions_international: list[str]
    region_labels: dict[str, str]
    sectors: list[str]
    years: list[int]
    supply_cost_tiers: dict[str, list[tuple[float, float]]]
    supply_anchors: dict[tuple[str, int], tuple[float, float]]
    lng_import: dict[str, tuple[float, float]]
    lng_export: dict[str, dict[int, float]]
    demand_elasticity: dict[str, float]
    # base_demand: dict[str, dict[str, float]]
    # demand_growth: dict[str, float]
    demand: dict[tuple[str, str, int], float]
    pipeline_arcs: list[tuple[str, str, float, float]]
    storage: dict[str, dict[str, float]]
    storage_opex: float
    supply_curve_shape: dict[str, list[float]]
    tariff_curve_shape: dict[str, list[float]]
    lng_demand_curve: dict[str, list[float] | float]
    losses: dict[str, dict[str, float]]
    gathering: dict[str, float]
    pipe_loss: dict[tuple[str, str], float]
    qp_scalars: dict[str, float]


def load_all(ng_config: NGConfig, common_config: CommonConfig) -> NGData:
    """Load all NG model parameters from CSV files.

    Parameters
    ----------
    data_dir : str or Path, optional
        Directory containing the NG input CSV files.
        Defaults to ``input/natural_gas/`` relative to the repository root.

    Returns
    -------
    NGData
        One entry per loader; see the `NGData` field list for keys and value types.
    """
    region_data = load_region_data(ng_config)
    sectors = load_sector_data(ng_config)
    data_path = ng_config.input_path

    # compute the projected demand...
    demand = project_demand(
        demand_table=load_base_demand(ng_config.input_path),
        growth_rate_table=load_demand_growth(ng_config.input_path),
        years=common_config.summary_years,
        regions=region_data['regions_analyze'],
        sectors=sectors,
    )

    return {
        'regions': region_data['regions'],
        'regions_domestic': region_data['regions_domestic'],
        'regions_analyze': region_data['regions_analyze'],
        'regions_international': region_data['regions_international'],
        'region_labels': region_data['region_labels'],
        'sectors': sectors,
        'years': sorted(common_config.summary_years),
        'supply_cost_tiers': load_supply_cost_tiers(data_path),
        # Optional year-varying anchor path
        'supply_anchors': load_supply_anchors(data_path),
        'lng_import': load_lng_import(data_path),
        'lng_export': load_lng_export(data_path),
        'demand_elasticity': load_demand_elasticity(data_path),
        # 'base_demand': load_base_demand(data_path),
        # 'demand_growth': load_demand_growth(data_path),
        'demand': demand,
        'pipeline_arcs': load_pipeline_arcs(data_path),
        'storage': load_storage(data_path),
        'storage_opex': load_storage_opex(data_path),
        # NGMM AEO2025 QP parameters
        'supply_curve_shape': load_supply_curve_shape(data_path),
        'tariff_curve_shape': load_tariff_curve_shape(data_path),
        'lng_demand_curve': load_lng_demand_curve(data_path),
        'losses': load_losses(data_dir=data_path),
        'gathering': load_gathering_charges(data_path),
        'pipe_loss': load_pipe_loss(data_path),
        'qp_scalars': load_qp_scalars(data_path),
    }
