"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Regional Portfolio Standard (RPS) feasibility report:  a pre-solve look, from the loaded sets and
parameter data, at the RPS requirements and whether they can be met.

After the groups come three tables.  *Requirements* lists every ``rps_requirement.csv`` row with
the model years it is in force.  *Requirement issues* lists only the problems, by group, region
key and years.  *Build To Minima member coverage* lists each BTM group member (region, tech) only
for the required years it is not buildable in.  Each problem table is followed by a legend of its
statuses, or replaced by a line saying there are none.
"""

import logging
import textwrap
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from tabulate import tabulate

from src.models.electricity.elec_config import RPSRequirementType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.rps import RPSKey, RPSRequirement, RPSRequirementRow, existing_by_tech

logger = logging.getLogger(__name__)

RPS_FEASIBILITY_FILE = 'rps_feasibility.txt'

# requirement issues, by (group, region key, year)
NO_GROUP_CAPACITY = 'no group capacity'
NO_MEMBER_BUILDABLE = 'no member buildable'
RLL_NOT_APPLIED = 'RLL not applied'
ISSUE_LEGEND = {
    NO_GROUP_CAPACITY: 'BTM:  no member has existing, buildable or earlier-built capacity in the '
    'region key and year, so the requirement cannot be met.  Also a validation error.',
    NO_MEMBER_BUILDABLE: 'BTM:  the existing capacity falls short of the minimum, and no member '
    'is buildable in the year or earlier.  Only retiring other generators can meet it.',
    RLL_NOT_APPLIED: 'RLL:  the existing share is below the minimum (or there are no existing '
    'generators), so no floor is written.  An RLL never forces builds.',
}

# BTM member coverage, by (region, tech, year)
PRIOR_BUILDS_ONLY = 'prior builds only'
EXISTING_ONLY = 'existing only'
UNAVAILABLE = 'unavailable'
COVERAGE_LEGEND = {
    PRIOR_BUILDS_ONLY: 'Not buildable in the year, but buildable earlier:  anything it adds must '
    'be built in an earlier year, so the BTM may force early builds.',
    EXISTING_ONLY: 'Not buildable in the year or earlier:  it can keep or retire its existing '
    'capacity, but not add to it.',
    UNAVAILABLE: 'No existing capacity, and not buildable in the year or earlier:  it can hold '
    'no capacity.',
}

LEGEND_WIDTH = 72
"""wrap width of a legend's meaning column"""


@dataclass(frozen=True)
class FeasibilityReport:
    """The report text, with counts of its findings for the log.

    Parameters
    ----------
    text : str
    requirement_rows : int
        rows of ``rps_requirement.csv``.
    in_force : int
        (group, region key, year) with a requirement in force.
    issues : Mapping[str, int]
        issue status -> (group, region key, year) with it.
    coverage : Mapping[str, int]
        coverage status -> BTM group members (region, tech) with it in some required year.
    """

    text: str
    requirement_rows: int
    in_force: int
    issues: Mapping[str, int]
    coverage: Mapping[str, int]

    def summary(self) -> str:
        """One line for the log."""
        issues = ', '.join(f'{self.issues.get(s, 0)} {s}' for s in ISSUE_LEGEND)
        coverage = ', '.join(f'{self.coverage.get(s, 0)} {s}' for s in COVERAGE_LEGEND)
        return (
            f'{self.requirement_rows} RPS requirement row(s), {self.in_force} (group, region, '
            f'year) in force.  Issues:  {issues}.  BTM members not buildable:  {coverage}'
        )


class _Buildability:
    """Where and when each (region, tech) can add capacity.

    A build persists, so a member can add capacity in a year if it is buildable in that year or
    any earlier one.
    """

    def __init__(self, model_sets: ModelSets) -> None:
        self._buildable = {(i.region, i.tech, i.year) for i in model_sets.buildable_capacity}
        self._first: dict[tuple[str, str], int] = {}
        for region, tech, year in self._buildable:
            self._first[region, tech] = min(self._first.get((region, tech), year), year)

    def in_year(self, region: str, tech: str, year: int) -> bool:
        """Buildable in ``year`` itself."""
        return (region, tech, year) in self._buildable

    def by_year(self, region: str, tech: str, year: int) -> bool:
        """Buildable in ``year`` or earlier."""
        return self._first.get((region, tech), year + 1) <= year


def _wrap(text: str) -> str:
    """Wrap a paragraph of the report at 100 characters."""
    return textwrap.fill(text, width=100)


def _ranges(years: Iterable[int], model_years: Sequence[int]) -> str:
    """Format years as runs of consecutive model years, e.g. ``'2025–2035, 2045'``; '-' if none."""
    position = {y: i for i, y in enumerate(model_years)}
    runs: list[list[int]] = []
    for year in sorted(set(years)):
        if runs and position[year] == position[runs[-1][-1]] + 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    if not runs:
        return '-'
    return ', '.join(str(r[0]) if len(r) == 1 else f'{r[0]}–{r[-1]}' for r in runs)


def _legend(legend: Mapping[str, str]) -> str:
    """A status legend table."""
    return tabulate(
        list(legend.items()),
        headers=['status', 'meaning'],
        tablefmt='github',
        maxcolwidths=[None, LEGEND_WIDTH],
    )


def _describe(requirement: RPSRequirement) -> str:
    """E.g. ``'BTM from 2030'``."""
    return f'{requirement.requirement_type.value} from {requirement.first_year}'


def _requirements_table(
    rows: Sequence[RPSRequirementRow],
    requirements: Mapping[RPSKey, RPSRequirement],
    years: Sequence[int],
) -> str:
    """Every requirement row as entered, with its years in force and why it is not in force."""
    table = []
    for row in rows:
        req = row.requirement
        in_force = [
            y
            for y in years
            if row.in_run and requirements.get((row.group_id, row.region, y)) == req
        ]
        notes = []
        if not row.in_run:
            notes.append('region not in this run')
        elif not years or req.first_year > years[-1]:
            notes.append('starts after the last model year')
        else:
            later = [y for y in years if y >= req.first_year and (not in_force or y > in_force[-1])]
            if later:
                other = requirements[row.group_id, row.region, later[0]]
                notes.append(
                    f'superseded from {later[0]} by {_describe(other)}'
                    if in_force
                    else f'never in force:  {_describe(other)} in force'
                )
        if in_force and req.min_share == 0.0:
            notes.append('minimum 0:  no constraint')
        table.append(
            [
                row.group_id,
                row.region,
                req.requirement_type.value,
                req.first_year,
                req.min_share,
                _ranges(in_force, years),
                '; '.join(notes),
            ]
        )
    return tabulate(
        table,
        headers=['group', 'region key', 'type', 'from', 'min share', 'in force', 'note'],
        tablefmt='github',
        floatfmt='.3f',
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
    floors = param_data.param_dicts['rps_rll_floor']
    existing = existing_by_tech(param_data.existing_capacity(), set(model_sets.tech_gen))
    years = sorted(set(model_sets.year_map.values()))
    build = _Buildability(model_sets)

    def existing_mw(rk: str, techs: Iterable[str], year: int) -> float:
        techs = list(techs)
        return sum(existing[r, t, year] for r in members[rk] for t in techs)

    # ---------------------------------------------------------------- requirement issues
    issue_years: defaultdict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for key in sorted(requirements):
        group, rk, year = key
        req = requirements[key]
        status = None
        if req.min_share == 0.0:
            pass
        elif req.requirement_type is RPSRequirementType.RLL:
            status = None if key in floors else RLL_NOT_APPLIED
        elif not model_sets.rps_group_sources[key]:
            status = NO_GROUP_CAPACITY
        elif existing_mw(rk, groups[group], year) < req.min_share * existing_mw(
            rk, model_sets.tech_gen, year
        ) and not any(build.by_year(r, t, year) for r in members[rk] for t in groups[group]):
            status = NO_MEMBER_BUILDABLE
        if status:
            issue_years[group, rk, req.requirement_type.value, status].append(year)
    issue_counts = Counter(dict.fromkeys(ISSUE_LEGEND, 0))
    issue_rows = []
    for (group, rk, type_name, status), ys in sorted(
        issue_years.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1][0])
    ):
        issue_counts[status] += len(ys)
        issue_rows.append([group, rk, type_name, _ranges(ys, years), status])
    if issue_rows:
        issues_text = (
            tabulate(
                issue_rows,
                headers=['group', 'region key', 'type', 'years', 'status'],
                disable_numparse=True,
                tablefmt='github',
            )
            + '\n\n'
            + _legend(ISSUE_LEGEND)
        )
    else:
        issues_text = 'No requirement issues found.'

    # ---------------------------------------------------------------- BTM member coverage
    required: defaultdict[tuple[str, str], defaultdict[int, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for group, rk, year in model_sets.rps_btm_index:
        for region in members[rk]:
            for tech in groups[group]:
                required[region, tech][year].add(f'{group}@{rk}')
    coverage_counts = Counter(dict.fromkeys(COVERAGE_LEGEND, 0))
    coverage_rows = []
    for (region, tech), by_year in sorted(required.items()):
        status_years: defaultdict[str, list[int]] = defaultdict(list)
        for year in sorted(by_year):
            if build.in_year(region, tech, year):
                continue
            if build.by_year(region, tech, year):
                status_years[PRIOR_BUILDS_ONLY].append(year)
            elif existing[region, tech, year] > 0.0:
                status_years[EXISTING_ONLY].append(year)
            else:
                status_years[UNAVAILABLE].append(year)
        for status, ys in sorted(status_years.items(), key=lambda kv: kv[1][0]):
            coverage_counts[status] += 1
            required_by = sorted(set().union(*(by_year[y] for y in ys)))
            coverage_rows.append([region, tech, _ranges(ys, years), status, ', '.join(required_by)])
    if not model_sets.rps_btm_index:
        coverage_text = '(no Build To Minima requirements in force)'
    elif coverage_rows:
        coverage_text = (
            tabulate(
                coverage_rows,
                headers=['region', 'tech', 'not buildable in', 'status', 'required by'],
                disable_numparse=True,
                tablefmt='github',
            )
            + '\n\n'
            + _legend(COVERAGE_LEGEND)
        )
    else:
        coverage_text = 'Every BTM group member is buildable in every year it is required.'

    # ---------------------------------------------------------------- assemble
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
            'Requirements\n\n'
            + _wrap(
                'One row per rps_requirement.csv row.  In force:  the model years it is the '
                'requirement in force for its group and region key.  Of the rows started by a '
                'year, the latest BTM is in force, or else the latest RLL.'
            )
            + '\n\n'
            + _requirements_table(model_sets.rps_requirement_rows, requirements, years),
            'Requirement issues\n\n'
            + _wrap('Problems only, by group, region key and model years.')
            + '\n\n'
            + issues_text,
            'Build To Minima member coverage\n\n'
            + _wrap(
                'Each member (region, tech) of a group with a BTM in force, in the required '
                'years it is not buildable in.  Members buildable in every required year are '
                'not listed.  Required by:  the group@region key BTMs that need the member in '
                'those years.'
            )
            + '\n\n'
            + coverage_text,
        ]
    )
    return FeasibilityReport(
        text=text + '\n',
        requirement_rows=len(model_sets.rps_requirement_rows),
        in_force=len(requirements),
        issues=dict(issue_counts),
        coverage=dict(coverage_counts),
    )
