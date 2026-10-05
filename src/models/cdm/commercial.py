"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

The C-CDM equations: floorspace, data-center servers, end-use consumption, other uses, the
data-center increment in other services, non-building uses and the price response.

It does the job of the service demand and consumption calculations of the Commercial Demand
Module in EIA's National Energy Modeling System (NEMS, https://github.com/EIAgov/NEMS, release
tag ``AEO2026-Public-Release``, Apache License 2.0). Line numbers cite that release's
``source/comm.f``; equation numbers match ``docs/models/cdm.md``. Energy is in trillion Btu (TBtu)
throughout; floorspace is in million sqft unless a name says otherwise. The main differences from
NEMS:

- floorspace by building type comes from AEO2026 Table 22, not from NEMS's stock model;
- end uses other than servers and other uses are scaled to AEO2026 Table 5, not built from
  equipment choice and shell efficiency;
- the rest of AEO2026 other uses is kept as non-building and CHP rows, not computed from
  historical data;
- prices act through ratios to a reference path, not to a fixed base-year price;
- there is no benchmarking to EIA's short-term outlook.
"""

from dataclasses import dataclass, field
from logging import getLogger

from src.models.cdm.data import BUILDINGS, MELS, REGIONS, SERVICES, YEARS, CDMData

logger = getLogger(__name__)

# Weights on this year's and the two previous years' prices, comm.f:7807
LAG_WEIGHTS = (0.50, 0.35, 0.15)
BTU_PER_KWH = 3412.0  # comm.f:12669-12678
# ServicesIndex scales the unexplained part of other-uses electricity (comm.f:3729-3745). NEMS takes
# it from its macroeconomic module (comm.f:1566), which publishes no input file.
# Placeholder for now.
SERVICES_INDEX = 1.0

# Services C-CDM scales to AEO2026 Table 5, by fuel; servers and other uses are modeled
SCALED = {
    'electricity': ('heating', 'cooling', 'water_heating', 'ventilation', 'cooking', 'lighting',
                    'refrigeration', 'computers_office_equipment'),
    'natural_gas': ('heating', 'cooling', 'water_heating', 'cooking'),
}  # fmt: skip
INCREMENT_SERVICES = ('cooling', 'ventilation', 'computers_office_equipment', 'other_uses')

# Buildings whose other-uses demand NEMS scales by the data-center multiplier: comm.f:3851-3857,
# 3917-3923, 4093-4099, 4159-4165, 4225-4231, 4294-4300, 4424-4430, 4492-4498. Not food sales,
# food service or mercantile/service.
OTHER_USES_SCALED = frozenset(
    {'assembly', 'education', 'health_care', 'lodging', 'large_office', 'small_office',
     'warehouse', 'other'}
)  # fmt: skip

# Building types each explicit miscellaneous electric load is shared to (comm.f:3795-4506, select
# case miscbyBT). Transformers and off-road vehicles have their own sharing rules, below.
_ALL = frozenset(BUILDINGS)
MEL_BUILDINGS = {
    'distribution_transformers': _ALL,
    'kitchen_ventilation': frozenset({'assembly', 'education', 'food_sales', 'food_service',
                                      'health_care', 'lodging', 'mercantile_service'}),
    'security_systems': _ALL,
    'lab_refrigerators_freezers': frozenset({'education', 'health_care', 'large_office',
                                             'small_office', 'other'}),
    'medical_imaging': frozenset({'health_care', 'large_office', 'small_office',
                                  'mercantile_service'}),
    'large_video_boards': frozenset({'assembly'}),
    'coffee_brewers': frozenset({'food_service', 'large_office', 'small_office'}),
    'off_road_electric_vehicles': _ALL,
    'fume_hoods': frozenset({'education', 'health_care', 'large_office', 'small_office', 'other'}),
    'laundry': frozenset({'health_care', 'lodging', 'mercantile_service'}),
    'elevators': _ALL - {'food_sales'},
    'escalators': frozenset({'assembly', 'education', 'health_care', 'lodging', 'large_office',
                             'mercantile_service', 'other'}),
    'it_equipment': _ALL,
    'uninterruptible_power_supplies': _ALL,
    # Shredders are computed for assembly and education but left out of their sums
    # (comm.f:3829-3835, 3896-3902), so they count everywhere else only
    'shredders': _ALL - {'assembly', 'education'},
    'private_branch_exchange': frozenset({'large_office', 'small_office'}),
    'voice_over_ip': frozenset({'large_office', 'small_office'}),
    'point_of_sale': _ALL,
    'warehouse_robots': frozenset({'warehouse'}),
    'televisions': _ALL,
}  # fmt: skip

Cell = tuple[str, str, str, int]  # (region, building type, service, year)


@dataclass
class Results:
    """C-CDM results in trillion Btu.

    Attributes
    ----------
    electricity : dict
        {(region, building, service, year)}: gross end-use electricity. Building types include
        ``non_building`` (service ``other_uses``).
    natural_gas : dict
        Same keys; building types include ``non_building`` and ``combined_heat_power``.
    increment : dict
        Data-center increment in electricity, a subset of ``electricity`` for the services in
        ``INCREMENT_SERVICES``.
    """

    electricity: dict[Cell, float] = field(default_factory=dict)
    natural_gas: dict[Cell, float] = field(default_factory=dict)
    increment: dict[Cell, float] = field(default_factory=dict)


def data_center_share(year: int) -> float:
    """Eq. 3: NEMS's data-center share of building service demand (comm.f:3430-3434).

    NEMS evaluates the cubic at ``curiyr - cmfirstyr`` with ``cmfirstyr`` the year after the
    CBECS year (comm.f:474-481), so the origin is 2019. The coefficients carry EIA's own
    "TODO - update coefficients" note.
    """
    n = year - 2019
    return 0.000002 * n**3 - 0.00002 * n**2 + 0.0173 * n + 0.001626


def floorspace(data: CDMData) -> dict[tuple[str, str, int], float]:
    """Eqs. 1-2: AEO2026 floorspace by building type, shared to divisions as in 2018."""
    out = {}
    for b in BUILDINGS:
        total = sum(data.floorspace_2018[r, b] for r in REGIONS)
        for r in REGIONS:
            phi = data.floorspace_2018[r, b] / total
            for y in YEARS:
                out[r, b, y] = 1000.0 * data.aeo_floorspace[b, y] * phi
    return out


def multiplier(data: CDMData, case: str, building: str, service: str, year: int) -> float:
    """Eq. 4: the factor NEMS applies to service demand for data centers (comm.f:3572-3720)."""
    if service == 'other_uses' and building not in OTHER_USES_SCALED:
        return 1.0
    d = data_center_share(year)
    return 1.0 - d + data.multiplier[case, building, service] * d


def _servers(data: CDMData, case: str, flr: dict, res: Results) -> None:
    """Eq. 5: server electricity, modeled the NEMS way and not scaled (comm.f:3667-3679)."""
    for (r, b, y), f in flr.items():
        s_eui = data.eui[r, b, 'data_center_servers', 'electricity']
        res.electricity[r, b, 'data_center_servers', y] = (
            s_eui * f * data.server_index[case, y] / 1000.0
        )


def _scaled(data: CDMData, case: str, flr: dict, res: Results) -> None:
    """Eqs. 6-8: services shared to divisions and buildings, scaled to AEO2026 Table 5."""
    for fuel, services in SCALED.items():
        out = res.electricity if fuel == 'electricity' else res.natural_gas
        for s in services:
            for y in YEARS:
                m = {b: multiplier(data, case, b, s, y) for b in BUILDINGS}
                w = {(r, b): data.eui[r, b, s, fuel] * flr[r, b, y] * m[b]
                     for r in REGIONS for b in BUILDINGS}  # fmt: skip
                total = sum(w.values())
                if total <= 0.0:
                    # Assumption: floorspace weights where the NEMS intensities are all zero
                    logger.warning('C-CDM: no NEMS intensity for %s %s %d; floorspace weights',
                                   fuel, s, y)  # fmt: skip
                    w = {(r, b): flr[r, b, y] for r in REGIONS for b in BUILDINGS}
                    total = sum(w.values())
                aeo = 1000.0 * data.aeo_consumption[case, fuel, s, y]
                for (r, b), wt in w.items():
                    out[r, b, s, y] = aeo * wt / total
                    if fuel == 'electricity' and s in INCREMENT_SERVICES:
                        res.increment[r, b, s, y] = out[r, b, s, y] * (m[b] - 1.0) / m[b]


def explicit_mels(
    data: CDMData, case: str, flr: dict, year: int, elec: dict[tuple[str, str], float]
) -> dict[tuple[str, str], float]:
    """Eqs. 9-11: explicit miscellaneous electric loads by division and building, TBtu.

    Each load is its base intensity (Btu per sqft) times its annual index times the floorspace of
    the building types it is shared to (comm.f:3488-3528, 3795-4506). Two loads differ:
    transformers are shared by electricity use, NEMS's by the previous year's over national
    purchased electricity, so part stays outside buildings (comm.f:3798-3801, 9458-9467), C-CDM's
    all by the same year's electricity in all other services (assumption); off-road vehicles once
    over non-warehouse floorspace and once more over warehouses (comm.f:3502-3504, 3806, 4378),
    as in NEMS, but the warehouse part over all warehouse floorspace, not surviving (assumption).

    Parameters
    ----------
    data : CDMData
        Inputs.
    case : str
        AEO2026 case.
    flr : dict
        Floorspace {(region, building, year): million sqft}.
    year : int
        Model year.
    elec : dict
        {(region, building): electricity in every service but other uses}, the transformer key.

    Returns
    -------
    dict
        {(region, building): TBtu}.
    """
    f = {(r, b): flr[r, b, year] for r in REGIONS for b in BUILDINGS}
    total = sum(f.values())
    warehouse = sum(f[r, 'warehouse'] for r in REGIONS)
    others = total - warehouse
    elec_total = sum(elec.values())
    out = dict.fromkeys(f, 0.0)
    for mel in MELS:
        q = data.mel_index[case, mel, year] * data.mel_base[mel] / 1.0e6  # TBtu per million sqft
        if mel == 'distribution_transformers':
            for k in f:
                out[k] += q * total * elec[k] / elec_total
        elif mel == 'off_road_electric_vehicles':
            national = q * (0.6 * warehouse + 0.4 * others)
            for (r, b), fl in f.items():
                out[r, b] += national * fl / (warehouse if b == 'warehouse' else others)
        else:
            for (r, b), fl in f.items():
                if b in MEL_BUILDINGS[mel]:
                    out[r, b] += q * fl
    return out


def _other_uses(data: CDMData, case: str, flr: dict, res: Results) -> None:
    """Eqs. 12-15: other uses in buildings, modeled the NEMS way and not scaled.

    Service demand T is the unexplained electricity, the natural gas and distillate parts and the
    explicit loads (comm.f:3729-3745, 3837-3841); NEMS scales T by the data-center multiplier and
    leaves gas and distillate at their base intensity, so electricity takes the rest
    (comm.f:7448-7457). The increment (m - 1) T is all electricity.
    """
    for y in YEARS:
        elec = {(r, b): sum(res.electricity[r, b, s, y] for s in SERVICES if s != 'other_uses')
                for r in REGIONS for b in BUILDINGS}  # fmt: skip
        mels = explicit_mels(data, case, flr, y, elec)
        for r in REGIONS:
            for b in BUILDINGS:
                f = flr[r, b, y] / 1000.0
                unexplained = data.eui[r, b, 'other_uses', 'electricity'] * (
                    1.0 - data.mel_share[case, b]
                )
                gas = data.eui[r, b, 'other_uses', 'natural_gas'] * f
                dist = data.eui[r, b, 'other_uses', 'distillate'] * f
                demand = unexplained * SERVICES_INDEX * f + gas + dist + mels[r, b]
                m = multiplier(data, case, b, 'other_uses', y)
                res.electricity[r, b, 'other_uses', y] = m * demand - gas - dist
                res.natural_gas[r, b, 'other_uses', y] = gas
                res.increment[r, b, 'other_uses', y] = (m - 1.0) * demand


def _non_building(data: CDMData, case: str, res: Results) -> None:
    """Eqs. 16-18: the rest of AEO2026 other uses, kept out of the building types.

    Placeholder for now: NEMS shares these uses to divisions with historical state data and
    population (comm.f:9035-9303), which its public inputs do not carry; C-CDM uses each
    division's share of its building consumption of the same fuel.
    """
    chp = {y: 1000.0 * data.aeo_consumption[case, 'natural_gas', 'combined_heat_power_input', y]
           for y in YEARS}  # fmt: skip
    for fuel, out in (('electricity', res.electricity), ('natural_gas', res.natural_gas)):
        for y in YEARS:
            by_region = dict.fromkeys(REGIONS, 0.0)
            for (r, _b, _s, yy), v in out.items():
                if yy == y:
                    by_region[r] += v
            national = sum(by_region.values())
            building_other = sum(out[r, b, 'other_uses', y] for r in REGIONS for b in BUILDINGS)
            rest = 1000.0 * data.aeo_consumption[case, fuel, 'other_uses', y] - building_other
            rows = {'non_building': rest}
            if fuel == 'natural_gas':
                rows = {'non_building': rest - chp[y], 'combined_heat_power': chp[y]}
            for name, amount in rows.items():
                if amount < 0.0:
                    logger.warning('C-CDM: %s %s %d is negative (%.1f TBtu)', name, fuel, y, amount)
                for r in REGIONS:
                    out[r, name, 'other_uses', y] = amount * by_region[r] / national


def price_factor(
    data: CDMData,
    reference: dict[tuple[str, str, int], float],
    prices: dict[tuple[str, str, int], float],
) -> dict[tuple[str, str, str, int], float]:
    """Eq. 19: short-run price response of service demand (comm.f:8922-8953).

    NEMS compares each of the three latest prices with a fixed base-year price; C-CDM compares each
    with the reference price path for the same year, so the factor is 1 at reference prices.
    Years before 2025 have no price and contribute 1, as NEMS's lags at or before its elasticity
    base year do (comm.f:8948-8949).

    Parameters
    ----------
    data : CDMData
        Inputs (elasticities).
    reference, prices : dict
        {(fuel, region, year): 2025 USD per MMBtu}, the fixed reference path and current prices.

    Returns
    -------
    dict
        {(region, service, fuel, year): factor}.
    """
    out = {}
    for (r, s, fuel), eps in data.elasticity.items():
        if fuel == 'distillate':
            continue
        for y in YEARS:
            k = 1.0
            for lag, w in enumerate(LAG_WEIGHTS):
                key = (fuel, r, y - lag)
                if key in prices:
                    k *= (prices[key] / reference[key]) ** (eps * w)
            out[r, s, fuel, y] = k
    return out


def _apply_prices(res: Results, factor: dict) -> None:
    """Eq. 20: scale every row by its price factor; non-building rows use the other-uses factor."""
    for fuel, out in (('electricity', res.electricity), ('natural_gas', res.natural_gas)):
        for (r, b, s, y), v in out.items():
            if b != 'combined_heat_power':
                out[r, b, s, y] = v * factor[r, s, fuel, y]
    for (r, b, s, y), v in res.increment.items():
        res.increment[r, b, s, y] = v * factor[r, s, 'electricity', y]


def solve(data: CDMData, case: str, factor: dict) -> Results:
    """Compute every C-CDM result for one AEO2026 case and one set of price factors.

    Parameters
    ----------
    data : CDMData
        Inputs.
    case : str
        ``reference`` or ``high_electricity_demand``.
    factor : dict
        Price factors from :func:`price_factor`.

    Returns
    -------
    Results
        Electricity and natural gas by division, building type, service and year, and the
        data-center increment, in trillion Btu.
    """
    flr = floorspace(data)
    res = Results()
    _servers(data, case, flr, res)
    _scaled(data, case, flr, res)
    _other_uses(data, case, flr, res)
    _non_building(data, case, res)
    _apply_prices(res, factor)
    return res
