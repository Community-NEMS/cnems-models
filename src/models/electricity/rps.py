"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Regional Portfolio Standard (RPS) data processing:  pure functions (no pyomo) that turn the raw
``rps_group`` / ``rps_requirement`` reads into checked group memberships and the requirement in
force for each (group, region key, model year).  A requirement is a minimum share of the region
key's annual load met by the group's in-region generation.

A requirement's *region key* is a region name or ``RPS_ALL_REGIONS``, which stands for the
analyzed regions summed together (one requirement, not one per region).  Every problem found in a
table is collected and raised as a single ``ValueError``, so one pass reports them all.
"""

import logging
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from src.models.electricity.elec_config import RPSRequirementType

logger = logging.getLogger(__name__)

RPS_ALL_REGIONS = 'all'
"""region keyword:  the requirement applies to the sum over all analyzed regions"""

DISJOINT_SEARCH_WARN_COUNT = 12
"""BTMs per (region key, year) above which the exhaustive disjoint-share search warns of slowness"""

type RPSKey = tuple[str, str, int]
"""(group_id, region key, model year)"""


@dataclass(frozen=True)
class RPSRequirement:
    """One row of ``rps_requirement.csv``, checked and typed.

    Parameters
    ----------
    requirement_type : RPSRequirementType
    min_share : float
        minimum group share of the region key's annual load, in [0, 1].
    first_year : int
        first year (inclusive) the requirement applies.
    """

    requirement_type: RPSRequirementType
    min_share: float
    first_year: int


@dataclass(frozen=True)
class RPSRequirementRow:
    """One row of ``rps_requirement.csv`` as entered, for reporting.

    Parameters
    ----------
    group_id : str
    region : str
        a region name or ``RPS_ALL_REGIONS`` (the region key).
    requirement : RPSRequirement
    in_run : bool
        False for a region the region filter excludes; such a row is never in force.
    """

    group_id: str
    region: str
    requirement: RPSRequirement
    in_run: bool


def _raise_if(errors: list[str], table: str) -> None:
    """Log each problem and raise them together, if there are any."""
    if not errors:
        return
    for error in errors:
        logger.error('%s: %s', table, error)
    raise ValueError(f'{len(errors)} problem(s) in {table}:\n  ' + '\n  '.join(errors))


def resolve_groups(
    rows: Iterable[tuple[str, str]], techs: Collection[str], generators: Collection[str]
) -> dict[str, frozenset[str]]:
    """Resolve ``rps_group`` rows into each group's member techs.

    Each row names one member; a group is the union of its rows.

    Parameters
    ----------
    rows : Iterable[tuple[str, str]]
        raw ``(group_id, tech)`` rows, blank cells as ``''``.
    techs : Collection[str]
        every tech in tech.csv.
    generators : Collection[str]
        the generator techs; every member must be one.

    Returns
    -------
    dict[str, frozenset[str]]
        group_id -> member techs.

    Raises
    ------
    ValueError
        listing every row with no group_id or tech, or naming an unknown tech, and every group
        with a non-generator member.
    """
    known = set(techs)
    members: defaultdict[str, set[str]] = defaultdict(set)
    errors = []
    for group_id, tech in rows:
        row = f'row {(group_id, tech)}'
        if not group_id:
            errors.append(f'{row}: missing group_id')
        elif not tech:
            errors.append(f'{row}: missing tech')
        elif tech not in known:
            errors.append(f'{row}: unknown tech {tech!r}')
        else:
            members[group_id].add(tech)

    for group_id, group_techs in sorted(members.items()):
        if non_generators := sorted(group_techs - set(generators)):
            errors.append(f'group {group_id!r} has non-generator members {non_generators}')
    _raise_if(errors, 'rps_group')
    return {group_id: frozenset(group_techs) for group_id, group_techs in members.items()}


def resolve_requirements(
    raw: Mapping[tuple[str, str, str, int], float],
    groups: Collection[str],
    regions: Collection[str],
    domestic: Collection[str],
    region_analyze: Collection[str],
    model_years: Iterable[int],
) -> dict[RPSKey, RPSRequirement]:
    """Check the ``rps_requirement`` rows and find the requirement in force each model year.

    For each (group, region key), a model year ``y`` sees the rows with ``first_year <= y``, and
    the one with the latest ``first_year`` applies.  Every row that is in force but displaced is
    logged at INFO, once.  Rows for a known region that the region filter excludes are skipped
    (logged at INFO).  Only BTM rows are accepted for now.

    Parameters
    ----------
    raw : Mapping[tuple[str, str, str, int], float]
        ``(group_id, region, requirement_type, first_year) -> min_share_frac``, as read.
    groups : Collection[str]
        the resolved group ids.
    regions : Collection[str]
        every region in region.csv.
    domestic : Collection[str]
        the domestic regions; RPS applies only to these.
    region_analyze : Collection[str]
        the regions in this run.
    model_years : Iterable[int]
        the solved (representative) years.

    Returns
    -------
    dict[RPSKey, RPSRequirement]
        (group_id, region key, year) -> the requirement in force; only years with one.

    Raises
    ------
    ValueError
        if there are no rows, or listing every row with an unknown or unsupported
        requirement_type (anything but BTM), an unknown group or region, an international
        region, a share outside [0, 1], or a duplicate (group, region, type, first_year) once the
        type's case is folded.
    """
    if not raw:
        msg = 'rps_required is set, but no RPS requirements were found'
        logger.error(msg)
        raise ValueError(msg)

    entries: defaultdict[tuple[str, str], list[RPSRequirement]] = defaultdict(list)
    errors = []
    for (group_id, region, type_name, first_year), share in raw.items():
        row = f'row {(group_id, region, type_name, first_year)}'
        problems = []
        requirement_type = next(
            (t for t in RPSRequirementType if t.value == type_name.upper()), None
        )
        if requirement_type is None:
            problems.append(
                f'unsupported requirement_type {type_name!r} (only BTM is supported for now)'
            )
        if group_id not in groups:
            problems.append(f'unknown group {group_id!r}')
        if region != RPS_ALL_REGIONS and region not in regions:
            problems.append(f'unknown region {region!r}')
        elif region != RPS_ALL_REGIONS and region not in domestic:
            problems.append(f'region {region!r} is not domestic')
        if not 0.0 <= share <= 1.0:
            problems.append(f'min_share_frac {share} is outside [0, 1]')
        if problems or requirement_type is None:
            errors.append(f'{row}: ' + '; '.join(problems))
            continue

        if region != RPS_ALL_REGIONS and region not in region_analyze:
            logger.info('RPS %s: region %s is not in this run; skipped', row, region)
            continue
        requirement = RPSRequirement(requirement_type, share, first_year)
        if any(
            e.requirement_type is requirement_type and e.first_year == first_year
            for e in entries[group_id, region]
        ):
            errors.append(f'{row}: duplicates another {requirement_type.value} row')
            continue
        entries[group_id, region].append(requirement)
    _raise_if(errors, 'rps_requirement')

    # walk the years, noting each requirement displaced by a more recent one
    years = sorted(set(model_years))
    res: dict[RPSKey, RPSRequirement] = {}
    for (group_id, region), requirements in sorted(entries.items()):
        displaced: set[RPSRequirement] = set()
        for year in years:
            in_force = [r for r in requirements if r.first_year <= year]
            if not in_force:
                continue
            chosen = max(in_force, key=lambda r: r.first_year)
            res[group_id, region, year] = chosen
            for r in in_force:
                if r is not chosen and r not in displaced:
                    displaced.add(r)
                    logger.info(
                        'RPS %s requirement (group %s, region %s, from %d) is superseded from '
                        '%d by the %s requirement from %d',
                        r.requirement_type.value,
                        group_id,
                        region,
                        r.first_year,
                        year,
                        chosen.requirement_type.value,
                        chosen.first_year,
                    )
        for r in requirements:
            if years and r.first_year > years[-1]:
                logger.info(
                    'RPS %s requirement (group %s, region %s) starts in %d, after the last '
                    'model year; no effect',
                    r.requirement_type.value,
                    group_id,
                    region,
                    r.first_year,
                )
    return res


def requirement_rows(
    raw: Mapping[tuple[str, str, str, int], float], region_analyze: Collection[str]
) -> list[RPSRequirementRow]:
    """Type the ``rps_requirement`` rows as entered, including those not in this run.

    Call after ``resolve_requirements`` has checked ``raw``.

    Parameters
    ----------
    raw : Mapping[tuple[str, str, str, int], float]
        ``(group_id, region, requirement_type, first_year) -> min_share_frac``, as read.
    region_analyze : Collection[str]
        the regions in this run.

    Returns
    -------
    list[RPSRequirementRow]
        one per row, sorted by group, region, first year and type.
    """
    rows = [
        RPSRequirementRow(
            group_id,
            region,
            RPSRequirement(RPSRequirementType(type_name.upper()), share, first_year),
            region == RPS_ALL_REGIONS or region in region_analyze,
        )
        for (group_id, region, type_name, first_year), share in raw.items()
    ]
    return sorted(
        rows,
        key=lambda r: (
            r.group_id,
            r.region,
            r.requirement.first_year,
            r.requirement.requirement_type.value,
        ),
    )


def region_members(
    region_keys: Iterable[str], region_analyze: Iterable[str]
) -> dict[str, tuple[str, ...]]:
    """Map each region key to the regions it sums over.

    Parameters
    ----------
    region_keys : Iterable[str]
        region names and/or ``RPS_ALL_REGIONS``.
    region_analyze : Iterable[str]
        the regions in this run (what ``RPS_ALL_REGIONS`` covers).

    Returns
    -------
    dict[str, tuple[str, ...]]
        region key -> its regions, sorted.
    """
    analyzed = tuple(sorted(region_analyze))
    return {rk: analyzed if rk == RPS_ALL_REGIONS else (rk,) for rk in region_keys}


def existing_by_tech(
    existing: Mapping[tuple[str, str, int, int], float], generators: Collection[str]
) -> defaultdict[tuple[str, str, int], float]:
    """Sum the existing generator capacity over steps.

    Parameters
    ----------
    existing : Mapping[tuple[str, str, int, int], float]
        existing capacity, ``(region, tech, step, year) -> MW``.
    generators : Collection[str]
        the generator techs; others are left out.

    Returns
    -------
    defaultdict[tuple[str, str, int], float]
        (region, tech, year) -> MW, 0.0 where there is none.
    """
    by_tech: defaultdict[tuple[str, str, int], float] = defaultdict(float)
    for (region, tech, _step, year), mw in existing.items():
        if tech in generators:
            by_tech[region, tech, year] += mw
    return by_tech


def _heaviest_disjoint(items: list[tuple[str, frozenset[str], float]]) -> tuple[float, list[str]]:
    """The pairwise-disjoint subset of ``items`` with the largest share sum (exhaustive search).

    Parameters
    ----------
    items : list[tuple[str, frozenset[str], float]]
        (group_id, member techs, share).

    Returns
    -------
    tuple[float, list[str]]
        the share sum and the chosen group_ids.
    """
    if not items:
        return 0.0, []
    (group_id, techs, share), rest = items[0], items[1:]
    skip = _heaviest_disjoint(rest)
    weight, chosen = _heaviest_disjoint([i for i in rest if not i[1] & techs])
    return max(skip, (weight + share, [group_id, *chosen]), key=lambda x: x[0])


def disjoint_share_conflicts(
    min_share: Mapping[RPSKey, float], groups: Mapping[str, Collection[str]]
) -> dict[tuple[str, int], tuple[list[str], float]]:
    """Find BTMs on disjoint groups whose shares, in one region key and year, sum past 1.

    Groups with no member in common hold separate parts of the region key's generation, so their
    shares add, and past 1 they hold together only if the region key generates more than its load
    (exporting the rest).  Groups that share members, and BTMs on different region keys, are not
    checked.  The search is exhaustive (worst case 2**n in the BTM count n), so a warning is logged
    for any (region key, year) with more than ``DISJOINT_SEARCH_WARN_COUNT`` BTMs.

    Parameters
    ----------
    min_share : Mapping[RPSKey, float]
        (group, region key, year) -> BTM minimum share (``param_dicts['rps_min_share']``).
    groups : Mapping[str, Collection[str]]
        group_id -> member techs.

    Returns
    -------
    dict[tuple[str, int], tuple[list[str], float]]
        (region key, year) -> the pairwise-disjoint groups with the largest share sum, and that
        sum, where it is above 1.
    """
    by_key: defaultdict[tuple[str, int], list[tuple[str, frozenset[str], float]]] = defaultdict(
        list
    )
    for (group_id, rk, year), share in sorted(min_share.items()):
        if share > 0.0:
            by_key[rk, year].append((group_id, frozenset(groups[group_id]), share))
    conflicts = {}
    for key, items in sorted(by_key.items()):
        if len(items) > DISJOINT_SEARCH_WARN_COUNT:
            logger.warning(
                'RPS region %s, year %d has %d BTMs in force (more than %d):  the disjoint-share '
                'check searches their combinations exhaustively and may be slow; investigate the '
                'BTM inputs',
                key[0],
                key[1],
                len(items),
                DISJOINT_SEARCH_WARN_COUNT,
            )
        total, chosen = _heaviest_disjoint(items)
        if total > 1.0 + 1e-9:
            conflicts[key] = (sorted(chosen), total)
    return conflicts
