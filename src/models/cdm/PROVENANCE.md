# C-CDM provenance: what came from NEMS, what from AEO results, and what is ours

This report goes through the C-CDM code, every number set in the code, and every input file, and
says where each came from. Paths to C-CDM files are relative to the c-nems repository root, with
line numbers as of this release.

"NEMS" is EIA's National Energy Modeling System, published at <https://github.com/EIAgov/NEMS>
under the Apache License 2.0. Its code and inputs are cited at release tag `AEO2026-Public-Release`:
code by line of `source/comm.f`, inputs by file and line under `input/cdm/`. "AEO2026 results" are
EIA's published Annual Energy Outlook 2026 tables (<https://www.eia.gov/outlooks/aeo/tables_ref.php>; the High Electricity Demand case
through the side-case tables linked there), cited by table, row and series code, for NEMS
runs `cb2026.d021826b` (Counterfactual Baseline) and `higheldmd.d021526a` (High Electricity
Demand). `NOTICE.md` has the attribution.

## 0. Classes used below

| Class | Meaning |
|---|---|
| NEMS code | an equation or rule that follows `comm.f`, cited by line |
| NEMS input | a value read from a NEMS `input/cdm/` file, unchanged |
| AEO2026 result | a value from EIA's published AEO2026 tables |
| Assumption | a modeling choice C-CDM makes where NEMS does something it cannot reproduce |
| Placeholder for now | a value with no source; it stays until one is found |
| Ours | code with no NEMS counterpart (reading, checking, writing) |

## 1. Summary

| Component | Class | In one line |
|---|---|---|
| Server electricity | NEMS code, NEMS input | `comm.f:3667-3679` with `kintens.txt` intensities and the `kmels.txt` server index |
| Data center share and multipliers | NEMS code, NEMS input | `comm.f:3430-3434`, `3572-3720` and the other-uses branches, with `kmels.txt` multipliers |
| Other uses in buildings | NEMS code, NEMS input, two assumptions | `comm.f:3488-3528`, `3729-3745`, `3795-4506`, `7448-7457` |
| Floorspace | AEO2026 result, assumption | Table 22 by building type; 2018 division shares (`kflspc.txt`) |
| Scaled end uses | AEO2026 result, assumption | Table 5 national totals, shared by 2018 intensity, floorspace and multiplier |
| Non-building and CHP rows | AEO2026 result, placeholder for now | remainder of Table 5 other uses, CHP from Table 22; division split placeholder for now |
| Price response | NEMS code, NEMS input, assumption | `comm.f:8922-8953`, weights `comm.f:7807`, `ksdela.txt`; reference path instead of base year |
| Reading, checking, writing | Ours | `data.py`, `postprocessor.py`, `sequencer.py`, `build_inputs.py` |

## 2. Code, file by file

| File | Class | What it does |
|---|---|---|
| `src/models/cdm/commercial.py` | NEMS code and Ours | The equations of `docs/models/cdm.md`; each NEMS rule cites its `comm.f` lines |
| `src/models/cdm/data.py` | Ours | Reads the input tables and checks coverage, ranges and the multiplier pattern |
| `src/models/cdm/cdm_model.py` | Ours | Holds inputs, the reference and current price paths, and results |
| `src/models/cdm/cdm_config.py` | Ours | The `[cdm]` settings |
| `src/models/cdm/sequencer.py` | Ours | Builds, runs and reports the model |
| `src/models/cdm/postprocessor.py` | Ours | Writes the four result tables |
| `src/models/cdm/build_inputs.py` | Ours | Builds `input/cdm/` from the NEMS files and AEO2026 workbooks |

## 3. Every number set in the code, with its origin

"Live" means the number changes results when edited.

| # | Name and value | File:line | Class | Notes |
|---|---|---|---|---|
| 1 | `LAG_WEIGHTS` = 0.50, 0.35, 0.15 | `commercial.py:35` | NEMS code | `comm.f:7807`; live |
| 2 | `BTU_PER_KWH` = 3412 | `commercial.py:36` | NEMS code | `comm.f:12669-12678`; converts results to TWh |
| 3 | `SERVICES_INDEX` = 1 | `commercial.py:40` | Placeholder for now | NEMS reads it from its macroeconomic module (`comm.f:1566`), which has no input file; live in the Counterfactual Baseline only |
| 4 | Data center share coefficients 0.000002, -0.00002, 0.0173, 0.001626 and origin 2019 | `commercial.py:121-122` | NEMS code | `comm.f:3430-3434`; origin from `comm.f:474-481`; live |
| 5 | `OTHER_USES_SCALED` (8 building types) | `commercial.py:53` | NEMS code | other-uses branches that apply the multiplier, `comm.f:3851-3857`, `3917-3923`, `4093-4099`, `4159-4165`, `4225-4231`, `4294-4300`, `4424-4430`, `4492-4498` |
| 6 | `MEL_BUILDINGS` (eligible building types of 20 loads) | `commercial.py:61` | NEMS code | assignments and sums in the select case `comm.f:3795-4506`; shredders not counted in assembly and education (`comm.f:3829-3835`, `3896-3902`) |
| 7 | Off-road vehicle sizing 0.6 and 0.4 | `commercial.py:220` | NEMS code | `comm.f:3502-3504` |
| 8 | Transformer sharing by same-year electricity in other services, all of it to buildings | `commercial.py` `explicit_mels` | Assumption | NEMS uses the previous year's electricity over national purchased electricity, which also counts non-building use and vehicle charging, so part of the load stays outside buildings (`comm.f:3798-3801`, `9302-9303`, `9458-9467`) |
| 9 | Warehouse off-road vehicles, video boards and warehouse robots shared over all floorspace | `commercial.py` `explicit_mels` | Assumption | NEMS uses surviving floorspace (`comm.f:3810`, `4378`, `4397`) |
| 10 | 1000 (billion to million sqft; quadrillion to trillion Btu), 1000 and 1e6 (intensity units to trillion Btu) | `commercial.py:133`, `150`, `170`, `215`, `244`, `264`, `274` | NEMS code | unit conversions as in `comm.f:3488-3528`, `3567-3570` |
| 11 | `SCALED`, `INCREMENT_SERVICES` | `commercial.py:43`, `48` | Ours | which AEO2026 rows exist; which services have a multiplier above 1 |
| 12 | `electricity_price_scale`, `natural_gas_price_scale` = 1 | `cdm_config.py:37-38` | Ours | scenario settings; 1 means reference prices |
| 13 | Result rounding to 6 decimals | `postprocessor.py:41` | Ours | output formatting |
| 14 | Series codes of the AEO2026 rows read | `build_inputs.py:67-105` | AEO2026 result | Tables 3, 5 and 22 |

## 4. Input files, by origin

| File | Class | Source |
|---|---|---|
| `nems_floorspace_2018.csv` | NEMS input | `kflspc.txt`, numeric lines 101-220, summed over vintage cohorts |
| `nems_eui_2018.csv` | NEMS input | `kintens.txt`, lines 101 on |
| `nems_dc_multipliers.csv` | NEMS input | `kmels.txt` and `kmels_higheldmd.txt`, lines 101-111 |
| `nems_mel_share.csv` | NEMS input | same files, line 115 |
| `nems_server_index.csv` | NEMS input | same files, line 119 |
| `nems_mel_base.csv` | NEMS input | `kmels.txt`, lines 126-145 (Btu per sqft; see its header) |
| `nems_mel_index.csv` | NEMS input | `kmels.txt` and `kmels_higheldmd.txt`, lines 149-168 |
| `nems_price_elasticity.csv` | NEMS input | `ksdela.txt`, numeric lines 101-198 |
| `aeo2026_floorspace.csv` | AEO2026 result | supplemental Table 22, Counterfactual Baseline, series `CST000:ca_*`; used for both cases (assumption) |
| `aeo2026_end_use.csv` | AEO2026 result | Table 5, both cases, series `CKI000:*`; CHP natural gas input from supplemental Table 22, `CST000:dge_NaturalGas` (Counterfactual Baseline, used for both cases) |
| `aeo2026_prices.csv` | AEO2026 result | Table 3, commercial natural gas and electricity, both cases, 2025 USD per MMBtu |

`input/cdm/cdm_data_pedigree.md` has the full list with the transformation applied to each.

## 5. What one data center number depends on

Servers in "other" buildings in the South Atlantic in 2050, Counterfactual Baseline (eqs. 1, 2
and 5): the 2018 server intensity of that cell, in thousand Btu per sqft (`kintens.txt`), times
the 2050 "other" floorspace of AEO2026 Table 22 in billion sqft, times 1000 for million sqft,
times the South Atlantic share of 2018 "other" floorspace (`kflspc.txt`), times the 2050 server
index 8.529 (`kmels.txt:119`), divided by 1000 for trillion Btu; then the price factor, which is
1 at the reference prices. The data center increment in the same cell adds the cooling,
ventilation, computers and office equipment, and other-uses increments (eqs. 8 and 15). They
depend in addition on the AEO2026 Table 5 totals of the first three services, on the other-uses
service demand of eq. 12, on the share $d_y$ and on the "other" multipliers 2.9, 1.8, 2.4 and 2.2
(`kmels.txt:111`).

## 6. Open items

| Item | Why it is open | What would close it |
|---|---|---|
| Services index (#3) | no NEMS input file; from NEMS's macroeconomic module | an AEO2026 table of non-manufacturing service output that matches NEMS's definition |
| Division split of the non-building and CHP rows | NEMS uses historical state data and population not in its public inputs | SEDS commercial consumption by state and a population projection |
| High Electricity Demand ramp | NEMS's ramp branches depend on new and surviving floorspace by building type | building-type floorspace by vintage from AEO2026 |
