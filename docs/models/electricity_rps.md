# Regional Portfolio Standards

A Regional Portfolio Standard (RPS) requires a group of technologies, typically renewables, to
generate a minimum share of the electricity used in a region. As in most real-world programs, the
share is of the region's annual load (its retail sales), and the requirement is met by the group's
annual generation. The electricity model represents RPS as requirements on technology groups, each
set for a region (or all the analyzed regions together) from a first year onward. The constraint is
equation (35) in [the model formulation](electricity.md#regional-portfolio-standards).

## Turning RPS On

RPS is off unless `rps_required` is set in `[elec_config]`:

```toml
[elec_config]
input_path = 'input/electricity/parameters'
rps_required = true
```

The requirements table lives in a `policy` folder beside the `input_path` (`parameters`) folder,
e.g. `input/electricity/policy`, and `ElecConfig` rejects `rps_required` without one.

`capacity_expansion` is not required. With it on, the model can build group capacity to meet a
requirement. With it off, the fleet is fixed and the model meets a requirement by shifting each
hour's generation toward the group and away from other generators. A fixed fleet that cannot
generate enough makes the solve infeasible; the [feasibility report](#the-feasibility-report)
flags that case before any solve.

With `rps_required` off, the RPS tables are not read and the model is unchanged.
`../../run_configs/rps_run_config.toml` solves the shipped demonstration, and
`../../run_configs/rps_check_config.toml` reports on it (see
[The Feasibility Report](#the-feasibility-report)).

## How a Requirement Is Measured

For a group, region key and year, the requirement is

> group generation ≥ minimum share × load

with both sides annual energy (MWh): the representative hours summed with their day weights, as
in the objective function.

- **Generation** is the `generation_total` of the group's members, over every step, in the region
  key's regions. Only generation inside the region key counts. Imports earn no credit, and there is
  no trading of renewable energy credits (RECs) between regions.
- **Load** is the region key's full `elec_load`. Unmet load does not reduce it.
- **Curtailment earns nothing.** The demand balance is an equality, so group output above the load
  has to be curtailed (generated below its capacity factor), exported or stored. It cannot be
  generated, thrown away, and still counted. Exported group generation does count for the
  exporting region, since it is generated there.
- **Year weights cancel.** Both sides are for one year, so year aggregation leaves the share
  unchanged.

### Requirement types

The `requirement_type` column is kept for future types, but only one is implemented:

| Type | Name            | Effect |
|:-----|:----------------|:-------|
| BTM  | Build To Minima | The group's annual generation must be at least the minimum share of the annual load. The model builds group capacity, or shifts generation to the group, as needed. |

Any other type is
rejected. An Alternative Compliance Payment (a priced shortfall instead of a hard limit) is also
stubbed out in the model for possible future implementation, but not yet implemented,
so a requirement that cannot be met makes the solve infeasible.

### Overlapping groups

Groups may share technologies, and a technology's generation counts toward every group it is in
(in the sample groups table below, `14` is in both `clean` and `onshore_wind`). Shares on groups
that share members do not add: BTMs of 0.6 on {Solar} and on {Solar, Gas} are both met by Solar
generating 60% of the load. Shares on groups with no member in common do add. If such BTMs, in
force in one region key and year, have shares summing past 1, the region key must generate more
than its load from those groups and export or store the rest. That can solve, but it is almost
surely an input error, so validation warns and the feasibility report lists it. The check covers
only groups with no member in common, in one region key.

A requirement with a minimum of 0 writes no constraint, as it can never bind. It still counts
when deciding which requirement is in force (see below), so a zero BTM switches RPS off for its
group and region from its first year.

## Input Tables

Both tables are optional files, read only when `rps_required` is on. In practice both are
needed: with no requirements, the run stops with an error. Each may end with a free-text `notes`
column, which is ignored, as in the other input tables. The shipped data under
`input/electricity/` includes the demonstration tables shown in [Sample](#sample).

### Groups: `properties/rps_group.csv`

One row per group member, in the `properties` folder (`common_data_path`):

| Column     | Contents |
|:-----------|:---------|
| `group_id` | The group's name, any string |
| `tech`     | A technology in `tech.csv`; it must be a generator |
| `notes`    | Optional free text, ignored |

A group is the union of its rows. Every member is listed explicitly, so a group changes only when
this table does: adding a technology to `tech.csv`, or changing its `is_*` flags, never changes a
group. Listing a technology twice in one group is an error.

```text
group_id,tech,notes
clean,10_regular,Regular Hydro
clean,10_seasonal,Seasonal Hydro
clean,13,Offshore Wind
clean,14,Onshore Wind
clean,15_end_use,End-Use Solar
clean,15_utility,Utility Solar
onshore_wind,14,
```

### Requirements: `policy/rps_requirement.csv`

One row per requirement, in the `policy` folder:

| Column             | Contents |
|:-------------------|:---------|
| `group_id`         | A group from `rps_group.csv` |
| `region`           | A domestic region in `region.csv`, or `all` |
| `requirement_type` | `BTM` (case-insensitive); the only type supported |
| `first_year`       | The first year the requirement applies (inclusive); it persists after that |
| `min_share_frac`   | The minimum share of the annual load, a fraction in [0, 1] |
| `notes`            | Optional free text, ignored |

```text
group_id,region,requirement_type,first_year,min_share_frac,notes
clean,8,BTM,2030,0.3,region 8 generates 30% of its load from clean sources
clean,all,BTM,2030,0.2,
```

### Region keys and `all`

A requirement's `region` is its *region key*. A region name applies the requirement to that
region alone. `all` applies it to the sum over every analyzed region (all domestic regions, or
those left by `region_filter`), written as **one** constraint, not one per region. The total can
then be met with more group generation in one region and less in another. For the same minimum
in every region separately, list each region in its own row.

A region filtered out of the run is skipped, with an INFO log. A region that is not in
`region.csv`, or is not domestic, is an error.

## Which Requirement Is In Force

A group can have several rows for the same region key, with different first years. Each (group,
region key, model year) has at most one requirement in force: of the rows whose `first_year` is at
or before that year, the one with the latest `first_year`. A later row replaces an earlier one,
whether its minimum is higher or lower. Each superseded row is logged at INFO, once, with the year
it was superseded.

Region keys never interact. A BTM on `all` does not supersede a requirement on region `8`, nor
the reverse: both are in force, and region 8's generation and load count toward both.

For example, with the model years 2025 to 2050 every 5 years, and these rows for group `clean`:

```text
group_id,region,requirement_type,first_year,min_share_frac
clean,8,BTM,2025,0.10
clean,8,BTM,2035,0.30
clean,8,BTM,2045,0.40
clean,all,BTM,2030,0.20
```

the requirements in force are:

```text
                     2025    2030    2035    2040    2045    2050
region 8
  BTM 0.10 from 2025  [=============]                               superseded by the later BTM
  BTM 0.30 from 2035                  [=============]               superseded by the later BTM
  BTM 0.40 from 2045                                  [=============]
  in force            .10     .10     .30     .30     .40     .40

region key all (independent of region 8)
  BTM 0.20 from 2030          [=============================================]
  in force               -    .20     .20     .20     .20     .20
```

So from 2035 on, region 8 must generate at least 30% (then 40%) of its own load from `clean`,
while regions 7, 8 and 9 together generate at least 20% of their combined load from it.

!!! note "Year aggregation"

    Requirements are resolved for the solved (representative) years. With `aggregate_years` on,
    a requirement applies to a representative year when that year is at or after its
    `first_year`, even if some of the calendar years the representative year stands for are
    earlier. See [Year Aggregation](electricity.md#year-aggregation).

## Checks

Problems in the RPS tables stop the run before the model is built, all reported in one error per
table:

- `rps_group.csv`: a row with no `group_id` or `tech`; an unknown `tech`; a member that is not a
  generator; a technology listed twice in one group.
- `rps_requirement.csv`: no rows at all; an unknown `group_id` or `region`; a `requirement_type`
  other than `BTM`; a region that is not domestic; a `min_share_frac` outside [0, 1]; two rows
  with the same group, region, type and `first_year`.

Then `validate_all` checks each BTM in force with a minimum above 0: some group member must have
existing or buildable capacity in its regions and year. Without one the group cannot generate, and
(35) is infeasible wherever the region key has load (see
[Data Validation](electricity.md#data-validation)). It also warns where BTMs on groups with no
member in common, in one region key and year, have shares summing past 1 (see
[Overlapping groups](#overlapping-groups)). The feasibility report goes further.

## The Feasibility Report

A BTM can ask for more generation than the inputs let the group produce. Whether a group member
is buildable in a region and year depends on several inputs (see
[Making a Technology Buildable](electricity.md#making-a-technology-buildable)), and a requirement
that cannot be met shows up only as an infeasible solve. The `rps_feasibility` run mode checks the
requirements before any solve: it loads and validates the electricity data, writes a report, and
stops. No model is built or solved.

### Running it

Set `mode = "rps_feasibility"` in `[common]`, with `electricity` in `models_to_run` and
`rps_required = true`:

```toml
[common]
mode = "rps_feasibility"
models_to_run = ['electricity']
```

then run `python main.py run_configs/rps_check_config.toml`. That sample config runs on the shipped
data and its demonstration RPS tables, and produces the report in [Sample](#sample).

The report goes to `rps_feasibility.txt` in the scenario output folder, beside `run.log`, and the
log gets a one-line summary. Validation runs non-strict in this mode, whatever
`strict_validation` says, so a failed check is logged and the report is still written. Other
models in `models_to_run` have no RPS check and are skipped. The run stops with an error if
electricity is not selected, or `rps_required` is off.

### What it reports

The report shows the problems and stays quiet about what is expected: a requirement the existing
fleet can meet has no row, and neither does a member buildable in every year its BTM requires.
Nothing is reported per hour. After a header with the analyzed regions and model years, it has
five parts.

**Groups** lists each group's member techs.

**Requirements** lists every row of `rps_requirement.csv`, with the model years it is in force
(see [Which Requirement Is In Force](#which-requirement-is-in-force)). A row that is never in
force shows `-`, and its note says why:

| Note | Meaning |
|:-----|:--------|
| `superseded from Y by T from F` | In force until model year Y, when the type T requirement from F replaced it. |
| `starts after the last model year` | The run ends before the requirement starts. |
| `region not in this run` | `region_filter` leaves the region out. |
| `minimum 0:  no constraint` | In force, but a zero minimum writes no constraint. |

**Requirement issues** lists structural problems by group, region key and years:

| Status | Meaning |
|:-------|:--------|
| `no group capacity` | No member has existing, buildable or earlier-built capacity in the region key and year, so the group cannot generate. Infeasible wherever the region key has load. Also a validation error. |
| `disjoint shares over 1` | BTMs on groups with no member in common, in one region key and year, have shares summing past 1. Each group in the set gets a row. Also a validation warning. |

**Energy gaps** compares, for each BTM in force, the requirement (minimum share × the region key's
annual load) with the most the *existing* group fleet could generate: each member's existing MW
times its annual yield at full output. The yield is the day- and hour-weighted sum of its
capacity factor over the representative hours: the hourly capacity factor for VRE, the seasonal
capacity factor for hydro, and 1 for other generators. For seasonal hydro this ignores the
seasonal discharge budget, so it is an upper bound. One row per (group, region key, year) where
the requirement exceeds that maximum, with the gap in MWh:

| Status | Meaning |
|:-------|:--------|
| `needs builds` | A member is buildable in the year. The *closing build* is the MW of the highest-yield buildable (region, tech) whose annual potential would cover the gap on its own: an annual-energy estimate, not a guarantee of compliance. |
| `needs early builds` | No member is buildable in the year itself, but one is earlier. Capacity built earlier stays in service, so the closing build must be made in an earlier year. |
| `unmeetable` | No member with any yield is buildable in the year or earlier, so the solve will be infeasible. Every shortfall is `unmeetable` with `capacity_expansion` off. |

A requirement that needs builds, with a member to build, is not a fault: forcing builds is what a
BTM is for. The row says how much. The comparison is a screen, not a solve. The existing fleet's
maximum assumes every member runs flat out in every hour, and surplus above the load in an hour
can only count if it is exported or stored. A requirement with no gap can still bind, and one that
needs builds can need more than the closing build. When no requirement has a gap, the report says
there is no annual generation-potential deficit, not that the requirements will be met: hourly
balance and other constraints may still prevent compliance.

**Build To Minima member coverage** lists each group member, by (region, tech), in the required
years it is not buildable in:

| Status | Meaning |
|:-------|:--------|
| `prior builds only` | Not buildable in the year, but buildable earlier. Anything it adds must be built in an earlier year, so the BTM may force early builds. |
| `existing only` | Not buildable in the year or earlier. It can generate from its existing capacity, but not add to it. |
| `unavailable` | No existing capacity, and not buildable in the year or earlier. It cannot generate. |

`required by` names the BTMs that need the member in those years, as `group@region key`. A broad
group lists every member that is never buildable. That is deliberate: a member is expected to be
buildable wherever a BTM needs it.

Each problem table is followed by a legend of its statuses. When there is nothing to list, a single
line replaces the table and its legend.

### Sample

The report from `../../run_configs/rps_check_config.toml`: the shipped data with
`region_filter = ['7', '8', '9']` and model years 2025, 2030 and 2035. Its group is the `clean`
group of the [Input Tables](#input-tables) example, and its requirements are the shipped
`policy/rps_requirement.csv` (notes left out here). For variety, the shipped build data makes
`10_regular` (regular hydro) buildable from 2035 on in these regions, and stops `15_utility`
(utility solar) in region 8 being buildable from 2035 on. Those rows are marked as placeholders
in their `notes`.

```text
group_id,region,requirement_type,first_year,min_share_frac
clean,7,BTM,2025,0.1
clean,8,BTM,2025,0.2
clean,8,BTM,2030,0.3
clean,9,BTM,2030,0.6
clean,all,BTM,2030,0.2
```

Techs `10_*` are hydro, `13` and `14` offshore and onshore wind, and `15_*` solar.

```text
RPS feasibility report

Regions analyzed:  7, 8, 9
Model years:  2025, 2030, 2035

Groups

| group   | member techs                                            |
|---------|---------------------------------------------------------|
| clean   | 10_regular, 10_seasonal, 13, 14, 15_end_use, 15_utility |

Requirements

One row per rps_requirement.csv row.  In force:  the model years it is the requirement in force for
its group and region key:  of the rows started by a year, the one with the latest first year.

| group   | region key   | type   |   from |   min share | in force   | note                                  |
|---------|--------------|--------|--------|-------------|------------|---------------------------------------|
| clean   | 7            | BTM    |   2025 |       0.100 | 2025–2035  |                                       |
| clean   | 8            | BTM    |   2025 |       0.200 | 2025       | superseded from 2030 by BTM from 2030 |
| clean   | 8            | BTM    |   2030 |       0.300 | 2030–2035  |                                       |
| clean   | 9            | BTM    |   2030 |       0.600 | 2030–2035  |                                       |
| clean   | all          | BTM    |   2030 |       0.200 | 2030–2035  |                                       |

Requirement issues

Structural problems, by group, region key and model years.

No requirement issues found.

Energy gaps

Each BTM in force whose requirement (min share x the region key's annual load) is more than the
existing group fleet could generate (existing MW x its annual yield at full capacity factor).
Requirements with no annual deficit are not listed; that does not confirm compliance, since hourly
balance and other constraints may still bind.

| group   | region key   |   year |   required MWh |   existing max MWh |    gap MWh | status       | closing build       |
|---------|--------------|--------|----------------|--------------------|------------|--------------|---------------------|
| clean   | 8            |   2025 |     14,398,090 |          2,834,186 | 11,563,904 | needs builds | 2,791 MW at (8, 14) |
| clean   | 8            |   2030 |     22,589,307 |          5,018,852 | 17,570,455 | needs builds | 4,240 MW at (8, 14) |
| clean   | 8            |   2035 |     23,581,480 |          6,011,882 | 17,569,598 | needs builds | 4,240 MW at (8, 14) |

| status             | meaning                                                                 |
|--------------------|-------------------------------------------------------------------------|
| needs builds       | The existing group fleet cannot generate the requirement, but a member  |
|                    | is buildable in the year.  Closing build:  the MW of the highest-yield  |
|                    | buildable (region, tech) whose annual potential would cover the gap     |
|                    | alone.  An annual-energy estimate, not a guarantee of compliance:       |
|                    | hourly balance and other constraints may need more.                     |
| needs early builds | As above, but no member is buildable in the year itself:  the closing   |
|                    | build must be made in an earlier year.                                  |
| unmeetable         | The existing group fleet cannot generate the requirement, and no member |
|                    | with any yield is buildable in the year or earlier.  The solve will be  |
|                    | infeasible.                                                             |

Build To Minima member coverage

Each member (region, tech) of a group with a BTM in force, in the required years it is not buildable
in.  Members buildable in every required year are not listed.  Required by:  the group@region key
BTMs that need the member in those years.

| region   | tech        | not buildable in   | status            | required by        |
|----------|-------------|--------------------|-------------------|--------------------|
| 7        | 10_regular  | 2025–2030          | existing only     | clean@7, clean@all |
| 7        | 10_seasonal | 2025–2035          | existing only     | clean@7, clean@all |
| 7        | 13          | 2025–2035          | existing only     | clean@7, clean@all |
| 7        | 15_end_use  | 2025–2035          | existing only     | clean@7, clean@all |
| 8        | 10_regular  | 2025–2030          | unavailable       | clean@8, clean@all |
| 8        | 10_seasonal | 2025–2035          | unavailable       | clean@8, clean@all |
| 8        | 13          | 2025–2035          | existing only     | clean@8, clean@all |
| 8        | 15_end_use  | 2025–2035          | existing only     | clean@8, clean@all |
| 8        | 15_utility  | 2035               | prior builds only | clean@8, clean@all |
| 9        | 10_regular  | 2030               | existing only     | clean@9, clean@all |
| 9        | 10_seasonal | 2030–2035          | existing only     | clean@9, clean@all |
| 9        | 13          | 2030–2035          | unavailable       | clean@9, clean@all |
| 9        | 15_end_use  | 2030–2035          | existing only     | clean@9, clean@all |

| status            | meaning                                                                  |
|-------------------|--------------------------------------------------------------------------|
| prior builds only | Not buildable in the year, but buildable earlier:  anything it adds must |
|                   | be built in an earlier year, so the BTM may force early builds.          |
| existing only     | Not buildable in the year or earlier:  it can generate from its existing |
|                   | capacity, but not add to it.                                             |
| unavailable       | No existing capacity, and not buildable in the year or earlier:  it      |
|                   | cannot generate.                                                         |
```

Reading it:

- **Requirements:** region 8's 20% from 2025 gives way to 30% in 2030.
- **Energy gaps:** only region 8 is short. Its existing clean fleet could generate at most about
  2.8 TWh in 2025 against a 14.4 TWh requirement, and onshore wind (`14`), its highest-yield
  buildable member, would need about 2.8 GW to close the gap alone (4.2 GW from 2030). Regions 7
  and 9, and the three regions together, can meet their requirements from the existing fleet, so
  they are not listed. Region 9's 60% is within its existing clean fleet's maximum.
- **Coverage:** regular hydro can be built only from 2035, so before then it holds only its
  existing capacity in regions 7 and 9, and is unavailable in region 8, which has none. Utility
  solar can't be built in region 8 in 2035, so any addition for that year must be built by 2030.
  Seasonal hydro, offshore wind (`13`) and end-use solar (`15_end_use`) can never be built in these
  regions. Onshore wind (`14`) is buildable throughout, so it is not listed.

Solving the same data (`../../run_configs/rps_run_config.toml`) bears this out: region 8's
requirement binds at exactly 20% in 2025 and 30% from 2030, and the others are met with room to
spare.
