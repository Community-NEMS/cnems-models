# Load model

Hourly electricity load profiles for the 25 EMM regions. A Python port of the load-profile step of the
EIA National Energy Modeling System (NEMS) Load and Demand-Side Management module (`source/uldsm.f`,
AEO2026 release). It turns **annual electricity load by sector and end use** into **hourly load**
(864 representative points, expanded to 8,760 hours). Nothing is optimized: the model has no solver and no
objective.

Status: runs **standalone** (`models_to_run = ['load']`). It is run once per scenario; other models read its
output files. It is not part of `models_to_run = ['all']`, the Jacobi or the Gauss-Seidel runs, and the
electricity model does not read its output yet.

The mathematical formulation, the input and output data, the checks and the differences from NEMS are in the
reference page [docs/models/load.md](../../../docs/models/load.md).

## At a glance

| | |
|---|---|
| Method | Basic (bottom-up sum of end-use shapes) or delta (NEMS system shape plus end-use growth), see the reference page |
| Regions | 25 EMM regions (Alaska and Hawaii are not modelled) |
| Time | 864 representative points (12 months x 3 day types x 24 hours) expanded to 8,760 hours of a 365-day calendar |
| Years | the run's `summary_years` |
| Inputs | NEMS load shape tables, annual load by sector, end use and census division (NEMS outputs or your own file), T&D loss schedule, day-type calendar |
| Main output | `hourly_load_mwh.csv` (MWh per EMM region, year and hour) |
| Units | MWh and MW |

## Run it

```bash
pixi shell
python main.py run_configs/basic_load_config.toml      # synthetic demo loads, needs no NEMS files
python main.py run_configs/baseline_load_config.toml   # NEMS AEO2026 outputs, see "NEMS outputs" below
```

Results go to `output/load/<scenario_name>/`.  Unlike the other models the folder is never suffixed (`_1`, ...): the load is built once per scenario and read by other models, so a rerun empties the folder and replaces the results.
Tests: `pytest tests/load -q`.

## How a run works

There are two layers: **the sequencer connects the model to C-NEMS, and `run()` does the load work.**
`run()` knows nothing about C-NEMS, and the sequencer does no load calculation.

```
python main.py run_configs/baseline_load_config.toml
        |
        v
 main.py          reads the TOML into settings (CommonConfig and LoadConfig)
        |
        v
 sequencer.py     turns the settings into plain arguments, finds the NEMS file,
        |         empties the results folder
        v
 run.py           run(): reads and checks the inputs, builds the load, writes the results
        |
        v
 load_model.py    the algorithm: annual load -> 864 points -> 8,760 hours
```

1. **Settings, `load_config.py`.** Defines what may appear in `[load_config]` and rejects wrong
   combinations, for example two sources of annual loads.
2. **Sequencer, `sequencer.py`.** The only file that talks to the C-NEMS framework
   (`LoadSequencer`, with the same build / solve / postprocess steps as the other models). It turns the
   project `scenario_name` into the NEMS scenario (`baseline` to `cb2026`) and finds that scenario's
   `restart.rlx`, empties `output/load/<scenario>/`, calls `run()` with the years and settings, and
   writes `scenario_used.csv` at the end.
3. **Run, `run.py`.** The function `run()` does one complete run and can also be called without C-NEMS
   (most tests do). In order, it:
    1. checks that the settings fit together;
    2. reads the shape tables, the calendar and the annual loads: `nems_aeo_reader.py` reads a NEMS
       file, `demand_overrides.py` swaps in a sector you replaced;
    3. checks the inputs (`data_validation.py`) and that the hydrogen rows are there (`hydrogen.py`);
    4. builds the load for every year and method with the algorithm, with the hydrogen profile from
       `hydrogen.py` added afterwards;
    5. checks that every region's profile adds up to its annual load (see below), stops on negative
       load, otherwise writes the hourly file and the other outputs.
4. **Algorithm, `load_model.py`.** Pure calculation, with no file or setting handling: it turns annual
   loads into the 864 representative points and expands them to 8,760 hours. `calendars.py` supplies the
   day types it needs.

Other files: `update_reader.py` and `update_writer.py` are empty shells for future data exchange with other models.

Input data: `input/load/` (`tables/` = the NEMS shape tables, `ulossadj_by_year.csv`, `base_load_2023.csv`
for the default calendar, `scenario_map.csv`). Synthetic demo loads: `tests/load/data/`.

## Settings

`[common]` supplies the years (`summary_years`), the scenario name and the output folder. The rest is in
`[load_config]`; relative paths are taken from the project root.

**Where the annual loads come from.** Give exactly one of the first three; the setting that is chosen decides
whether the loads are NEMS's or yours.

| Key | Meaning |
|---|---|
| `nems_outputs_dir` | NEMS outputs by scenario, `<dir>/<nems scenario>/<run>/restart.rlx` (the usual route; see "NEMS outputs") |
| `census_division_loads` | your CSV `year,sector,euse,census,mwh` (annual load by census division) |
| `emm_region_loads` | your CSV `year,region,sector,euse,mwh` (already by EMM region) |

`nems_outputs_dir` may also be given **next to** `census_division_loads` or `emm_region_loads`: the loads then
come from your file and the NEMS restart file supplies only the on-site generation file.

Your file must contain the **hydrogen rows** (industrial sector, end use 4) for every projection year and every
census division (or region), with 0 where there is no hydrogen. A file without them stops the run; see
"Hydrogen" below.

**Other settings.**

| Key | Meaning |
|---|---|
| `scenario_map` | project scenario to NEMS folder, default `input/load/scenario_map.csv` |
| `method` | `delta` (default), `basic` or `both` |
| `base_year` / `base_freeze_year` | delta method: exactly one; the NEMS rule is `base_freeze_year = 2026` |
| `ulossadj_file` / `loss_adj` | T&D loss adjustment by year, or one number (not both; neither means 1.0) |
| `demand_overrides` | table `sector = file` replacing a sector with your own forecast, see below |
| `calendar_year`, `calendar_csv`, `holidays`, `peak_days_csv`, `load_dir` | the day-type calendar |
| `hourly8760` | write the hourly file, default `true` |
| `save_profiles_864` | also write the 864-point profile of every region, default `false` (large) |
| `renormalize`, `validate_inputs` | defaults `true` |

## NEMS outputs and scenario names

The project's `scenario_name` and the NEMS scenario folders are different names. The file
`input/load/scenario_map.csv` has two columns, `scenario_name` and `nems_scenario`, and one line per
scenario:

| `scenario_name` (your run) | `nems_scenario` (NEMS folder) |
|---|---|
| `baseline` | `cb2026` |
| `high_elec_demand` | `higheldmd` |

With `nems_outputs_dir` set, the run looks up its `scenario_name` in this file and reads
`<nems_outputs_dir>/<nems scenario>/<run>/restart.rlx`. A name that is not in the file stops the run and lists
the valid ones. Add a line to the file for a new scenario (and put its restart file in the NEMS outputs
folder). The NEMS scenario used is recorded in `scenario_used.csv` in the results folder. Put the NEMS outputs
in `input/load/AEO2026_outputs_by_scenario/` (git-ignored, see the README.txt there) or point
`nems_outputs_dir` at any folder with that layout.

## Replacing a sector with your own forecast

By default every sector's annual load by end use and census division comes from NEMS. You can replace one or
more whole sectors with your own annual forecast and keep NEMS for the others. The choice is written in the run
config, never inferred from file names:

```toml
[load_config]
nems_outputs_dir = 'input/load/AEO2026_outputs_by_scenario'
base_year = 2023

[load_config.demand_overrides]          # leave a sector out to keep NEMS
commercial = 'inputs/commercial_mine.csv'
```

Sectors: `residential`, `commercial`, `industrial`, `transport`. `demand_overrides` works with
`nems_outputs_dir` or `census_division_loads`, not with `emm_region_loads` (the replacement is by census
division). An industrial file must include the hydrogen end use (`euse` 4), with 0 where there is none: the
hydrogen of a replaced industrial sector is yours, never NEMS's.

**The file you provide.** One row per year x end use x census division, annual load in **MWh**, gross of
on-site generation (the same basis as NEMS residential and commercial loads). A file with a `tbtu` or `gwh`
column is refused. The empty file is produced for you, with every required row listed and the `mwh` column
blank:

```python
from src.models.load.run import write_override_template

write_override_template(
    'commercial',
    'inputs/commercial_mine.csv',
    range(2023, 2051),
    restart_file='input/load/AEO2026_outputs_by_scenario/cb2026/<run>/restart.rlx',
    tables_dir='input/load/tables',
)
```

| column | meaning |
|---|---|
| `year` | calendar year, whole number (must include the base year and every projection year) |
| `sector` | sector code (1 residential, 2 commercial, 3 industrial, 4 transport); optional |
| `sector_name`, `end_use` | readable labels; ignored when the file is read |
| `euse` | NEMS end-use code of the sector (the same list as NEMS, 116 for commercial) |
| `census` | census division 1-9 |
| `mwh` | **your forecast** (not blank, not negative) |
| `nems_reference_mwh` | what NEMS has for the same row, for orientation; ignored when read |

Only `year`, `euse`, `census` and `mwh` are required; a forecasting module can write exactly those four columns.

**Creating the file is a separate step.** `run()` never creates it. If the config does not list a sector, that
sector stays NEMS. If it lists a file that does not exist, the run stops with an error that shows the command
to create it. Run `write_override_template(...)` once (in a notebook or script), fill the `mwh` column, then
point the config to the file.

**What the code checks.** Every end use x census division x year must be present with a number; end-use codes
must exist in NEMS for that sector; no duplicates; one sector per file. If anything is wrong the run stops
with a message naming the file and the first offending row. It never fills a gap from NEMS silently.

**What the code does with it.** The rows of that sector in the NEMS table are replaced by yours, including the
base year, so the delta method compares your projection with your own base year. The load shapes are NEMS's:
your annual numbers are spread over the hours with the NEMS system and end-use shapes, exactly as for NEMS
demand. **No NEMS-style adjustments are applied to your forecast**: your end-use numbers are taken to be the
final gross load. The only operations on it are the ones every input gets: census-to-region mapping, the shape
normalization, the delta method and the T&D loss factor.

**What you see in the output folder.**
- `demand_sources.csv`: one line per sector saying whether it came from NEMS or from which file, and the years
  covered. The same table is logged at the start of the run.
- `demand_comparison.csv`: the annual total of every sector, NEMS against what was used, and the ratio (1.0
  for sectors not replaced). Use it to catch unit or sign errors.
- `census_loads_used.csv`: the exact table the model received.
- `region_loads_used.csv`: the same loads by EMM region (`year, region, sector, euse, mwh`) after the
  census-to-region mapping, which is what the profiles are built from. Alaska and Hawaii are not in it.
- `onsite_generation_by_year_region.csv` is flagged in its `annual_loads_source` column when a sector was replaced:
  the generation stays NEMS's and is not rescaled.

**Other sectors do not respond to your forecast.** The other sectors stay at the NEMS scenario's values. In
NEMS the demand sectors react to electricity prices and to each other through the iterations of the full
model. When you replace one sector, nothing feeds back, so treat the other sectors as fixed inputs for that
scenario, not as results of your assumptions.

## Outputs

In `output/load/<scenario_name>/`:

- `hourly_load_mwh.csv`: columns `region_id` (1..25), `region` (name), `year`, `hour` (1..8760, 1 = 00:00-01:00
  on 1 January), `sales_mwh` (MWh sold in that hour, before T&D losses), `load_mwh` (MWh in that hour, including
  T&D losses). Sorted by year, region, hour. With `method = "both"` one file each in `basic/` and `delta/`.
- `summary_by_year_region.csv`: one row per year, region and method (annual load, `slr`, `td`, peak).
  `system_load_mwh` is the total annual load including hydrogen; `slr` is the system load ratio of the end uses
  without hydrogen (the same as before when hydrogen is zero).
- `onsite_generation_by_year_region.csv`: annual on-site generation by sector, see the next section.
- `<year>/`: `profile864_*.csv` (864-point profiles) only with `save_profiles_864 = true` (default false; the files are large).
- `crosswalk_864_to_8760.csv`, `calendar_used_<year>.csv`, `demand_*` and `census_loads_*` audit files,
  `scenario_used.csv`.

Every run checks the energy balance: for each region, year and method the 864-point profile and the hourly
`sales_mwh` must add up to `system_load_mwh` (to a relative 1e-9), or the run stops with an error naming the
regions.

Negative hourly loads can occur in the delta method (for example FRCC 2049-2050 in `higheldmd`; NEMS has no
floor either).  The run then stops with an error that lists the rows, saves all of them in
`negative_load_rows.csv` and writes no `hourly_load_mwh.csv`. Column definitions and the formulas are in the
reference page.

## Hydrogen

Hydrogen electrolyzers are industrial end use 4. They have no load shape, so the load decomposition (basic and
delta method) does not account for them; the hydrogen load is added to the profile afterwards, in MW at every
point, and goes through the T&D factor like the rest. It is part of industrial load: there is no separate
column in the output files, and no setting. Hydrogen follows the annual loads:

- **NEMS loads** (`nems_outputs_dir`): the hydrogen module's hourly demand (`qelhm_hr`) by EMM region. Its annual
  energy and hour-of-day pattern are kept and spread evenly over all days of the year.
- **Your file or a replaced industrial sector:** the end use 4 rows of the file, spread evenly over all hours.
  The rows are required (0 if none); a file without them is an error, never read as zero and never filled from
  NEMS.

The seasonal pattern of the hydrogen module is lost (in the NEMS outputs checked, most of it falls in one NEMS season), so
hydrogen load is overstated in the peak seasons and understated in its own; annual energy is exact. The procedure,
the formulas and the differences from NEMS are in the reference page.

## Gross or net of on-site generation

Some customers generate electricity where they use it (rooftop solar, combined heat and power, fuel
cells, small wind). The load in `hourly_load_mwh.csv` is the load NEMS builds for the electricity
model, and its basis differs by sector:

| Sector | The load is | Generation used on site | Generation sold to the grid |
|---|---|---|---|
| Residential | **Gross**: everything the households use, including what their own units produce | inside the load | not in the load |
| Commercial | **Gross**, same as residential (NEMS rescales the end uses to commercial sales plus own-use generation) | inside the load | not in the load |
| Industrial | **Net**: only the electricity the plants buy (`QELIN`) | not in the load | not in the load |
| Transport | electric rail and similar; NEMS has no on-site generation for it | none | none |

So the hourly load is **not** the demand that grid generators must meet: for residential and commercial
customers the self-generated part is still in it. Nothing in this module nets the load against generation.

`onsite_generation_by_year_region.csv` gives the generation so it can be handled downstream: one row per year
and EMM region, MWh per year (annual totals, no hourly pattern), columns `year, region_id, region`, then
`<category>_own_use_mwh` and `<category>_sold_mwh` for `residential`, `commercial`, `industrial`, `refinery`
and `transport`, and `annual_loads_source`. Transport is always 0 because NEMS does not provide on-site generation for
transportation. Refinery cogeneration is kept in its own columns. It is not confirmed whether it feeds the
industrial purchased-electricity series. What to subtract from the load, how the arrays are read, and the
limits of this description are in the reference page, section "On-site generation".

The file is written whenever a NEMS restart file is available. With a user load file it is written only if
`nems_outputs_dir` is given as well.

## Rules that hold in this model

- Units are MWh and MW everywhere. NEMS TBtu is converted once, in `nems_aeo_reader.py`, with 292,830 MWh/TBtu.
- The weight of a point is the number of days of that (month, day type) in the calendar year; shapes are
  renormalized so annual energy is conserved.
- Sector load curves carry no T&D losses; the output system curve does.
- No silent fixes: missing years, rows or codes stop the run with a message naming the problem.
- Override data is used as given; no NEMS-style corrections are applied to it.
- The model has no Alaska or Hawaii; their loads are dropped and shown in the input-versus-output table.
