# C-HSM provenance: what came from NEMS, what from AEO results, and what is ours

This report goes through the C-HSM code, every number set in the code, and every input file, and says
where each came from. Paths to C-HSM files are relative to the c-nems repository root, with line
numbers as of this release.

"NEMS" is EIA's National Energy Modeling System, published at <https://github.com/EIAgov/NEMS> under
the Apache License 2.0. Its code and inputs are cited by release tag, `AEO2025-Public-Release` or
`AEO2026-Public-Release`, and by path under `models/hsm/`. "AEO2026 results" are EIA's published
supplemental tables, `AEO2026Results/sup_ogc.xlsx`. `NOTICE.md` has the attribution.

## 0. Classes used below

| Class | Meaning |
|---|---|
| **NEMS code** | Python adapted from NEMS `models/hsm/*.py` |
| **NEMS input** | a CSV from NEMS `models/hsm/input/`, EIA's own model input (not a published result), unchanged |
| **NEMS-derived** | a value computed from NEMS inputs (for example a share read from a NEMS deck) |
| **AEO2026 result** | a value from EIA's published AEO2026 supplemental tables |
| **AEO2025 result** | the same for EIA's archived AEO2025 side cases |
| **Fitted** | produced by fitting to AEO2026-derived targets |
| **Model output** | produced by running C-HSM itself |
| **Ours** | written or chosen for C-HSM: hand-set constants, literature values, fallbacks, and code with no NEMS counterpart |

Code similarity is measured on normalised code: comments, docstrings and formatting are removed (the
Python `ast` module parses both files and prints them back), then the lines are compared with
`difflib`. A function scoring 0.995 or more is "identical", 0.8 to 0.995 "near", and below 0.8
"changed". The NEMS-derived files were compared with both releases; they are closest to
`AEO2025-Public-Release`, and the figures below are against it.

## 1. Summary

| Component | Class | In one line |
|---|---|---|
| `names.py` | NEMS code, unchanged | all 736 names identical to AEO2025 (similarity 1.00) |
| `common.py` | NEMS code, nearly unchanged | 5 of 8 functions identical to AEO2025 |
| `submodule.py` | NEMS code, nearly unchanged | 0.92 similar; 3 of 5 members identical, 2 near |
| `canada.py` | NEMS code, changed | 0.83 similar; all 13 members present; the formulas are AEO2025's; the changes replace the restart and pickle files (§2.4) |
| `module.py` | Ours, using NEMS names | does the job of NEMS `module_unf.py` but shares almost no code with it |
| `us_onshore.py` | Ours, reading NEMS decks | reads the NEMS project decks; its drilling rules are rewritten from AEO2026 `drilling_equations.py` and keep its constants |
| `us_gas.py` | Ours | the reduced-form US supply; no NEMS counterpart |
| `hsm_config.py`, `data.py`, `hsm_model.py`, `sequencer.py`, `postprocessor.py` | Ours | the c-nems wrappers: config, input readers, model object, sequencer, result tables |
| `input/hsm/`, 27 files | NEMS input | 9 identical in both releases, 7 only in AEO2025, 11 only in AEO2026 |
| `input/hsm/`, 10 files | Built or set for C-HSM | 3 from AEO2026 results, 1 from AEO2025 results and NEMS decks, 1 from NEMS decks, 2 fitted, 2 from AEO2026 results and C-HSM output, 1 set by hand |
| Numbers inside the code | mixed | §3 lists them |

C-HSM also reads two C-NGMM inputs, `input/natural_gas/ng_region_data.csv` and
`input/natural_gas/ng_supply_cost_tiers.csv`. Their provenance is C-NGMM's, in
`input/natural_gas/ng_data_pedigree.md`.

## 2. Code, file by file

### 2.1 `src/models/hsm/common.py`: NEMS code, nearly unchanged

Counterpart AEO2025 `common.py`. Five of eight functions are identical: `calculate_inflation` (:100),
`assign_ngpls_to_district` (:114), `leg_cost_conversion` (:215), `array_to_df` (:235),
`df_to_array` (:262). `df_interpolate_2` (:141) is near (0.94): a warning gains `stacklevel=2` and a
`pass` after `exit()` is dropped. `read_dataframe` (:48, 0.73) takes the file type from the text after
the *last* dot (:72), where NEMS uses `split('.')[1]` and fails on any path with a dot in a folder
name; it also compares types with `is` and gives its warning `stacklevel=2`. `print_out` (:35) sends
its text to the logger instead of printing it. Two numbers come from NEMS: `to_year = 1987` (:108;
NEMS :88, "hardcoded in NEMS") and 365 days in `leg_cost_conversion` (:229; NEMS :205).

### 2.2 `src/models/hsm/names.py`: NEMS code, unchanged

Counterpart AEO2025 `names.py`. All 736 module-level names are the same, with the same values
(similarity 1.00). The file is reformatted and has a new docstring. No numbers.

### 2.3 `src/models/hsm/submodule.py`: NEMS code, nearly unchanged

Counterpart AEO2025 `submodule.py`. 0.92 similar. `setup` (:53), `run` (:73) and `_load_dataframe`
(:77) are identical; `Submodule` (:27, 0.97) and `__init__` (:38, 0.92) are near: the restart
attribute is `None` and imports point at `src.models.hsm`. No numbers.

### 2.4 `src/models/hsm/canada.py`: NEMS code, changed

Counterpart AEO2025 `canada.py`. 0.83 similar. All 13 members are present. Near: `__init__` (:54,
0.98), `sum_and_merge_production` (:434, 0.95), `wells_setup` (:293, 0.92), `setup` (:98, 0.91),
`write_intermediate_variables` (:512, 0.91), `run` (:179, 0.89), `calculate_na_prod` (:403, 0.89),
`calibrate_ad_gas` (:260, 0.88), `canada_base_prod_setup` (:210, 0.86), `Canada` (:45, 0.84).
Changed: `calculate_wells` (:334, 0.70), `report_results_unf` (:531, 0.49), `prod_profile_setup`
(:301, 0.47). The changes replace the NEMS restart file and pickle files with values kept in memory,
and restructure some loops; they do not change the formulas.

`calculate_wells` in detail: the new-well equation (:362-381) is the AEO2025 formula (NEMS
:441-448): a polynomial in the benchmark Henry Hub price, times the pipeline price benchmark
`can_ng_price_bench`, times `calibration_var ** 0.75`, with `calibration_var` the Henry Hub
benchmark `hh_cal` times the Brent term. The AEO2026 release drops `hh_cal` and
`can_ng_price_bench` from this equation (AEO2026 `canada.py` :447-454). C-HSM rounds the well counts
and sets negative ones to zero once after the loop over regions and gas types (:395-396); NEMS does it
after every step of the loop. The result is the same. All the Henry Hub terms come from fixed inputs
(`canada/can_benchmark_prices.csv` and the fixed Canada pipeline price, row 35), so Canadian
production does not respond to the Henry Hub price C-HSM is given, only to Brent: with the Henry Hub
path doubled, Canadian NA and AD production do not change at all, while US capacity in 2040 rises 37%.

Numbers that are NEMS's own (AEO2025 line numbers): `output_start_year = 1990`,
`model_start_year = 2005`, `output_end_year = 2051` (:71-73; NEMS :110-112); `can_well_v_HH = [0,
0.85]` (:81; NEMS :122), of which only the 0 is used, added to the Henry Hub benchmark (:337; NEMS
:416); the `× 0.5` on the first decline-curve value (:323; NEMS :387); the `+ 0.01` guard in the
decline denominator (:311; NEMS :384); the `× 365` day conversions (:314, :563; NEMS :382, :698); the
`0.96 / deflator[2016]` price adjustment (:351; NEMS :428); and the 0.75 exponent (:381; NEMS :443,
:448).

### 2.5 `src/models/hsm/module.py`: ours, using NEMS names

`HSMModule` (:112) does the job of NEMS `module_unf.py`, the HSM driver, and keeps its variable names
(`rest_henry_hub`, `ogsmout_ogcnpprd`, `rest_mc_jpgdp`), but shares almost no code with it. Its
methods, `__init__` (:142), `_build_price_dfs` (:324), `_load_hh_reference` (:360),
`_load_brent_reference` (:379), `update_prices` (:399), `reset_canada` (:413), `reset_us_gas` (:429),
`setup_canada` (:434) and `run_year` (:439), are ours. It reads the NEMS `setup.csv` (:175) for its
years, builds the NEMS-derived `Canada` submodule (:223), the optional engine when a settings file is
given (:236), and our `USGasModule` (:245). Its numbers are in §3.

### 2.6 `src/models/hsm/us_onshore.py`: ours, reading NEMS decks

`OnshoreLegacy` (:72) reads the NEMS producing and CO2-EOR decks (`PRODUCING_DECKS`, :50) with
`on_process_codes.csv` and `mapping.csv` and adds up the production of wells already producing. The
deck format (`GP1..GP40` and `OP1..OP40`, `PROFILE_YEARS = 40`, :55) and the AD rule
(`AD_WELL_TYPES_BELOW = 3`, :57) are NEMS conventions. `OnshoreEngine` (:511) is the optional
well-level engine, with its settings read and checked by `read_engine_settings` (:475);
`ENGINE_PLAN.md` has its status, three known bugs and the steps still to do.

The three drilling functions are rewritten from AEO2026 `drilling_equations.py` (the AEO2025 release
does not have two of them) and keep its constants: `calculate_price_adjustment` (:250; NEMS
:388-530), `_base_well_count` (:321; from `_calculate_base_well_count`, NEMS :65-167) and
`on_next_wells` (:358; NEMS :170-385, without the undiscovered-resource branch). The code is rewritten
(similarity 0.04 to 0.38), but the constants in `calculate_price_adjustment` (:272-281) are NEMS's
(row 40). Only the engine uses them.

### 2.7 `src/models/hsm/us_gas.py`: ours

No NEMS counterpart. `USGasModule` (:423): the three-tier check (:481); base capacity per region,
cost tier and gas type from C-NGMM's cost tiers (:545); the price response `_price_driven_cost_tiers`
(:567), `capacity = base × (p / 2.50)^ε × (1 + trend)^(y − 2023)`, with depletion on the low-cost
tier floored at 0.50 (:586); `compute_capacity` (:590), with the engine branch (:655), the NA/AD split
(:745) and the older oil add-on used only without the split (:784); the `(k, g, c)` calibration
applied at :761 (split on, the shipped case) and :803 (split off), read by `_load_calibration` (:330),
which raises on a file it cannot use.

### 2.8 The c-nems wrappers: ours

`hsm_config.py` (the `[hsm]` settings), `data.py` (the Henry Hub and Brent readers, and the regions
and cost tiers read with C-NGMM's own loaders), `hsm_model.py` (the model object), `sequencer.py`
(build, run, write results; `pixi run hsm`) and `postprocessor.py` (the six result tables) were
written for C-HSM on the c-nems pattern.

## 3. Every number set in the code, with its origin

"Live" means the default reduced-form run uses the value. Several constants are fallbacks that the
shipped inputs replace.

| # | Name and value | File:line | Class | Notes |
|---|---|---|---|---|
| 1 | `SUPPLY_ELASTICITY` conventional 0.30, tight 0.55, shale 0.65, cbm 0.25 | `us_gas.py:67` | **Ours (literature)** | Newell et al. (2016). We should verify and update as needed. |
| 2 | `OIL_ASSOCIATED_GAS_ELASTICITY` WSC 0.35, Mountain 0.20, WNC 0.15, … | `us_gas.py:77` | **Ours** (by hand) | Not used when the NA/AD split is on, which it is with the shipped inputs. |
| 3 | `BASE_WELLHEAD_PRICE_PER_MMBTU = 2.50` | `us_gas.py:89` | **Ours** | "2023 US average wellhead price". The denominator of every gas price ratio. Live. |
| 4 | `BASE_YEAR = 2023` | `us_gas.py:90` | Ours | Start of the technology and depletion clocks. Live. |
| 5 | `BASE_OIL_PRICE_PER_BBL = 65.0` | `us_gas.py:91` | **Ours** | "2023 WTI approximate". Used when there is no Brent price or it is below the $20 check (row 21). |
| 6 | `_OIL_ASSOC_COST_TIER_FRACTION_FALLBACK = 0.15` | `us_gas.py:96` | NEMS-derived (a copy of the deck value) | Live value read from NEMS `on_constraint_params.csv`, `share_ratio` (:372); reading `share_ratio` as an oil-associated share is ours. Not used when the split is on. |
| 7 | `_TECH_TREND_FALLBACK` conventional 0.0025, tight 0.010, shale 0.010, cbm 0.0025 | `us_gas.py:104-109` | NEMS-derived copies, **except shale**: the fallback is 0.010, the shipped deck has 0.02 | Live values read from `on_tech_levers.csv`, `tier_1_eur_tech` (:182), so 0.02 is what runs for shale. Using a per-well recovery trend as a capacity trend is ours. |
| 8 | `_COST_TIER_TYPE_SHARES_FALLBACK` low: conventional 0.55 / cbm 0.45; medium: tight **0.40** / shale **0.60**; high: conventional **0.20** / shale **0.80** | `us_gas.py:113-120` | **Split**: the low-cost pair is recomputed from `on_region_avg_cost.csv` (:237) by a rule of ours; **the medium and high-cost shares are ours and live** (:116-119) | Four of the six live shares are set by hand. |
| 9 | `_CONVENTIONAL_DEPLETION_RATE_FALLBACK`, 0.005–0.020 by region | `us_gas.py:123` | Ours (fallback) | Live values from NEMS `on_dryhole_rate.csv` and `mapping.csv`, **rescaled to 0.005–0.020 per year** (:267): NEMS-derived ranking, our range. |
| 10 | `_CENSUS_TO_REGION` 1 → new_england … 9 → pacific | `us_gas.py:140` | NEMS-derived | from `mapping.csv`, `census_division` |
| 11 | `_WELL_TYPE_TO_GAS` 3 conventional, 4 tight, 5 shale, 6 cbm | `us_gas.py:153` | NEMS-derived | from `on_process_codes.csv` |
| 12 | `CAL_ANCHOR_YEAR = 2025` | `us_gas.py:327` | Ours | The year the calibration is normalised to. |
| 13 | depletion floor 0.50 | `us_gas.py:586` | Ours | Keeps conventional supply from falling below half. |
| 14 | gas price floor 0.01 $/MMBtu | `us_gas.py:728, :747` (and :664, :714 in the engine) | Ours | Guards the power law, actual and reference prices. |
| 15 | engine first year 2024 | `us_gas.py:658-659, :678` | Ours (engine only) | GP1 = 2024, see `us_onshore.py:56`. |
| 16 | calibration form `k (1 + g)^x exp(c x²)`, x = year − 2025 | `us_gas.py:761` (split on), `:803` (split off); `:669, :694, :719` (engine) | Ours (form); **Fitted** (values) | Values from `us_gas_calibration.csv`, or `us_gas_calibration_engine.csv` with the engine (`calibration_file`, `module.py:251`). |
| 17 | `_GDP_DEFLATOR`, 1987 = 1.000 … 2050 = 3.326 | `module.py:44` | **Ours** (entered by hand) | "Approximate BEA values". Not from NEMS. |
| 18 | `REGIONAL_BASIS` NE +0.80, MA +0.20, ENC +0.10, WNC 0, SA −0.10, ESC −0.20, WSC −0.50, Mtn −0.30, Pac +0.15 | `module.py:458` | **Ours** (by hand) | Regional wellhead price differentials, $/MMBtu, added to Henry Hub. The calibration fit used the same values. Live, every year. |
| 19 | Henry Hub fallback 3.5 (1987 $/MMBtu) | `module.py:352, :356, :455` | Ours | For a missing year. `HSMModel` rejects a price path, or a reference path, that misses a year, so this is not reached in a C-HSM run. |
| 20 | Brent fallback 4.0 (1987 $/bbl) | `module.py:333, :337, :497` | Ours | As row 19. |
| 21 | Brent check: at least 20 (2023 $/bbl) | `module.py:489, :502, :522` | Ours | Below it the US supply drops the Brent price and uses row 5; Canada uses Brent as given. |
| 22 | 1987 \$ to 2023 \$ with the fixed 2023 deflator, `× _GDP_DEFLATOR[2023]` | `module.py:474, :484, :488, :499, :515, :521` | Ours | Turns the 1987 \$ inputs into real 2023 \$ before rows 3 and 18 apply. |
| 23 | `brent_multiplier` default 1.0 | `module.py:150` | Ours | Oil price scenario setting; also in `hsm_config.py`. |
| 24 | engine switch: `onshore_engine_file` in the config | `module.py:236`, `hsm_config.py:51` | Ours | Off by default. |
| 25 | `PRODUCING_DECKS`, three NEMS file names | `us_onshore.py:50` | NEMS input names | |
| 26 | `PROFILE_YEARS = 40` | `us_onshore.py:55` | NEMS convention | The GP1..GP40 and OP1..OP40 deck columns. |
| 27 | `GP1_CALENDAR_YEAR = 2024` | `us_onshore.py:56` | Ours (assumption) | Profile year 1 is calendar 2024; to be checked against the AEO2026 decks (`ENGINE_PLAN.md`, step 2). |
| 28 | `AD_WELL_TYPES_BELOW = 3` | `us_onshore.py:57` | NEMS convention | The rule NEMS uses for its AD gas output. |
| 29 | `CENSUS_TO_REGION` | `us_onshore.py:59` | NEMS-derived | as row 10 |
| 30 | `PLAY_TO_BASIN`, `WELL_TYPE_MERGE` | `us_onshore.py:434, :454` | Ours (engine only) | Keyword maps for the drilling cost regression. |
| 31 | `MMBTU_PER_MMCF = 1037.0`, `MCF_PER_BOE = 5.6` | `us_onshore.py:455-456` | Physical constants | |
| 32 | Canada years 1990, 2005, 2051 | `canada.py:71-73` | NEMS code | AEO2025 `canada.py:110-112` |
| 33 | Canada `× 0.5` first decline value; `+ 0.01` guard; `× 365`; `0.96 / deflator[2016]`; `** 0.75` on `calibration_var`; `[0, 0.85]` `can_well_v_HH` | `canada.py:323, :311, :314, :351, :381, :81` | **NEMS code** | AEO2025 `canada.py:387, :384, :382, :428, :443/:448, :122` |
| 34 | Canada well count `× hh_cal` (inside `calibration_var`) and `× can_ng_price_bench` | `canada.py:362-381` | **NEMS code** (AEO2025) | AEO2025 `canada.py:441-448`; dropped in AEO2026. Fixed inputs, so Canada does not respond to Henry Hub (§2.4). |
| 35 | Canada pipeline price 4.0 for both regions, every year (`ogsmout_ogcnpprd`) | `module.py:208` | Ours | A fixed stand-in for a price NEMS computes; enters `calculate_wells` through row 34. |
| 36 | deflator fallback 1.0 for a year outside `_GDP_DEFLATOR` | `module.py:213` | Ours | |
| 37 | depletion rate 0.010 per year for a region missing from the loaded table | `us_gas.py:584` | Ours | |
| 38 | oil price floor 1.0 $/bbl, actual and reference | `us_gas.py:732, :750` | Ours | |
| 39 | AD share clamped to [0, 1]; a region with no Brent elasticity gets (0, 0) | `us_gas.py:754, :755` | Ours | A region missing from `us_ad_elasticity.csv` gets no Brent response. |
| 40 | drilling constants: `MAX_PRICE_ADJUSTMENT 2.0`, `MAX_GAS_ADJ_RATIO 1.5`, `GAS_TO_OIL_CONVERSION 5600`, `HIGH_GAS_OIL_RATIO_THRESHOLD 6000`, `DAMP_EXP 0.5`, bounds 0.98 and 1.02, `LOW_PRICE_FLAG_MULT 1.25` | `us_onshore.py:272-281` | **NEMS code** | from AEO2026 `drilling_equations.py:423-442`. Engine only. |
| 41 | engine settings: base gas price 2.50, ramp-up 5 years, pre-decline share 0.70, low price flag 0, discount rate 0.10, well caps 11,000 oil and 9,000 gas a year, cap growth 0, capex deflator 2.17, royalty 0.1875, severance 0.06, decline overrides on | `input/hsm/us_onshore_engine.csv`, read at `us_onshore.py:544-547, :604-610, :713-717` | Ours (engine only, set by hand) | Every setting must be in the file (`read_engine_settings`). |
| 42 | engine oil price default 50.0 $/bbl for a play with no base price | `us_onshore.py:564` | Ours (engine only) | |
| 43 | engine cost fill-ins: log-cost intercept 13.0; opex 3.0; facility capex 3e5; overhead 5e5 | `us_onshore.py:673, :704, :707-708` | Ours (engine only) | Used where a deck row has no value. |
| 44 | new-well technology rates 0.01 (tight oil, shale), 0.0025 (coalbed methane) | `us_onshore.py:613` | Ours (engine only) | Always used today: a known bug stops the engine reading `on_tech_levers.csv` (`ENGINE_PLAN.md`). |
| 45 | `to_year = 1987`; 365 days | `common.py:108, :229` | NEMS code | AEO2025 `common.py:88, :205` |

## 4. Input files, by origin

The full per-file table is `input/hsm/hsm_data_pedigree.md`, and the SHA-256 of every file is in
`input_sources.csv`.

### 4.1 NEMS inputs: 27 files

Each is the file at `models/hsm/input/` in the NEMS repository (NEMS keeps some onshore
files in subfolders; the pedigree gives each path):

- **9** are the same in both releases (for example `mapping.csv`, `onshore/on_process_codes.csv`).
- **7** match only `AEO2025-Public-Release`: `setup.csv`, `can_setup.csv`, three Canada tables, and
  two engine cost tables. `setup.csv` gives `aeo_year` 2025 and `history_year` 2023, so the model
  years follow the AEO2025 cycle.
- **11** match only `AEO2026-Public-Release`: the onshore project decks and decline rates,
  `on_region_avg_cost.csv`, `on_tech_levers.csv` and `base_oil_prc_by_play.csv`.

The other 119 files of the NEMS HSM input folder are not shipped because nothing reads them;
`INPUTS_PLAN.md` lists each with its release and says how to add them back.

### 4.2 Built or set for C-HSM: 10 files

The scripts that built these files are not part of C-HSM; the method is given here so each file can
be rebuilt. We should update these as we advance the model as necessary, think of these as placeholders.

| File | How it was made | Class of the numbers |
|---|---|---|
| `hh_reference_path.csv` | `AEO2026Results/sup_ogc.xlsx`, Table 59, "Henry Hub Spot Price" (nominal $/MMBtu), divided by `_GDP_DEFLATOR` (row 17) | **AEO2026 result**, our deflator |
| `henry_hub_path_aeo2026_1987usd.csv` | the same Table 59 series, the same conversion; the path the run config uses | AEO2026 result |
| `brent_reference_path.csv` | Table 57, "Brent Spot Price" in $/gallon, × 42, divided by the deflator | AEO2026 result |
| `us_ad_gas_share.csv` | NEMS `on_process_codes.csv`, the producing and CO2-EOR decks and `mapping.csv`: share of first-year gas from projects with `well_type_number` < 3 | **NEMS-derived** |
| `us_ad_elasticity.csv` | EIA AEO2025 High and Low Oil Price side cases (Table 12, Brent; Table 14, crude by type), 2030–2050 mean response, mixed by the NEMS decks' tight oil share | AEO2025 result + NEMS-derived |
| `us_gas_calibration.csv` | `(k, g, c)` fitted by region so that reduced-form capacity at `hh_reference_path.csv` prices, with `REGIONAL_BASIS`, matches `Q0 = Σ cost tier capacity × q0_mult` from C-NGMM's `ng_supply_cost_tiers.csv` and `ng_supply_anchors.csv` | **Fitted** |
| `us_gas_calibration_engine.csv` | the same fit with the engine on, made with an earlier set of engine settings; approximate with the shipped settings | **Fitted** |
| `us_onshore_engine.csv` | the engine settings (row 41) | Ours |
| `can_na_reference.csv` | C-HSM's own Canada NA production at the reference Henry Hub path | **Model output** |
| `can_us_export_share.csv` | Table 61 net pipeline imports from Canada (imports − exports; missing exports taken as 0; a missing year takes the earliest), divided by **western Canada** NA reference production only, clipped to [0, 1] | AEO2026 result ÷ model output, with three rules of ours |

C-NGMM's `q0` on c-nems `main` equals all 54 targets the reduced-form calibration was fitted to
(checked to the file's 0.1 BCF precision), so that calibration carries over to c-nems unchanged. It
has to be refitted if C-NGMM's cost tiers or anchors change.

## 5. What one capacity number depends on

Following one number in `hsm_us_gas_capacity.csv` back through `us_gas.py` (reduced form):

1. **Base capacity**: C-NGMM's `ng_supply_cost_tiers.csv` (AEO2026, with C-NGMM's own rules) split by
   gas type (row 8) (:544).
2. **× price response** `(p / 2.50)^ε`: `p` is Henry Hub (AEO2026 Table 59, our deflator) plus our
   regional basis; 2.50 and ε are ours (:566-582).
3. **× technology trend**: NEMS `on_tech_levers.csv` (:181).
4. **× depletion**, low-cost tier only: NEMS dry-hole rates rescaled to our range, our 0.50 floor
   (:266, :585).
5. **× (1 − AD share)** for NA gas: NEMS-derived `us_ad_gas_share.csv` (:764).
6. **AD gas × f_AD**: AEO2025-derived and NEMS-derived `us_ad_elasticity.csv`, with Brent from
   AEO2026 Table 57 (:756, :769).
7. **× calibration** `k (1 + g)^x exp(c x²)`, fitted to AEO2026-derived targets (:760).

So the *level* comes from AEO2026, through C-NGMM's anchors and the fit; the *price response* is ours
(elasticities, basis, base prices, floors), shaped by NEMS inputs (trends, shares, dry-hole rates);
and *Canada* is NEMS's AEO2025 Canada code, responding to Brent only.

## 6. Open items

| Item | Why it is open | What would close it |
|---|---|---|
| The deck calendar: the AEO2026 onshore decks are read with the AEO2025 model years (§4.1; the mix of releases itself is kept on purpose) | `GP1` may be calendar 2025, not 2024, in the AEO2026 cycle | `ENGINE_PLAN.md`, step 2 |
| The fixed Canada pipeline price 4.0 (row 35) | stand-in for a NEMS-computed price; with the AEO2025 formula it sets the level of Canadian drilling | a source for the value, or a computed price |
| `_GDP_DEFLATOR` values | entered by hand, "approximate BEA" | replace with a BEA series file and cite its vintage |
| The 2.50 \$/MMBtu and 65 \$/bbl base prices | set by hand | cite the EIA series and year they stand for |
| `REGIONAL_BASIS` | set by hand | cite a basis differential source, or fit it |
| Medium and high-cost gas type shares (row 8) | set by hand | a NEMS or literature source |
| The engine's settings and fill-ins (rows 41-44) | set by hand | `ENGINE_PLAN.md`, steps 4 to 6 |
