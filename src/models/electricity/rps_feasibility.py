"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Regional Portfolio Standard (RPS) feasibility report:  a pre-solve look, from the loaded sets and
parameter data, at each RPS requirement in force and whether its group can meet it.

Two tables:  the requirements in force, each with its existing share and status, and the
coverage of each Build To Minima group, member (region, tech) by member, flagging the members
that can hold no capacity (neither existing nor buildable) in some of the requirement's years, and
those held to their existing capacity (nothing buildable by then).
"""

import logging
import textwrap
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from tabulate import tabulate

from src.models.electricity.elec_config import RPSRequirementType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.rps import existing_by_tech

logger = logging.getLogger(__name__)

RPS_FEASIBILITY_FILE = 'rps_feasibility.txt'

NO_GROUP_CAPACITY = 'NO GROUP CAPACITY'
UNAVAILABLE = 'WARNING'
EXISTING_ONLY = 'EXISTING ONLY'


@dataclass(frozen=True)
class FeasibilityReport:
    """The report text, with counts of its findings for the log.

    Parameters
    ----------
    text : str
    requirements : int
        (group, region key, year) requirements in force.
    needs_builds : int
        BTM requirements the existing capacity does not meet, with a group member buildable.
    needs_retirements : int
        BTM requirements the existing capacity does not meet, with no group member buildable:
        only retiring other generators can meet them.
    no_group_capacity : int
        BTM requirements whose group can hold no capacity at all; these cannot be met.
    unavailable_members : int
        BTM group members (region, tech) with no possible capacity in some required year.
    existing_only_members : int
        BTM group members (region, tech) held to their existing capacity in some required year.
    """

    text: str
    requirements: int
    needs_builds: int
    needs_retirements: int
    no_group_capacity: int
    unavailable_members: int
    existing_only_members: int

    def summary(self) -> str:
        """One line for the log."""
        return (
            f'{self.requirements} RPS requirement(s) in force:  {self.needs_builds} need builds, '
            f'{self.needs_retirements} need retirements (no group member buildable), '
            f'{self.no_group_capacity} with no group capacity; BTM group members:  '
            f'{self.unavailable_members} unavailable, {self.existing_only_members} existing only'
        )


def _wrap(text: str) -> str:
    """Wrap a paragraph of the report at 100 characters."""
    return textwrap.fill(text, width=100)


def _years(years: Iterable[int], all_years: Sequence[int]) -> str:
    """Format a set of years:  'all' (of ``all_years``), '-' (none), or the list."""
    years = sorted(years)
    if not years:
        return '-'
    return 'all' if years == sorted(all_years) else ', '.join(map(str, years))


def _btm_status(
    share: float, group_mw: float, total_mw: float, buildable: bool
) -> tuple[str, str | None]:
    """Status of a BTM against the existing capacity, and the kind of shortfall.

    With a group member buildable, the shortfall is the group MW to build, assuming the other
    generators stay at their existing capacity:  ``share / (1 - share)`` times their MW, less the
    group's.  With none buildable, it is the other generators' MW to retire, the group held at its
    existing capacity:  their MW less ``(1 - share) / share`` times the group's.

    Returns
    -------
    tuple of (str, str or None)
        The status, and ``'builds'``, ``'retirements'`` or None (met by existing).
    """
    if group_mw >= share * total_mw:
        return 'met by existing', None
    others = total_mw - group_mw
    if share >= 1.0 or (not buildable and group_mw <= 0.0):
        return 'needs all other generators retired', 'retirements'
    if buildable:
        short = share / (1.0 - share) * others - group_mw
        return f'needs builds (>= {short:.1f} MW more, others at existing)', 'builds'
    retire = others - (1.0 - share) / share * group_mw
    return (
        f'no group member buildable:  needs >= {retire:.1f} MW of other generators retired',
        'retirements',
    )


def rps_feasibility_report(model_sets: ModelSets, param_data: ParamData) -> FeasibilityReport:
    """Build the RPS feasibility report.

    Parameters
    ----------
    model_sets : ModelSets
        with the RPS sets built (``rps_required`` on).
    param_data : ParamData
        with the RPS params.

    Returns
    -------
    FeasibilityReport
    """
    requirements = model_sets.rps_requirements
    members = model_sets.rps_region_members
    groups = model_sets.rps_group_techs
    btm_index = set(model_sets.rps_btm_index)
    floors = param_data.param_dicts['rps_rll_floor']
    existing = existing_by_tech(param_data.existing_capacity(), set(model_sets.tech_gen))
    years = sorted(set(model_sets.year_map.values()))
    # a build persists, so a member can add capacity in a year from its first buildable year on
    first_build: dict[tuple[str, str], int] = {}
    for idx in model_sets.buildable_capacity:
        key = (idx.region, idx.tech)
        first_build[key] = min(first_build.get(key, idx.year), idx.year)

    def can_add(region: str, tech: str, year: int) -> bool:
        return first_build.get((region, tech), year + 1) <= year

    def existing_mw(rk: str, techs: Iterable[str], year: int) -> float:
        techs = list(techs)
        return sum(existing[r, t, year] for r in members[rk] for t in techs)

    # ---------------------------------------------------------------- requirements in force
    rows = []
    counts: Counter[str] = Counter()
    for key in sorted(requirements):
        group, rk, year = key
        req = requirements[key]
        group_mw = existing_mw(rk, groups[group], year)
        total_mw = existing_mw(rk, model_sets.tech_gen, year)
        if req.min_share == 0.0:
            status = 'no constraint (minimum is 0)'
        elif req.requirement_type is RPSRequirementType.RLL:
            if key in floors:
                status = f'floor {floors[key]:.1f} MW'
            elif total_mw <= 0.0:
                status = 'not applied (no existing generators)'
            else:
                status = 'not applied (existing share below minimum)'
        elif key in btm_index and not model_sets.rps_group_sources[key]:
            status = NO_GROUP_CAPACITY
            counts['no_group_capacity'] += 1
        else:
            buildable = any(can_add(r, t, year) for r in members[rk] for t in groups[group])
            status, shortfall = _btm_status(req.min_share, group_mw, total_mw, buildable)
            if shortfall:
                counts[shortfall] += 1
        rows.append(
            [
                group,
                rk,
                year,
                req.requirement_type.value,
                req.first_year,
                req.min_share,
                f'{group_mw / total_mw:.3f}' if total_mw > 0.0 else 'n/a',
                status,
            ]
        )
    requirement_table = tabulate(
        rows,
        headers=['group', 'region', 'year', 'type', 'from', 'min share', 'existing', 'status'],
        tablefmt='github',
        floatfmt='.3f',
    )

    # ---------------------------------------------------------------- BTM group coverage
    btm_years: defaultdict[tuple[str, str], list[int]] = defaultdict(list)
    for group, rk, year in model_sets.rps_btm_index:
        btm_years[group, rk].append(year)
    available = {(idx.region, idx.tech, idx.year) for idx in model_sets.capacity_index}
    buildable = {(idx.region, idx.tech, idx.year) for idx in model_sets.buildable_capacity}
    existing_idx = {(idx.region, idx.tech, idx.year) for idx in model_sets.existing_capacity}

    coverage = []
    for (group, rk), req_years in sorted(btm_years.items()):
        for region in members[rk]:
            for tech in sorted(groups[group]):
                missing = [y for y in req_years if (region, tech, y) not in available]
                existing_only = [
                    y
                    for y in req_years
                    if (region, tech, y) in existing_idx and not can_add(region, tech, y)
                ]
                counts['unavailable'] += bool(missing)
                counts['existing_only'] += bool(existing_only)
                flags = [UNAVAILABLE] * bool(missing) + [EXISTING_ONLY] * bool(existing_only)
                coverage.append(
                    [
                        group,
                        rk,
                        region,
                        tech,
                        existing[region, tech, req_years[0]],
                        _years((y for y in req_years if (region, tech, y) in buildable), req_years),
                        _years(existing_only, req_years),
                        _years(missing, req_years),
                        ', '.join(flags),
                    ]
                )
    coverage_table = (
        tabulate(
            coverage,
            headers=[
                'group',
                'region key',
                'region',
                'tech',
                'existing MW',
                'buildable in',
                'existing only in',
                'unavailable in',
                '',
            ],
            tablefmt='github',
            floatfmt='.1f',
        )
        if coverage
        else '(no Build To Minima requirements)'
    )

    group_table = tabulate(
        [[g, ', '.join(sorted(techs))] for g, techs in sorted(groups.items())],
        headers=['group', 'member techs'],
        tablefmt='github',
    )
    text = '\n\n'.join(
        [
            'RPS feasibility report',
            (
                f'Regions analyzed:  {", ".join(sorted(model_sets.region_analyze))}\n'
                f'Model years:  {", ".join(map(str, years))}'
            ),
            'Groups\n\n' + group_table,
            'Requirements in force\n\n'
            + _wrap(
                'Existing share:  the group share of the existing generator capacity.  '
                'Requirements superseded by another are not listed (see the log).'
            )
            + '\n\n'
            + requirement_table,
            'Build To Minima group coverage\n\n'
            + _wrap(
                'Existing MW is for the first required year.  A member is existing only in a '
                'year it has existing capacity but is not buildable in that year or earlier:  it '
                'can keep or retire its capacity, not add to it.  A member is unavailable in a '
                'year it has neither existing nor buildable capacity, nor capacity built '
                f'earlier.  "{NO_GROUP_CAPACITY}" above means no member is available:  the '
                'requirement cannot be met.'
            )
            + '\n\n'
            + coverage_table,
        ]
    )
    return FeasibilityReport(
        text=text + '\n',
        requirements=len(requirements),
        needs_builds=counts['builds'],
        needs_retirements=counts['retirements'],
        no_group_capacity=counts['no_group_capacity'],
        unavailable_members=counts['unavailable'],
        existing_only_members=counts['existing_only'],
    )
