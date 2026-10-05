"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Writes the C-CDM result tables.

Electricity is gross end use (before electric-vehicle charging is added and on-site generation for
own use is taken off) in terawatt-hours; natural gas is in trillion Btu. The data-center table
lists servers and the data-center increment in other services; both already sit inside the
electricity table, so adding the two tables double counts. The summary compares national totals
with the AEO2026 rows they follow or check against.
"""

import csv
from collections import defaultdict
from logging import getLogger
from pathlib import Path

from src.models.cdm.commercial import BTU_PER_KWH, INCREMENT_SERVICES, Results
from src.models.cdm.data import BUILDINGS, REGIONS, SERVICES, YEARS, CDMData

GAS_SERVICES = ('heating', 'cooling', 'water_heating', 'cooking', 'other_uses')

logger = getLogger(__name__)

TBTU_TO_TWH = 1.0e12 / BTU_PER_KWH / 1.0e9


def _write(path: Path, columns: list[str], rows: list[list]) -> None:
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh, lineterminator='\n')
        writer.writerow(columns)
        writer.writerows(rows)
    logger.info('C-CDM: wrote %s (%d rows)', path, len(rows))


def _round(value: float | str) -> float | str:
    return value if isinstance(value, str) else round(value, 6)


def _totals(table: dict) -> dict[tuple[str, str, int], float]:
    """National totals {(group, service, year)}; the group is ``buildings`` or the row name."""
    out: dict[tuple[str, str, int], float] = defaultdict(float)
    for (_r, b, s, y), v in table.items():
        group = b if b in ('non_building', 'combined_heat_power') else 'buildings'
        out[group, s, y] += v
    return out


def _aeo(data: CDMData, case: str, fuel: str, item: str, year: int) -> float:
    return 1000.0 * data.aeo_consumption[case, fuel, item, year]


def _summary(data: CDMData, case: str, res: Results) -> list[list]:
    """National C-CDM totals beside the AEO2026 rows, trillion Btu."""
    totals = {'electricity': _totals(res.electricity), 'natural_gas': _totals(res.natural_gas)}
    services = {'electricity': SERVICES, 'natural_gas': GAS_SERVICES}
    rows: list[list] = []
    for y in YEARS:
        for fuel, t in totals.items():
            for s in services[fuel]:
                cdm = sum(t[g, s, y] for g in ('buildings', 'non_building', 'combined_heat_power'))
                rows.append([case, y, fuel, s, cdm, _aeo(data, case, fuel, s, y), 'Table 5'])
            rows.append([case, y, fuel, 'other_uses_buildings', t['buildings', 'other_uses', y],
                         '', ''])  # fmt: skip
            rows.append([case, y, fuel, 'other_uses_non_building',
                         t['non_building', 'other_uses', y], '', ''])  # fmt: skip
        rows.append([case, y, 'natural_gas', 'other_uses_combined_heat_power',
                     totals['natural_gas']['combined_heat_power', 'other_uses', y],
                     _aeo(data, case, 'natural_gas', 'combined_heat_power_input', y),
                     'Table 22'])  # fmt: skip
        gross = sum(v for (_g, _s, yy), v in totals['electricity'].items() if yy == y)
        ev = _aeo(data, case, 'electricity', 'ev_charging', y)
        own = _aeo(data, case, 'electricity', 'own_generation', y)
        balance = (('gross_end_use_total', gross), ('ev_charging', ''), ('own_generation', ''),
                   ('purchased_total', gross + ev - own))  # fmt: skip
        for item, cdm in balance:
            source = 'Table 5'
            if item == 'purchased_total':
                source = 'Table 5; C-CDM value = its gross + Table 5 EV charging - own generation'
            rows.append([case, y, 'electricity', item, cdm,
                         _aeo(data, case, 'electricity', item, y), source])  # fmt: skip
        inc = sum(v for (_r, _b, _s, yy), v in res.increment.items() if yy == y)
        servers = totals['electricity']['buildings', 'data_center_servers', y]
        rows.append([case, y, 'electricity', 'data_center_increment', inc, '', ''])
        rows.append([case, y, 'electricity', 'data_center_total', servers + inc, '', ''])
    return [[*r[:4], _round(r[4]), _round(r[5]), r[6]] for r in rows]


def report(data: CDMData, case: str, res: Results, output_dir: Path) -> None:
    """Write the four C-CDM result tables to ``output_dir``.

    Parameters
    ----------
    data : CDMData
        Inputs, for the AEO2026 rows in the summary.
    case : str
        AEO2026 case that was run.
    res : Results
        Model results.
    output_dir : Path
        Folder to write into; created if missing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(
        output_dir / 'cdm_electricity.csv',
        ['region', 'building_type', 'service', 'year', 'electricity_twh'],
        [[*k, _round(v * TBTU_TO_TWH)] for k, v in sorted(res.electricity.items())],
    )
    _write(
        output_dir / 'cdm_natural_gas.csv',
        ['region', 'building_type', 'service', 'year', 'natural_gas_tbtu'],
        [[*k, _round(v)] for k, v in sorted(res.natural_gas.items())],
    )
    dc_rows = []
    for r in REGIONS:
        for b in BUILDINGS:
            for y in YEARS:
                servers = res.electricity[r, b, 'data_center_servers', y]
                inc = [res.increment[r, b, s, y] for s in INCREMENT_SERVICES]
                dc_rows.append([r, b, y, *(_round(v * TBTU_TO_TWH) for v in (servers, *inc)),
                                _round((servers + sum(inc)) * TBTU_TO_TWH)])  # fmt: skip
    _write(
        output_dir / 'cdm_data_centers.csv',
        ['region', 'building_type', 'year', 'servers_twh',
         *(f'{s}_increment_twh' for s in INCREMENT_SERVICES), 'total_twh'],
        dc_rows,
    )  # fmt: skip
    _write(
        output_dir / 'cdm_summary.csv',
        ['case', 'year', 'fuel', 'item', 'cdm_trillion_btu', 'aeo2026_trillion_btu',
         'aeo2026_table'],
        _summary(data, case, res),
    )  # fmt: skip
