# The well-level engine: status and implementation plan

`us_onshore.py` holds an optional well-level engine, `OnshoreEngine`, that can replace the reduced
form as C-HSM's US onshore supply. It is kept, it runs, and it is tested, but it is not ready to be
the default. This page says what it is, what the tests and runs show today, and the steps to finish
it, in an order someone else can follow.

## What it does

- **Existing wells.** Production of wells already producing, from the NEMS producing and CO2-EOR
  project decks (`OnshoreLegacy`, the parent class; the reduced form uses it too, for two outputs).
- **New wells.** For each of the 3,181 continuous (unconventional) projects, new wells each year by
  the NEMS drilling rule (`on_next_wells`, `calculate_price_adjustment`, rewritten from NEMS
  `drilling_equations.py`), up to the project's total well count (`totpat`).
- **Which projects drill.** A per-well cash flow test: discounted revenue after royalty and
  severance, less drilling capex (the NEMS cost regression in `on_drill_cost_eqs.csv`), facility
  capex, overhead and operating cost. Only projects with a positive value drill, best first, until
  a national yearly cap on oil-directed and on gas-directed wells runs out.
- **Production.** Each year's new wells follow their project's production profile, improved a little
  for later vintages (fixed yearly rates today; see the known bugs below). Gas from oil well types is associated-dissolved (AD);
  the rest is non-associated (NA). New England has no projects in the decks and stays on the reduced
  form.
- **Capacity.** The engine's gas by region and year is spread over C-NGMM's cost tiers (NA by the
  tiers' base capacity, AD under `medium_cost`) and multiplied by a `(k, g, c)` calibration, as in
  the reduced form.

## How to run it

In `[hsm]` of a run config:

```toml
onshore_engine_file = 'us_onshore_engine.csv'
calibration_file = 'us_gas_calibration_engine.csv'
```

The engine's settings are in `input/hsm/us_onshore_engine.csv`. Tests:
`pixi run pytest tests/hsm/test_hsm_engine.py` runs the fast tests (a few seconds);
`CHSM_ENGINE_FULL_RUN=1 pixi run pytest tests/hsm/test_hsm_engine.py -n0` adds the full 2023–2050
run (about 2 minutes).

## What it shows today

Measured with the shipped inputs and settings, at the AEO2026 reference prices unless stated.

| Check | Result |
|---|---|
| Build | 3,181 continuous projects, about 3 seconds |
| Full run, 2023–2050 | about 130 seconds (the reduced form takes 2); 972 result rows, all finite and non-negative; 2023 about three times too high (known bug 3) |
| Capacity vs C-NGMM's supply anchors, national | +1.3% (2025), −3.0% (2030), +1.9% (2035), +1.4% (2040), −2.1% (2045), +0.8% (2050) |
| Capacity vs anchors, worst region-year | +13% (East North Central, 2030) |
| Henry Hub +10% | national 2040 capacity +9.6%; up in 166 region-years, unchanged in 80, down in 6 (South Atlantic 2036–2041, NA gas, up to 0.35%: the national gas-well cap moves wells to more profitable regions) |
| Henry Hub × 0.4 | national 2040 capacity falls by half (104,486 to 49,298 BCF): drilling stops where it no longer pays |
| Brent +50% | AD share of capacity, 2025–2050: West South Central 0.372 to 0.399, Mountain 0.594 to 0.604, West North Central 0.841 to 0.861 |
| Short runs (2024–2027) | NA gas rises with the gas price (1.0, 3.5, 5.0 \$/MMBtu); AD gas and crude rise with oil (60 to 95 \$/bbl) |
| Crude, million barrels a day | 13.70 (2025), 11.62 (2030), 8.65 (2040), 5.52 (2050). AEO2026 Lower 48 onshore (`AEO2026Results/sup_ogc.xlsx`, Table 58): 11.24, 11.16, 10.44, 10.61. Too high at first, then falling far too fast |

## Where it is simpler than NEMS

- The drilling rules are rewritten from NEMS, not copied, and leave out the branch for undiscovered
  resources. There is no discovery: `on_projects_undiscovered.csv` and `on_discovery_order.csv` are
  not used.
- The cash flow test has no taxes or depreciation (NEMS `cash_flow.py`), uses flat royalty and
  severance rates, and averages operating costs by well type instead of by basin.
- Two national yearly well caps (11,000 oil, 9,000 gas) stand in for NEMS's rig, footage and capital
  constraint equations.
- Drilling cost terms are matched to projects by a keyword map of play names (`PLAY_TO_BASIN`), and
  vertical feet are taken as drill depth.
- The settings in `us_onshore_engine.csv` are set by hand: `ramp_up_years` 5, `drill_predecline`
  0.70, `discount_rate` 0.10, the two caps, `royalty_rate` 0.1875, `severance_rate` 0.06,
  `capex_deflator_1987_to_2023` 2.17.
- The first deck column is taken as calendar 2024 (`GP1_CALENDAR_YEAR`), which fits the AEO2025
  cycle, but the decks come from the AEO2026 release (`input/hsm/hsm_data_pedigree.md`).

## Known bugs

The first two change which projects drill, so fix them before refitting the calibration. The
third affects only the first model year and the shape of the results.

1. **Drilling cost ignores the project's details.** `OnshoreEngine.__init__` keeps only a few columns
   of `on_projects_continuous.csv` (`us_onshore.py:569-573`) and drops `state`, `Latlen` and
   `drill_depth_ft`, which the deck has. The cost regression (`us_onshore.py:669-699`) then uses an
   empty state and zero lateral length and depth for every project, so drilling cost comes from the
   intercept and the basin term alone. Fix: keep the three columns.
2. **New-well technology ignores `on_tech_levers.csv`.** `us_onshore.py:613-632` looks for a
   column whose name contains `rate` or `value`; the file has `tier_1_eur_tech` and others, so the
   fixed rates (1% a year for tight oil and shale, 0.25% for coalbed methane) always apply and the
   file's 2% for shale never does. The failure is caught and logged at debug level only. Fix: read
   `tier_1_eur_tech` by `well_type_number`, as `_load_tech_trend` in `us_gas.py` does, and raise if
   the file cannot be read.
3. **The first model year gets the engine's calibration on the reduced form's capacity.** The
   engine starts in 2024, so in an engine run `USGasModule.compute_capacity` computes 2023 with the
   reduced form's price-driven cost tiers (`us_gas.py:655-671`), but multiplies them by the
   calibration it was given, which in an engine run is `us_gas_calibration_engine.csv`. That file's
   2023 factor is 0.7 to 22 times the reduced form's (East South Central 22×, East North Central
   6.3×, West North Central 3.8×, Middle Atlantic 2.1×), so national capacity in 2023 is 249,605 BCF
   in an engine run against 80,798 in a reduced-form run at the same prices. The same path writes
   only `na` rows, so no region has an `ad` row in 2023; with New England, which gets no `ad` row
   from the engine in any year, that is why an engine run has 972 rows where the reduced form has
   1,008. `test_full_engine_run` compares national totals only for 2025 to 2050 in steps of five and
   pins 972 rows, so it passes with both. Fix: give 2023 the reduced form's own calibration
   (`us_gas_calibration.csv`), or settle the deck calendar (step 2) so the engine covers 2023
   itself; write a zero `ad` row wherever the reduced form writes one; then update the row count in
   `test_full_engine_run`.

## Plan

Each step says what to do, where, and how to know it is done. Steps 1 to 4 make the engine usable;
5 to 8 bring it closer to NEMS; 9 and 10 decide whether it becomes the default.

1. **Fix the known bugs** above. *Done when* the fast engine tests pass and a short test checks that
   drilling cost varies with lateral length and depth, that the shale technology rate is the
   file's value, and that an engine run has 1,008 rows and the same 2023 capacity as a reduced-form
   run (unless step 2 moves the engine to start in 2023).

2. **Check the deck calendar.**
   Find which calendar year the first profile column (`GP1`, `OP1`) is in the AEO2026 decks: read
   how NEMS `onshore.py` (production from the decks) and `setup.csv` (`history_year`) line up, and
   compare national `GP1` gas and `OP1` crude with EIA's actual onshore production for 2024 and 2025.
   Four places assume the answer and must move together: `GP1_CALENDAR_YEAR` (`us_onshore.py:56`),
   the engine's first year in `us_gas.py` (`:658-659`, `:678`), the year new-well technology is
   counted from (`us_onshore.py:828`), and `history_year` and `aeo_year` in `setup.csv`. The input
   vintage (a mix of the AEO2025 and AEO2026 releases) is a settled decision: if the answer means
   changing `setup.csv` or moving it to the AEO2026 release, agree that with the model owner first.
   The reduced form's existing-well outputs move too.
   *Done when* the year is settled with evidence, and `tests/hsm/expected/` is re-captured with a
   note if outputs change.

3. **Refit its calibration.**
   Write a calibration builder in c-nems (for example `src/models/hsm/calibrate.py`, with a pixi
   task). It runs C-HSM at the reference prices, computes C-NGMM's anchors
   (`q0 = sum of cost tier capacity × q0_mult`, from `input/natural_gas/`), fits `(k, g, c)` per
   region by least squares on the log ratio, and writes `us_gas_calibration_engine.csv`. Fit with
   exactly the price files the engine runs will use (the basic config's Henry Hub file,
   `henry_hub_path_aeo2026_1987usd.csv`, and `brent_reference_path.csv`): the cash flow test is a yes
   or no decision, so tiny price differences can flip projects. The same builder, with the engine
   off, should reproduce `us_gas_calibration.csv` to within 1e-6, which checks it.
   *Done when* the national residual is within ±1% every year, the regional table is written next to
   the file, and `test_full_engine_run` can tighten its band from 3.5% to 1.5%.

4. **Make it fast.**
   Each model year calls `simulate` again from 2024 to that year, so the work grows with the square
   of the horizon, and the inner loop reads one project at a time (`self._cont.iloc[i]`). Simulate
   the whole price path once (`module.py` already passes all years to the engine) and reuse it, and
   turn the per-project loop into array operations. *Done when* a full run takes under 20 seconds with
   output identical to before, so the full engine test can run by default.

5. **Full cash flow.**
   Replace the simple per-well value with the NEMS project cash flow (`cash_flow.py`): taxes,
   depreciation, state taxes, using `depreciation_schedules.csv`, `discounting.csv` and
   `state_tax.csv` (see `INPUTS_PLAN.md`). Use operating costs by basin from
   `on_basin_avg_cost.csv` rather than type averages, and replace the hand-set royalty and severance
   with the NEMS values. *Done when* breakeven prices by play are within the range of EIA's published
   assumptions, and the calibration is refitted (step 3).

6. **Drilling constraints.**
   Replace the two national caps with the NEMS rig, footage and capital constraint equations
   (`on_rig_constraint_eq.csv`, `on_footage_constraint_eq.csv`, `on_capital_constraint_eq.csv`,
   `on_wells_per_rig.csv`; see `INPUTS_PLAN.md`). *Done when* total wells a year at reference prices
   are close to EIA's history for the first years, and the South Atlantic effect in the table above is
   either gone or explained by the new constraints.

7. **Undiscovered resources.**
   Add the undiscovered projects and discovery order (`on_projects_undiscovered.csv`,
   `on_discovery_order.csv`) and the undiscovered branch of `on_next_wells` (NEMS
   `drilling_equations.py`, AEO2026 release). *Done when* production after 2040 no longer depends
   only on already known projects.

8. **Crude.**
   Compare the engine's crude with AEO2026 Table 58 (Lower 48 onshore) every year and find why it
   starts high and falls fast (likely the deck calendar, step 2, the missing discovery, step 7, and
   the cash flow, step 5). *Done when* crude is within ±5% of Table 58 at the reference prices, or the
   gap is explained.

9. **Validation before it becomes the default.**
   At reference prices: national capacity within ±1%, crude as in step 8. Off reference: Henry Hub
   ±10% and ×0.4, Brent ±50%, and a price step (does supply ramp over several years rather than jump?).
   Compare the Brent responses with the elasticities in `us_ad_elasticity.csv` (they should be
   within about ±0.15). Record runtime. Keep all of these as tests.

10. **Decide the default.**
    If step 9 passes, switch the basic config to the engine, re-capture `tests/hsm/expected/`, and
    update `README.md`, `EVALUATION.md` and `PROVENANCE.md`. Until then the reduced form stays the
    default.
