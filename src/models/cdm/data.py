"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Reads and checks the C-CDM input tables in ``input/cdm``.

Every table is a tidy CSV whose ``#`` header names its source; ``build_inputs.py`` writes them
from the NEMS CDM input files (release tag ``AEO2026-Public-Release``) and the AEO2026 tables.
Loading checks the column names, the coverage of every index and the sign of every value, so a
damaged or partial table fails here rather than as a wrong number later.
"""

import csv
import math
from dataclasses import dataclass
from pathlib import Path

REGIONS = (
    'new_england', 'middle_atlantic', 'east_north_central', 'west_north_central',
    'south_atlantic', 'east_south_central', 'west_south_central', 'mountain', 'pacific',
)  # fmt: skip
BUILDINGS = (
    'assembly', 'education', 'food_sales', 'food_service', 'health_care', 'lodging',
    'large_office', 'small_office', 'mercantile_service', 'warehouse', 'other',
)  # fmt: skip
SERVICES = (
    'heating', 'cooling', 'water_heating', 'ventilation', 'cooking', 'lighting',
    'refrigeration', 'data_center_servers', 'computers_office_equipment', 'other_uses',
)  # fmt: skip
FUELS = ('electricity', 'natural_gas')
EUI_FUELS = ('electricity', 'natural_gas', 'distillate')
CASES = ('reference', 'high_electricity_demand')
YEARS = tuple(range(2025, 2051))
MELS = (
    'distribution_transformers', 'kitchen_ventilation', 'security_systems',
    'lab_refrigerators_freezers', 'medical_imaging', 'large_video_boards', 'coffee_brewers',
    'off_road_electric_vehicles', 'fume_hoods', 'laundry', 'elevators', 'escalators',
    'it_equipment', 'uninterruptible_power_supplies', 'shredders', 'private_branch_exchange',
    'voice_over_ip', 'point_of_sale', 'warehouse_robots', 'televisions',
)  # fmt: skip
# Every AEO2026 row C-CDM reads from aeo2026_end_use.csv
AEO_ROWS = (
    *(('electricity', s) for s in SERVICES),
    *(('natural_gas', s) for s in ('heating', 'cooling', 'water_heating', 'cooking', 'other_uses')),
    *(('electricity', s) for s in ('gross_end_use_total', 'ev_charging', 'own_generation',
                                   'purchased_total')),
    ('natural_gas', 'delivered_total'),
    ('natural_gas', 'combined_heat_power_input'),
)  # fmt: skip
# Services whose multiplier is 1 in every building and case; C-CDM reports no increment for them
UNIT_MULTIPLIER_SERVICES = (
    'heating', 'water_heating', 'cooking', 'lighting', 'refrigeration', 'data_center_servers',
)  # fmt: skip


@dataclass
class CDMData:
    """All C-CDM inputs, keyed by name tuples.

    Attributes
    ----------
    floorspace_2018 : dict
        {(region, building): million sqft}, NEMS kflspc (CBECS 2018).
    eui : dict
        {(region, building, service, fuel): thousand Btu per sqft}, NEMS kintens (CBECS 2018);
        fuel includes distillate, used only in the other-uses fuel split.
    multiplier : dict
        {(case, building, service): data-center intensity multiplier}, NEMS kmels [dcf].
    server_index : dict
        {(case, year): server energy index, 2018 = 1}, NEMS kmels [DataCtrServPenetration].
    mel_share : dict
        {(case, building): explicit share}, NEMS kmels [xplicitmiscshr].
    mel_base : dict
        {mel: Btu per sqft of eligible floorspace}, NEMS kmels [MelsElQ].
    mel_index : dict
        {(case, mel, year): index, 2018 = 1}, NEMS kmels [MarketPenetrationMels].
    elasticity : dict
        {(region, service, fuel): short-run price elasticity}, NEMS ksdela.
    aeo_floorspace : dict
        {(building, year): billion sqft}, AEO2026 Table 22.
    aeo_consumption : dict
        {(case, fuel, service_or_item, year): quadrillion Btu}, AEO2026 Tables 5 and 22.
    aeo_price : dict
        {(case, fuel, year): 2025 USD per MMBtu}, AEO2026 Table 3, commercial.
    """

    floorspace_2018: dict[tuple[str, str], float]
    eui: dict[tuple[str, str, str, str], float]
    multiplier: dict[tuple[str, str, str], float]
    server_index: dict[tuple[str, int], float]
    mel_share: dict[tuple[str, str], float]
    mel_base: dict[str, float]
    mel_index: dict[tuple[str, str, int], float]
    elasticity: dict[tuple[str, str, str], float]
    aeo_floorspace: dict[tuple[str, int], float]
    aeo_consumption: dict[tuple[str, str, str, int], float]
    aeo_price: dict[tuple[str, str, int], float]


def _rows(path: Path, columns: list[str]) -> list[dict[str, str]]:
    """Read a tidy CSV, skipping ``#`` lines, and check its column names."""
    with open(path, encoding='utf-8') as fh:
        reader = csv.DictReader(line for line in fh if not line.startswith('#'))
        if reader.fieldnames != columns:
            raise ValueError(f'{path.name}: columns {reader.fieldnames}, expected {columns}')
        return list(reader)


def _table(path: Path, keys: list[str], value: str, extra: tuple[str, ...] = ()) -> dict:
    """Return {key tuple: float} from a tidy CSV, with integer years."""
    out: dict = {}
    for row in _rows(path, [*keys, value, *extra]):
        parts = tuple(int(row[k]) if k == 'year' else row[k] for k in keys)
        key = parts if len(parts) > 1 else parts[0]
        if key in out:
            raise ValueError(f'{path.name}: duplicate row {key}')
        out[key] = float(row[value])
    return out


def _check_cover(name: str, table: dict, *axes: tuple, keep=None) -> None:
    """Check that ``table`` has exactly the keys of the product of ``axes`` (filtered by keep)."""
    expected = [()]
    for axis in axes:
        expected = [(*e, a) for e in expected for a in axis]
    want = {e if len(e) > 1 else e[0] for e in expected if keep is None or keep(e)}
    have = set(table) if keep is None else {k for k in table if keep(k if len(axes) > 1 else (k,))}
    if have != want:
        missing, extra = sorted(want - have)[:5], sorted(have - want)[:5]
        raise ValueError(f'{name}: missing {missing}, unexpected {extra}')


def _check_values(name: str, table: dict, low: float, high: float = math.inf) -> None:
    """Check every value is finite and within [low, high]."""
    bad = [k for k, v in table.items() if not (math.isfinite(v) and low <= v <= high)]
    if bad:
        raise ValueError(f'{name}: value missing or outside [{low}, {high}] at {bad[:5]}')


def load_all(input_path: Path) -> CDMData:
    """Load and check every C-CDM input table.

    Parameters
    ----------
    input_path : Path
        Folder holding the tables written by ``build_inputs.py``.

    Returns
    -------
    CDMData
        The checked inputs.
    """
    p = input_path
    data = CDMData(
        floorspace_2018=_table(p / 'nems_floorspace_2018.csv', ['region', 'building_type'],
                               'floorspace_million_sqft'),
        eui=_table(p / 'nems_eui_2018.csv', ['region', 'building_type', 'service', 'fuel'],
                   'eui_kbtu_per_sqft'),
        multiplier=_table(p / 'nems_dc_multipliers.csv', ['case', 'building_type', 'service'],
                          'multiplier'),
        server_index=_table(p / 'nems_server_index.csv', ['case', 'year'], 'server_index'),
        mel_share=_table(p / 'nems_mel_share.csv', ['case', 'building_type'], 'explicit_share'),
        mel_base=_table(p / 'nems_mel_base.csv', ['mel'], 'base_btu_per_sqft'),
        mel_index=_table(p / 'nems_mel_index.csv', ['case', 'mel', 'year'], 'index'),
        elasticity=_table(p / 'nems_price_elasticity.csv', ['region', 'service', 'fuel'],
                          'elasticity'),
        aeo_floorspace=_table(p / 'aeo2026_floorspace.csv', ['building_type', 'year'],
                              'floorspace_billion_sqft', ('series_code', 'source_row')),
        aeo_consumption=_table(p / 'aeo2026_end_use.csv', ['case', 'fuel', 'service', 'year'],
                               'consumption_quad', ('series_code', 'source_row')),
        aeo_price=_table(p / 'aeo2026_prices.csv', ['case', 'fuel', 'year'],
                         'price_2025usd_per_mmbtu', ('series_code', 'source_row')),
    )  # fmt: skip
    _check(data)
    return data


def _in_years(key: tuple) -> bool:
    return key[-1] in YEARS


def _check(d: CDMData) -> None:
    """Check coverage, signs and the multiplier pattern the model relies on."""
    in_years = _in_years
    _check_cover('nems_floorspace_2018', d.floorspace_2018, REGIONS, BUILDINGS)
    _check_cover('nems_eui_2018', d.eui, REGIONS, BUILDINGS, SERVICES, EUI_FUELS)
    _check_cover('nems_dc_multipliers', d.multiplier, CASES, BUILDINGS, SERVICES)
    _check_cover('nems_server_index', d.server_index, CASES, YEARS, keep=in_years)
    _check_cover('nems_mel_share', d.mel_share, CASES, BUILDINGS)
    _check_cover('nems_mel_base', d.mel_base, MELS)
    _check_cover('nems_mel_index', d.mel_index, CASES, MELS, YEARS, keep=in_years)
    _check_cover('nems_price_elasticity', d.elasticity, REGIONS, SERVICES, EUI_FUELS)
    _check_cover('aeo2026_floorspace', d.aeo_floorspace, BUILDINGS, YEARS, keep=in_years)
    _check_cover('aeo2026_prices', d.aeo_price, CASES, FUELS, YEARS, keep=in_years)
    for case in CASES:
        for fuel, item in AEO_ROWS:
            for year in YEARS:
                if (case, fuel, item, year) not in d.aeo_consumption:
                    raise ValueError(f'aeo2026_end_use: missing {case} {fuel} {item} {year}')
    for name in ('floorspace_2018', 'eui', 'server_index', 'mel_base', 'mel_index',
                 'aeo_floorspace', 'aeo_consumption'):  # fmt: skip
        _check_values(name, getattr(d, name), 0.0)
    _check_values('multiplier', d.multiplier, 1.0)
    _check_values('mel_share', d.mel_share, 0.0, 1.0)
    _check_values('elasticity', d.elasticity, -math.inf, 0.0)
    _check_values('aeo_price', d.aeo_price, math.ulp(0.0))
    empty = [b for b in BUILDINGS if sum(d.floorspace_2018[r, b] for r in REGIONS) <= 0.0]
    if empty:
        raise ValueError(f'nems_floorspace_2018: no 2018 floorspace for {empty}')
    off = [k for k, v in d.multiplier.items() if k[2] in UNIT_MULTIPLIER_SERVICES and v != 1.0]
    if off:
        raise ValueError(f'nems_dc_multipliers: expected 1 for {off[:5]}; C-CDM reports no '
                         'data-center increment for these services')  # fmt: skip
