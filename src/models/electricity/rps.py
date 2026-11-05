"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Regional Portfolio Standard (RPS) data processing:  pure functions (no pyomo) that turn the raw
``rps_group`` / ``rps_requirement`` reads into checked group memberships and the requirement in
force for each (group, region key, model year).

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

type RPSKey = tuple[str, str, int]
"""(group_id, region key, model year)"""


@dataclass(frozen=True)
class RPSRequirement:
    """One row of ``rps_requirement.csv``, checked and typed.

    Parameters
    ----------
    requirement_type : RPSRequirementType
    min_share : float
        minimum group share of generator capacity, in [0, 1].
    first_year : int
        first year (inclusive) the requirement applies.
    """

    requirement_type: RPSRequirementType
    min_share: float
    first_year: int


def _raise_if(errors: list[str], table: str) -> None:
    """Log each problem and raise them together, if there are any."""
    if not errors:
        return
    for error in errors:
        logger.error('%s: %s', table, error)
    raise ValueError(f'{len(errors)} problem(s) in {table}:\n  ' + '\n  '.join(errors))


def resolve_groups(
    rows: Iterable[tuple[str, str, str]],
    tech_flags: Mapping[str, Iterable[str]],
    generators: Collection[str],
) -> dict[str, frozenset[str]]:
    """Resolve ``rps_group`` rows into each group's member techs.

    Membership is additive across rows; a tech named directly and through a ``tech_group`` (or
    twice) is simply a member.

    Parameters
    ----------
    rows : Iterable[tuple[str, str, str]]
        raw ``(group_id, tech, tech_group)`` rows, blank cells as ``''``.
    tech_flags : Mapping[str, Iterable[str]]
        the tech.csv property read:  ``'tech'`` -> every tech, and each ``is_*`` column -> the
        techs flagged True.  The ``is_*`` names are the valid ``tech_group`` values.
    generators : Collection[str]
        the generator techs; every member must be one.

    Returns
    -------
    dict[str, frozenset[str]]
        group_id -> member techs.

    Raises
    ------
    ValueError
        listing every row with no group_id, with both or neither of tech / tech_group, or naming
        an unknown tech or tech_group, and every group that is empty or has a non-generator
        member.
    """
    techs = set(tech_flags['tech'])
    members: defaultdict[str, set[str]] = defaultdict(set)
    errors = []
    for group_id, tech, tech_group in rows:
        row = f'row {(group_id, tech, tech_group)}'
        if not group_id:
            errors.append(f'{row}: missing group_id')
        elif bool(tech) == bool(tech_group):
            errors.append(f'{row}: give exactly one of tech / tech_group')
        elif tech:
            if tech in techs:
                members[group_id].add(tech)
            else:
                errors.append(f'{row}: unknown tech {tech!r}')
        elif tech_group != 'tech' and tech_group in tech_flags:
            members[group_id].update(tech_flags[tech_group])
        else:
            errors.append(
                f'{row}: unknown tech_group {tech_group!r} (expected a tech.csv is_* column)'
            )

    for group_id, group_techs in sorted(members.items()):
        if not group_techs:
            errors.append(f'group {group_id!r} has no member techs')
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

    For each (group, region key), a model year ``y`` sees the rows with ``first_year <= y``.  If
    any of those are BTM, the BTM with the latest ``first_year`` applies; otherwise the latest
    RLL does.  Every row that is in force but displaced is logged at INFO, once.  Rows for a
    known region that the region filter excludes are skipped (logged at INFO).

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
        if there are no rows, or listing every row with an unknown requirement_type, group or
        region, an international region, a share outside [0, 1], or a duplicate (group, region,
        type, first_year) once the type's case is folded.
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
            problems.append(f'unknown requirement_type {type_name!r} (expected RLL or BTM)')
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

    # this segment walks through the requirements to detect if any existing requirement is
    # "displaced" by either a more recent one, or in the case that it is a RLL, any previous BTM
    # which alwasys supersedes RLL
    years = sorted(set(model_years))
    res: dict[RPSKey, RPSRequirement] = {}
    for (group_id, region), requirements in sorted(entries.items()):
        displaced: set[RPSRequirement] = set()
        for year in years:
            in_force = [r for r in requirements if r.first_year <= year]
            if not in_force:
                continue
            btm = [r for r in in_force if r.requirement_type is RPSRequirementType.BTM]
            chosen = max(btm or in_force, key=lambda r: r.first_year)
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


def rll_floors(
    requirements: Mapping[RPSKey, RPSRequirement],
    groups: Mapping[str, Collection[str]],
    region_analyze: Iterable[str],
    generators: Collection[str],
    existing: Mapping[tuple[str, str, int, int], float],
) -> dict[RPSKey, float]:
    """Compute the capacity floor of each RLL requirement from the existing capacity.

    The floor is ``min_share`` times the existing generator capacity of the requirement's
    regions and year.  It is kept only where the group's existing share already meets
    ``min_share``; otherwise (or with no existing generator capacity) the requirement is dropped
    and logged at INFO, since RLL never forces builds.

    Parameters
    ----------
    requirements : Mapping[RPSKey, RPSRequirement]
        the requirements in force (see ``resolve_requirements``); non-RLL entries are ignored.
    groups : Mapping[str, Collection[str]]
        group_id -> member techs.
    region_analyze : Iterable[str]
        the regions in this run.
    generators : Collection[str]
        the generator techs (the share's denominator).
    existing : Mapping[tuple[str, str, int, int], float]
        existing capacity, ``(region, tech, step, year) -> MW``.

    Returns
    -------
    dict[RPSKey, float]
        (group_id, region key, year) -> floor MW, for the RLL requirements kept.
    """
    # existing MW summed over steps, by (region, tech, year)
    by_tech: defaultdict[tuple[str, str, int], float] = defaultdict(float)
    for (region, tech, _step, year), mw in existing.items():
        if tech in generators:
            by_tech[region, tech, year] += mw

    rll = {k: r for k, r in requirements.items() if r.requirement_type is RPSRequirementType.RLL}
    members = region_members({rk for _, rk, _ in rll}, region_analyze)
    floors: dict[RPSKey, float] = {}
    for (group_id, rk, year), requirement in sorted(rll.items()):
        group_mw = sum(
            by_tech.get((r, t, year), 0.0) for r in members[rk] for t in groups[group_id]
        )
        total_mw = sum(by_tech.get((r, t, year), 0.0) for r in members[rk] for t in generators)
        if total_mw <= 0.0 or group_mw < requirement.min_share * total_mw:
            logger.info(
                'RPS RLL (group %s, region %s, year %d) not applied:  existing share %s is below '
                'the minimum %.3f',
                group_id,
                rk,
                year,
                f'{group_mw / total_mw:.3f}' if total_mw > 0.0 else 'n/a (no generators)',
                requirement.min_share,
            )
            continue
        floors[group_id, rk, year] = requirement.min_share * total_mw
    return floors
