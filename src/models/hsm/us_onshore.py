"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  7/12/26

US onshore production from the NEMS project decks in ``input/hsm/onshore/``.

Two classes live here:

- ``OnshoreLegacy`` adds up the production of wells that are already producing (the producing
  and CO2-EOR decks, no new drilling) by census division, gas type and year. C-HSM uses it for
  two outputs, ``hsm_us_legacy_gas.csv`` and ``hsm_us_crude.csv``. It does not feed the supply
  capacity.
- ``OnshoreEngine`` is an optional well-level engine: existing wells plus new drilling that
  responds to prices, with a simple per-well cash flow test. It is off unless the run config
  names its settings file (``[hsm] onshore_engine_file``). ``ENGINE_PLAN.md`` says what it
  still needs before its results are used.

The drilling rules are rewritten from ``models/hsm/drilling_equations.py`` of EIA's National
Energy Modeling System (NEMS), https://github.com/EIAgov/NEMS, release tag
``AEO2026-Public-Release``, licensed under the Apache License 2.0, and keep its constants.

About the decks (checked against ``models/hsm/onshore.py`` of the same release,
``calculate_production``):

- ``GP1..GP40`` are natural gas in MMcf/yr and ``OP1..OP40`` are crude plus lease condensate in
  thousand barrels/yr, one column per year of the well's life.
- ``GP1`` is taken as calendar year 2024, so column ``GPk`` is calendar year 2023 + k. The decks
  are from the AEO2026 release, whose first projection year is 2025; see ``ENGINE_PLAN.md``,
  step 2.
- Gas from projects with ``well_type_number`` below 3 (conventional oil and tight oil, in
  ``on_process_codes.csv``) counts as associated-dissolved (AD); the rest is non-associated (NA).
  This is the same rule NEMS uses for its AD gas output.
"""

# Single values are read with .at throughout, as in the NEMS code this follows.
# ruff: noqa: PD008

from __future__ import annotations

import logging
import math

import pandas as pd

logger = logging.getLogger(__name__)

PRODUCING_DECKS = [
    'on_projects_producing_oil.csv',
    'on_projects_producing_gas.csv',
    'on_projects_co2_eor.csv',
]
PROFILE_YEARS = 40  # GP1..GP40 / OP1..OP40
GP1_CALENDAR_YEAR = 2024  # GPk <-> GP1_CALENDAR_YEAR + (k-1)
AD_WELL_TYPES_BELOW = 3  # well_type_number < 3 is oil, so its gas is AD

CENSUS_TO_REGION = {
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


class OnshoreLegacy:
    """Production of wells already producing in the base year, from the producing decks.

    Parameters
    ----------
    onshore_path : str
        Path to ``input/hsm/onshore/`` (trailing slash tolerated).
    mapping_path : str
        Path to ``input/hsm/mapping.csv`` (district_number -> census_division).
    apply_decline_overrides : bool
        Reshape the oil and shale gas decline curves the way NEMS does on load. Off by default,
        and only the optional engine can switch it on.
    """

    def __init__(
        self, onshore_path: str, mapping_path: str, apply_decline_overrides: bool = False
    ) -> None:
        op = str(onshore_path).rstrip('/\\') + '/'

        codes = pd.read_csv(op + 'on_process_codes.csv', skiprows=1)
        well_type = codes.set_index('process_code')['well_type_number']
        oil_type = codes.set_index('process_code')['oil_type_number']
        gas_type_num = codes.set_index('process_code')['gas_type_number']

        mapping = pd.read_csv(mapping_path)[['district_number', 'census_division']]
        cd_of = mapping.drop_duplicates().set_index('district_number')['census_division']

        gp_cols = [f'GP{i}' for i in range(1, PROFILE_YEARS + 1)]
        op_cols = [f'OP{i}' for i in range(1, PROFILE_YEARS + 1)]

        frames = []
        for fname in PRODUCING_DECKS:
            df = pd.read_csv(op + fname, skiprows=1, low_memory=False)
            keep = (
                ['process_code', 'district_number', 'play_name', 'region_name'] + gp_cols + op_cols
            )
            frames.append(df[[c for c in keep if c in df.columns]])
        decks = pd.concat(frames, ignore_index=True)
        for c in gp_cols + op_cols:
            if c not in decks.columns:
                decks[c] = 0.0
            decks[c] = pd.to_numeric(decks[c], errors='coerce').fillna(0.0)

        # Optional (off by default): reshape decline curves the way NEMS does on load, see
        # NEMS models/hsm/onshore.py, _apply_decline_curve. Rates are looked up by play
        # first, then by (region, type). Oil projects get the oil rates on both columns, since
        # their gas declines with the well; shale gas projects get the gas rates. The profile is
        # rebuilt as year 2 = year 1 x (1 - first-year decline), then a constant decline rate.
        if apply_decline_overrides:

            def _rates(path, key_cols):
                df = pd.read_csv(op + path, skiprows=1)
                df.columns = [str(c).strip() for c in df.columns]
                out = {}
                for r in df.itertuples():
                    key = tuple(
                        getattr(r, k, None) if pd.notna(getattr(r, k, None)) else None
                        for k in key_cols
                    )
                    out[key] = (float(r.year_1_decline), float(r.decline_rate))
                return out

            oil_play = _rates('on_producing_oil_decline_rates_plays.csv', ['play_name'])
            oil_reg = _rates(
                'on_producing_oil_decline_rates_regions.csv', ['region_name', 'oil_type_number']
            )
            gas_play = _rates('on_producing_gas_decline_rates_play.csv', ['play_name'])
            gas_reg = _rates(
                'on_producing_gas_decline_rates_regions.csv', ['region_name', 'gas_type_number']
            )

            wt = decks['process_code'].map(well_type)
            ot = decks['process_code'].map(oil_type)
            gt = decks['process_code'].map(gas_type_num)
            n_applied = 0
            for i in decks.index:
                if decks.at[i, 'process_code'] > 7:
                    continue  # legacy producing only (NEMS filter)
                play = str(decks.at[i, 'play_name']) if 'play_name' in decks.columns else ''
                region = (
                    str(decks.at[i, 'region_name']).strip()
                    if 'region_name' in decks.columns
                    else ''
                )
                is_oil = wt[i] < AD_WELL_TYPES_BELOW and decks.at[i, 'OP1'] > 0
                is_sgas = wt[i] == 5 and decks.at[i, 'GP1'] > 0
                if not (is_oil or is_sgas):
                    continue
                if is_oil:
                    y1, rate = oil_play.get((play,), (0.0, 0.0))
                    if y1 <= 0:
                        y1, rate = oil_reg.get(
                            (region, ot[i]), oil_reg.get((region, None), (0.0, 0.0))
                        )
                else:
                    y1, rate = gas_play.get((play,), (0.0, 0.0))
                    if y1 <= 0:
                        y1, rate = gas_reg.get(
                            (region, gt[i]), gas_reg.get((region, None), (0.0, 0.0))
                        )
                if y1 <= 0:
                    continue
                ret = 1.0 - y1
                for cols, base in ((op_cols, decks.at[i, 'OP1']), (gp_cols, decks.at[i, 'GP1'])):
                    if base <= 0:
                        continue
                    decks.at[i, cols[1]] = base * ret
                    for k in range(3, PROFILE_YEARS + 1):
                        decks.at[i, cols[k - 1]] = base * ret * (1.0 - rate) ** (k - 2)
                n_applied += 1
            logger.info('OnshoreLegacy: decline overrides applied to %d legacy projects', n_applied)

        decks['well_type_number'] = decks['process_code'].map(well_type)
        if decks['well_type_number'].isna().any():
            missing = sorted(decks.loc[decks.well_type_number.isna(), 'process_code'].unique())
            raise ValueError(f'process_code(s) missing from on_process_codes.csv: {missing}')
        decks['gas_type'] = decks['well_type_number'].map(
            lambda w: 'ad' if w < AD_WELL_TYPES_BELOW else 'na'
        )
        decks['cd'] = decks['district_number'].map(cd_of)
        if decks['cd'].isna().any():
            missing = sorted(decks.loc[decks.cd.isna(), 'district_number'].unique())
            raise ValueError(f'district_number(s) missing from mapping.csv: {missing}')
        decks['region'] = decks['cd'].map(CENSUS_TO_REGION)

        # Add up once by (region, gas_type); columns are the year of the well's life
        gas = decks.groupby(['region', 'gas_type'])[gp_cols].sum()
        crude = decks.groupby(['region', 'gas_type'])[op_cols].sum()
        gas.columns = crude.columns = range(1, PROFILE_YEARS + 1)
        self._gas = gas  # MMcf/yr by profile position
        self._crude = crude  # Mbbl/yr by profile position
        self.n_projects = len(decks)
        logger.info(
            'OnshoreLegacy: %d producing projects loaded (%d regions)',
            self.n_projects,
            decks['region'].nunique(),
        )

    def annual_production(self, years: list[int]) -> pd.DataFrame:
        """Production of the existing wells in the calendar ``years``.

        Returns
        -------
        pd.DataFrame
            Columns: region, gas_type (na/ad), year, gas_bcf, crude_mbbl.
            Years past the 40-year profile return 0 (the wells are treated as depleted);
            years before GP1_CALENDAR_YEAR are skipped.
        """
        rows = []
        for year in years:
            k = year - GP1_CALENDAR_YEAR + 1
            if k < 1:
                continue
            in_range = k <= PROFILE_YEARS
            for region, gas_type in self._gas.index:
                rows.append(
                    {
                        'region': region,
                        'gas_type': gas_type,
                        'year': year,
                        'gas_bcf': round(self._gas.at[(region, gas_type), k] / 1e3, 3)
                        if in_range
                        else 0.0,  # MMcf -> Bcf
                        'crude_mbbl': round(self._crude.at[(region, gas_type), k], 1)
                        if in_range
                        else 0.0,
                    }
                )
        return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Drilling rules, used only by the optional engine. They are rewritten from
# NEMS models/hsm/drilling_equations.py (AEO2026 release; line numbers in each docstring) and
# keep its constants; where they are simplified, the docstring says so.
# ---------------------------------------------------------------------------


def calculate_price_adjustment(
    oil_price,
    ng_price,
    well_type_num,
    base_oil_prc,
    base_gas_prc,
    oil_prod,
    gas_prod,
    low_price_flag=0,
):
    """How much drilling moves with prices.

    From drilling_equations.calculate_price_adjustment (NEMS lines 388-530).

    Oil wells (well_type 1 or 2) respond to the oil price over its base; gas wells to the gas
    price over its base. For tight oil and for shale or tight gas, the exponent grows with the
    well's production on the way up and shrinks on the way down, so bigger producers respond
    more to price rises and less to price falls; any change is at least 2%. Oil wells with a
    high gas-oil ratio also respond to the gas price, and wells with no production take the
    square root of the ratio. The adjustment is capped at 2.0, and ``low_price_flag`` softens
    the downside by 1.25x.
    """
    MAX_PRICE_ADJUSTMENT = 2.0
    MAX_GAS_ADJ_RATIO = 1.5
    GAS_TO_OIL_CONVERSION = 5.6 * 1000
    HIGH_GAS_OIL_RATIO_THRESHOLD = 6000
    DAMP_EXP = 0.5
    TIGHT_OIL_MIN_PROD, TIGHT_OIL_DIV = 1, 100
    TIGHT_OIL_MIN_ADJ, TIGHT_OIL_MAX_ADJ = 0.98, 1.02
    SHALE_GAS_MIN_PROD, SHALE_GAS_DIV = 10, 1000
    SHALE_GAS_MIN_ADJ, SHALE_GAS_MAX_ADJ = 0.98, 1.02
    LOW_PRICE_FLAG_MULT = 1.25

    if any(pd.isna(x) for x in (oil_price, ng_price, base_oil_prc, base_gas_prc)):
        return 1.0
    is_oil_well = well_type_num <= 2
    price_adj = (oil_price / base_oil_prc) if is_oil_well else (ng_price / base_gas_prc)

    if is_oil_well:
        gas_adj = min(ng_price / base_gas_prc, MAX_GAS_ADJ_RATIO)
        if oil_prod > 0:
            gas_oil_ratio = gas_prod * GAS_TO_OIL_CONVERSION / oil_prod
            if (well_type_num == 2) and (oil_prod >= TIGHT_OIL_MIN_PROD):
                if price_adj < 1:
                    sens = 1.0 / ((oil_prod / TIGHT_OIL_DIV) ** DAMP_EXP)
                    price_adj = min(price_adj**sens, TIGHT_OIL_MIN_ADJ)
                elif price_adj > 1:
                    sens = (oil_prod / TIGHT_OIL_DIV) ** DAMP_EXP
                    price_adj = max(price_adj**sens, TIGHT_OIL_MAX_ADJ)
            if gas_oil_ratio > HIGH_GAS_OIL_RATIO_THRESHOLD:
                price_adj = price_adj ** (gas_adj**DAMP_EXP)
        else:
            price_adj = price_adj**DAMP_EXP
    else:
        if gas_prod > 0:
            if well_type_num in (4, 5) and gas_prod >= SHALE_GAS_MIN_PROD:
                if price_adj < 1:
                    sens = 1.0 / ((gas_prod / SHALE_GAS_DIV) ** DAMP_EXP)
                    price_adj = min(price_adj**sens, SHALE_GAS_MIN_ADJ)
                if price_adj > 1:
                    sens = (gas_prod / SHALE_GAS_DIV) ** DAMP_EXP
                    price_adj = max(price_adj**sens, SHALE_GAS_MAX_ADJ)
        else:
            price_adj = price_adj**DAMP_EXP

    price_adj = min(price_adj, MAX_PRICE_ADJUSTMENT)
    if low_price_flag == 1 and price_adj < 1:
        price_adj = price_adj * LOW_PRICE_FLAG_MULT
    return price_adj


def _base_well_count(
    well_decline_limit,
    max_wells,
    past_drilling,
    last_year_drilling,
    max_drill_rate,
    max_drill_rate_frac,
    drill_predecline,
    ramp_up_years,
    start_year,
):
    """Wells drilled before any price response.

    From drilling_equations._calculate_base_well_count (NEMS lines 65-167).
    """
    cumulative, current, decline_start, year = 0, 0, 0, start_year
    if (well_decline_limit > 0) and (max_wells > past_drilling):
        while cumulative <= past_drilling:
            frac = cumulative / well_decline_limit
            if year < ramp_up_years:
                current = max(last_year_drilling, int((max_drill_rate / ramp_up_years) * year))
                if frac > drill_predecline:
                    current = int(current * (1.0 - max_drill_rate_frac) ** (year - decline_start))
                    break
                decline_start = year
            else:
                current = int(max_drill_rate)
                if frac > drill_predecline:
                    current = int(current * (1.0 - max_drill_rate_frac) ** (year - decline_start))
                else:
                    decline_start = year
            current = max(current, 5)
            cumulative += current
            year += 1
    return current, year, decline_start


def on_next_wells(
    well_decline_limit,
    max_wells,
    past_drilling,
    last_year_drilling,
    max_drill_rate,
    max_drill_rate_frac,
    drill_predecline,
    ramp_up_years,
    oil_price,
    ng_price,
    well_type_num,
    base_oil_prc,
    base_gas_prc,
    oil_prod,
    gas_prod,
    max_year_drill_percent,
    low_price_flag=0,
):
    """Wells a continuous project drills next year.

    From drilling_equations.on_next_wells (NEMS lines 170-385, the continuous-project path).

    Simplified: the branch for undiscovered resources (``undiscovered_drill_flag``) is left
    out, and ``current_model_year`` is not used.
    """
    import math

    if last_year_drilling > max_drill_rate * 0.8:
        start_year = ramp_up_years
    else:
        start_year = math.ceil(last_year_drilling / max(1, max_drill_rate) * ramp_up_years) + 1

    current, sim_year, _ = _base_well_count(
        well_decline_limit,
        max_wells,
        past_drilling,
        last_year_drilling,
        max_drill_rate,
        max_drill_rate_frac,
        drill_predecline,
        ramp_up_years,
        start_year,
    )

    if (current > last_year_drilling * 2.0) and (last_year_drilling > 0):
        current = max(last_year_drilling * 2.0, 10)

    remaining = max_wells - past_drilling
    max_allowed = max(remaining * max_year_drill_percent, 0)
    if sim_year < ramp_up_years:
        max_allowed *= sim_year / ramp_up_years

    current = max(current, 0)
    current *= calculate_price_adjustment(
        oil_price,
        ng_price,
        well_type_num,
        base_oil_prc,
        base_gas_prc,
        oil_prod,
        gas_prod,
        low_price_flag,
    )
    current = min(current, max_allowed)
    if current < 0:
        raise ValueError('negative drilling calculated')
    current = max(current, last_year_drilling * 0.8)  # limit decline rate
    return math.floor(current)


# Constants for the optional engine's cash flow test. On its own the drilling rule drills
# about 50% too much; NEMS decides which projects drill with a discounted cash flow, and the
# engine does a simple version of that. PLAY_TO_BASIN matches play names to the basin terms
# in the drilling cost regression (on_drill_cost_eqs.csv); a project with no match falls
# back to its state term, then to the intercept alone.
PLAY_TO_BASIN = {
    'Bakken': 'Williston Basin',
    'Marcellus': 'Appalachian Basin (Marcellus)',
    'Utica': 'Appalachian Basin (Utica)',
    'Haynesville': 'East Texas Basin/LA-MS Salt Basins (Haynesville)',
    'Barnett': 'Bend Arch-Ft. Worth Basin',
    'Fayetteville': 'Arkoma Basin',
    'Woodford': 'Anadarko Basin',
    'Anadarko': 'Anadarko Basin',
    'Niobrara': 'Denver Basin',
    'Spraberry': 'Permian Basin',
    'Wolfcamp': 'Permian Basin',
    'Bone': 'Permian Basin',
    'Avalon': 'Permian Basin',
    'Permian': 'Permian Basin',
    'San_Juan': 'San Juan Basin',
    'Uinta': 'Uinta-Piceance Basin',
    'Piceance': 'Uinta-Piceance Basin',
    'Powder': 'Powder River Basin',
}
WELL_TYPE_MERGE = {12: 'Tight Oil', 13: 'Shale Gas', 15: 'Coalbed Methane'}
MMBTU_PER_MMCF = 1037.0
MCF_PER_BOE = 5.6

# Every setting the engine reads from its settings file (us_onshore_engine.csv).
ENGINE_SETTINGS = (
    'base_gas_price_2023',
    'ramp_up_years',
    'drill_predecline',
    'low_price_flag',
    'discount_rate',
    'rig_wells_cap_oil',
    'rig_wells_cap_gas',
    'rig_growth_rate',
    'capex_deflator_1987_to_2023',
    'royalty_rate',
    'severance_rate',
    'apply_decline_overrides',
)


def read_engine_settings(path: str) -> pd.Series:
    """Read the engine settings file, checking that every setting is there exactly once.

    Parameters
    ----------
    path : str
        A CSV with columns ``parameter`` and ``value`` (``#`` lines are comments).

    Returns
    -------
    pd.Series
        Value by setting name, as floats.

    Raises
    ------
    ValueError
        If a setting in ``ENGINE_SETTINGS`` is missing or repeated, a name is not one of them
        (for example a misspelling), or a value is blank or not a finite number.
    """
    df = pd.read_csv(path, comment='#')
    names = df['parameter'].astype(str).str.strip()
    unknown = sorted(set(names) - set(ENGINE_SETTINGS))
    missing = sorted(set(ENGINE_SETTINGS) - set(names))
    repeated = sorted(set(names[names.duplicated()]))
    if unknown or missing or repeated:
        raise ValueError(
            f'{path}: unknown settings {unknown}, missing settings {missing}, '
            f'repeated settings {repeated}'
        )
    values = pd.to_numeric(df['value'], errors='coerce')
    bad = sorted(names[~values.apply(lambda v: pd.notna(v) and math.isfinite(v))])
    if bad:
        raise ValueError(f'{path}: blank or non-numeric values for {bad}')
    return pd.Series(values.to_numpy(dtype=float), index=names.to_numpy())


class OnshoreEngine(OnshoreLegacy):
    """Optional well-level supply engine: existing wells plus new drilling that responds to price.

    Used only when ``[hsm] onshore_engine_file`` names its settings file (normally
    ``input/hsm/us_onshore_engine.csv``). It then replaces the reduced-form supply in
    ``us_gas.py``, with its own ``(k, g, c)`` calibration (``us_gas_calibration_engine.csv``).
    ``ENGINE_PLAN.md`` has its status and the steps still to do.

    It works on the continuous decks (unconventional projects still being developed, process
    codes 12, 13 and 15). Each project's GP/OP columns are the profile of one well pattern;
    ``past_wells`` and ``totpat`` are the wells drilled so far and the most the resource allows.
    Each year every project that passes the cash flow test drills wells by the NEMS rule above,
    and production in year y is the existing wells plus, for each earlier year v, the wells
    drilled in v times the profile at age y - v + 1.

    Where the parameters come from: ``on_drill_eq_constraints.csv`` (``max_annual_wells``,
    ``max_annual_%_dev``), ``on_process_codes.csv`` (``max_drill_rate_frac``),
    ``base_oil_prc_by_play.csv`` (oil price threshold by play, $/bbl, 50 if missing). Simplified:
    ``drill_predecline`` is 0.70, the typical value in the NEMS documentation, and the ramp-up
    years and base gas price come from the settings file, read by ``read_engine_settings``.
    """

    def __init__(self, onshore_path: str, mapping_path: str, engine_config_path: str) -> None:
        # The settings decide, among other things, whether to reshape the decline curves of the
        # existing wells (apply_decline_overrides).
        cfg = read_engine_settings(engine_config_path)
        super().__init__(
            onshore_path,
            mapping_path,
            apply_decline_overrides=bool(int(float(cfg.get('apply_decline_overrides', 0)))),
        )
        op = str(onshore_path).rstrip('/\\') + '/'

        self.base_gas_price = float(cfg.get('base_gas_price_2023', 2.50))
        self.ramp_up_years = int(float(cfg.get('ramp_up_years', 5)))
        self.drill_predecline = float(cfg.get('drill_predecline', 0.70))
        self.low_price_flag = int(float(cfg.get('low_price_flag', 0)))

        codes = pd.read_csv(op + 'on_process_codes.csv', skiprows=1)
        wt = codes.set_index('process_code')['well_type_number']
        frac = codes.set_index('process_code')['max_drill_rate_frac']
        cons = pd.read_csv(op + 'on_drill_eq_constraints.csv', skiprows=1)
        cons.columns = [str(c).strip() for c in cons.columns]
        cons = cons.set_index('process_code')

        base_oil = pd.read_csv(op + 'base_oil_prc_by_play.csv', skiprows=1)
        self._base_oil_by_play = dict(
            zip(
                base_oil['play_name'].astype(str),
                pd.to_numeric(base_oil['base_oil_prc'], errors='coerce'),
                strict=True,
            )
        )
        self._base_oil_default = 50.0

        gp_cols = [f'GP{i}' for i in range(1, PROFILE_YEARS + 1)]
        op_cols = [f'OP{i}' for i in range(1, PROFILE_YEARS + 1)]
        cont = pd.read_csv(op + 'on_projects_continuous.csv', skiprows=1, low_memory=False)
        keep = (
            ['process_code', 'district_number', 'play_name', 'past_wells', 'totpat']
            + gp_cols
            + op_cols
        )
        cont = cont[[c for c in keep if c in cont.columns]].copy()
        for c in gp_cols + op_cols + ['past_wells', 'totpat']:
            cont[c] = pd.to_numeric(cont.get(c), errors='coerce').fillna(0.0)
        cont['well_type_number'] = cont['process_code'].map(wt)
        cont['gas_type'] = cont['well_type_number'].map(
            lambda w: 'ad' if w < AD_WELL_TYPES_BELOW else 'na'
        )
        mapping = pd.read_csv(mapping_path)[['district_number', 'census_division']]
        cd_of = mapping.drop_duplicates().set_index('district_number')['census_division']
        cont['region'] = cont['district_number'].map(cd_of).map(CENSUS_TO_REGION)
        cont['max_drill_rate'] = cont['process_code'].map(cons['max_annual_wells'])
        cont['max_year_drill_percent'] = cont['process_code'].map(cons['max_annual_%_dev'])
        cont['max_drill_rate_frac'] = cont['process_code'].map(frac)
        cont['base_oil_prc'] = (
            cont['play_name'].astype(str).map(self._base_oil_by_play).fillna(self._base_oil_default)
        )
        self._cont = cont.reset_index(drop=True)
        self._gp = self._cont[gp_cols].to_numpy()  # per-pattern MMcf/yr profiles
        self._op = self._cont[op_cols].to_numpy()  # per-pattern Mbbl/yr profiles
        logger.info('OnshoreEngine: %d continuous projects loaded', len(cont))
        self._sim_cache_key = None
        self._sim_cache = None

        # Cash flow test, one number per project:
        #     NPV_i(Pg, Po) = G_i * Pg + O_i * Po - fixed_i
        # G_i and O_i are the discounted lifetime gas (MMBtu) and oil (bbl) of one pattern.
        # fixed_i is drilling capex (the log-linear regression in on_drill_cost_eqs.csv,
        # 1987 $ scaled to 2023 $), facility capex, and discounted overhead and per-boe opex.
        import numpy as np

        self.discount_rate = float(cfg.get('discount_rate', 0.10))
        # Separate yearly well caps for oil-directed and gas-directed drilling. One shared cap
        # let a high oil price crowd out gas drilling by about 7%; NEMS's rig limits are
        # per type and bind in booms, not in the reference case.
        self.rig_cap_oil = float(cfg.get('rig_wells_cap_oil', 11000))
        self.rig_cap_gas = float(cfg.get('rig_wells_cap_gas', 9000))
        self.capex_deflator = float(cfg.get('capex_deflator_1987_to_2023', 2.17))
        # New wells get better each year they are drilled later: a simple version of NEMS
        # calculate_prod_tech_improvement, using the annual rates in on_tech_levers.csv
        self.tech_trend_by_type = {12: 0.01, 13: 0.01, 15: 0.0025}  # tight oil/shale/CBM
        try:
            lev = pd.read_csv(op + 'on_tech_levers.csv', skiprows=1)
            lev.columns = [str(c).strip() for c in lev.columns]
            col = [c for c in lev.columns if 'rate' in c.lower() or 'value' in c.lower()]
            if col and 'well_type_number' in lev.columns:
                m = dict(
                    zip(
                        lev['well_type_number'],
                        pd.to_numeric(lev[col[0]], errors='coerce'),
                        strict=True,
                    )
                )
                self.tech_trend_by_type = {
                    12: float(m.get(2, 0.01)),
                    13: float(m.get(5, 0.01)),
                    15: float(m.get(6, 0.0025)),
                }
        except Exception as exc:  # noqa: BLE001 - keep the default trends
            logger.debug('OnshoreEngine: trends not read from on_tech_levers.csv (%s)', exc)

        eq = pd.read_csv(op + 'on_drill_cost_eqs.csv', skiprows=1)
        eq['coef'] = pd.to_numeric(eq['coef'], errors='coerce')
        stats_rows = {
            'hist_cost_quantile_01',
            'hist_cost_median',
            'hist_cost_quantile_99',
            'avg_vfeet',
            'avg_latlen',
        }
        self._eq = {
            rt: eq[(eq.resource_type == rt) & (~eq.coef_name.isin(stats_rows))]
            .set_index('coef_name')['coef']
            .to_dict()
            for rt in ('oil', 'gas')
        }

        basin = pd.read_csv(op + 'on_basin_avg_cost.csv', skiprows=1)
        for c in (
            'production_opex_brl',
            'transport_opex_brl',
            'facility_capex_well',
            'sga_opex_well',
        ):
            basin[c] = pd.to_numeric(basin[c], errors='coerce')
        self._opex = basin.groupby('well_type_merge')[
            ['production_opex_brl', 'transport_opex_brl', 'facility_capex_well', 'sga_opex_well']
        ].mean()  # simplified: averages by well type, not by basin

        df = np.array([1.0 / (1.0 + self.discount_rate) ** t for t in range(PROFILE_YEARS)])
        gas_mmbtu_pv = (self._gp * df).sum(axis=1) * MMBTU_PER_MMCF  # MMBtu PV
        oil_bbl_pv = (self._op * df).sum(axis=1) * 1000.0  # bbl PV
        boe_pv = oil_bbl_pv + (self._gp * df).sum(axis=1) * 1000.0 / MCF_PER_BOE
        annuity = df.sum()

        capex = np.zeros(len(self._cont))
        latlen_col = 'Latlen' if 'Latlen' in cont.columns else 'lateral_length_ft'
        for i, row in self._cont.iterrows():
            rt = 'oil' if row['well_type_number'] < AD_WELL_TYPES_BELOW else 'gas'
            coefs = self._eq[rt]
            ln_cost = coefs.get('Intercept', 13.0)
            play = str(row.get('play_name', ''))
            basin_coef = next(
                (
                    coefs[b]
                    for key, b in PLAY_TO_BASIN.items()
                    if key.lower() in play.lower() and b in coefs
                ),
                None,
            )
            if basin_coef is not None:
                ln_cost += basin_coef
            else:
                ln_cost += coefs.get(str(row.get('state', '')), 0.0)
            latlen = float(
                pd.to_numeric(pd.Series([row.get(latlen_col, 0)]), errors='coerce')
                .fillna(0.0)
                .iloc[0]
            )
            depth = float(
                pd.to_numeric(pd.Series([row.get('drill_depth_ft', 0)]), errors='coerce')
                .fillna(0.0)
                .iloc[0]
            )
            ln_cost += coefs.get('num_latlen', 0.0) * latlen
            ln_cost += coefs.get('num_vfeet', 0.0) * depth  # vertical feet taken as drill depth
            capex[i] = float(np.exp(ln_cost)) * self.capex_deflator

        wt_merge = self._cont['process_code'].map(WELL_TYPE_MERGE)
        opex_boe = (
            wt_merge.map(self._opex['production_opex_brl'] + self._opex['transport_opex_brl'])
            .fillna(3.0)
            .to_numpy()
        )
        facility = wt_merge.map(self._opex['facility_capex_well']).fillna(3e5).to_numpy()
        sga = wt_merge.map(self._opex['sga_opex_well']).fillna(5e5).to_numpy()

        # Royalty and severance taken off revenue as flat rates from the engine config (NEMS
        # cash_flow.py does this project by project). Both default to 0. They scale only the
        # revenue terms; costs stay in _npv_fixed.
        self.royalty_rate = float(cfg.get('royalty_rate', 0.0))
        self.severance_rate = float(cfg.get('severance_rate', 0.0))
        _net = 1.0 - self.royalty_rate - self.severance_rate
        # Optional growth of the yearly well caps (default 0: constant caps)
        self.rig_growth_rate = float(cfg.get('rig_growth_rate', 0.0))

        # NPV(Pg $/MMBtu 2023$, Po $/bbl 2023$) = _npv_g*Pg + _npv_o*Po - _npv_fixed
        self._npv_g = gas_mmbtu_pv * _net
        self._npv_o = oil_bbl_pv * _net
        self._npv_fixed = capex + facility + sga * annuity + opex_boe * boe_pv
        logger.info(
            'OnshoreEngine: capex median $%.1fM (2023$), %d projects with basin match',
            float(np.median(capex)) / 1e6,
            int(
                sum(
                    1
                    for i, row in self._cont.iterrows()
                    if any(
                        k.lower() in str(row.get('play_name', '')).lower() for k in PLAY_TO_BASIN
                    )
                )
            ),
        )

    # -- simulation ---------------------------------------------------------
    def simulate(self, gas_prices, oil_prices, first_year: int, last_year: int) -> pd.DataFrame:
        """Drilling and production from ``first_year`` to ``last_year``.

        ``gas_prices`` and ``oil_prices`` map (region, year) to a 2023 $ price; a missing entry
        means the base price. Returns one row per region, gas type and year with ``gas_bcf``
        and ``crude_mbbl``, existing wells plus new ones.
        """
        # The engine starts at GP1_CALENDAR_YEAR (2024), so an empty range such as 2023 alone
        # returns an empty table with the right columns.
        if last_year < first_year:
            return pd.DataFrame(columns=['region', 'gas_type', 'year', 'gas_bcf', 'crude_mbbl'])
        key = (
            first_year,
            last_year,
            tuple(sorted(gas_prices.items())),
            tuple(sorted((oil_prices or {}).items())),
        )
        if key == self._sim_cache_key:
            return self._sim_cache

        n = len(self._cont)
        years = list(range(first_year, last_year + 1))
        add_gas = {}
        add_oil = {}
        cum = self._cont['past_wells'].to_numpy().copy()
        last = [0.0] * n
        wells_by_vintage = []  # list per year of np arrays
        import numpy as np

        regions_arr = self._cont['region'].to_numpy()
        base_oil_arr = self._cont['base_oil_prc'].to_numpy()
        for year in years:
            # Cash flow test at this year's prices: only projects with NPV > 0 drill, best NPV
            # first, until the yearly well cap runs out (NEMS also ranks projects by
            # profitability before drilling).
            pg = np.array([gas_prices.get((r, year), self.base_gas_price) for r in regions_arr])
            po = np.array(
                [
                    (oil_prices or {}).get((r, year), b)
                    for r, b in zip(regions_arr, base_oil_arr, strict=True)
                ]
            )
            npv = self._npv_g * pg + self._npv_o * po - self._npv_fixed

            wells = np.zeros(n)
            # This year's well caps, oil and gas separately (constant unless rig_growth_rate > 0)
            _grow = (1.0 + self.rig_growth_rate) ** (year - GP1_CALENDAR_YEAR)
            budget = {'oil': self.rig_cap_oil * _grow, 'gas': self.rig_cap_gas * _grow}
            for i in np.argsort(-npv):
                row = self._cont.iloc[i]
                totpat = row['totpat']
                rig_type = 'oil' if row['well_type_number'] < AD_WELL_TYPES_BELOW else 'gas'
                if npv[i] <= 0.0 or cum[i] >= totpat or totpat <= 0 or budget[rig_type] <= 0:
                    last[i] = 0.0
                    continue
                w = on_next_wells(
                    well_decline_limit=totpat,
                    max_wells=totpat,
                    past_drilling=cum[i],
                    last_year_drilling=last[i],
                    max_drill_rate=row['max_drill_rate'],
                    max_drill_rate_frac=row['max_drill_rate_frac'],
                    drill_predecline=self.drill_predecline,
                    ramp_up_years=self.ramp_up_years,
                    oil_price=po[i],
                    ng_price=pg[i],
                    well_type_num=row['well_type_number'],
                    base_oil_prc=row['base_oil_prc'],
                    base_gas_prc=self.base_gas_price,
                    oil_prod=self._op[i, 0],
                    gas_prod=self._gp[i, 0],
                    max_year_drill_percent=row['max_year_drill_percent'],
                    low_price_flag=self.low_price_flag,
                )
                w = min(w, totpat - cum[i], budget[rig_type])
                cum[i] += w
                last[i] = w
                wells[i] = w
                budget[rig_type] -= w
            wells_by_vintage.append(wells)

        # Production from each year's new wells, with later wells a little more productive
        # (the on_tech_levers.csv rates)
        trend_arr = self._cont['process_code'].map(self.tech_trend_by_type).fillna(0.0).to_numpy()
        for yi, year in enumerate(years):
            gas_mmcf = np.zeros(n)
            oil_mbbl = np.zeros(n)
            for vi in range(yi + 1):
                age = yi - vi  # profile column index (0-based; age 0 -> GP1)
                if age < PROFILE_YEARS:
                    tech = (1.0 + trend_arr) ** (years[vi] - 2023)
                    gas_mmcf += wells_by_vintage[vi] * self._gp[:, age] * tech
                    oil_mbbl += wells_by_vintage[vi] * self._op[:, age] * tech
            g = pd.DataFrame(
                {
                    'region': self._cont['region'],
                    'gas_type': self._cont['gas_type'],
                    'gas_mmcf': gas_mmcf,
                    'oil_mbbl': oil_mbbl,
                }
            )
            agg = g.groupby(['region', 'gas_type'])[['gas_mmcf', 'oil_mbbl']].sum()
            for (region, gas_type), r in agg.iterrows():
                add_gas[(region, gas_type, year)] = r['gas_mmcf'] / 1e3  # -> Bcf
                add_oil[(region, gas_type, year)] = r['oil_mbbl']

        legacy = self.annual_production(years)
        legacy['gas_bcf'] = legacy.apply(
            lambda r: r['gas_bcf'] + add_gas.get((r['region'], r['gas_type'], r['year']), 0.0),
            axis=1,
        )
        legacy['crude_mbbl'] = legacy.apply(
            lambda r: r['crude_mbbl'] + add_oil.get((r['region'], r['gas_type'], r['year']), 0.0),
            axis=1,
        )
        self._sim_cache_key, self._sim_cache = key, legacy
        return legacy
