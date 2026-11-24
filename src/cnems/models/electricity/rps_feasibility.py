"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Regional Portfolio Standard (RPS) feasibility report:  a pre-solve look, from the loaded sets and
parameter data, at the RPS requirements, to show data problems and infeasibilities that can be
seen before any solve.  It reports problems; it does not suggest fixes, and it is not a check of
compliance.

After the groups come four tables.  *Requirements* lists every ``rps_requirement.csv`` row with
the model years it is in force.  *Requirement issues* lists the structural problems by group,
region key and years.  *Energy gaps* compares, for each BTM in force, the required annual
generation (share x annual load) with the most the existing group fleet can generate within the
region key's own load:  in each hour, no more than that hour's load.  It assumes no help from
storage or exports, so a requirement it lists may still be met with them, and one it does not
list may still bind.  *Build To Minima member coverage* lists each BTM group member (region,
tech) only for the required years it is not buildable in.  Each problem table is followed by a
legend of its statuses, or replaced by a line saying there are none.

Annual energy is the day-weighted sum over the representative hours, as in the model.  Nothing
is reported per hour.
"""

import logging
import math
import textwrap
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd
from tabulate import tabulate

from cnems.models.electricity.model_sets import CI, ModelSets
from cnems.models.electricity.param_data import ParamData
from cnems.models.electricity.rps import (
    RPSKey,
    RPSRequirement,
    RPSRequirementRow,
    disjoint_share_conflicts,
    existing_by_tech,
)

logger = logging.getLogger(__name__)

RPS_FEASIBILITY_FILE = 'rps_feasibility.txt'

# requirement issues, by (group, region key, year)
NO_GROUP_CAPACITY = 'no group capacity'
DISJOINT_SHARES_OVER_1 = 'disjoint shares over 1'
ISSUE_LEGEND = {
    NO_GROUP_CAPACITY: 'No member has existing, buildable or earlier-built capacity in the region '
    'key and year, so the group cannot generate at all.  Infeasible wherever the region key has '
    'load.  A validation error.',
    DISJOINT_SHARES_OVER_1: 'BTMs on groups with no member in common, in one region key and '
    'year, have shares summing past 1, so together they are not satisfiable within the region '
    "key's own load:  meeting them would rely on exports or storage.  A validation warning; "
    'likely a data error.',
}

# energy gaps, by (group, region key, year)
NEEDS_BUILDS = 'needs builds'
NEEDS_EARLY_BUILDS = 'needs early builds'
EXCEEDS_OWN_LOAD = 'exceeds own load'
UNMEETABLE = 'unmeetable'
GAP_LEGEND = {
    NEEDS_BUILDS: "The existing group fleet cannot meet the requirement within the region key's "
    'own load, but a member is buildable in the year:  the requirement relies on new builds.',
    NEEDS_EARLY_BUILDS: 'As above, but no member is buildable in the year itself:  the requirement '
    'relies on builds made in an earlier year.',
    EXCEEDS_OWN_LOAD: "Not satisfiable within the region key's own load:  even unlimited builds of "
    'the buildable members cannot generate enough in the hours the load can take it.  It may '
    'rely on storage or exports, and may be infeasible.',
    UNMEETABLE: 'The existing group fleet cannot generate the requirement even ignoring the '
    'load, and no member with any yield is buildable in the year or earlier.  The solve will be '
    'infeasible.',
}

# BTM member coverage, by (region, tech, year)
PRIOR_BUILDS_ONLY = 'prior builds only'
EXISTING_ONLY = 'existing only'
UNAVAILABLE = 'unavailable'
COVERAGE_LEGEND = {
    PRIOR_BUILDS_ONLY: 'Not buildable in the year, but buildable earlier:  anything it adds must '
    'be built in an earlier year, so the BTM may force early builds.',
    EXISTING_ONLY: 'Not buildable in the year or earlier:  it can generate from its existing '
    'capacity, but not add to it.',
    UNAVAILABLE: 'No existing capacity, and not buildable in the year or earlier:  it cannot '
    'generate.',
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
    gaps : Mapping[str, int]
        energy gap status -> (group, region key, year) with it.
    coverage : Mapping[str, int]
        coverage status -> BTM group members (region, tech) with it in some required year.
    """

    text: str
    requirement_rows: int
    in_force: int
    issues: Mapping[str, int]
    gaps: Mapping[str, int]
    coverage: Mapping[str, int]

    def summary(self) -> str:
        """One line for the log."""

        def counts(found: Mapping[str, int], legend: Mapping[str, str]) -> str:
            return ', '.join(f'{found.get(s, 0)} {s}' for s in legend)

        return (
            f'{self.requirement_rows} RPS requirement row(s), {self.in_force} (group, region, '
            f'year) in force.  Issues:  {counts(self.issues, ISSUE_LEGEND)}.  Energy gaps:  '
            f'{counts(self.gaps, GAP_LEGEND)}.  BTM members not buildable:  '
            f'{counts(self.coverage, COVERAGE_LEGEND)}'
        )


class _Buildability:
    """Where and when each (region, tech) can add capacity.

    A build persists, so a member can add capacity in a year if it is buildable in that year or
    any earlier one.  Nothing is buildable with ``capacity_expansion`` off.
    """

    def __init__(self, model_sets: ModelSets) -> None:
        self.entries: list[CI] = (
            list(model_sets.buildable_capacity) if model_sets.elec_config.capacity_expansion else []
        )
        self._buildable = {(i.region, i.tech, i.year) for i in self.entries}
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
        # group and region key are identifiers:  '001' and '1' must not both print as 1.000
        disable_numparse=[0, 1],
        tablefmt='github',
        floatfmt='.3f',
    )


def _frame_dict(frame: pd.DataFrame) -> dict:
    """A one-column param frame as ``{index: value}``; empty if the frame is."""
    return {} if frame.empty else dict(frame.iloc[:, 0].items())


Hourly = dict[int, float]
"""hour -> energy (MWh) in that representative hour, before the day weight"""


class _Energy:
    """Energy from the param data:  load, and each source's output per MW.

    A source's output per MW in an hour comes from its generation upper bound:  the hourly
    capacity factor for VRE, the seasonal capacity factor for hydro (ignoring the seasonal
    discharge budget, so an upper bound), and 1 for the other generators, times the hour weight.
    Its yield is that output summed over the year with the day weights.
    """

    def __init__(self, model_sets: ModelSets, param_data: ParamData) -> None:
        frames = param_data.param_frames
        weight_day = _frame_dict(frames['weight_day'])
        day_of = _frame_dict(frames['map_hour_day'])
        self._wh = _frame_dict(frames['weight_hour'])
        self._season_of = _frame_dict(frames['map_hour_season'])
        self._wd = {hr: weight_day[day_of[hr]] for hr in model_sets.hour}
        self._load = _frame_dict(frames['elec_load'])
        self._cf_hourly = _frame_dict(frames['capacity_factor_hourly'])
        self._cf_seasonal = _frame_dict(frames['capacity_factor_seasonal'])
        self._vre = set(model_sets.tech_vre)
        self._hydro = set(model_sets.tech_hydro)
        self._output: dict[tuple[str, str, int], Hourly] = {}

    def hourly_load(self, regions: Iterable[str], year: int) -> Hourly:
        """Load (MWh) in each hour, summed over ``regions``."""
        return {hr: sum(self._load.get((r, year, hr), 0.0) for r in regions) for hr in self._wd}

    def annual(self, energy: Hourly) -> float:
        """Annual (day-weighted) sum of ``energy`` (MWh)."""
        return sum(self._wd[hr] * e for hr, e in energy.items())

    def within_load(self, output: Hourly, load: Hourly) -> float:
        """Annual energy of ``output`` the load can take:  in each hour, at most the load (MWh)."""
        return sum(wd * min(output.get(hr, 0.0), load[hr]) for hr, wd in self._wd.items())

    def output_per_mw(self, region: str, tech: str, step: int) -> Hourly:
        """The most one MW of (region, tech, step) can generate in each hour (MWh)."""
        key = (region, tech, step)
        if key not in self._output:
            if tech in self._vre:
                cf = {hr: self._cf_hourly.get((*key, hr), 0.0) for hr in self._wh}
            elif tech in self._hydro:
                cf = {
                    hr: self._cf_seasonal.get((*key, self._season_of[hr]), 0.0) for hr in self._wh
                }
            else:
                cf = dict.fromkeys(self._wh, 1.0)
            self._output[key] = {hr: cf[hr] * self._wh[hr] for hr in self._wd}
        return self._output[key]


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
    existing_rtsy = param_data.existing_capacity()
    existing = existing_by_tech(existing_rtsy, set(model_sets.tech_gen))
    years = sorted(set(model_sets.year_map.values()))
    build = _Buildability(model_sets)
    energy = _Energy(model_sets, param_data)

    # ---------------------------------------------------------------- requirement issues
    issue_years: defaultdict[tuple[str, str, str], list[int]] = defaultdict(list)
    for key in model_sets.rps_btm_index:
        if not model_sets.rps_group_sources[key]:
            group, rk, year = key
            issue_years[group, rk, NO_GROUP_CAPACITY].append(year)
    min_share = {k: requirements[k].min_share for k in model_sets.rps_btm_index}
    for (rk, year), (chosen, _total) in disjoint_share_conflicts(min_share, groups).items():
        for group in chosen:
            issue_years[group, rk, DISJOINT_SHARES_OVER_1].append(year)
    issue_counts = Counter(dict.fromkeys(ISSUE_LEGEND, 0))
    issue_rows = []
    for (group, rk, status), ys in sorted(
        issue_years.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1][0])
    ):
        issue_counts[status] += len(ys)
        issue_rows.append([group, rk, _ranges(ys, years), status])
    if issue_rows:
        issues_text = (
            tabulate(
                issue_rows,
                headers=['group', 'region key', 'years', 'status'],
                disable_numparse=True,
                tablefmt='github',
            )
            + '\n\n'
            + _legend(ISSUE_LEGEND)
        )
    else:
        issues_text = 'No requirement issues found.'

    # ---------------------------------------------------------------- energy gaps
    gap_counts = Counter(dict.fromkeys(GAP_LEGEND, 0))
    gap_rows = []
    for key in model_sets.rps_btm_index:
        group, rk, year = key
        regions, techs = set(members[rk]), groups[group]
        load = energy.hourly_load(regions, year)
        required = requirements[key].min_share * energy.annual(load)
        existing_output: defaultdict[int, float] = defaultdict(float)
        for (r, t, s, y), mw in existing_rtsy.items():
            if y == year and r in regions and t in techs:
                for hr, e in energy.output_per_mw(r, t, s).items():
                    existing_output[hr] += mw * e
        existing_max = energy.within_load(existing_output, load)
        gap = required - existing_max
        tolerance = 1e-6 * max(required, 1.0)
        if gap <= tolerance:
            continue
        candidates = [
            i
            for i in build.entries
            if i.year <= year
            and i.region in regions
            and i.tech in techs
            and any(e > 0.0 for e in energy.output_per_mw(i.region, i.tech, i.step).values())
        ]
        # with unlimited builds, the group meets the load in every hour a candidate generates
        most = energy.within_load(
            {
                hr: math.inf
                if any(energy.output_per_mw(i.region, i.tech, i.step)[hr] > 0.0 for i in candidates)
                else e
                for hr, e in ((hr, existing_output[hr]) for hr in load)
            },
            load,
        )
        generable = math.inf if candidates else energy.annual(existing_output)
        if most < required - tolerance and generable >= required - tolerance:
            status = EXCEEDS_OWN_LOAD
        elif not candidates:
            status = UNMEETABLE
        elif any(i.year == year for i in candidates):
            status = NEEDS_BUILDS
        else:
            status = NEEDS_EARLY_BUILDS
        gap_counts[status] += 1
        gap_rows.append([group, rk, year, required, existing_max, gap, status])
    if not model_sets.rps_btm_index:
        gaps_text = '(no Build To Minima requirements in force)'
    elif gap_rows:
        gaps_text = (
            tabulate(
                gap_rows,
                headers=[
                    'group',
                    'region key',
                    'year',
                    'required MWh',
                    'existing max MWh',
                    'gap MWh',
                    'status',
                ],
                disable_numparse=[0, 1],
                tablefmt='github',
                floatfmt=',.0f',
            )
            + '\n\n'
            + _legend(GAP_LEGEND)
        )
    else:
        gaps_text = _wrap(
            'No energy gaps:  the existing group fleet can meet every requirement in force within '
            "the region key's own load.  This does not confirm compliance:  hourly balance and "
            'other constraints may still prevent it.'
        )

    # ---------------------------------------------------------------- BTM member coverage
    required_by: defaultdict[tuple[str, str], defaultdict[int, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for group, rk, year in model_sets.rps_btm_index:
        for region in members[rk]:
            for tech in groups[group]:
                required_by[region, tech][year].add(f'{group}@{rk}')
    coverage_counts = Counter(dict.fromkeys(COVERAGE_LEGEND, 0))
    coverage_rows = []
    for (region, tech), by_year in sorted(required_by.items()):
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
            needed_by = sorted(set().union(*(by_year[y] for y in ys)))
            coverage_rows.append([region, tech, _ranges(ys, years), status, ', '.join(needed_by)])
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
        disable_numparse=True,
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
                'requirement in force for its group and region key:  of the rows started by a '
                'year, the one with the latest first year.'
            )
            + '\n\n'
            + _requirements_table(model_sets.rps_requirement_rows, requirements, years),
            'Requirement issues\n\n'
            + _wrap('Structural problems, by group, region key and model years.')
            + '\n\n'
            + issues_text,
            'Energy gaps\n\n'
            + _wrap(
                "Each BTM in force whose requirement (min share x the region key's annual "
                'load) is more than the existing group fleet can generate within the region '
                "key's own load:  at full capacity factor, but in each hour no more than that "
                "hour's load.  No help from storage or exports is assumed, so a listed "
                'requirement may still be met with them.  Requirements not listed may still '
                'bind:  hourly balance and other constraints are not checked.'
            )
            + '\n\n'
            + gaps_text,
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
        gaps=dict(gap_counts),
        coverage=dict(coverage_counts),
    )
