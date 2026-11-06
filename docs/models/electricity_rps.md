# Regional Portfolio Standards

A Regional Portfolio Standard (RPS) requires a group of technologies, typically renewables or
VRE, to make up a minimum share of the generator capacity in a region. The electricity model
represents RPS as capacity-based requirements on technology groups, each set for a region (or all
the analyzed regions together) from a first year onward. The constraints are equations (35) and
(36) in [the model formulation](electricity.md#regional-portfolio-standards).

## Turning RPS On

RPS is off unless `rps_required` is set in `[elec_config]`:

```toml
[elec_config]
input_path = 'input/electricity/parameters'
capacity_expansion = true   # required by rps_required
rps_required = true
```

`ElecConfig` rejects `rps_required` in two cases:

- **`capacity_expansion` is off.** Without expansion the capacity is fixed by
  `available_capacity`, so there is nothing for a requirement to act on.
- **There is no `policy` folder.** The requirements table lives in a `policy` folder beside the
  `input_path` (`parameters`) folder, e.g. `input/electricity/policy`.

With `rps_required` off, the RPS tables are not read and the model is unchanged.
`run_configs/rps_elec_config.toml` is a worked example (see
[The Feasibility Report](#the-feasibility-report)).

## Requirement Types

The **share** is the group's capacity divided by the capacity of every generator
(`is_generator` techs), summed over the requirement's regions and over every supply step. It is
a share of capacity, not of generation. There are two requirement types:

| Type | Name                    | Effect |
|:-----|:------------------------|:-------|
| BTM  | Build To Minima         | The group's share must be at least the minimum, counting existing capacity, retirements and new builds in that year. The model builds group capacity (or retires other generators) as needed. |
| RLL  | Retirement Lower Limit  | Limits the group's retirements, and never forces builds. Where the group's existing share meets the minimum, the group may not retire below the minimum share of the *existing* generator capacity. Where it does not, no group member may retire at all. |

An RLL is decided year by year from the existing capacity (`available_capacity`) of the
requirement's regions:

- **Existing share at or above the minimum: a floor.** The group's capacity must stay at or above
  the minimum share times that year's existing generator capacity. The floor is a fixed number of
  MW, so retiring nothing always satisfies it.
- **Existing share below the minimum, or no existing generators: a retirement hold.** No group
  member in the region key's regions may retire that year, in any step. The hold writes no
  constraint: those members simply have no retirement variable that year (logged at INFO, with
  the count removed).

The two meet at the threshold, where the floor equals the group's existing capacity, so the RLL
moves smoothly between them as the existing fleet changes. A hold applies to each member, not to
the group's net total: retiring one member and building another is not allowed. Builds are never
restricted, and a hold does not undo retirements made in earlier years. The test sees only the
existing capacity, so the model's own builds never turn a hold into a floor.

A BTM compares two decision quantities, so building other generators raises what the group must
hold.

A requirement with a minimum of 0 writes no constraint (and an RLL no hold), as it can never
bind. It still counts
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
| `requirement_type` | `BTM` or `RLL` (case-insensitive) |
| `first_year`       | The first year the requirement applies (inclusive); it persists after that |
| `min_share_frac`   | The minimum share, a fraction in [0, 1] |
| `notes`            | Optional free text, ignored |

```text
group_id,region,requirement_type,first_year,min_share_frac,notes
clean,7,RLL,2025,0.1,
clean,8,BTM,2030,0.3,region 8 builds toward 30%
clean,all,BTM,2030,0.2,
```

### Region keys and `all`

A requirement's `region` is its *region key*. A region name applies the requirement to that
region alone. `all` applies it to the sum over every analyzed region (all domestic regions, or
those left by `region_filter`), written as **one** constraint, not one per region. The region
total can then be met with more of the group in one region and less in another. For the same
minimum in every region separately, list each region in its own row.

A region filtered out of the run is skipped, with an INFO log. A region that is not in
`region.csv`, or is not domestic, is an error.

## Which Requirement Is In Force

Requirements can overlap: a group can have several rows for the same region key, of either type
and with different first years. Each (group, region key, model year) has at most one requirement
in force, chosen from the rows whose `first_year` is at or before that year:

1. If any of them is a BTM, the BTM with the latest `first_year` applies.
2. Otherwise, the RLL with the latest `first_year` applies.

A BTM always supersedes an RLL, even an RLL that starts later. A later BTM replaces an earlier
one, whether its minimum is higher or lower. Each superseded row is logged at INFO, once, with
the year it was superseded.

Region keys never interact. A BTM on `all` does not supersede a requirement on region `8`, nor
the reverse: both are in force, and region 8's capacity counts toward both.

For example, with the model years 2025 to 2050 every 5 years, and these rows for group `clean`:

```text
group_id,region,requirement_type,first_year,min_share_frac
clean,8,RLL,2025,0.10
clean,8,RLL,2030,0.15
clean,8,BTM,2035,0.30
clean,8,BTM,2045,0.40
clean,8,RLL,2050,0.50
clean,all,BTM,2030,0.20
```

the requirements in force are:

```text
                     2025    2030    2035    2040    2045    2050
region 8
  RLL 0.10 from 2025  [=====]                                       superseded by the later RLL
  RLL 0.15 from 2030          [=====]                               superseded by a BTM
  BTM 0.30 from 2035                  [=============]               superseded by the later BTM
  BTM 0.40 from 2045                                  [=============]
  RLL 0.50 from 2050                                          x     never applies: a BTM is in force
  in force            RLL .10 RLL .15 BTM .30 BTM .30 BTM .40 BTM .40

region key all (independent of region 8)
  BTM 0.20 from 2030          [=============================================]
  in force               -    BTM .20 BTM .20 BTM .20 BTM .20 BTM .20
```

So from 2035 on, region 8 must hold at least 30% (then 40%) of its own generator capacity in
`clean`, while regions 7, 8 and 9 together hold at least 20%.

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
- `rps_requirement.csv`: no rows at all; an unknown `group_id`, `region` or
  `requirement_type`; a region that is not domestic; a `min_share_frac` outside [0, 1]; two rows
  with the same group, region, type and `first_year`.

Then `validate_all` checks each BTM in force with a minimum above 0: some group member must have
existing or buildable capacity in its regions and year, or the BTM can never be met (see
[Data Validation](electricity.md#data-validation)). That check is all that validation can say
without solving. The feasibility report goes further.

## The Feasibility Report

A BTM can ask for more than the inputs let the model build. Whether a group member is buildable
in a region and year depends on several inputs (see
[Making a Technology Buildable](electricity.md#making-a-technology-buildable)), and an infeasible
requirement shows up only as an infeasible solve. The `rps_feasibility` run mode checks the
requirements before any solve: it loads and validates the electricity data, writes a report,
and stops. No model is built or solved.

### Running it

Set `mode = "rps_feasibility"` in `[common]`, with `electricity` in `models_to_run` and
`rps_required = true`:

```toml
[common]
mode = "rps_feasibility"
models_to_run = ['electricity']
```

then run `python main.py run_configs/rps_elec_config.toml`. That sample config runs on the shipped
data and its demonstration RPS tables, and produces the report in [Sample](#sample).

The report goes to `rps_feasibility.txt` in the scenario output folder, beside `run.log`, and the
log gets a one-line summary. Validation runs non-strict in this mode, whatever
`strict_validation` says, so a failed check is logged and the report is still written. That
includes the BTM capacity check above, which is the case the report is most useful for. Other
models in `models_to_run` have no RPS check and are skipped. The run stops with an error if
electricity is not selected, or `rps_required` is off.

### What it reports

The report shows the problems and stays quiet about what is expected. A member buildable in every
year its BTM requires has no row, and a requirement that can be met has none either. Years are
written as runs of consecutive model years, such as `2030–2045`. After a header with the analyzed
regions and model years, it has four parts.

**Groups** lists each group's member techs.

**Requirements** lists every row of `rps_requirement.csv`, with the model years it is in force
(see [Which Requirement Is In Force](#which-requirement-is-in-force)). A row that is never in
force shows `-`, and its note says why:

| Note | Meaning |
|:-----|:--------|
| `superseded from Y by T from F` | In force until model year Y, when the type T requirement from F replaced it. |
| `never in force:  T from F in force` | Another requirement was already in force from the row's first year on. |
| `starts after the last model year` | The run ends before the requirement starts. |
| `region not in this run` | `region_filter` leaves the region out. |
| `minimum 0:  no constraint` | In force, but a zero minimum writes no constraint. |

**Requirement issues** lists the problems, and the RLL retirement holds, by group, region key and
years:

| Status | Meaning |
|:-------|:--------|
| `no group capacity` | A BTM no member can hold capacity for (no existing, buildable or earlier-built capacity), so it cannot be met. This also fails validation. |
| `no member buildable` | The existing capacity falls short of a BTM, and no member is buildable in the year or earlier. Only retiring other generators can meet it. |
| `RLL holds retirements` | The existing share is below an RLL's minimum (or there are no existing generators), so no group member in the region key may retire that year. Not a fault: it is listed so the hold is visible. |

A BTM that needs builds, and has a member to build, is not an issue: forcing builds is what a BTM
is for.

**Build To Minima member coverage** lists each group member, by (region, tech), in the required
years it is not buildable in:

| Status | Meaning |
|:-------|:--------|
| `prior builds only` | Not buildable in the year, but buildable earlier. Anything it adds must be built in an earlier year, so the BTM may force early builds. |
| `existing only` | Not buildable in the year or earlier. It can keep or retire its existing capacity, but not add to it. |
| `unavailable` | No existing capacity, and not buildable in the year or earlier. It can hold no capacity. |

A member is buildable "in the year or earlier" because capacity built in one year stays in service
in later years. Buildability depends only on the region, tech and year, so each member has one row
per status. `required by` names the BTMs that need it in those years, as `group@region key`. A
broad group lists every member that is never buildable. That is deliberate: a member is expected
to be buildable wherever a BTM needs it.

Each problem table is followed by a legend of its statuses. When there is nothing to list, a single
line replaces the table and its legend.

The checks are made against the existing fleet and the build data, not a solve. In particular, a
BTM that can only be met by retiring other generators is not checked against the RLL floors and
holds that may block them.

### Sample

The report from `run_configs/rps_elec_config.toml`: the shipped data with
`region_filter = ['7', '8', '9']` and model years 2025, 2030 and 2035. Its group is the `clean`
group of the [Input Tables](#input-tables) example, and its requirements are the shipped
`policy/rps_requirement.csv` (notes left out here). For variety, the shipped build data makes
`10_regular` (regular hydro) buildable from 2035 on in these regions, and stops `15_utility`
(utility solar) in region 8 being buildable from 2035 on. Those rows are marked as placeholders
in their `notes`.

```text
group_id,region,requirement_type,first_year,min_share_frac
clean,7,RLL,2025,0.1
clean,8,RLL,2025,0.1
clean,8,BTM,2030,0.3
clean,8,RLL,2035,0.2
clean,9,RLL,2025,0.6
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
its group and region key.  Of the rows started by a year, the latest BTM is in force, or else the
latest RLL.

| group   | region key   | type   |   from |   min share | in force   | note                                    |
|---------|--------------|--------|--------|-------------|------------|-----------------------------------------|
| clean   | 7            | RLL    |   2025 |       0.100 | 2025–2035  |                                         |
| clean   | 8            | RLL    |   2025 |       0.100 | 2025       | superseded from 2030 by BTM from 2030   |
| clean   | 8            | BTM    |   2030 |       0.300 | 2030–2035  |                                         |
| clean   | 8            | RLL    |   2035 |       0.200 | -          | never in force:  BTM from 2030 in force |
| clean   | 9            | RLL    |   2025 |       0.600 | 2025–2035  |                                         |
| clean   | all          | BTM    |   2030 |       0.200 | 2030–2035  |                                         |

Requirement issues

Problems, and RLL retirement holds, by group, region key and model years.

| group   | region key   | type   | years     | status                |
|---------|--------------|--------|-----------|-----------------------|
| clean   | 8            | RLL    | 2025      | RLL holds retirements |
| clean   | 9            | RLL    | 2025–2035 | RLL holds retirements |

| status                | meaning                                                                  |
|-----------------------|--------------------------------------------------------------------------|
| no group capacity     | BTM:  no member has existing, buildable or earlier-built capacity in the |
|                       | region key and year, so the requirement cannot be met.  Also a           |
|                       | validation error.                                                        |
| no member buildable   | BTM:  the existing capacity falls short of the minimum, and no member is |
|                       | buildable in the year or earlier.  Only retiring other generators can    |
|                       | meet it.                                                                 |
| RLL holds retirements | RLL:  the existing share is below the minimum (or there are no existing  |
|                       | generators), so no group member in the region key may retire that year.  |
|                       | Not a fault:  an RLL never forces builds.                                |

Build To Minima member coverage

Each member (region, tech) of a group with a BTM in force, in the required years it is not buildable
in.  Members buildable in every required year are not listed.  Required by:  the group@region key
BTMs that need the member in those years.

| region   | tech        | not buildable in   | status            | required by        |
|----------|-------------|--------------------|-------------------|--------------------|
| 7        | 10_regular  | 2030               | existing only     | clean@all          |
| 7        | 10_seasonal | 2030–2035          | existing only     | clean@all          |
| 7        | 13          | 2030–2035          | existing only     | clean@all          |
| 7        | 15_end_use  | 2030–2035          | existing only     | clean@all          |
| 8        | 10_regular  | 2030               | unavailable       | clean@8, clean@all |
| 8        | 10_seasonal | 2030–2035          | unavailable       | clean@8, clean@all |
| 8        | 13          | 2030–2035          | existing only     | clean@8, clean@all |
| 8        | 15_end_use  | 2030–2035          | existing only     | clean@8, clean@all |
| 8        | 15_utility  | 2035               | prior builds only | clean@8, clean@all |
| 9        | 10_regular  | 2030               | existing only     | clean@all          |
| 9        | 10_seasonal | 2030–2035          | existing only     | clean@all          |
| 9        | 13          | 2030–2035          | unavailable       | clean@all          |
| 9        | 15_end_use  | 2030–2035          | existing only     | clean@all          |

| status            | meaning                                                                  |
|-------------------|--------------------------------------------------------------------------|
| prior builds only | Not buildable in the year, but buildable earlier:  anything it adds must |
|                   | be built in an earlier year, so the BTM may force early builds.          |
| existing only     | Not buildable in the year or earlier:  it can keep or retire its         |
|                   | existing capacity, but not add to it.                                    |
| unavailable       | No existing capacity, and not buildable in the year or earlier:  it can  |
|                   | hold no capacity.                                                        |
```

Reading it:

- **Requirements:** region 8's RLL gives way to its BTM in 2030. Its RLL from 2035 never takes
  effect, because the BTM is still in force.
- **Requirement issues:** region 9's RLL asks for a 60% share it doesn't have, so it holds
  retirements every year: no clean member in region 9 may retire. Region 8's RLL holds in 2025,
  its only year in force. Region 7's RLL, met by its existing share, is a floor and not listed.
- **Coverage:** regular hydro can be built only from 2035, so for 2030 it holds only its existing
  capacity in regions 7 and 9, and is unavailable in region 8, which has none. Utility solar can't
  be built in region 8 in 2035, so any addition for that year must be built by 2030. Seasonal
  hydro, offshore wind (`13`) and end-use solar (`15_end_use`) can never be built in these regions.
  Onshore wind (`14`) is buildable throughout, so it is not listed.
