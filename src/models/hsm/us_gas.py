"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

US natural gas supply capacity for C-HSM. This is a reduced-form model written for C-HSM; NEMS
has no file like it (NEMS builds US supply from its full onshore and offshore submodules).

For each region (census division), cost tier and year it gives a production capacity in BCF/yr
that moves with the gas price, the oil price and technology:

- Base capacity: each cost tier's capacity from C-NGMM's ``ng_supply_cost_tiers.csv``, split
  across four gas types (conventional, tight, shale, coalbed methane) by fixed shares.
- Price response: base x (gas price / 2.50) ^ elasticity of the gas type.
- Technology: x (1 + trend) ^ (year - base year), trends from NEMS ``on_tech_levers.csv``.
- Depletion: low-cost capacity falls by a regional rate each year, to no less than half its base.
- NA/AD split: a regional share of capacity is associated-dissolved (AD) gas, which follows
  the oil price rather than the gas price (``us_ad_gas_share.csv``, ``us_ad_elasticity.csv``).
- Calibration: x k * (1 + g) ^ x * exp(c * x^2), x = year - 2025, with (k, g, c) by region from
  ``us_gas_calibration.csv``, fitted so that capacity at the AEO2026 reference prices matches
  C-NGMM's supply anchors.

Gas types and cost tiers:
    - conventional + CBM  -> low_cost
    - tight + shale       -> medium_cost
    - deep conventional + frontier shale/offshore -> high_cost

Parameters read from the NEMS onshore files in ``input/hsm/onshore/`` (taken unchanged from
``models/hsm/input/onshore/`` of NEMS, https://github.com/EIAgov/NEMS; see
``input/hsm/hsm_data_pedigree.md``):

    on_tech_levers.csv       -> technology trend by gas type (tier_1_eur_tech by well_type_number)
    on_region_avg_cost.csv   -> low-cost tier shares (inverse-cost weights: conventional vs CBM)
    on_dryhole_rate.csv      -> conventional depletion rate (gas dry-hole rates, mapped to
                                census divisions with mapping.csv)
    on_constraint_params.csv -> oil-associated share of medium-cost capacity (share_ratio)

Set here, with no NEMS file behind them:
    SUPPLY_ELASTICITY             -- from the literature (Newell et al. 2016)
    OIL_ASSOCIATED_GAS_ELASTICITY -- set by hand; only used when the NA/AD split is off
    medium and high-cost tier shares -- NEMS onshore has no separate tight gas cost file
"""

# Single values are read with .at throughout, as in the NEMS code this follows.
# ruff: noqa: PD008

from __future__ import annotations

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

# The three supply cost tiers, cheapest first. The regions and the capacity of each tier come
# from C-NGMM's input files and are passed in by the sequencer (see data.py).
COST_TIER_LABELS: list[str] = ['low_cost', 'medium_cost', 'high_cost']

# ---------------------------------------------------------------------------
# Module-level constants — price elasticities (no NEMS CSV source)
# ---------------------------------------------------------------------------

# Price elasticity of supply by gas type, from the literature (Newell et al. 2016).
SUPPLY_ELASTICITY: dict[str, float] = {
    'conventional': 0.30,
    'tight': 0.55,
    'shale': 0.65,
    'cbm': 0.25,
}

# How much gas supply rises with the oil price, by region: oil-directed wells also produce gas.
# Set by hand, guided by EIA AEO2025 oil and gas supply sensitivities and Newell et al. (2016).
# Only used when the NA/AD split is off.
OIL_ASSOCIATED_GAS_ELASTICITY: dict[str, float] = {
    'west_south_central': 0.35,  # Permian Basin + Eagle Ford oil wells
    'mountain': 0.20,  # DJ Basin (Weld County) + Bakken (ND portion)
    'west_north_central': 0.15,  # Bakken (ND main), Williston Basin
    'south_atlantic': 0.05,  # minimal oil-associated gas
    'new_england': 0.00,
    'middle_atlantic': 0.00,
    'east_north_central': 0.00,
    'east_south_central': 0.05,
    'pacific': 0.05,  # SoCal / Cook Inlet minor oil fields
}

BASE_WELLHEAD_PRICE_PER_MMBTU: float = 2.50  # 2023 US avg wellhead price (nominal $/MMBtu)
BASE_YEAR: int = 2023
BASE_OIL_PRICE_PER_BBL: float = 65.0  # $/bbl reference (2023 WTI approximate)

# Share of medium-cost (tight/shale) capacity that is oil-associated, used only when the NA/AD
# split is off. Read from on_constraint_params.csv (share_ratio); this is the value used if the
# file cannot be read.
_OIL_ASSOC_COST_TIER_FRACTION_FALLBACK: float = 0.15  # from on_constraint_params.csv share_ratio

# ---------------------------------------------------------------------------
# Fallback values, used only if a NEMS file cannot be read
# ---------------------------------------------------------------------------

# Technology trend by gas type (annual improvement in recovery per well). Depletion of
# conventional and CBM supply is handled separately, by the depletion rate below.
_TECH_TREND_FALLBACK: dict[str, float] = {
    'conventional': 0.0025,  # well_type_number 3 tier_1_eur_tech in on_tech_levers.csv
    'tight': 0.0100,  # well_type_number 4 tier_1_eur_tech
    'shale': 0.0100,  # well_type_number 5 tier_1_eur_tech (tier_2 used to be 0.02)
    'cbm': 0.0025,  # well_type_number 6 tier_1_eur_tech
}

# Split of each cost tier across gas types. The low-cost shares are recomputed from
# on_region_avg_cost.csv; the medium and high-cost shares are always these values.
_COST_TIER_TYPE_SHARES_FALLBACK: dict[tuple[str, str], float] = {
    ('low_cost', 'conventional'): 0.55,  # from on_region_avg_cost.csv cost ratios
    ('low_cost', 'cbm'): 0.45,
    ('medium_cost', 'tight'): 0.40,  # no Tight Gas cost file in NEMS onshore
    ('medium_cost', 'shale'): 0.60,
    ('high_cost', 'conventional'): 0.20,  # deep conventional
    ('high_cost', 'shale'): 0.80,  # frontier shale/offshore
}

# Conventional depletion rate by region, per year. Normally derived from on_dryhole_rate.csv.
_CONVENTIONAL_DEPLETION_RATE_FALLBACK: dict[str, float] = {
    'new_england': 0.020,
    'middle_atlantic': 0.005,
    'east_north_central': 0.015,
    'west_north_central': 0.012,
    'south_atlantic': 0.020,
    'east_south_central': 0.015,
    'west_south_central': 0.008,
    'mountain': 0.010,
    'pacific': 0.018,
}

# ---------------------------------------------------------------------------
# Internal lookup tables
# ---------------------------------------------------------------------------

# Census division number → C-NEMS region name (from mapping.csv census_division column)
_CENSUS_TO_REGION: dict[int, str] = {
    1: 'new_england',
    2: 'middle_atlantic',
    3: 'east_north_central',
    4: 'west_north_central',
    5: 'south_atlantic',
    6: 'east_south_central',
    7: 'west_south_central',
    8: 'mountain',
    9: 'pacific',
}

# well_type_number → gas type string (from on_process_codes.csv gas_type column)
_WELL_TYPE_TO_GAS: dict[int, str] = {
    3: 'conventional',  # Conventional (Gas)
    4: 'tight',  # Tight Gas
    5: 'shale',  # Shale Gas
    6: 'cbm',  # Coalbed Methane
}

# ---------------------------------------------------------------------------
# Readers for the NEMS files
# ---------------------------------------------------------------------------


def _load_tech_trend(levers_path: str) -> dict[str, float]:
    """Load TECH_TREND from on_tech_levers.csv using tier_1_eur_tech per well type.

    well_type_number mapping (from on_process_codes.csv):
        3 → conventional gas
        4 → tight gas
        5 → shale gas
        6 → coalbed methane
    """
    try:
        df = pd.read_csv(levers_path, skiprows=1)
        df.columns = df.columns.str.strip()
    except (FileNotFoundError, Exception) as exc:  # noqa: BLE001 - use the fallback
        logger.warning('on_tech_levers.csv not found or unreadable (%s); using fallback', exc)
        return dict(_TECH_TREND_FALLBACK)

    idx_col = 'well_type_number'
    val_col = 'tier_1_eur_tech'
    if idx_col not in df.columns or val_col not in df.columns:
        logger.warning(
            'on_tech_levers.csv missing columns %s or %s; using fallback', idx_col, val_col
        )
        return dict(_TECH_TREND_FALLBACK)

    df = df.set_index(idx_col)
    result = dict(_TECH_TREND_FALLBACK)
    for wt_num, gas_type in _WELL_TYPE_TO_GAS.items():
        if wt_num in df.index:
            result[gas_type] = float(df.at[wt_num, val_col])
    logger.info('TECH_TREND loaded from NEMS on_tech_levers.csv: %s', result)
    return result


def _load_cost_tier_shares(cost_path: str) -> dict[tuple[str, str], float]:
    """Derive low-cost cost_tier shares from on_region_avg_cost.csv unit opex.

    Shares are computed as inverse-cost weights:
        share_i = (1 / unit_opex_i) / sum_j(1 / unit_opex_j)

    Only the low-cost (conventional + CBM) cost_tier shares are updated.
    Medium and high-cost shares remain at hardcoded fallback values because
    there is no separate Tight Gas cost entry in on_region_avg_cost.csv.
    """
    shares = dict(_COST_TIER_TYPE_SHARES_FALLBACK)
    try:
        df = pd.read_csv(cost_path, skiprows=1)
        df.columns = df.columns.str.strip()
    except (FileNotFoundError, Exception) as exc:  # noqa: BLE001 - use the fallback
        logger.warning(
            'on_region_avg_cost.csv not found (%s); using fallback cost_tier shares', exc
        )
        return shares

    wt_col = 'well_type_merge'
    prod_col = 'production_opex_brl'
    trans_col = 'transport_opex_brl'
    if not all(c in df.columns for c in [wt_col, prod_col, trans_col]):
        logger.warning(
            'on_region_avg_cost.csv missing expected columns; using fallback cost_tier shares'
        )
        return shares

    df['unit_opex'] = df[prod_col] + df[trans_col]
    nat_avg = df.groupby(wt_col)['unit_opex'].mean()

    conv_cost = nat_avg.get('Conventional')
    cbm_cost = nat_avg.get('Coalbed Methane')

    if conv_cost and cbm_cost and conv_cost > 0 and cbm_cost > 0:
        inv_conv = 1.0 / conv_cost
        inv_cbm = 1.0 / cbm_cost
        total = inv_conv + inv_cbm
        shares[('low_cost', 'conventional')] = round(inv_conv / total, 4)
        shares[('low_cost', 'cbm')] = round(inv_cbm / total, 4)
        logger.info(
            'Low-cost cost_tier shares from NEMS on_region_avg_cost.csv: '
            'conventional=%.4f  cbm=%.4f',
            shares[('low_cost', 'conventional')],
            shares[('low_cost', 'cbm')],
        )
    else:
        logger.warning(
            'Conventional or CBM cost entry missing in on_region_avg_cost.csv; '
            'using fallback low-cost cost_tier shares'
        )
    return shares


def _load_depletion_rate(dryhole_path: str, mapping_path: str) -> dict[str, float]:
    """Derive CONVENTIONAL_DEPLETION_RATE from on_dryhole_rate.csv + mapping.csv.

    Method
    ------
    1. Filter on_dryhole_rate.csv to drill_category=3 (development conventional)
       and resource_type='gas' — these rates reflect conventional resource quality.
    2. From mapping.csv, compute the state-count-weighted average NEMS dry-hole
       rate for each Census division (census_division column, onshore only).
    3. Linearly scale the resulting rates to [DEPL_MIN=0.005, DEPL_MAX=0.020]/yr:
           depl(cd) = DEPL_MIN + (dh(cd) - min_dh) / (max_dh - min_dh) × (DEPL_MAX - DEPL_MIN)
       Higher dry-hole rate → harder to find gas → faster effective depletion.
    4. Map census_division 1-9 → C-NEMS region name.
    """
    DEPL_MIN, DEPL_MAX = 0.005, 0.020
    fallback = dict(_CONVENTIONAL_DEPLETION_RATE_FALLBACK)

    try:
        dh_df = pd.read_csv(dryhole_path, skiprows=1)
        map_df = pd.read_csv(mapping_path)
    except (FileNotFoundError, Exception) as exc:  # noqa: BLE001 - use the fallback
        logger.warning(
            'Could not load dryhole or mapping file (%s); using fallback depletion rates', exc
        )
        return fallback

    dh_df.columns = dh_df.columns.str.strip()
    map_df.columns = map_df.columns.str.strip()

    # Category 3 = development conventional, resource_type = gas
    cat3 = dh_df[(dh_df['drill_category'] == 3) & (dh_df['resource_type'] == 'gas')][
        ['region_number', 'dryhole_rate']
    ]
    if cat3.empty:
        logger.warning('No category-3 gas dry-hole data in on_dryhole_rate.csv; using fallback')
        return fallback

    dh_by_nems_region = cat3.set_index('region_number')['dryhole_rate'].to_dict()

    # Onshore states only (census_division > 0)
    states = map_df[map_df['census_division'] > 0][['region_number', 'census_division']].copy()
    states['dryhole'] = states['region_number'].map(dh_by_nems_region)
    states = states.dropna(subset=['dryhole'])

    if states.empty:
        logger.warning('State → NEMS region mapping produced no valid dry-hole entries; fallback')
        return fallback

    # Census-division weighted-average dry-hole rate (weighted by state count)
    census_dh = states.groupby('census_division')['dryhole'].mean()

    dh_min = census_dh.min()
    dh_max = census_dh.max()
    if dh_max > dh_min:
        census_depl = DEPL_MIN + (census_dh - dh_min) / (dh_max - dh_min) * (DEPL_MAX - DEPL_MIN)
    else:
        census_depl = census_dh * 0.0 + (DEPL_MIN + DEPL_MAX) / 2.0

    result = dict(fallback)
    for cd, rate in census_depl.items():
        region = _CENSUS_TO_REGION.get(int(cd))
        if region:
            result[region] = round(float(rate), 4)

    logger.info('CONVENTIONAL_DEPLETION_RATE from NEMS on_dryhole_rate.csv: %s', result)
    return result


# Calibration layer. Capacity is multiplied by k_r * (1 + g_r)^x * exp(c_r * x^2), with
# x = year - CAL_ANCHOR_YEAR and (k, g, c) fitted by region so that capacity at the AEO2026
# reference price path matches C-NGMM's supply anchors (Q0 = sum of the cost tier capacities x
# q0_mult). k takes up level differences, such as the tier shares and the base price; g and c
# take up the trend and the rise-and-fall shape of AEO2026 production that the reduced form
# cannot produce on its own.
CAL_ANCHOR_YEAR: int = 2025  # the year the calibration is normalised to


def _load_calibration(calibration_path: str) -> dict[str, tuple[float, float, float]]:
    """Load per-region (k, g, c) from a calibration file such as us_gas_calibration.csv.

    A missing ``c`` column means c = 0, a plain exponential.

    Raises
    ------
    ValueError
        If the file cannot be read, lacks a ``region``, ``k`` or ``g`` column, or has a value
        that is not a finite number.
    """
    try:
        df = pd.read_csv(calibration_path, comment='#', encoding='utf-8')
    except (OSError, ValueError) as exc:
        raise ValueError(f'calibration file {calibration_path} could not be read: {exc}') from exc
    missing = {'region', 'k', 'g'} - set(df.columns)
    if missing:
        raise ValueError(f'calibration file {calibration_path} has no {sorted(missing)} column')
    result = {}
    for r in df.itertuples():
        c = float(getattr(r, 'c', 0.0)) if hasattr(r, 'c') else 0.0
        result[str(r.region)] = (float(r.k), float(r.g), c)
    bad = sorted(reg for reg, kgc in result.items() if not all(math.isfinite(v) for v in kgc))
    if bad:
        raise ValueError(f'calibration file {calibration_path} has non-finite values for {bad}')
    logger.info(
        'USGasModule calibration loaded (%d regions) from %s', len(result), calibration_path
    )
    return result


def _load_oil_assoc_fraction(constraint_path: str) -> float:
    """Read OIL_ASSOC_COST_TIER_FRACTION from on_constraint_params.csv (share_ratio).

    The NEMS share_ratio parameter represents the fraction of drilling activity
    allocated to secondary/associated resource development — used here as a proxy
    for the fraction of the medium-cost cost_tier that is oil-directed (associated gas).
    """
    try:
        df = pd.read_csv(constraint_path, skiprows=1)
        df.columns = df.columns.str.strip()
        df = df.set_index('parameter')
        val = float(df.at['share_ratio', 'value'])
        logger.info('OIL_ASSOC_COST_TIER_FRACTION from NEMS on_constraint_params.csv: %.4f', val)
        return val
    except (FileNotFoundError, KeyError, Exception) as exc:  # noqa: BLE001 - use the fallback
        logger.warning(
            'Could not read share_ratio from on_constraint_params.csv (%s); using fallback %.2f',
            exc,
            _OIL_ASSOC_COST_TIER_FRACTION_FALLBACK,
        )
        return _OIL_ASSOC_COST_TIER_FRACTION_FALLBACK


# NA/AD split. It is on only when both us_ad_gas_share.csv and us_ad_elasticity.csv load; both
# are shipped, so it is on. Without them capacity is not split by gas type, and the older
# oil-associated add-on in compute_capacity is used instead.
def _load_ad_share(path: str | None) -> dict[str, float]:
    """Load per-region AD gas share (region -> ad_share); {} if absent (split off)."""
    if not path:
        return {}
    try:
        df = pd.read_csv(path, comment='#')
        share = dict(zip(df['region'].astype(str), df['ad_share'].astype(float), strict=True))
        logger.info('USGasModule: AD shares loaded (%d regions) from %s', len(share), path)
        return share
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.info('USGasModule: no AD-share file (%s) — NA/AD split inactive', exc)
        return {}


def _load_ad_elasticity(path: str | None) -> dict[str, tuple[float, float]]:
    """Load per-region asymmetric Brent elasticities (region -> (up, down)); {} if absent."""
    if not path:
        return {}
    try:
        df = pd.read_csv(path, comment='#')
        elas = {
            str(row['region']): (float(row['elas_up']), float(row['elas_down']))
            for _, row in df.iterrows()
        }
        logger.info('USGasModule: AD elasticities loaded (%d regions) from %s', len(elas), path)
        return elas
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.info('USGasModule: no AD-elasticity file (%s) — NA/AD split inactive', exc)
        return {}


# ---------------------------------------------------------------------------
# USGasModule class
# ---------------------------------------------------------------------------


class USGasModule:
    """Price-responsive US domestic natural gas supply module.

    Parameters
    ----------
    regions : list[str]
        Supply regions, from C-NGMM's ``ng_region_data.csv``.
    supply_cost_tiers : dict[str, list[tuple[float, float]]]
        ``{region: [(capacity_bcf, cost_per_mmbtu), ...]}`` in low, medium, high cost order,
        from C-NGMM's ``ng_supply_cost_tiers.csv``. Only the capacities are used. Every
        region must have all three tiers.
    base_year : int
        Reference year for technology trends and base capacities.
        Defaults to BASE_YEAR (2023).
    onshore_path : str or Path, optional
        Path to the ``input/hsm/onshore/`` directory.  When provided the
        following NEMS CSV files are loaded to replace hardcoded parameters:
        ``on_tech_levers.csv``, ``on_region_avg_cost.csv``,
        ``on_constraint_params.csv``, ``on_dryhole_rate.csv``.
    mapping_path : str or Path, optional
        Path to ``input/hsm/mapping.csv``.  Required together with
        ``onshore_path`` to derive ``CONVENTIONAL_DEPLETION_RATE`` from
        ``on_dryhole_rate.csv``.
    calibration_path : str, optional
        A calibration file, the (k, g, c) by region; it must be readable and cover every region.
        None: no calibration.
    ad_share_path, ad_elasticity_path : str, optional
        ``us_ad_gas_share.csv`` and ``us_ad_elasticity.csv``. The NA/AD split is on only if
        both load.
    onshore_engine : OnshoreEngine, optional
        The optional well-level engine from ``us_onshore.py``. None (the default) means the
        reduced form.

    Raises
    ------
    ValueError
        If a region in ``regions`` does not have exactly three cost tiers.
    """

    def __init__(
        self,
        regions: list[str],
        supply_cost_tiers: dict[str, list[tuple[float, float]]],
        base_year: int = BASE_YEAR,
        onshore_path: str | None = None,
        mapping_path: str | None = None,
        calibration_path: str | None = None,
        ad_share_path: str | None = None,
        ad_elasticity_path: str | None = None,
        onshore_engine=None,
    ) -> None:
        self.regions = list(regions)
        # Every region needs all three tiers: the capacities below are read by position
        # (0 = low, 1 = medium, 2 = high), so a missing tier would shift the others.
        for region in self.regions:
            tiers = supply_cost_tiers.get(region, [])
            if len(tiers) != len(COST_TIER_LABELS):
                raise ValueError(
                    f'supply cost tiers for {region}: expected {len(COST_TIER_LABELS)} '
                    f'(low, medium, high), found {len(tiers)}'
                )
        self._supply_cost_tiers = supply_cost_tiers
        self.base_year = base_year

        # Calibration (k, g, c) by region; none means k = 1, g = 0, c = 0.
        self._calib: dict[str, tuple[float, float, float]] = (
            _load_calibration(calibration_path) if calibration_path else {}
        )
        # A calibration file must cover every region; a missing one would run uncalibrated.
        uncalibrated = [r for r in self.regions if self._calib and r not in self._calib]
        if uncalibrated:
            raise ValueError(f'calibration file {calibration_path} has no row for {uncalibrated}')

        # NA/AD split, on only when both files load. With it, results are keyed
        # (region, cost_tier, gas_type, year); without it, (region, cost_tier, year).
        self._ad_share: dict[str, float] = _load_ad_share(ad_share_path)
        self._ad_elas: dict[str, tuple[float, float]] = _load_ad_elasticity(ad_elasticity_path)
        self._na_ad_split_active: bool = bool(self._ad_share) and bool(self._ad_elas)
        if self._na_ad_split_active:
            logger.info('USGasModule: NA/AD split active (results keyed by gas type too)')

        # Optional well-level engine (us_onshore.py). If given, US supply comes from its well
        # stock and cash flow simulation instead of the reduced form below; the NA/AD split then
        # follows from the project types and the oil add-on is not used. The (k, g, c)
        # calibration still applies, and would have to be refitted against the engine.
        self._engine = onshore_engine
        if self._engine is not None:
            logger.info('USGasModule: well-level engine active (reduced form bypassed)')

        # Read the NEMS parameters when the paths are given; otherwise use the fallback values.
        if onshore_path is not None:
            _op = str(onshore_path).rstrip('/\\') + '/'
            self._tech_trend = _load_tech_trend(_op + 'on_tech_levers.csv')
            self._cost_tier_shares = _load_cost_tier_shares(_op + 'on_region_avg_cost.csv')
            self._oil_assoc_fraction = _load_oil_assoc_fraction(_op + 'on_constraint_params.csv')
            if mapping_path is not None:
                self._depletion_rate = _load_depletion_rate(
                    _op + 'on_dryhole_rate.csv', str(mapping_path)
                )
            else:
                logger.warning(
                    'USGasModule: mapping_path not supplied; '
                    'using fallback CONVENTIONAL_DEPLETION_RATE'
                )
                self._depletion_rate = dict(_CONVENTIONAL_DEPLETION_RATE_FALLBACK)
        else:
            logger.info(
                'USGasModule: onshore_path not supplied; using hardcoded fallback parameters'
            )
            self._tech_trend = dict(_TECH_TREND_FALLBACK)
            self._cost_tier_shares = dict(_COST_TIER_TYPE_SHARES_FALLBACK)
            self._depletion_rate = dict(_CONVENTIONAL_DEPLETION_RATE_FALLBACK)
            self._oil_assoc_fraction = _OIL_ASSOC_COST_TIER_FRACTION_FALLBACK

        # Compute base capacities per (region, cost_tier, gas_type) from the cost tiers.
        # base_cap[(region, cost_tier, gas_type)] = BCF
        self._base_cap: dict[tuple[str, str, str], float] = {}
        for region in self.regions:
            for cost_tier_idx, cost_tier_label in enumerate(COST_TIER_LABELS):
                cost_tier_bcf = self._supply_cost_tiers[region][cost_tier_idx][0]
                for (t_label, gas_type), share in self._cost_tier_shares.items():
                    if t_label == cost_tier_label:
                        self._base_cap[(region, cost_tier_label, gas_type)] = cost_tier_bcf * share

        # Result storage: {(region, cost_tier, gas_type, year): capacity_BCF}, or without
        # gas_type when the NA/AD split is off
        self._results: dict[tuple[str, str, int], float] = {}

        # Cumulative production tracker for conventional depletion:
        # {(region, 'low_cost'): cumulative_BCF_since_base_year}
        self._cumulative_prod: dict[tuple[str, str], float] = {
            (r, 'low_cost'): 0.0 for r in self.regions
        }

        logger.debug(
            'USGasModule initialised: base_year=%d, %d (region, cost_tier, gas_type) entries',
            self.base_year,
            len(self._base_cap),
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def _price_driven_cost_tiers(
        self, region: str, year: int, gas_price: float
    ) -> dict[str, float]:
        """Capacity by cost tier (BCF) for one region and year from the gas price alone.

        Base capacity x price response x technology trend, with depletion applied to the
        low-cost tier. No oil term and no calibration.
        """
        cost_tier_totals: dict[str, float] = dict.fromkeys(COST_TIER_LABELS, 0.0)
        for (r, cost_tier, gas_type), base_cap in self._base_cap.items():
            if r != region:
                continue
            elasticity = SUPPLY_ELASTICITY[gas_type]
            trend = self._tech_trend[gas_type]
            price_ratio = gas_price / BASE_WELLHEAD_PRICE_PER_MMBTU
            tech_factor = (1.0 + trend) ** (year - self.base_year)
            cost_tier_totals[cost_tier] += base_cap * (price_ratio**elasticity) * tech_factor
        depl_rate = self._depletion_rate.get(region, 0.010)
        years_elapsed = max(year - self.base_year, 0)
        depl_factor = max(1.0 - depl_rate * years_elapsed, 0.50)
        cost_tier_totals['low_cost'] *= depl_factor
        return cost_tier_totals

    def compute_capacity(
        self,
        prices_by_region_year: dict[tuple[str, int], float],
        years: list[int],
        oil_prices_by_region_year: dict[tuple[str, int], float] | None = None,
        ref_prices_by_region_year: dict[tuple[str, int], float] | None = None,
        ref_oil_prices_by_region_year: dict[tuple[str, int], float] | None = None,
    ) -> dict:
        """Compute cost_tier capacity by region and year from wellhead and oil prices.

        For each region ``r``, year ``y``, and gas type ``g``:

            cap_gas(g, r, y) = base_cap(g, r)
                               × (gas_price / BASE_GAS_PRICE) ^ gas_elasticity(g)
                               × (1 + tech_trend(g)) ^ (y - BASE_YEAR)

        For the low-cost (conventional) cost_tier, a resource depletion factor
        reduces capacity proportionally to years elapsed since BASE_YEAR:

            depletion_factor = max(1 - depletion_rate × (y - BASE_YEAR), 0.5)

        The floor of 0.5 prevents total conventional supply from collapsing —
        reflecting that some production is always economic even in mature fields.

        With the NA/AD split on (the shipped inputs), a share ``ad_share(r)`` of capacity is
        associated-dissolved gas. It is evaluated at the reference gas price and scaled by
        ``(oil / reference oil) ^ elasticity``, with a different elasticity for rises and falls;
        the rest is non-associated gas at the actual gas price. At reference prices the two add
        up to the unsplit total.

        With the split off, an oil-associated amount is added to the medium-cost tier instead:

            cap_assoc(r, y) = oil_assoc_fraction × base_medium_cap(r)
                              × (oil_price / BASE_OIL_PRICE) ^ oil_elasticity(r)

        Either way, the result is then multiplied by the calibration factor
        ``k * (1 + g) ^ x * exp(c * x^2)``, x = y - CAL_ANCHOR_YEAR.

        Parameters
        ----------
        prices_by_region_year : dict[(region, year), float]
            Wellhead gas prices, 2023 $/MMBtu.
        years : list[int]
            Years to compute.
        oil_prices_by_region_year : dict[(region, year), float] | None
            Crude oil prices, 2023 $/bbl.  If None, the base oil price is used
            (no oil-price stimulus above the baseline).
        ref_prices_by_region_year, ref_oil_prices_by_region_year : dict | None
            Reference gas and oil prices for the NA/AD split, same units and keys. A missing
            entry falls back to the actual price, which makes the split neutral for it.

        Returns
        -------
        dict
            Capacity in BCF/year, keyed ``(region, cost_tier, gas_type, year)`` with the NA/AD
            split on, ``(region, cost_tier, year)`` with it off.
        """
        result: dict = {}

        # ── Optional well-level engine ────────────────────────────────────────────
        # The engine gives (region, gas_type, year) totals from its drilling and production
        # simulation over the whole price path (module.py passes all years). NA gas is spread
        # across the region's cost tiers in proportion to their base capacities; AD gas goes to
        # medium_cost, where NEMS also books tight oil's associated gas. The (k, g, c)
        # calibration applies on top; the reduced-form NA/AD split and oil add-on do not.
        if self._engine is not None:
            # Years before the engine's first year (2024) use the reduced form for all regions,
            # so a run can still start in the history year (2023).
            pre_engine_years = [y for y in years if y < 2024]
            years = [y for y in years if y >= 2024]
            for region in self.regions:
                for year in pre_engine_years:
                    gas_price = max(
                        prices_by_region_year.get((region, year), BASE_WELLHEAD_PRICE_PER_MMBTU),
                        0.01,
                    )
                    cost_tiers = self._price_driven_cost_tiers(region, year, gas_price)
                    k_cal, g_cal, c_cal = self._calib.get(region, (1.0, 0.0, 0.0))
                    x_cal = year - CAL_ANCHOR_YEAR
                    cal = k_cal * (1.0 + g_cal) ** x_cal * math.exp(c_cal * x_cal * x_cal)
                    for t, v in cost_tiers.items():
                        result[(region, t, 'na', year)] = max(v * cal, 0.0)
            if not years:
                return result
            price_years = [y for (_r, y) in prices_by_region_year] or list(years)
            sim = self._engine.simulate(
                prices_by_region_year,
                oil_prices_by_region_year or {},
                max(min(min(price_years), min(years)), 2024),
                max(years),
            )
            cost_tier_share: dict[str, dict[str, float]] = {}
            for region in self.regions:
                totals = dict.fromkeys(COST_TIER_LABELS, 0.0)
                for (r, cost_tier, _g), cap in self._base_cap.items():
                    if r == region:
                        totals[cost_tier] += cap
                s = sum(totals.values()) or 1.0
                cost_tier_share[region] = {t: v / s for t, v in totals.items()}
            for row in sim.itertuples():
                if row.year not in years:
                    continue
                k_cal, g_cal, c_cal = self._calib.get(row.region, (1.0, 0.0, 0.0))
                x_cal = row.year - CAL_ANCHOR_YEAR
                cal = k_cal * (1.0 + g_cal) ** x_cal * math.exp(c_cal * x_cal * x_cal)
                if row.gas_type == 'ad':
                    key = (row.region, 'medium_cost', 'ad', row.year)
                    result[key] = result.get(key, 0.0) + max(row.gas_bcf * cal, 0.0)
                else:
                    for t, share in cost_tier_share[row.region].items():
                        key = (row.region, t, 'na', row.year)
                        result[key] = result.get(key, 0.0) + max(row.gas_bcf * share * cal, 0.0)
            # Regions with no projects in the decks (New England, about 50 BCF) keep the reduced
            # form: the engine cannot represent them, and a calibration of k x 0 could never
            # bring them back.
            deck_regions = {r for (r, _g) in self._engine._gas.index} | set(
                self._engine._cont['region']
            )
            for region in self.regions:
                if region in deck_regions:
                    continue
                for year in years:
                    gas_price = max(
                        prices_by_region_year.get((region, year), BASE_WELLHEAD_PRICE_PER_MMBTU),
                        0.01,
                    )
                    cost_tiers = self._price_driven_cost_tiers(region, year, gas_price)
                    k_cal, g_cal, c_cal = self._calib.get(region, (1.0, 0.0, 0.0))
                    x_cal = year - CAL_ANCHOR_YEAR
                    cal = k_cal * (1.0 + g_cal) ** x_cal * math.exp(c_cal * x_cal * x_cal)
                    for t, v in cost_tiers.items():
                        result[(region, t, 'na', year)] = max(v * cal, 0.0)
            return result

        for region in self.regions:
            for year in years:
                gas_price = max(
                    prices_by_region_year.get((region, year), BASE_WELLHEAD_PRICE_PER_MMBTU),
                    0.01,
                )
                oil_price = max(
                    (oil_prices_by_region_year or {}).get((region, year), BASE_OIL_PRICE_PER_BBL),
                    1.0,
                )

                # ── Gas-price-driven capacity ───────────────────────────────
                cost_tier_totals = self._price_driven_cost_tiers(region, year, gas_price)

                # ── NA/AD split ──────────────────────────────────────────────
                # AD gas does not respond to the gas price: it is ad_share x the capacity at
                # the reference gas price x f_ad, where f_ad is the oil price over its reference
                # raised to a regional elasticity (one for rises, one for falls). NA gas is
                # (1 - ad_share) x the capacity at the actual gas price. At reference prices NA +
                # AD equals the unsplit total, so the calibration still holds. The oil add-on
                # further down is not used when the split is on.
                if self._na_ad_split_active:
                    ref_gas_price = max(
                        (ref_prices_by_region_year or {}).get((region, year), gas_price), 0.01
                    )
                    ref_oil_price = max(
                        (ref_oil_prices_by_region_year or {}).get((region, year), oil_price), 1.0
                    )
                    ref_cost_tiers = self._price_driven_cost_tiers(region, year, ref_gas_price)

                    s_ad = min(max(self._ad_share.get(region, 0.0), 0.0), 1.0)
                    elas_up, elas_down = self._ad_elas.get(region, (0.0, 0.0))
                    oil_ratio = oil_price / ref_oil_price
                    f_ad = oil_ratio ** (elas_up if oil_ratio > 1.0 else elas_down)

                    k_cal, g_cal, c_cal = self._calib.get(region, (1.0, 0.0, 0.0))
                    x_cal = year - CAL_ANCHOR_YEAR
                    cal_factor = k_cal * (1.0 + g_cal) ** x_cal * math.exp(c_cal * x_cal * x_cal)

                    for cost_tier, cap_val in cost_tier_totals.items():
                        result[(region, cost_tier, 'na', year)] = max(
                            (1.0 - s_ad) * cap_val * cal_factor, 0.0
                        )
                    ad_total = s_ad * sum(ref_cost_tiers.values()) * f_ad
                    # AD is reported under medium_cost (tight/shale), where NEMS also books
                    # tight oil's associated gas
                    result[(region, 'medium_cost', 'ad', year)] = max(ad_total * cal_factor, 0.0)
                    if abs(f_ad - 1.0) > 1e-9:
                        logger.debug(
                            '%s year=%d: AD %.1f BCF (f_AD=%.4f, oil ratio %.3f)',
                            region,
                            year,
                            ad_total,
                            f_ad,
                            oil_ratio,
                        )
                    continue  # this (region, year) is done

                # ── Oil-price associated gas (medium-cost cost_tier) ──────────
                # Only reached when the NA/AD split is off.
                oil_assoc_elas = OIL_ASSOCIATED_GAS_ELASTICITY.get(region, 0.0)
                if abs(oil_assoc_elas) > 1e-9:
                    # Base medium cost_tier capacity
                    mid_idx = COST_TIER_LABELS.index('medium_cost')
                    base_medium = self._supply_cost_tiers[region][mid_idx][0]
                    oil_ratio = oil_price / BASE_OIL_PRICE_PER_BBL
                    assoc_cap = self._oil_assoc_fraction * base_medium * (oil_ratio**oil_assoc_elas)
                    cost_tier_totals['medium_cost'] += assoc_cap
                    logger.debug(
                        '%s year=%d: oil-assoc gas +%.1f BCF (oil=%.1f $/bbl)',
                        region,
                        year,
                        assoc_cap,
                        oil_price,
                    )

                # ── Calibration layer (see CAL_ANCHOR_YEAR above) ────────────
                k_cal, g_cal, c_cal = self._calib.get(region, (1.0, 0.0, 0.0))
                x_cal = year - CAL_ANCHOR_YEAR
                cal_factor = k_cal * (1.0 + g_cal) ** x_cal * math.exp(c_cal * x_cal * x_cal)

                for cost_tier, cap_val in cost_tier_totals.items():
                    result[(region, cost_tier, year)] = max(cap_val * cal_factor, 0.0)

        return result

    def run_year(
        self,
        year: int,
        prices: dict[tuple[str, int], float],
        oil_prices: dict[tuple[str, int], float] | None = None,
        ref_prices: dict[tuple[str, int], float] | None = None,
        ref_oil_prices: dict[tuple[str, int], float] | None = None,
    ) -> None:
        """Compute and store capacity results for a single year.

        Parameters
        ----------
        year : int
            Calendar year to compute.
        prices : dict[(region, year), float]
            Wellhead gas prices, 2023 $/MMBtu.
        oil_prices : dict[(region, year), float] | None
            Crude oil prices, 2023 $/bbl.  If None, oil-associated gas
            is computed at BASE_OIL_PRICE_PER_BBL (no oil-price stimulus).
        ref_prices, ref_oil_prices : dict[(region, year), float] | None
            Reference prices for the NA/AD split (see ``compute_capacity``).
        """
        year_results = self.compute_capacity(
            prices,
            [year],
            oil_prices_by_region_year=oil_prices,
            ref_prices_by_region_year=ref_prices,
            ref_oil_prices_by_region_year=ref_oil_prices,
        )
        self._results.update(year_results)
        total = sum(v for k, v in year_results.items() if k[-1] == year)
        logger.debug(
            'USGasModule.run_year(%d): total capacity %.2f BCF across all regions/cost_tiers',
            year,
            total,
        )

    def get_capacity_updates(self, years: list[int]) -> dict[tuple[str, str, int], float]:
        """Return capacity updates for the requested years.

        Parameters
        ----------
        years : list[int]
            Years to include in the returned dict.

        Returns
        -------
        dict
            Capacity in BCF/year, with the same keys as ``compute_capacity``; the year is
            always the last element of the key.
        """
        return {key: val for key, val in self._results.items() if key[-1] in years}

    def reset(self) -> None:
        """Clear result state for a new Gauss-Seidel iteration."""
        self._results.clear()
        logger.debug('USGasModule.reset(): results cleared')
