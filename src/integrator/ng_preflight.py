"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/29/26

Resolve the coupled natural gas and electricity footprint once, before the first solve.

A coupled run has to answer several related questions before it can iterate: how much of each gas
division the active electricity regions cover, which gas demand cells the gas model hands over,
which of its cells stay price responsive, and which electricity ``ng_fuel_adj`` entries a gas
price can reach.

Those are different key spaces over different index shapes, and they are not interchangeable. The
population-weighted region crosswalk (``region_crosswalk.py``) splits an electricity region across
gas divisions, and ``ng_fuel_adj`` is indexed by technology, step and season as well. Deriving one
count from another is how a coverage check ends up validating the wrong thing.

:class:`PreflightReport` computes all of them once and holds them together, so a driver reads
rather than recomputes, and a coverage assertion can name the set it actually means.
"""

import logging
from dataclasses import dataclass

from src.common.models_modes import ModelType
from src.common.update_package import NGElectricalDemandPackage
from src.integrator.region_crosswalk import REGION_COLUMN, WEIGHT_COLUMN, load_region_weights
from src.models.electricity.constants import NG_PRICE_LINKED_TECHS
from src.models.electricity.electricity_model import PowerModel
from src.models.natural_gas.ng_model import GI, NGModel
from src.models.natural_gas.update_reader import SECTOR_SUPERSEDED_BY

logger = logging.getLogger(__name__)

# the gas demand sector the electricity model's gas burn replaces
SECTOR = SECTOR_SUPERSEDED_BY[NGElectricalDemandPackage]


@dataclass(frozen=True)
class PreflightReport:
    """The coupled footprint, resolved once at setup.

    Frozen against rebinding. Per-region and per-cell values are tuples of pairs rather than dicts
    because ``frozen=True`` does not prevent mutation of a contained mapping, and this object is
    a record rather than a workspace.
    """

    elec_regions_active: frozenset[str]
    #: Active electricity regions with some crosswalk weight in a gas division the gas model
    #: carries, so a gas price reaches them.
    elec_regions_mapped: frozenset[str]
    elec_regions_unmapped: frozenset[str]
    #: ``(electricity region, share)`` for each active region with crosswalk weight in gas
    #: divisions the gas model does not carry. That share of its gas burn is never transmitted.
    elec_burn_share_unheld: tuple[tuple[str, float], ...]

    ng_regions_active: frozenset[str]
    #: ``(gas region, share)`` for every active gas division, the population share of the
    #: division lying in active electricity regions.
    ng_coverage: tuple[tuple[str, float], ...]
    #: Divisions with any coverage. Their ``SECTOR`` demand is handed over to the coupling.
    ng_regions_reached: frozenset[str]
    #: Reached divisions touching an inactive electricity region. Their cells are topped up.
    ng_regions_partly_covered: frozenset[str]
    #: Divisions with none. Their ``SECTOR`` demand stays with the gas model, price responsive.
    ng_regions_unreached: frozenset[str]

    #: Years both models carry. The exchange covers exactly these.
    years: tuple[int, ...]
    #: Gas years with no electricity counterpart. Their ``SECTOR`` demand stays with the gas
    #: model, the temporal form of an unreached division.
    ng_years_uncovered: tuple[int, ...]
    #: Electricity years the gas model does not carry. Their gas burn is never transmitted.
    elec_years_uncovered: tuple[int, ...]

    #: Gas-side region-year keys the coupling exchanges.
    coupled_demand_keys: frozenset[GI]
    #: ``(quantity, region, sector, year)`` cells the gas model must declare external.
    ownership_cells: frozenset[tuple[str, str, str, int]]
    #: ``((region, year), Bcf)`` added to the received burn for each partly covered cell:
    #: ``(1 - coverage)`` of the gas model's own projection, held fixed. Fully covered cells have
    #: no entry.
    topup_by_cell: tuple[tuple[tuple[str, int], float], ...]
    #: Electricity ``ng_fuel_adj`` entries a gas price can reach. Taken from the electricity
    #: index, never from the gas-side footprint.
    fuel_adj_keys: frozenset[tuple]
    #: Gas cells eligible for the internal price response, after ownership and the zero
    #: elasticity rule. A cell is updated only when its solved and reference prices are usable.
    internal_demand_keys: frozenset[tuple[str, str, int]]
    #: Every active gas region-year of the exchange. Prices are expected for all of these.
    expected_price_keys: frozenset[GI]

    partial: bool
    #: ``(year, Bcf)`` of ``SECTOR`` demand in unreached divisions, left with the gas model.
    uncoupled_baseline_by_year: tuple[tuple[int, float], ...]

    def summary(self) -> str:
        """Return a one-line coverage summary for the run log."""
        full = len(self.ng_regions_reached) - len(self.ng_regions_partly_covered)
        return (
            f'coupled {len(self.ng_regions_reached)}/{len(self.ng_regions_active)} gas regions '
            f'({full} fully covered), {len(self.elec_regions_mapped)}/'
            f'{len(self.elec_regions_active)} electricity regions mapped, '
            f'{len(self.coupled_demand_keys)} exchanged region-years, '
            f'{len(self.fuel_adj_keys)} adjustment entries' + (' [PARTIAL]' if self.partial else '')
        )


def build_preflight(
    elec_model: PowerModel,
    ng_model: NGModel,
    *,
    allow_partial_coverage: bool = False,
) -> PreflightReport:
    """Resolve the coupled footprint and enforce the coverage policy.

    Coverage comes from the population-weighted crosswalk. A gas division's coverage is the sum
    of its ``NATURAL_GAS -> ELECTRICITY`` weights over the active electricity regions. Every
    division with any coverage is handed over. A partly covered one receives only the active
    regions' burn, so the rest is topped up with ``(1 - coverage)`` of the gas model's own
    projection for the cell, on the assumption that the population share stands in for the share
    of gas burn. The top-up is fixed and does not respond to price.

    Whether a division is fully covered, and whether a region's burn stays inside the gas model,
    is decided by which regions are active rather than by summing weights, so rounding in the
    weights can neither hide a missing region nor invent one.

    Parameters
    ----------
    elec_model : PowerModel
        Built electricity model.
    ng_model : NGModel
        Built natural gas model.
    allow_partial_coverage : bool
        Permit any shortfall: a gas division not fully covered, electricity burn falling in gas
        divisions the gas model does not carry, or years only one model carries. Off by default,
        because a partial run still converges and looks like a healthy one.

    Returns
    -------
    PreflightReport

    Raises
    ------
    ValueError
        If the models share no years, an active region of either model is not in the crosswalk,
        the crosswalk names a gas region the gas model does not know, no active gas division is
        covered at all, or coverage is partial without ``allow_partial_coverage``.
    """
    elec_years = {int(y) for y in elec_model.year}
    ng_years = {int(y) for y in ng_model.year}
    years = tuple(sorted(elec_years & ng_years))
    if not years:
        raise ValueError(
            f'electricity and gas models share no years, so the coupling would exchange '
            f'nothing. Electricity has {sorted(elec_years)}, gas has {sorted(ng_years)}.'
        )
    ng_years_uncovered = tuple(sorted(ng_years - elec_years))
    elec_years_uncovered = tuple(sorted(elec_years - ng_years))

    elec_active = frozenset(str(r) for r in elec_model.region_analyze)
    ng_active = frozenset(str(r) for r in ng_model.region_analyze)
    ng_known = frozenset(str(r) for r in ng_model.region_dom)
    e_col = REGION_COLUMN[ModelType.ELECTRICITY]
    g_col = REGION_COLUMN[ModelType.NATURAL_GAS]

    # Missing or unknown regions are data errors, not a small run.
    to_ng = load_region_weights(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
    absent = sorted(elec_active - set(to_ng[e_col]))
    if absent:
        raise ValueError(f'active electricity region(s) {absent} are not in the crosswalk.')
    to_ng = to_ng[to_ng[e_col].isin(elec_active) & (to_ng[WEIGHT_COLUMN] > 0.0)]
    unknown = sorted(set(to_ng[g_col]) - ng_known)
    if unknown:
        raise ValueError(
            f'crosswalk maps active electricity regions onto gas region(s) {unknown}, which the '
            f'gas model does not know. Its regions are {sorted(ng_known)}.'
        )
    to_elec = load_region_weights(ModelType.NATURAL_GAS, ModelType.ELECTRICITY)
    absent = sorted(ng_active - set(to_elec[g_col]))
    if absent:
        raise ValueError(f'active gas region(s) {absent} are not in the crosswalk.')
    to_elec = to_elec[to_elec[g_col].isin(ng_active) & (to_elec[WEIGHT_COLUMN] > 0.0)]

    # Electricity side: the share of each region's burn landing in divisions the gas model
    # does not carry.
    held = to_ng[g_col].isin(ng_active)
    mapped = frozenset(str(e) for e in to_ng.loc[held, e_col])
    unmapped = elec_active - mapped
    unheld_share = to_ng[~held].groupby(e_col)[WEIGHT_COLUMN].sum().sort_index()
    burn_share_unheld = tuple((str(e), float(share)) for e, share in unheld_share.items())

    # Gas side: the share of each division the active electricity regions cover.
    active = to_elec[e_col].isin(elec_active)
    coverage = (
        to_elec[active]
        .groupby(g_col)[WEIGHT_COLUMN]
        .sum()
        .reindex(sorted(ng_active), fill_value=0.0)
        .clip(upper=1.0)
    )
    ng_coverage = tuple((str(g), float(share)) for g, share in coverage.items())
    reached = frozenset(str(g) for g in to_elec.loc[active, g_col])
    unreached = ng_active - reached
    partly_covered = reached & frozenset(str(g) for g in to_elec.loc[~active, g_col])
    partly = tuple((g, share) for g, share in ng_coverage if g in partly_covered)

    if not reached:
        raise ValueError(
            f'no active gas region is covered by an active electricity region, so the coupling '
            f'would exchange nothing and converge immediately. Active electricity regions are '
            f'{sorted(elec_active)}, active gas regions are {sorted(ng_active)}.'
        )

    partial = bool(
        unreached or partly or burn_share_unheld or ng_years_uncovered or elec_years_uncovered
    )
    if partial and not allow_partial_coverage:
        shortfalls = []
        if partly:
            shortfalls.append(
                f'{len(partly)} gas region(s) are only partly covered:  '
                + ', '.join(f'{g} {share:.2f}' for g, share in partly)
            )
        if unreached:
            shortfalls.append(
                f'{len(unreached)} of {len(ng_active)} active gas regions are not covered at '
                f'all:  {sorted(unreached)}'
            )
        if burn_share_unheld:
            shortfalls.append(
                'electricity burn falls in gas regions the gas model does not carry:  '
                + ', '.join(f'{e} {share:.2f}' for e, share in burn_share_unheld)
            )
        if ng_years_uncovered:
            shortfalls.append(
                f'{len(ng_years_uncovered)} gas year(s) have no electricity counterpart:  '
                f'{list(ng_years_uncovered)}'
            )
        if elec_years_uncovered:
            shortfalls.append(
                f'{len(elec_years_uncovered)} electricity year(s) are not carried by the gas '
                f'model:  {list(elec_years_uncovered)}'
            )
        raise ValueError(
            'coupling coverage is partial, and a partial run still converges and looks like a '
            'healthy one:\n  '
            + '\n  '.join(shortfalls)
            + '\nPass allow_partial_coverage=True to accept that deliberately.'
        )

    base = ng_model._base_demand
    coupled_demand_keys = frozenset(GI(region=g, year=y) for g in reached for y in years)
    expected_price_keys = frozenset(GI(region=g, year=y) for g in ng_active for y in years)
    ownership_cells = frozenset((ng_model.DEMAND, g, SECTOR, y) for g in reached for y in years)
    topup_by_cell = tuple(
        ((g, y), (1.0 - share) * float(base.get((g, SECTOR, y), 0.0)))
        for g, share in partly
        for y in years
    )

    # Every cell the price response could still update: owned by neither this coupling nor
    # another model, and elasticity not zero. Mirrors the cell skips in
    # NGModel.calculate_demand_from_price; its price skips are only known after a solve.
    owned = {(g, SECTOR, y) for g in reached for y in years}
    internal_demand_keys = frozenset(
        (r, s, y)
        for r in ng_active
        for s in ng_model.sectors
        for y in ng_years
        if abs(ng_model.demand_price_elasticity[s]) >= 1e-9
        and (r, s, y) not in owned
        and not ng_model.is_external(ng_model.DEMAND, r, s, y)
    )

    # Taken from the electricity index; a gas price exists only for mapped regions in shared
    # years. ng_fuel_adj keys flatten to (region, tech, step, year, season).
    fuel_adj_keys = frozenset(
        key
        for key in elec_model.ng_fuel_adj
        if key[1] in NG_PRICE_LINKED_TECHS and str(key[0]) in mapped and int(key[3]) in years
    )

    uncoupled_baseline_by_year = tuple(
        (y, sum((float(base.get((g, SECTOR, y), 0.0)) for g in unreached), 0.0)) for y in years
    )

    report = PreflightReport(
        elec_regions_active=elec_active,
        elec_regions_mapped=mapped,
        elec_regions_unmapped=unmapped,
        elec_burn_share_unheld=burn_share_unheld,
        ng_regions_active=ng_active,
        ng_coverage=ng_coverage,
        ng_regions_reached=reached,
        ng_regions_partly_covered=partly_covered,
        ng_regions_unreached=unreached,
        years=years,
        ng_years_uncovered=ng_years_uncovered,
        elec_years_uncovered=elec_years_uncovered,
        coupled_demand_keys=coupled_demand_keys,
        ownership_cells=ownership_cells,
        topup_by_cell=topup_by_cell,
        fuel_adj_keys=fuel_adj_keys,
        internal_demand_keys=internal_demand_keys,
        expected_price_keys=expected_price_keys,
        partial=partial,
        uncoupled_baseline_by_year=uncoupled_baseline_by_year,
    )
    _log_report(report)
    return report


def _log_report(report: PreflightReport) -> None:
    """Log the summary, then every shortfall a partial run accepted.

    Parameters
    ----------
    report : PreflightReport
        The resolved footprint.
    """
    logger.info('Coupling preflight:  %s', report.summary())
    for y in report.years:
        topup = sum(bcf for (_, year), bcf in report.topup_by_cell if year == y)
        if topup > 0.0:
            logger.warning(
                'Coupling preflight:  %.1f Bcf of %s demand in %d is topped up at the gas model '
                'projection for partly covered gas regions',
                topup,
                SECTOR,
                y,
            )
    for y, total in report.uncoupled_baseline_by_year:
        if total > 0.0:
            logger.warning(
                'Coupling preflight:  %.1f Bcf of %s demand in %d is in uncovered gas regions '
                'and stays with the gas model',
                total,
                SECTOR,
                y,
            )
    if report.elec_burn_share_unheld:
        logger.warning(
            'Coupling preflight:  share of electricity burn outside the gas model (never '
            'transmitted):  %s',
            ', '.join(f'{e} {share:.2f}' for e, share in report.elec_burn_share_unheld),
        )
    if report.ng_years_uncovered:
        logger.warning(
            'Coupling preflight:  gas year(s) %s have no electricity counterpart and stay with '
            'the gas model',
            list(report.ng_years_uncovered),
        )
    if report.elec_years_uncovered:
        logger.warning(
            'Coupling preflight:  electricity year(s) %s are not carried by the gas model, so '
            'their gas burn is never transmitted',
            list(report.elec_years_uncovered),
        )
