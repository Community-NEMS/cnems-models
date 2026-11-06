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
| RLL  | Retirement Lower Limit  | The group may not retire below the minimum share of the *existing* generator capacity. It never forces builds, and it applies only where the existing share already meets the minimum; otherwise it is skipped (logged at INFO). |

An RLL floor is a fixed number of MW, the minimum share times the existing generator capacity of
that year, so retiring nothing always satisfies it. A BTM compares two decision quantities, so
building other generators raises what the group must hold.

A requirement with a minimum of 0 writes no constraint, as it can never bind. It still counts
when deciding which requirement is in force (see below), so a zero BTM switches RPS off for its
group and region from its first year.

## Input Tables

Both tables are optional files, read only when `rps_required` is on. In practice both are
needed: with no requirements, the run stops with an error.

### Groups: `properties/rps_group.csv`

One row per group member, in the `properties` folder (`common_data_path`). A row names a member
by `tech` or by `tech_group`, never both:

| Column       | Contents |
|:-------------|:---------|
| `group_id`   | The group's name, any string |
| `tech`       | A technology in `tech.csv` |
| `tech_group` | A membership column of `tech.csv` (`is_vre`, `is_hydro`, `is_wind`, ...): every tech flagged `True` in it |

Membership is additive: a group is the union of its rows, and a tech named both directly and
through a `tech_group` is simply a member. Every member must be a generator.

```text
group_id,tech,tech_group
clean,,is_vre
clean,,is_hydro
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

```text
group_id,region,requirement_type,first_year,min_share_frac
clean,7,RLL,2025,0.1
clean,8,BTM,2030,0.3
clean,all,BTM,2030,0.2
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

- `rps_group.csv`: a row with no `group_id`, or with both or neither of `tech` / `tech_group`;
  an unknown `tech` or `tech_group`; a member that is not a generator.
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

then run `python main.py run_configs/rps_elec_config.toml`. The sample config runs on the shipped
data, which has no RPS tables: add `properties/rps_group.csv` and `policy/rps_requirement.csv`
under `input/electricity/` first, for example the tables above.

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

**Requirement issues** lists the problems by group, region key and years:

| Status | Meaning |
|:-------|:--------|
| `no group capacity` | A BTM no member can hold capacity for (no existing, buildable or earlier-built capacity), so it cannot be met. This also fails validation. |
| `no member buildable` | The existing capacity falls short of a BTM, and no member is buildable in the year or earlier. Only retiring other generators can meet it. |
| `RLL not applied` | The existing share is below an RLL's minimum (or there are no existing generators), so no floor is written. |

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
group defined by a broad `tech_group`, such as `is_vre`, will list every member that is never
buildable. That is deliberate: a member is expected to be buildable wherever a BTM needs it.

Each problem table is followed by a legend of its statuses. When there is nothing to list, a single
line replaces the table and its legend.

The checks are made against the existing fleet and the build data, not a solve. In particular, a
BTM that can only be met by retiring other generators is not checked against the RLL floors that
may block them.

### Sample

The report from the shipped data with `region_filter = ['7', '8', '9']`, model years 2025, 2030
and 2035, and the `clean` group of the [Input Tables](#input-tables) example. Two changes to the
build data give it some variety: `10_regular` (regular hydro) is made buildable from 2035 on, and
`15_utility` (utility solar) in region 8 is not buildable from 2035 on. The requirements are:

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

Problems only, by group, region key and model years.

| group   | region key   | type   | years     | status          |
|---------|--------------|--------|-----------|-----------------|
| clean   | 8            | RLL    | 2025      | RLL not applied |
| clean   | 9            | RLL    | 2025–2035 | RLL not applied |

| status              | meaning                                                                  |
|---------------------|--------------------------------------------------------------------------|
| no group capacity   | BTM:  no member has existing, buildable or earlier-built capacity in the |
|                     | region key and year, so the requirement cannot be met.  Also a           |
|                     | validation error.                                                        |
| no member buildable | BTM:  the existing capacity falls short of the minimum, and no member is |
|                     | buildable in the year or earlier.  Only retiring other generators can    |
|                     | meet it.                                                                 |
| RLL not applied     | RLL:  the existing share is below the minimum (or there are no existing  |
|                     | generators), so no floor is written.  An RLL never forces builds.        |

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
- **Requirement issues:** region 9's RLL asks for a 60% share it doesn't have, so it never applies.
  Region 8's RLL isn't applied in 2025, its only year in force.
- **Coverage:** in region 8, regular hydro can be built only from 2035, so it is unavailable for
  2030. Utility solar can't be built in 2035, so any addition for that year must be built by 2030.
  Offshore wind (`13`) and end-use solar (`15_end_use`) can never be built in these regions, so
  they hold only their existing capacity.
