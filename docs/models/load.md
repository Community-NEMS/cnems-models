# Load

The load model turns **annual electricity load by sector, end use and census division** into **hourly load
for the 25 EMM regions**. It is a Python port of the load-profile step of the Load and Demand-Side Management
module of EIA's National Energy Modeling System (`source/uldsm.f`, AEO2026 release). Nothing is optimized: there
is no solver and no objective. The model is run once per scenario, and other models read the files it writes.

How to run it, the `[load_config]` settings, the scenario map and the way to replace a sector with your own
forecast are in the module README,
[src/models/load/README.md](https://github.com/Community-NEMS/cnems-models/blob/main/src/models/load/README.md).
This page is
the reference: the formulation, the data, the checks, and the differences from NEMS.

## Model Overview

The model builds, for each region and year, an hourly profile in two steps. Annual energy is first spread over
864 representative points (12 months x 3 day types x 24 hours), using load shapes from the NEMS shape tables.
The points are then laid on the 8,760 hours of a 365-day calendar. Two ways of building the profile are
available, the **basic** and the **delta** approach, selected with `method`.

The calculation was taken from the NEMS Fortran file `source/uldsm.f`. The table below lists the calculation steps in the order they are carried out, with the Fortran subroutine that does the
step in NEMS and the Python method that does it here. All methods are in the class `NemsLoad` in
`src/models/load/load_model.py`. The formulas of each step are in the next section.

| Step | NEMS Fortran subroutine (`uldsm.f`) | Python method (`NemsLoad`) |
|---|---|---|
| 1. Read the load-shape tables and lay them out on the 864 representative points | `CALLSYSLOAD`, `CALLSYSLOAD_EU` | `_build` |
| 2. Count how many days of the calendar fall on each month and day type (the day weights) | `LOAD_LDSM_CAL` | `_build` |
| 3. Match each end use to its load shape by name | `DSMRST` | `shape_name` |
| 4. Split the annual load of each census division over the EMM regions | `DSMEMMV`, with the normalization in `DSMRST` | `census_to_region` |
| 5. Delta approach only: subtract the scaled base-year load from the projection-year load | `DSMDLT` | `delta_profile` |
| 6. Turn annual load into the 864-point profile (shape times annual energy, summed over end uses) | `DSMNWS` | `delta_profile` (delta approach), `basic_profile` (basic approach) |
| 7. Add transmission and distribution losses and the hydrogen load | `DSMHLM` | `_finish` |

The expansion from the 864 points to 8,760 hours is an addition of this model, not a step of `uldsm.f`; it is
done by `NemsLoad.crosswalk`.

Not ported is everything that happens after the hourly load exists (load duration curves, dispatch,
capacity planning) and the NEMS demand modules that produce the annual loads. Those annual loads are an input.

## Mathematical Formulation

### Notation

#### Sets

| Set | Symbol | Content |
|---|---|---|
| Regions | $r \in R$ | the 25 EMM regions (Alaska and Hawaii are not modelled) |
| Census divisions | $k \in K$ | 9 divisions |
| Sectors | $s \in S$ | 1 residential, 2 commercial, 3 industrial, 4 transport |
| End uses | $e \in E_s$ | the end uses of sector $s$ (NEMS codes: 11 residential, 116 commercial, 4 industrial, 1 transport). The sums in Eqs. 3 to 6 leave out industrial end use 4, hydrogen, which is added in a separate step, see *Hydrogen* |
| Months, day types, hours of day | $m \in M$, $d \in D$, $j \in J$ | 12, 3 (weekday, weekend or holiday, peak day), 24 |
| Representative points | $q \in Q$ | $Q = M \times D \times J$, 864 points |
| Hours of the year | $t \in T$ | 1 to 8,760 |
| Years | $y \in Y$ | the run's `summary_years` |

The index of a point is $q(m,d,j) = ((m-1)\,|D| + (d-1))\,|J| + j - 1$ (0-based), as in `uldsm.f`.

#### Parameters

| Parameter | Symbol | Description |
|---|---|---|
| Annual load by census division | $A_{k,s,e,y}$ | MWh, from the NEMS outputs or a user file |
| Census-to-region map | $C_{r,s,k}$ | share of census division $k$'s sector-$s$ load that falls in region $r$ (`MappCtoN`, normalized so that the regions plus Alaska and Hawaii add up to 1) |
| Day weight | $w_q$ | number of days of the calendar with the month and day type of point $q$; $\sum_q w_q = 24 \cdot 365 = 8760$ |
| End-use shape | $\phi_{r,s,e,q}$ | share of the annual energy of end use $(s,e)$ in region $r$ that falls in one representative hour of point $q$ |
| System shape | $\phi^{sys}_{r,q}$ | the same for the region's historical system load |
| T&D loss rate | $\tau_r$ | `UQTDLS`, from `V_EMM_LDSMSTR_RGN` |
| Loss adjustment | $a_y$ | `ULOSSADJ`, a multiplier on $\tau_r$ (0.8 from 2020 in NEMS); `ulossadj_file` or `loss_adj`; 1.0 if neither |
| Hydrogen electrolyzer demand | $H_{r,q}$ | MW at point $q$, built from the hydrogen of the annual loads (end use 4) and added to the profile after the decomposition, see *Hydrogen* |
| Base year | $b(y)$ | the reference year of the delta approach, see below |

#### Derived quantities

| Quantity | Symbol | Description |
|---|---|---|
| Regional annual load | $L_{r,s,e,y}$ | MWh by region, sector and end use (the `load1` of the code) |
| Base-year load | $B_{r,s,e,y}$ | $L_{r,s,e,b(y)}$ (`BaseYrLd`) |
| System load | $\Lambda_{r,y}$ | $\sum_{s,e} L_{r,s,e,y}$ over all end uses except hydrogen (`SystemLoad`) |
| System load ratio | $\rho_{r,y}$ | $\Lambda_{r,y} / \sum_{s,e} B_{r,s,e,y}$, also without hydrogen (`slr`) |
| Loss factor | $\theta_{r,y}$ | $1 + \tau_r\, a_y$ (`td`) |
| Sales profile | $S_{r,y,q}$ | MW at point $q$, before T&D losses (`sales_mw`) |
| Load profile | $P_{r,y,q}$ | MW at point $q$, including T&D losses (`load_mw`) |

### Annual load by region

The NEMS demand modules give annual loads by census division. They are mapped to the EMM regions with

$$
L_{r,s,e,y} = \sum_{k \in K} C_{r,s,k}\, A_{k,s,e,y} \tag{1}
$$

The weights $C_{r,s,k}$ are normalized against the national total of the mapping table, so Alaska and Hawaii,
which have no region, take a small share and drop out. The regional totals are therefore a little below the
census totals (about 0.4 to 0.5% of the load in the shipped data, and more for some quantities, for example
about 1.3% of residential own-use generation in 2025); the run reports an input-versus-output table by
sector, and the unexplained difference must be zero. A user file given by region (`emm_region_loads`) skips
this step.

### Day weights and shape normalization

NEMS does not provide a load shape for every year. Its shape tables describe one historical year, a set of 864
values per shape: the share of the annual energy that falls in one representative hour of each month, day type
and hour of day. Because a month has several days of the same type (for example 21 weekdays), the shares only
add up to one when each value is multiplied by the number of days of that month and day type, the *day weight*
$w_q$, and summed over the 864 points. The day weights are those of the year the shapes were built for, which in
the NEMS tables is the NEMS calendar (`V_SYS_LOAD_WEIGHTS`: 240 weekdays, 113 weekend days and holidays and 12
peak days, see *Day-type calendar*).

The day weights change from one calendar year to another. The year always has 365 days, but the weekdays and
weekends fall differently in each month: a month that has four weekends in one year may have five in another,
and the holidays land on different days. So the day weights of the calendar this model uses (2023 by default,
see *Day-type calendar*) differ from the weights the shapes were built with, and the shapes no longer add up
to one when weighted with them (for the 2023 calendar the end-use shapes are off by -1.1% to +0.7% and the
system shapes by -0.10% to -0.004%). Used as they are, the shapes would create or lose energy, and the annual
energy of the hourly profile would not match the annual load that went in.

The model therefore keeps the shape of each representative day but rescales it to the day composition of the
calendar in use, dividing each shape by its own day-weighted sum:

$$
\tilde{\phi}_{q} = \frac{\phi_{q}}{\sum_{q' \in Q} w_{q'}\, \phi_{q'}} \tag{2}
$$

so that $\sum_q w_q \tilde{\phi}_q = 1$ to rounding, and the annual energy is conserved for any calendar. The
tilde is dropped below: all shapes are the normalized ones unless `renormalize = false`. The weights $w_q$ are
counted from the calendar of the run; the `V_SYS_LOAD_WEIGHTS` table is only used for checks.

### Basic approach

The profile is the bottom-up sum of the end-use shapes, each weighted by its annual load (Eq. 2-2 of the EMM
report; `method = "basic"`):

$$
S^{basic}_{r,y,q} = \sum_{s \in S}\sum_{e \in E_s} \phi_{r,s,e,q}\, L_{r,s,e,y} \tag{3}
$$

It needs the end-use shapes and the annual load of the projection year, no base year. In `uldsm.f` this is the
formula behind the *sector curves*; the system load that goes on to planning and dispatch uses the delta
approach. The report states that the system result is then multiplied by the T&D loss factor, which is what the
basic method here does.

### Delta approach

The delta approach (Eqs. 2-3 and 2-4; `method = "delta"`) starts from the historical system shape of the
region and adds the change of each end use relative to the change of the total:

$$
S^{delta}_{r,y,q} = \phi^{sys}_{r,q}\, \Lambda_{r,y}
+ \sum_{s \in S}\sum_{e \in E_s} \phi_{r,s,e,q}\,\bigl(L_{r,s,e,y} - \rho_{r,y}\, B_{r,s,e,y}\bigr) \tag{4}
$$

with

$$
\rho_{r,y} = \frac{\Lambda_{r,y}}{\sum_{s,e} B_{r,s,e,y}} \tag{5}
$$

The bracket is the amount by which an end use grew faster (positive) or slower (negative) than the region's
total. If every end use grows by the same factor, the bracket is zero and the profile is the system shape times
the system load. A base-year system load of zero is an error.

**Annual energy is conserved.** Summing Eq. 4 over the points with the weights and using
$\sum_q w_q \phi_q = 1$:

$$
\sum_{q} w_q\, S^{delta}_{r,y,q} = \Lambda_{r,y} + \sum_{s,e}\bigl(L_{r,s,e,y} - \rho_{r,y} B_{r,s,e,y}\bigr)
= \Lambda_{r,y} + \Lambda_{r,y} - \rho_{r,y}\sum_{s,e} B_{r,s,e,y} = \Lambda_{r,y} \tag{6}
$$

and likewise $\sum_q w_q S^{basic}_{r,y,q} = \Lambda_{r,y}$. The delta term moves energy between hours, not between
years. Hydrogen is not part of $\Lambda$ or of these sums: its energy is added in the next step, so the
annual energy of the final sales profile is $\Lambda_{r,y}$ plus the hydrogen energy.

**Negative hours.** The bracket in Eq. 4 can be negative and large in the hours where one end use dominates its
shape. If that end use's load fell while the region's total grew, the term $\phi\,(L - \rho B)$ can exceed the
positive terms and the sales go below zero. Example: in `higheldmd` in 2050, FRCC's residential space heating
(`HTRCON`) falls from 13.6 TWh in the base year 2026 to 10.7 TWh while the region's total load grows by a
factor of 1.62; `L - rho*B` is -11.3 TWh, and times the heating shape in the coldest early hours it subtracts
41,000 MW against a system-shape term of 49,700 MW. NEMS does not floor the result, and neither does this model:
a negative hourly load stops the run (see *Data validation*).

### Hydrogen

Hydrogen electrolyzers are one end use of the industrial sector (end use 4). Unlike every other end use they
have no load shape, because their demand does not follow a fixed daily pattern: NEMS takes it from its hydrogen
module as an hourly demand instead of spreading an annual total with a shape. This model follows the same
split in two steps.

**Step 1: the decomposition does not account for hydrogen.** In Eqs. 3 to 6 the sums, $\Lambda_{r,y}$, the
base-year sum and $\rho_{r,y}$ leave out industrial end use 4. The hydrogen row of the annual loads is not
spread with a shape (the shape name `INDH2XXX` is the one name that is allowed to have none) and it does not
influence $\rho_{r,y}$, so the base-year hydrogen does not matter.

**Step 2: the profile is corrected by the hydrogen load.** The hydrogen demand $H_{r,q}$, in MW at each
representative point, is added to the sales profile as it is, before the losses of the next subsection:

$$
S_{r,y,q} \leftarrow S_{r,y,q} + H_{r,q}
$$

It is also added to the industrial sector curve, without losses. The annual energy of the hydrogen is
$\sum_q w_q H_{r,q}$, so the annual energy of the final sales profile is exactly $\Lambda_{r,y}$ plus the hydrogen
energy, for the basic and the delta approach and for any base year. Hydrogen is a component of industrial load:
it has no column of its own in the output files, and it is inside `sales_mwh` and `load_mwh`.

#### Where the hydrogen comes from

Hydrogen follows the annual loads:

| Annual loads from | Hydrogen is taken from | Profile of $H_{r,q}$ |
|---|---|---|
| NEMS outputs (`nems_outputs_dir`), industrial sector not replaced | `qelhm_hr` of the restart file, by EMM region | hour-of-day pattern of the year, spread evenly over all days |
| a user file (`census_division_loads` or `emm_region_loads`), or an industrial sector replaced with `demand_overrides` | the industrial end use 4 rows of that file | flat over all hours, $H = \text{annual} / 8760$ |

A user file or an industrial replacement file **must** contain end use 4 rows for every year of the run and
every census division (or region), with 0 where there is no hydrogen; a file without them stops the run with an
error. Hydrogen is never taken from NEMS for a user's industrial demand and is never filled in silently. The
rows are mapped from census divisions to regions with the industrial map, like the rest of industrial load, so
the Alaska and Hawaii share is dropped.

**NEMS hydrogen module.** The array `qelhm_hr` has shape (region, season, hour of day, year); each value is the
energy in MWh of one hour of the day summed over all the days of the season, not a power. The 25 EMM regions are
read; Alaska, Hawaii and the US total are not used. For a region $r$ and year $y$:

1. add the four seasons: $E_{r,h,y} = \sum_{s=1}^{4} \texttt{qelhm\_hr}_{r,s,h,y}$, the annual energy of hour of
   the day $h$ (MWh);
2. divide by the number of days of the calendar in use, $n_{days}$ (365): $MW_{h} = E_{r,h,y} / n_{days}$;
3. every day of the year, in every month and day type, gets the same 24 values:
   $H_{r,q} = MW_{h(q)}$, where $h(q)$ is the hour of the day of point $q$. On the 8,760 hours this is the same
   24 values on every date.

The energy is exact: $\sum_q w_q H_{r,q} = n_{days} \sum_h MW_h = \sum_{s,h} \texttt{qelhm\_hr}_{r,s,h,y}$, and
the run checks it. Hour 1 of the day is taken to be 00:00-01:00, the same as in the hourly file; the NEMS file
does not say. The module total agrees with the census-level hydrogen row (`qelinh2e`) apart from a unit
conversion constant, see the limitation on `census_loads_used.csv` below.

**Limitations.**

- **The seasonal pattern of the hydrogen module is lost.** NEMS spreads each season's value over the days of that
  season; this model keeps the annual energy and the hour-of-day pattern and spreads them evenly over the whole
  year. In the restart files checked the hydrogen is strongly seasonal, with most of it in one NEMS season and
  none in another in some years. Spread evenly, hydrogen load is overstated in the peak seasons and
  understated in the season where NEMS places it. Annual energy is not affected.
- **Hydrogen can be concentrated in a few regions.** In the restart files checked only a few regions have any,
  and one of them carries most of it, so the regional effect on those regions can be large relative to the
  national total.
- **`census_loads_used.csv` shows the census-level row.** With NEMS loads the hydrogen row of that file (from
  `qelinh2e`) is not the hydrogen used, which is the regional `qelhm_hr`. The two totals differ slightly,
  by the same relative amount in every year: the module is in MWh, while the census row is in TBtu and is
  converted here with the NEMS constant of 292,830 MWh per TBtu, which is not exactly the factor the hydrogen
  module uses. It is a conversion constant, not Alaska and Hawaii.
  With a user file or a replaced industrial sector the end use 4 rows of the file are exactly what is used.
- **An hourly hydrogen input from another module is not implemented.** The Python argument `h2_hourly_mw` of
  `build_all_regions` accepts a profile per region (MW at the 864 points) and replaces the annual row.

### T&D losses

The load that the electricity model has to serve is larger than the sales to customers, because some of the
energy is lost in transmission and distribution (T&D) on the way. NEMS accounts for this with a multiplicative
factor, and the model does the same, after the hydrogen demand has been added:

$$
P_{r,y,q} = \theta_{r,y}\, S_{r,y,q}, \qquad \theta_{r,y} = 1 + \tau_r\, a_y \tag{7}
$$

- **Loss rate $\tau_r$** (`UQTDLS`, table `V_EMM_LDSMSTR_RGN`): the losses as a share of sales, one value per
  EMM region and constant over the years. It ranges from 0.045 (TRE) to 0.105 (BASN) in the shipped table.
- **Loss adjustment $a_y$** (`ULOSSADJ`): a multiplier on the loss rate that NEMS sets for each year in
  `prepett.f`. The file `input/load/ulossadj_by_year.csv` holds that schedule.
  
- **The same factor is used in every hour** of a region and year. The shape of the profile does not change; only
  its level, so the annual energy of the load is $\theta_{r,y}$ times the annual energy of the sales (the energy
  conservation of Eq. 6 refers to the sales). Both are written to the hourly file as `sales_mwh` and `load_mwh`,
  and the factor to the summary file as `td`.
- **The sector curves carry no losses** (as in `uldsm.f`); only the system curve does.

How $a_y$ is set in a run:

| Setting | Effect |
|---|---|
| `ulossadj_file` | a year-by-year schedule as above (columns `year`, `ulossadj`); it must cover every year of the run, otherwise the run stops. Both shipped run configurations use it. |
| `loss_adj` | one number used for all years. |
| neither | $a_y = 1.0$ and a note in the log. The loss factor is then the full $1 + \tau_r$, which is higher than NEMS from 2020 on (BASN: 1.105 against 1.084). |

Give at most one of the first two.

### Sector curves

For each sector the model also keeps the curve $SC_{s,r,y,q} = \sum_{e} \phi_{r,s,e,q}\, L_{r,s,e,y}$ (the
`sales_mw_<sector>` columns of the optional 864-point files). In the basic approach the sector curves add up to
the sales profile. In the delta approach they do not, because the sector curves use the end-use loads as they
are while the system profile also carries the delta correction:

$$
S^{delta}_{r,y,q} - \sum_{s} SC_{s,r,y,q} = \phi^{sys}_{r,q}\,\Lambda_{r,y} - \rho_{r,y}\sum_{s,e}\phi_{r,s,e,q}\,B_{r,s,e,y} \tag{8}
$$

The two agree in annual energy (Eq. 6) but differ hour by hour.

### Base year

Two different things are called a base year. `base_year` and `base_freeze_year` choose the year of the
*annual loads* used as the reference in Eq. 4:

$$
b(y) = \begin{cases} \texttt{base\_year} & \text{fixed base year} \\ \min(y,\ \bar{y}) & \text{NEMS rule, } \bar{y} = \texttt{base\_freeze\_year} \end{cases} \tag{9}
$$

Give exactly one of the two. In NEMS (`DSMFOR`, `uldsm.f`) the base load is overwritten with that year's load
for every year index up to `MSEDYR + 3`, which is 2026: years up to 2026 are their own base (the delta term
is zero) and later years use the 2026 loads. `base_freeze_year = 2026` reproduces this; `base_year = B` keeps
one fixed base year for the whole run. The annual-load file must hold the projection year and every base year
it needs.

The *calendar* year (`calendar_year`, default 2023) is unrelated: it only fixes the order of weekdays,
weekends and peak days and the hourly pattern repeated in every year. The two years can differ.

### From 864 points to 8,760 hours

The hourly value is the value of the point that stands for that hour. For hour $t$ of the year (1-based),
day $n = \lfloor (t-1)/24 \rfloor + 1$ and hour of day $j = ((t-1) \bmod 24) + 1$; the calendar gives the month
$m_n$ and day type $d_n$ of day $n$, and

$$
x_{r,y,t} = P_{r,y,\,q(m_n,\,d_n,\,j)} \tag{10}
$$

so every point is repeated on each of the $w_q$ dates it stands for. Annual energy is therefore conserved, $\sum_t x_{r,y,t} = \sum_q w_q P_{r,y,q}$, and the same holds for the sales.
`crosswalk_864_to_8760.csv` is this map: one row per hour with `hour_index` (1 to 8,760), `datetime`, `month`,
`day`, `hour_of_day`, `day_type` and `point_index` (0 to 863). Hour 1 is 00:00-01:00 on 1 January of the
calendar year and the hours run in date order. The map depends only on the calendar, so every projection
year uses the same one: the 2035 profile is laid on the positions of the 2023 calendar, with its weekday and
weekend pattern, and the projection year appears in the `year` column, not in the dates. A calendar with 366
days is refused when `hourly8760 = true`.

### Summary quantities

The summary file reports, for each year, region and method: `system_load_mwh` (the total annual load,
$\Lambda$ plus the annual hydrogen), `slr` ($\rho$, empty for the basic method; it is the ratio of the end uses
without hydrogen), `td` ($\theta$), `annual_sales_mwh` ($\sum_q w_q S_q$ with hydrogen, equal to
`system_load_mwh`), `annual_load_mwh` ($\theta \sum_q w_q S_q$) and `peak_load_mw` ($\max_q P_q$). Because an
hour is one hour long, a value in MW and the energy in that hour in MWh are the same number.

## Data

### Inputs

| # | What | Needed for | Setting |
|---|---|---|---|
| 1 | **Shape and structure tables**, one CSV per table, all in one folder: `V_SYS_LOAD_by_EndUse`, `V_SYS_LOAD_by_EMM_REGION`, `V_SYS_LOAD_WEIGHTS`, `V_EMM_LDSMSTR_EU_SECT`, `V_EMM_LDSMSTR_SECT_EU_DIM`, `V_EMM_LDSMSTR_SECT_DIM`, `V_EMM_LDSMSTR_RGN`, `V_EMM_LDSMSTR_RGN_CEN` | both | `tables_dir` |
| 2 | **Annual load by end use, projection years**: from the NEMS outputs, by census division (`year,sector,euse,census,mwh`), or by region (`year,region,sector,euse,mwh`) | both | `nems_outputs_dir`, `census_division_loads` or `emm_region_loads` |
| 3 | **Annual load by end use, base year** (the same data as 2) | delta only | `base_year` or `base_freeze_year` |
| 4 | **Hydrogen**: the industrial end use 4 rows of input 2 (required in a user file or an industrial replacement file, 0 if none); with NEMS loads it is read from the hydrogen module instead | both | no setting; see *Hydrogen* |
| 5 | `ULOSSADJ` schedule (optional) | both | `ulossadj_file` or `loss_adj` |
| 6 | **Day-type calendar** | both | `calendar_year`, `calendar_csv`, `load_dir`, `holidays`, `peak_days_csv` |

The `ulossadj_by_year.csv` file holds the NEMS schedule from `prepett.f`: 1.0 to 1998, 0.97-0.91 for 1999-2006,
1.0 for 2007-2010, 1.05 for 2011-2013, 1.02 for 2014, 1.0 for 2015-2019, **0.8 from 2020**. A year that is not in
the file stops the run. The projection years are the run's `summary_years`.

**Units.** Energy is in MWh and power in MW everywhere: the input tables, the 864-point profiles, the summaries
and the hourly file. NEMS stores its loads in trillion Btu (TBtu); `nems_aeo_reader.py` converts them once, when
it reads the NEMS output, at **292,830 MWh per TBtu** (the NEMS constant, 292.83 GWh per TBtu). Files you supply
(region loads, census loads, sector replacements) must be in MWh; a file with a `tbtu` or `gwh` column is
refused with a message that says how to convert it.

**Census-division loads.** `census_division_loads` takes `year,sector,euse,census,mwh` for divisions 1-9;
codes 10 (California) and 11 (total US) are ignored with a warning because they overlap divisions 1-9. The
output is smaller than the input by the Alaska and Hawaii share of the Pacific division.

### Day-type calendar

The calendar says, for each day of one year, whether it is a weekday, a weekend or holiday, or the peak day of
its month. The model **counts the days** of each (month, day type) to get the weights $w_q$ and uses the
calendar to lay the 864 points on the 8,760 hours. All the date logic is in `calendars.py`. The calendar is
built at every run; none is stored.

| `calendar_year` | Calendar | How it is made |
|---|---|---|
| (not given) | **2023**, the default | built from the hourly load file `base_load_2023.csv` (`hour`, `base_load_mwh`; one row per hour for the system, or one row per region and hour, in which case regions are summed) in `load_dir`: weekend = Saturday, Sunday and the 11 US federal holidays (observed dates); peak = the non-holiday weekday of the month with the highest system load. Without a usable load file the run uses 2018 and logs a note |
| 2018 | the **NEMS** calendar | needs no data. Its day counts equal `V_SYS_LOAD_WEIGHTS` (240 weekday / 113 weekend / 12 peak days); weekend = Saturday, Sunday and 9 weekday holidays (Jan 1, Feb 19, May 28, Jul 4, Sep 3, Oct 8, Nov 12, Nov 22, Dec 25); peak = the weekday nearest the 15th. The holidays and the peak rule are a reconstruction that reproduces the weights; the repository does not state them |
| any other year | you complete it | `holidays = "federal"` (or a CSV `month,day`) **and** peak days: `peak_days_csv` (12 rows `month,day`) or `load_dir` (peak = best weekday of each month). Without them the run stops and says what is missing |
| `calendar_csv` | your own calendar | CSV `date,month,day_type` (1/2/3 or weekday/weekend/peak), one row per day |

Given holidays and peak days, every other day is classified by its real weekday: Saturday and Sunday are
weekend, a listed holiday is weekend, a listed peak day is peak, and all the rest are weekdays. No load data is
needed for that.

**Fallback.** With no `calendar_year`, the run tries to build 2023 from `load_dir`. If there is no load file,
or it is unusable (wrong number of hours, missing columns), it uses 2018 and logs a note saying why. A year you
name is never replaced: if you ask for 2023 and it cannot be built, you get an error.

**Peak days.** Only non-holiday weekdays can be the peak day. For the 2023 data the two months where the
highest-load day was a weekend (Feb 4, Oct 1) take the best weekday instead. The load file is summed over the 25
regions, so one calendar serves all regions, as in NEMS. Hour 1 of the file is taken as 00:00 on 1 January.
Only the shape of the load file matters, not its level.

A custom calendar (`calendar_csv`) is used in the order given; the dates are not sorted or checked for being
consecutive.

### Reading the NEMS outputs

With `nems_outputs_dir`, the project's `scenario_name` is mapped to a NEMS scenario folder with
`input/load/scenario_map.csv`, and `nems_aeo_reader.py` reads that scenario's `restart.rlx` (a NumPy archive in
the AEO2026 release). It repeats the part of `uldsm.f` that builds the census-division load table
(`DSMARE`, `DSMACM`, `DSMAIN`, `DSMATR`, `DSMCADJ`), but stops before the census-to-region mapping, which the
model does itself. The restart file holds one snapshot (the final iteration of one scenario).

The load aggregator in NEMS does not use the sector module outputs as they are; it adjusts them first. The
reader handles this in two stages, and the second one is this model's own:

1. **NEMS adjustments** (`_extract_year`). The reader rebuilds the end-use loads of each census division from
   the restart file and applies the adjustments of `uldsm.f` that can be rebuilt from it (rows 1, 2, 7 and 8
   of the table below). The result is the column `mwh_nems`: the end-use loads as NEMS would pass them on.
2. **Benchmarking to sales** (`extract_loads`). A second, proportional rescaling then makes the end uses of
   each sector and division add up to the sales figure stored in the same file. It gives the column `mwh`,
   which is what the model uses by default. It is not a NEMS step, see *Benchmarking* below.

The table lists each adjustment of the NEMS load aggregator and says whether the reader applies it.

| # | NEMS adjustment | What it does in NEMS | In this model |
|---|---|---|---|
| 1 | Commercial rescaling (`DSMCADJ`) | Scales one commercial service, "other" (service 10, in every building type), so that all commercial end uses plus commercial electric vehicles add up to commercial sales plus on-site generation. If the factor would fall below -1, "other" is set to zero and the remaining services are scaled instead. Electric vehicles are not scaled. | **Applied** in stage 1, so it is already in `mwh_nems`. It is a large correction: in `cb2026` the commercial end uses read from the file add up to 3,815 TBtu in 2025 and `DSMCADJ` raises them to 5,185 TBtu, the sales figure. Stage 2 then changes nothing for commercial (the two columns are equal in every year of `cb2026`). |
| 2 | Behind-the-meter generation | Commercial sales (`QELCM`) are net of on-site generation (`CGCOMMGEN`, the part used on site), so it is added back to the target of row 1. Residential end uses are gross of on-site generation, so they exceed residential sales (`QELRS`) by `CGRESGEN`. | **Applied** in row 1's target, and in the stage 2 control totals for residential (`QELRS` + generation) and commercial (`QELCM` + generation). |
| 3 | Foresight factor | Multiplies each load by (sector sales in the projection year / sales in the base year) x `DEMCE*`. In the EMM run both years are the same, so the first term is 1. | Not applied (it is 1). |
| 4 | Demand-side management factor (`DEMCE*`) | 1 minus the demand-side-management reduction, only when that switch is on. | Not applied: the switch is not in the restart file. |
| 5 | Historical regional benchmark (`Histshr`) | For 2010-2024, scales every end-use load by (historical regional sales / modelled regional sales). | Not applied: NEMS codes it for 13 regions only, so it is inactive with 25 regions. |
| 6 | High-demand multiplier (`UQELFAC`) | Multiplies loads by a yearly factor when `USW_CEL > 0`. | Not applied: the switch is not in the file (0 in the standard control file). |
| 7 | Hydrogen | NEMS takes the industrial hydrogen load from the hydrogen module. | The census-division array `qelinh2e` is read as industrial end use 4 of the load table, but it is not used for the profile: with NEMS loads the hydrogen comes from the hydrogen module by region (`qelhm_hr`), spread evenly over the year, and it is added after the decomposition (see *Hydrogen*). |
| 8 | Electric vehicles | Residential charging is vehicle type 1, commercial types 2-9 (types 6-8 summed), electric rail is type 10. | **Applied** in stage 1: they become residential end use 11, commercial end uses 111-116 and transport end use 1. |
| 9 | Base-year freeze | NEMS stores base-year end-use loads only for years up to the last historical data year plus 3. | Not in the reader. It is reproduced by the model setting `base_freeze_year` (see *Base year*). |
| 10 | T&D losses | Load = sales x (1 + loss rate x `ULOSSADJ`(year)). | Not in the reader. Done in the model, Eq. 7. |

Apart from row 1, the adjustments are small or not active in the standard run.

**Benchmarking (stage 2).** The restart file holds the end-use arrays as the modules produced them, and they do
not always add up to the sector sales stored in the same file. For each year the reader therefore writes two
columns, `mwh_nems` (the end use after the NEMS adjustments of stage 1) and `mwh`, obtained by one
multiplication per sector, census division and year:

$$
\texttt{mwh}_{e} = \texttt{mwh\_nems}_{e}\ \frac{\text{control}}{\sum_{e'} \texttt{mwh\_nems}_{e'}}
$$

where the control is the sales figure NEMS stores for that sector and division, plus own-use generation for
residential and commercial: `QELRS` + own use, `QELCM` + own use, `QELIN`, `QELTR`. Every end use of a sector
and division is scaled by the same factor, so the split between end uses is NEMS's and only the total is moved
to sales. `restart_values = "mwh_nems"` selects the other column. The two are equal wherever the stage 1 result
already adds up to sales: commercial in every year (because `DSMCADJ` forces it, row 1), residential and
industrial from 2020 (industrial also 1990-2009 and 2013-2019, apart from differences of a fraction of a
percent in a few early years).
Residential end uses before 2020 are 10-35% below sales, industrial divisions in 2010-2012 are off by up to
30%, and transport has no end-use data for 1990-1994; in those years only the sector total is anchored to
sales, so treat the split between end uses as approximate. The reader also compares the extracted sector
totals with the NEMS sales and logs the largest relative difference.

> **This rescaling is this model's own method, not a NEMS procedure.** NEMS does not spread a gap
> across all end uses. For residential electricity it adds the whole gap (`BNCHFCT = QSELRS - RSFLCN`,
> subroutine `RSBENCH` in `resd.f`) to one end use, single-family "other appliances", and only from the
> residential survey year onward; before that it applies no benchmark. The restart file does not say which
> end use NEMS would have used in the early years, so the reader scales every end use in proportion. EV
> charging, which is one of the residential or commercial end uses, is scaled with the rest. User-supplied
> demand is never rescaled.

## On-site generation

Some customers produce electricity where they use it: rooftop solar, combined heat and power, fuel cells,
small wind. Electricity a customer makes for itself never has to be delivered by the grid, so if it were left
in the load that the electricity model must supply, the system would be asked to produce it twice. NEMS
handles this differently for each sector and in two stages.

### Stage 1: the load handed to the electricity model

| Sector | What the load contains | Generation used on site | Generation sold to the grid |
|---|---|---|---|
| Residential | Everything the households use, including what their own units produce (**gross**) | inside the load | not in the load |
| Commercial | Same as residential; NEMS also rescales the end uses to commercial sales plus own-use generation (**gross**) | inside the load | not in the load |
| Industrial | Only electricity the plants buy (**net**); what a plant generates for itself is never counted as demand | not in the load | not in the load |
| Transport | Electric rail and similar; no on-site generation is attached | none | none |

For homes and commercial buildings NEMS first counts all the electricity used, whatever its source, and takes
the self-generated part off later. For industry the self-generated part is left out from the start. This is why
the residential and commercial end-use figures in the restart file are larger than the sales figures (sales are
what is bought from the grid). In the `cb2026` run the residential gap was about 185 TBtu in 2025, which equals
the own-use generation.

### Stage 2: how the electricity model removes it again

Going by a reading of the NEMS source code, the electricity model does not subtract on-site generation from
the hourly load profile. It subtracts it when it plans capacity and dispatches plants, in two ways:

1. **Rooftop (distributed) solar** is a power source with a daily solar pattern, so it offsets the load when
   the sun shines and not at night. How much exists comes from the residential and commercial demand modules.
2. **All other on-site generation** (combined heat and power, fuel cells, small wind, biomass, waste, hydro and
   similar) is taken off as a constant amount in every hour, with no daily pattern. For residential and
   commercial customers the constant covers both the own-use part and the part sold to the grid. For industry
   it covers only the part sold to the grid, because the own-use part was never in the load.

The same constant is also taken off the demand used when the model decides how much new capacity to build, so
the peak requirement is reduced as well.

### What this module does

This module reproduces Stage 1 only. The hourly profile is **gross of own-use generation for residential and
commercial customers** and is not the demand that grid generators have to meet. Used as grid demand without
subtracting on-site generation (shaped for solar, flat for the rest, industrial grid sales only), it would
overstate the requirement by roughly the size of the own-use generation (about 3-5% of residential sales in
the `cb2026` run, rising over time).

To make the netting possible downstream, the module writes `onsite_generation_by_year_region.csv`: one row per
year and EMM region, in MWh per year (annual totals, no hourly pattern).

| Column | Content |
|---|---|
| `year`, `region_id`, `region` | as in the other files |
| `<category>_own_use_mwh` | generation consumed on site |
| `<category>_sold_mwh` | generation delivered to the grid |
| `annual_loads_source` | where the annual loads that this generation accompanies come from: `NEMS AEO2026`, `user file`, or either one `with replaced <sectors>` |

The categories are `residential`, `commercial`, `industrial`, `refinery` and `transport`. **Transport is always
0**: NEMS does not provide on-site generation for transportation. **Refinery** (`cgrefgen`, the refinery part of
industrial cogeneration) has its own columns; it is not confirmed from the NEMS source whether refinery
generation feeds the industrial purchased-electricity series (`QELIN`), so it is not folded into `industrial`.

What to subtract from the hourly load, going by NEMS (a reading of the source code, not traced through a run):

| Sector | Subtract |
|---|---|
| Residential, commercial | own use + sold (the load includes own use, and NEMS takes both off) |
| Industrial | sold only (own use was never in the load) |
| Transport | nothing |

NEMS applies rooftop solar with an hourly solar pattern and all other on-site generation as a constant. The
module has no solar profile, so it does **not** reproduce that split and writes annual numbers only.

**How the arrays are read.** The four arrays `COGEN/cgresgen`, `cgcommgen`, `cgindlgen` and `cgrefgen` have
shape `(census 11, year 61, technology 13, 2)` in GWh. The census axis has the nine divisions at positions 0-8;
position 9 is empty and position 10 is the US total, so only 0-8 are used. The year index is the year minus
1990. The technologies are summed (the file has no labels for them). On the last axis, index 1 is generation
used on site and index 0 is generation sold to the grid; checked on the `cb2026` and `higheldmd` files, where
index 1 is the larger slot and residential own use in 2025 equals the 185 TBtu above. GWh are converted to MWh
with a factor of 1,000. The census divisions are mapped to the 25 regions with Eq. 1, so Alaska and Hawaii drop
out in the same way as for the load.

Individual technology cells can be slightly negative in historical years (as low as -13.9 GWh in `cgrefgen`,
2014 and 2022); the reader checks the requested years after summing, and stops with an error if a sum is
negative. The years from 2025 on are clean in the shipped scenarios. A missing array, an unexpected shape or a
year outside the file is an error naming the variable.

**When the file is written.** Whenever a NEMS restart file is available. With a user load file
(`census_division_loads`, `emm_region_loads`) it is written only if `nems_outputs_dir` is given as well; the
generation is then NEMS's while the demand is the user's, which `annual_loads_source` says and the log warns about.
The same flag marks sectors replaced through `demand_overrides`. The numbers are not rescaled to the user's
demand.

### Limits of this description

It is based on reading the NEMS source code, not on tracing a run. It was not verified that the solar switch
was on in the `cb2026` run, whether industrial solar sold to the grid is non-zero, or how behind-the-meter
batteries and small wind are handled beyond the constant offset. Treat these as open points until confirmed by
EIA.

## Data Validation

The model assumes its inputs are clean, so the checks are in a separate module, `data_validation.py`, that
`run()` calls first (`validate_inputs = true`). Each check returns issues with a level, ERROR or WARN;
`check_*` raises `ValueError` on any ERROR and returns the warnings.

**Shape and structure tables:** tables and columns are present; no missing or non-numeric values; month, day
type and hour run 1..N without gaps and agree across the shape and weights tables; one weights row per month and
day type, 365 or 366 days in total; every shape has exactly one value per point, no duplicates, no negatives;
every shape sums to 1 day-weighted (a warning); all 25 regions are present; every shape name in the structure
table exists (the hydrogen shape excepted); the census map has its national-total row.

**Annual loads** (`check_loads`, `check_census_loads`): a (sector, end use) pair that is not in
`V_EMM_LDSMSTR_EU_SECT` is an **error** (its energy would be dropped without a message); a valid pair that is
absent for a year and region is a **warning** (treated as 0 MWh, as `DSMNWS` does). Hydrogen is the exception:
the industrial end use 4 rows are **required** for every projection year and every census division (or region)
of a user file or an industrial replacement file, and a missing, empty or negative value is an **error** (0 is
allowed); with NEMS loads the hydrogen module must hold the years and have no negative or non-finite values.
The loads must hold every projection year and every base year the delta method needs.
Replacement files (`demand_overrides`) must cover every end use, census division 1-9 and year, with finite,
non-negative numbers.

**T&D schedule** (`check_ulossadj`): a missing year, a duplicate year or a non-positive value.

**Calendar** (`check_calendar`): readable and unique dates, one year with all its days, month agrees with the
date, months and day types inside the grid, and a warning for a month without a day of some type. The day
counts are compared with `V_SYS_LOAD_WEIGHTS` for information only; they are meant to differ.

**Hourly output.** The delta approach can give a negative hourly load (see Eq. 4). If any `load_mwh` is below
zero, the run stops with an error that gives the number of hours, the lowest value, the regions and years and
the most negative rows, saves all of the rows in `negative_load_rows.csv`, and writes no hourly file. Zero is
allowed.

**Energy balance.** For every region, year and method the profile must add up to the annual load that came in
(`system_load_mwh`, hydrogen included): the weighted sum of the 864-point sales profile, and the sum of the 8,760
hourly `sales_mwh` when the hourly file is requested, must both equal it to a relative 1e-9. Otherwise the run
stops with an error that names the method, the year and the regions (with the values found and expected). The
check covers the decomposition and the expansion to hours; the census-to-region mapping has its own tests and
the `census_loads_used.csv` reconciliation. It runs before the negative-load check, and on real data as well.

**On-site generation:** arrays present, shape `(census >= 9, year, technology, 2)`, requested years inside
the file, sums finite and not negative; a warning if US residential own use in 2025 is outside 30,000 to 80,000
GWh.

There is no silent fixing: missing years, rows or codes stop the run with a message naming the problem.

## Outputs

All files are in `output/load/<scenario_name>/`.

**`hourly_load_mwh.csv`** is the file for the electricity model: long format, one row per region, year and
hour, sorted by year, region and hour (one file per method, in `basic/` and `delta/` when `method = "both"`).

| Column | Content |
|---|---|
| `region_id` | EMM region number 1 to 25 (the order of `V_EMM_LDSMSTR_RGN`: 1 TRE, 2 FRCC, ...) |
| `region` | EMM region name |
| `year` | projection year |
| `hour` | hour of the year, 1 to 8760; 1 = 00:00-01:00 on 1 January of the calendar year, in date order |
| `sales_mwh` | energy sold in that hour, MWh, **before T&D losses** |
| `load_mwh` | system load in that hour, MWh (average MW over the hour), **including T&D losses** (`sales_mwh` x `td`) |

Size: 25 regions x 8,760 hours x 25 years is 5.5 million rows (about 170 MB as CSV).

| File | Content |
|---|---|
| `summary_by_year_region.csv` | one row per year, region and method: `system_load_mwh`, `slr`, `td`, `annual_sales_mwh`, `annual_load_mwh`, `peak_load_mw` |
| `onsite_generation_by_year_region.csv` | annual on-site generation by sector, see *On-site generation* |
| `crosswalk_864_to_8760.csv` | the hour map of Eq. 10 |
| `calendar_used_<year>.csv` | the calendar the run used |
| `census_loads_from_NEMS_AEO2026.csv`, `census_loads_used.csv`, `demand_sources.csv`, `demand_comparison.csv` | audit files for the annual loads |
| `region_loads_used.csv` | the annual loads by EMM region (columns `year, region, sector, euse, mwh`) that the profiles were built from: the census loads after the census-to-region mapping, or the regional file as read. Alaska and Hawaii are not in it, so its total is below `census_loads_used.csv` |
| `scenario_used.csv` | project scenario, NEMS scenario and the name of the source file (no folder path) |
| `<year>/profile864_<nn>_<region>.csv` | the 864-point profile of each region (MW: sales, load, sector curves); only with `save_profiles_864 = true`, in a sub-folder per year when there are several years and per method when `method = "both"` |
| `negative_load_rows.csv` | only when the run stopped on negative hours |
| `run.log` | the run's log (load-only runs) |

## Coupling Interface

The load model is not part of the iterative runs and takes no update packages (`update_reader.py` and
`update_writer.py` are shells). It runs once per scenario with `models_to_run = ['load']`, at a fixed place,
`output/load/<scenario_name>/`, which a rerun empties and replaces. It is left out of `models_to_run = ['all']`.

The intended use is that another model reads `hourly_load_mwh.csv` (and, for the netting of on-site
generation, `onsite_generation_by_year_region.csv`) from that folder for its own scenario. The files are by EMM
region id 1 to 25, by `summary_years`, in MWh, with 8,760 hours per region and year in the calendar's date
order. The electricity model does not read them yet; the reader there would have to check that every model year
is in the file, and will need to pick the columns by name. `LoadProfileModel.regions` lists the region ids as
strings (`'1'` to `'25'`), the form the electricity model uses.

## Assumptions and Differences from NEMS

- The module is a re-implementation from reading the source and has **not been validated against numbers from
  a real NEMS run**. The self-test checks internal consistency: shapes sum to 1 once weighted by days, energy is
  conserved, the delta profile reduces to the system shape when all end uses grow equally, the basic sector
  curves add up to the system curve, and the 8,760-hour total equals the 864-point weighted total.
- The annual end-use loads come from the NEMS demand modules and are not in the repository; `BSYRLD` in the
  NEMS database is 0 everywhere.
- In NEMS only the delta result goes on to planning and dispatch. The basic system curve here follows the EMM
  report (Eq. 2-2 plus the loss factor); the source applies the basic formula only to sector curves. With
  made-up end-use splits the basic approach gives very different peaks from the delta approach (the synthetic
  demo shows this); the report says bottom-up sums do not reproduce actual system curves.
- `FORESIGHTadj`, the demand-side management factor, the historical regional benchmark and the high-demand
  multiplier are not applied (see the corrections table); apply them to your loads first if you need them.
- The proportional rescaling to sales in the NEMS reader is this model's own method, not NEMS's.
- **Hydrogen** differs from NEMS in three ways:
    1. NEMS includes hydrogen in the system load and in the base-year system load, skips it in the delta loop and
       adds its hourly profile on top. This is exact only if the base-year hydrogen is zero; otherwise NEMS counts
       an extra $\rho\, B_{H_2}$, and in the base year itself the whole hydrogen load. This model leaves hydrogen
       out of the decomposition and adds it afterwards, so the energy is exact for any base year.
    2. NEMS spreads `qelhm_hr` over the days of each season (a seasonal and an hourly pattern). This model keeps
       the annual hour-of-day pattern and spreads it evenly over all days of the year, so the seasonal pattern is
       lost and the annual energy is kept.
    3. NEMS takes hydrogen by EMM region from the hydrogen module, and so does this model for NEMS loads; hydrogen
       in a user file is by census division or region and is mapped with the industrial census map.
- Alaska and Hawaii (rows 26-27 of the mapping) are dropped, as in NEMS.
- All projection years use the calendar of one year (2023 by default), so the weekday and weekend pattern does
  not move between years, and there is no 29 February.
- On-site generation is reported as annual totals and the NEMS solar and constant treatment is not reproduced
  (see *On-site generation*).
- The profiles work for any layout of months x day types x hours (sizes are read from the tables). The
  ready-made calendars (2018, 2023) assume 12 months x 3 day types, so for another layout supply a calendar
  with `calendar_csv`, numbering the day types as in your tables.
- `tests/load/data/demo_*_SYNTHETIC.csv` is made-up data that shows the file layout; it is not a forecast.
