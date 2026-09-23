# C-HSM input data pedigree

Where each file in `input/hsm/` comes from, and which runs read it.

## Summary

`input/hsm/` holds the 37 files C-HSM reads. They come from three places:

| Origin | Files |
|---|---:|
| NEMS input file in both the AEO2025 and the AEO2026 NEMS release | 9 |
| NEMS input file in the AEO2025 NEMS release | 7 |
| NEMS input file in the AEO2026 NEMS release | 11 |
| Built or set for C-HSM (method below) | 10 |
| **Total** | **37** |

"NEMS release" means EIA's public NEMS repository, <https://github.com/EIAgov/NEMS>, at the tags
`AEO2025-Public-Release` (published 15 April 2025) and `AEO2026-Public-Release` (published
8 April 2026), under `models/hsm/input/`. NEMS is released under the Apache License 2.0. The NEMS files are unchanged; C-HSM keeps the onshore ones in one flat
`onshore/` folder where NEMS has subfolders (the table gives the NEMS path).

A reduced-form run (the default) reads 26 of the files. A run with the well-level engine reads 36:
the same files except the reduced form's calibration, plus the engine's calibration, its settings
and nine more NEMS onshore files. Both read the Henry Hub file named in the run config (the basic
config names `henry_hub_path_aeo2026_1987usd.csv`). C-HSM also reads two of C-NGMM's files,
`input/natural_gas/ng_region_data.csv` (the regions) and `input/natural_gas/ng_supply_cost_tiers.csv`
(the base capacity of each cost tier), so the two models share one set.

The rest of the NEMS HSM input folder (119 files: offshore, Alaska, gas processing, history, STEO,
and the onshore files nothing reads yet) is left out. `src/models/hsm/INPUTS_PLAN.md` lists each one
with its NEMS path and release, and says how to add them back if needed in future versions.

`input_sources.csv`, which ships with the C-HSM release rather than in c-nems, has the SHA-256 of
every file here.

## Which NEMS release

The NEMS files C-HSM reads are a mix of the two releases:

- `setup.csv`, `can_setup.csv` and three Canada tables match the AEO2025 release, as do two cost
  tables used only by the engine (`onshore/on_basin_avg_cost.csv`, `onshore/on_drill_cost_eqs.csv`).
  `setup.csv` gives `aeo_year` 2025 and `history_year` 2023 (AEO2026: 2026 and 2024), so the model
  years follow the AEO2025 cycle.
- The onshore project decks, decline rates, `on_region_avg_cost.csv`, `on_tech_levers.csv` and
  `base_oil_prc_by_play.csv` match the AEO2026 release.
- Nine files are the same in both.

Keeping this mix is a deliberate choice. One consequence to check: C-HSM takes the first column of
the AEO2026 project decks to be calendar 2024 (`GP1_CALENDAR_YEAR` in `us_onshore.py`), which fits
the AEO2025 cycle; in the AEO2026 cycle the first projection year is 2025 (`ENGINE_PLAN.md`, step 2). We can update this later.

## Files built or set for C-HSM

| File | How it was made |
|---|---|
| `hh_reference_path.csv` | AEO2026 Henry Hub spot price (`AEO2026Results/sup_ogc.xlsx`, Table 59, nominal $/MMBtu) divided by the GDP deflator in `src/models/hsm/module.py` (1987 = 1.0). The default Henry Hub path, and the reference gas price for the NA/AD split. |
| `henry_hub_path_aeo2026_1987usd.csv` | The same Table 59 price, nominal and in 1987$. The run config uses this one.  |
| `brent_reference_path.csv` | AEO2026 Brent spot price (`AEO2026Results/sup_ogc.xlsx`, Table 57, \$ /gallon x 42 = $/bbl) divided by the same deflator. The default Brent path, and the reference Brent for the NA/AD split. |
| `us_ad_gas_share.csv` | Share of gas that is associated-dissolved, by census division: first-year gas (GP1) of projects with `well_type_number` < 3 in the NEMS producing and CO2-EOR decks, mapped to census divisions with `mapping.csv`. |
| `us_ad_elasticity.csv` | Brent elasticity of associated-dissolved gas by census division: national values from EIA's AEO2025 High and Low Oil Price cases (AEO2026 has none), mixed by each division's tight oil share of AD gas from the NEMS decks. |
| `us_gas_calibration.csv` | The reduced form's `(k, g, c)` calibration by census division, fitted so that US capacity at the AEO2026 reference prices matches C-NGMM's supply anchors (`ng_supply_cost_tiers.csv` x `ng_supply_anchors.csv`, both built from `AEO2026Results/sup_ogc.xlsx`, Tables 59 and 62). Refit it if those C-NGMM files change. |
| `us_gas_calibration_engine.csv` | The same calibration for the well-level engine, fitted with an earlier set of engine settings. Approximate with the shipped settings (about 3% nationally, 13% at worst regionally); refit before using engine results (`ENGINE_PLAN.md`, step 3). |
| `us_onshore_engine.csv` | The engine's settings (discount rate, well caps, royalty and severance, and so on), set for C-HSM (`ENGINE_PLAN.md`). |
| `can_na_reference.csv` | Canada's non-associated production from C-HSM itself, run at the AEO2026 reference Henry Hub path. Used only by a coupled run. |
| `can_us_export_share.csv` | AEO2026 net US pipeline imports from Canada (`AEO2026Results/sup_ogc.xlsx`, Table 61) over Canada's reference production. Used only by a coupled run. |

Each built file also says in its own header where it came from. The scripts that made them are not
part of C-HSM.

## Note for committing these files

`onshore/on_projects_producing_oil.csv` is 2.5 MB, over the 2 MB limit of the
`check-added-large-files` pre-commit check, so it has an exception in
`.pre-commit-config.yaml`. `onshore/on_projects_continuous.csv` (1.99 MB) and
`onshore/on_projects_producing_gas.csv` (1.99 MB) are just under the limit, but if we edit them, we might need to add exceptions for them too.

## All files

| File | Origin | Used by | Source or method |
|---|---|---|---|
| `brent_reference_path.csv` | built for C-HSM | reduced form and engine | AEO2026 Brent (AEO2026Results/sup_ogc.xlsx, Table 57), nominal to 1987 $/bbl |
| `can_na_reference.csv` | built for C-HSM | reduced form and engine | C-HSM Canada NA production at the AEO2026 reference Henry Hub path |
| `can_setup.csv` | NEMS AEO2025 release | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/can_setup.csv |
| `can_us_export_share.csv` | built for C-HSM | reduced form and engine | AEO2026 net pipeline imports from Canada (AEO2026Results/sup_ogc.xlsx, Table 61) over Canada NA reference production |
| `canada/can_benchmark_prices.csv` | NEMS AEO2025 release | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_benchmark_prices.csv |
| `canada/can_elasticity_ad_gas.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_elasticity_ad_gas.csv |
| `canada/can_natgas_baseline_prod.csv` | NEMS AEO2025 release | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_natgas_baseline_prod.csv |
| `canada/can_natgas_dc_vars.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_natgas_dc_vars.csv |
| `canada/can_natgas_no_export_prod.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_natgas_no_export_prod.csv |
| `canada/can_natgas_poly_eqs.csv` | NEMS AEO2025 release | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_natgas_poly_eqs.csv |
| `canada/can_wells.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/canada/can_wells.csv |
| `henry_hub_path_aeo2026_1987usd.csv` | built for C-HSM | reduced form and engine | AEO2026 Henry Hub (AEO2026Results/sup_ogc.xlsx, Table 59), nominal and 1987 $/MMBtu |
| `hh_reference_path.csv` | built for C-HSM | reduced form and engine | AEO2026 Henry Hub (AEO2026Results/sup_ogc.xlsx, Table 59), nominal to 1987 $/MMBtu |
| `mapping.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/mapping.csv |
| `onshore/base_oil_prc_by_play.csv` | NEMS AEO2026 release | engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/configuration/base_oil_prc_by_play.csv |
| `onshore/on_basin_avg_cost.csv` | NEMS AEO2025 release | engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/onshore/on_basin_avg_cost.csv |
| `onshore/on_constraint_params.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/onshore/on_constraint_params.csv |
| `onshore/on_drill_cost_eqs.csv` | NEMS AEO2025 release | engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/onshore/on_drill_cost_eqs.csv |
| `onshore/on_drill_eq_constraints.csv` | NEMS AEO2025 and AEO2026 releases | engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/onshore/on_drill_eq_constraints.csv |
| `onshore/on_dryhole_rate.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/onshore/on_dryhole_rate.csv |
| `onshore/on_process_codes.csv` | NEMS AEO2025 and AEO2026 releases | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/onshore/on_process_codes.csv |
| `onshore/on_producing_gas_decline_rates_play.csv` | NEMS AEO2026 release | engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/decline_rates/on_producing_gas_decline_rates_play.csv |
| `onshore/on_producing_gas_decline_rates_regions.csv` | NEMS AEO2026 release | engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/decline_rates/on_producing_gas_decline_rates_regions.csv |
| `onshore/on_producing_oil_decline_rates_plays.csv` | NEMS AEO2026 release | engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/decline_rates/on_producing_oil_decline_rates_plays.csv |
| `onshore/on_producing_oil_decline_rates_regions.csv` | NEMS AEO2026 release | engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/decline_rates/on_producing_oil_decline_rates_regions.csv |
| `onshore/on_projects_co2_eor.csv` | NEMS AEO2026 release | reduced form and engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/projects/on_projects_co2_eor.csv |
| `onshore/on_projects_continuous.csv` | NEMS AEO2026 release | engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/projects/on_projects_continuous.csv |
| `onshore/on_projects_producing_gas.csv` | NEMS AEO2026 release | reduced form and engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/projects/on_projects_producing_gas.csv |
| `onshore/on_projects_producing_oil.csv` | NEMS AEO2026 release | reduced form and engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/projects/on_projects_producing_oil.csv |
| `onshore/on_region_avg_cost.csv` | NEMS AEO2026 release | reduced form and engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/costs/on_region_avg_cost.csv |
| `onshore/on_tech_levers.csv` | NEMS AEO2026 release | reduced form and engine | EIAgov/NEMS AEO2026-Public-Release, models/hsm/input/onshore/configuration/on_tech_levers.csv |
| `setup.csv` | NEMS AEO2025 release | reduced form and engine | EIAgov/NEMS AEO2025-Public-Release, models/hsm/input/setup.csv |
| `us_ad_elasticity.csv` | built for C-HSM | reduced form and engine | AEO2025 High/Low Oil Price cases, weighted by the NEMS project decks |
| `us_ad_gas_share.csv` | built for C-HSM | reduced form and engine | NEMS onshore project decks (AD = oil well types), by census division |
| `us_gas_calibration.csv` | built for C-HSM | reduced form | fitted (k, g, c): reduced-form capacity at AEO2026 reference prices vs C-NGMM supply anchors |
| `us_gas_calibration_engine.csv` | built for C-HSM | engine | fitted (k, g, c) for the engine, with earlier engine settings; approximate |
| `us_onshore_engine.csv` | built for C-HSM | engine | engine settings, set for C-HSM (ENGINE_PLAN.md) |
