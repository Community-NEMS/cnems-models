# Adding the NEMS inputs C-HSM does not use yet

C-HSM ships only the input files it reads: 37 files in `input/hsm/`, listed in
`input/hsm/hsm_data_pedigree.md`. The other 119 files of the NEMS HSM input folder were left out,
because no C-HSM code reads them yet. This page says where each one is and how to add a group back
when the code that reads it is written.

## Where the files are

Every left-out file is byte for byte a file in EIA's public NEMS repository,
<https://github.com/EIAgov/NEMS>, under `models/hsm/input/`, at the release tag given in the table
at the end:

| Release tag | Files |
|---|---:|
| `AEO2025-Public-Release` | 114 (50 of them unchanged in `AEO2026-Public-Release`) |
| `AEO2026-Public-Release` only | 5 |

NEMS is released under the Apache License 2.0 (see `NOTICE.md` in this folder). Keep the files as
EIA published them, and record the tag you took them from.

The NEMS inputs C-HSM already uses are a mix (`input/hsm/hsm_data_pedigree.md`): the onshore project
decks, decline rates and cost tables match the AEO2026 release, while `setup.csv`, the Canada files
and two engine cost files match the AEO2025 release. For a left-out group, take the release given in
the table unless you are moving that part of C-HSM to AEO2026, and then refit. NEMS keeps some onshore files in
subfolders (`onshore/projects/`, `onshore/configuration/`, `onshore/decline_rates/`); C-HSM keeps
them flat in `input/hsm/onshore/`. The table gives the NEMS path. For example:

```bash
TAG=AEO2025-Public-Release
curl -fsSL -o input/hsm/off_setup.csv \
  https://raw.githubusercontent.com/EIAgov/NEMS/$TAG/models/hsm/input/off_setup.csv
```

## What each group is for

| Group | Files | Read in NEMS by | What adding it involves |
|---|---:|---|---|
| Offshore: `offshore/`, `off_setup.csv` | 33 | `offshore.py` | Port the offshore submodule on the pattern of `canada.py` (a `Submodule` subclass run from `module.py`). Decide which C-NGMM regions offshore production belongs to. Offshore gas is inside the reduced form's calibration today, so the calibration must be refitted. |
| Alaska: `alaska/`, `ak_setup.csv` | 9 | `alaska.py` | As offshore. Alaska sits in C-NGMM's `pacific` region. |
| Gas processing: `ngp/`, `ngp_setup.csv` | 4 | `ngp.py`, `ngp_cash_flow.py` | Only needed for natural gas plant liquids; not for gas capacity. |
| History: `history/`, `hist_setup.csv` | 33 | `history_steo.py` | Historical production and wells, used to overwrite the model years up to the last history year. |
| STEO: `steo/`, `steo_setup.csv` | 16 | `history_steo.py` | Near-term benchmarks to EIA's Short-Term Energy Outlook. |
| Onshore, the rest: 19 files in `onshore/`, `on_setup.csv` | 20 | `onshore.py` | Undiscovered projects and discovery order, rig, footage and capital constraint equations, wells per rig, EOR and CO2 costs, NGPL costs, methane emission factors, play maps, override switches. Most belong to the engine's later steps (`ENGINE_PLAN.md`, steps 6 and 7). |
| Project cash flow: `depreciation_schedules.csv`, `discounting.csv`, `state_tax.csv` | 3 | `cash_flow.py`, `onshore.py`, `offshore.py`, `alaska.py` | The full NEMS project cash flow (taxes, depreciation). Belongs to the engine's cash flow step (`ENGINE_PLAN.md`, step 5). |
| `canada/canada_setup.csv` | 1 | nothing in NEMS | Not needed; C-HSM reads `can_setup.csv`. |

## Steps for adding a group

1. **Write or port the code that reads the group first.** Do not add input files that nothing reads.
2. **Fetch the files** from the release tag in the table, keep their NEMS names, and put them under
   `input/hsm/` (flat for onshore, as the other onshore files are).
3. **Check them against the pre-commit hooks.** Files over 2 MB need an exception or Git LFS. The
   whitespace hooks rewrite trailing spaces and add a final newline: five of the left-out files have
   trailing spaces in a header line (`offshore/off_dev_cost.csv`,
   `offshore/off_platform_abandonment.csv`, `onshore/on_co2_pipe_seg_costs.csv`,
   `onshore/on_future_wells_configuration.csv`, `onshore/shale_gas_play_map.csv`) and three have no
   final newline (`ak_setup.csv`, `hist_setup.csv`, `ngp_setup.csv`). Make sure the new code does not
   depend on a trailing space in a column name.
4. **Record them**: add rows to `input/hsm/hsm_data_pedigree.md` and to the release's
   `input_sources.csv` with the SHA-256, the NEMS path and the tag.
5. **Record what a run reads.** Run C-HSM once with every `pandas.read_csv` call logged, and update
   the "read by C-HSM" column of the pedigree.
6. **Refit the calibration** (`us_gas_calibration.csv`, and `us_gas_calibration_engine.csv` if the
   engine is affected) against C-NGMM's supply anchors whenever US supply changes, and re-capture
   `tests/hsm/expected/` with a note saying why.
7. **Add tests** for the new submodule on the pattern of `tests/hsm/`.

## The left-out files

| File in C-HSM layout | NEMS path | Release tag | Also unchanged in AEO2026 |
|---|---|---|---|
| `ak_setup.csv` | `models/hsm/input/ak_setup.csv` | AEO2025-Public-Release | no |
| `alaska/ak_dev_drill_schedule.csv` | `models/hsm/input/alaska/ak_dev_drill_schedule.csv` | AEO2025-Public-Release | yes |
| `alaska/ak_exp_drill_schedule.csv` | `models/hsm/input/alaska/ak_exp_drill_schedule.csv` | AEO2025-Public-Release | yes |
| `alaska/ak_facility_costs.csv` | `models/hsm/input/alaska/ak_facility_costs.csv` | AEO2025-Public-Release | yes |
| `alaska/ak_field_size_classes.csv` | `models/hsm/input/alaska/ak_field_size_classes.csv` | AEO2025-Public-Release | yes |
| `alaska/ak_mapping.csv` | `models/hsm/input/alaska/ak_mapping.csv` | AEO2025-Public-Release | yes |
| `alaska/ak_opex.csv` | `models/hsm/input/alaska/ak_opex.csv` | AEO2025-Public-Release | yes |
| `alaska/ak_projects_known.csv` | `models/hsm/input/alaska/ak_projects_known.csv` | AEO2025-Public-Release | no |
| `alaska/ak_projects_undiscovered.csv` | `models/hsm/input/alaska/ak_projects_undiscovered.csv` | AEO2025-Public-Release | yes |
| `canada/canada_setup.csv` | `models/hsm/input/canada/canada_setup.csv` | AEO2025-Public-Release | no |
| `depreciation_schedules.csv` | `models/hsm/input/depreciation_schedules.csv` | AEO2025-Public-Release | yes |
| `discounting.csv` | `models/hsm/input/discounting.csv` | AEO2025-Public-Release | yes |
| `hist_setup.csv` | `models/hsm/input/hist_setup.csv` | AEO2025-Public-Release | no |
| `history/hist_EPL2.csv` | `models/hsm/input/history/hist_EPL2.csv` | AEO2025-Public-Release | no |
| `history/hist_EPLLBAI.csv` | `models/hsm/input/history/hist_EPLLBAI.csv` | AEO2025-Public-Release | no |
| `history/hist_EPLLBAN.csv` | `models/hsm/input/history/hist_EPLLBAN.csv` | AEO2025-Public-Release | no |
| `history/hist_EPLLEA.csv` | `models/hsm/input/history/hist_EPLLEA.csv` | AEO2025-Public-Release | no |
| `history/hist_EPLLPA.csv` | `models/hsm/input/history/hist_EPLLPA.csv` | AEO2025-Public-Release | no |
| `history/hist_EPLP.csv` | `models/hsm/input/history/hist_EPLP.csv` | AEO2025-Public-Release | no |
| `history/hist_crude_lfmm.csv` | `models/hsm/input/history/hist_crude_lfmm.csv` | AEO2025-Public-Release | no |
| `history/hist_dcrdwhp.csv` | `models/hsm/input/history/hist_dcrdwhp.csv` | AEO2025-Public-Release | no |
| `history/hist_eor_prod.csv` | `models/hsm/input/history/hist_eor_prod.csv` | AEO2025-Public-Release | no |
| `history/hist_hcadgprd.csv` | `models/hsm/input/history/hist_hcadgprd.csv` | AEO2025-Public-Release | no |
| `history/hist_hcbmprd.csv` | `models/hsm/input/history/hist_hcbmprd.csv` | AEO2025-Public-Release | no |
| `history/hist_hcbmwells.csv` | `models/hsm/input/history/hist_hcbmwells.csv` | AEO2025-Public-Release | no |
| `history/hist_hcgasprd.csv` | `models/hsm/input/history/hist_hcgasprd.csv` | AEO2025-Public-Release | no |
| `history/hist_hcgwells.csv` | `models/hsm/input/history/hist_hcgwells.csv` | AEO2025-Public-Release | no |
| `history/hist_hcoilprd.csv` | `models/hsm/input/history/hist_hcoilprd.csv` | AEO2025-Public-Release | no |
| `history/hist_hcowells.csv` | `models/hsm/input/history/hist_hcowells.csv` | AEO2025-Public-Release | no |
| `history/hist_hdryholes.csv` | `models/hsm/input/history/hist_hdryholes.csv` | AEO2025-Public-Release | no |
| `history/hist_hgasres.csv` | `models/hsm/input/history/hist_hgasres.csv` | AEO2025-Public-Release | no |
| `history/hist_hoilres.csv` | `models/hsm/input/history/hist_hoilres.csv` | AEO2025-Public-Release | no |
| `history/hist_hsgasprd.csv` | `models/hsm/input/history/hist_hsgasprd.csv` | AEO2025-Public-Release | no |
| `history/hist_hsgwells.csv` | `models/hsm/input/history/hist_hsgwells.csv` | AEO2025-Public-Release | no |
| `history/hist_htadgprd.csv` | `models/hsm/input/history/hist_htadgprd.csv` | AEO2025-Public-Release | no |
| `history/hist_htgasprd.csv` | `models/hsm/input/history/hist_htgasprd.csv` | AEO2025-Public-Release | no |
| `history/hist_htgwells.csv` | `models/hsm/input/history/hist_htgwells.csv` | AEO2025-Public-Release | no |
| `history/hist_htoilprd.csv` | `models/hsm/input/history/hist_htoilprd.csv` | AEO2025-Public-Release | no |
| `history/hist_htowells.csv` | `models/hsm/input/history/hist_htowells.csv` | AEO2025-Public-Release | no |
| `history/hist_lfmm_prod.csv` | `models/hsm/input/history/hist_lfmm_prod.csv` | AEO2025-Public-Release | no |
| `history/hist_ngplprd.csv` | `models/hsm/input/history/hist_ngplprd.csv` | AEO2025-Public-Release | no |
| `history/hist_ognowell.csv` | `models/hsm/input/history/hist_ognowell.csv` | AEO2025-Public-Release | yes |
| `history/hist_ogqshlgas.csv` | `models/hsm/input/history/hist_ogqshlgas.csv` | AEO2025-Public-Release | no |
| `history/hist_ogqshloil.csv` | `models/hsm/input/history/hist_ogqshloil.csv` | AEO2025-Public-Release | no |
| `history/hist_rfqtdcrd.csv` | `models/hsm/input/history/hist_rfqtdcrd.csv` | AEO2025-Public-Release | no |
| `ngp/netl_natural_gas_calculations.csv` | `models/hsm/input/ngp/netl_natural_gas_calculations.csv` | AEO2025-Public-Release | yes |
| `ngp/netl_purchased_power_cost.csv` | `models/hsm/input/ngp/netl_purchased_power_cost.csv` | AEO2025-Public-Release | yes |
| `ngp/ngp_facility_input.csv` | `models/hsm/input/ngp/ngp_facility_input.csv` | AEO2025-Public-Release | yes |
| `ngp_setup.csv` | `models/hsm/input/ngp_setup.csv` | AEO2025-Public-Release | no |
| `off_setup.csv` | `models/hsm/input/off_setup.csv` | AEO2025-Public-Release | no |
| `offshore/off_1990_fields.csv` | `models/hsm/input/offshore/off_1990_fields.csv` | AEO2025-Public-Release | yes |
| `offshore/off_announced_fields.csv` | `models/hsm/input/offshore/off_announced_fields.csv` | AEO2025-Public-Release | yes |
| `offshore/off_cumulative_nfws.csv` | `models/hsm/input/offshore/off_cumulative_nfws.csv` | AEO2025-Public-Release | no |
| `offshore/off_delays.csv` | `models/hsm/input/offshore/off_delays.csv` | AEO2025-Public-Release | yes |
| `offshore/off_delineation_wells.csv` | `models/hsm/input/offshore/off_delineation_wells.csv` | AEO2025-Public-Release | yes |
| `offshore/off_dev_cost.csv` | `models/hsm/input/offshore/off_dev_cost.csv` | AEO2025-Public-Release | yes |
| `offshore/off_development_wells.csv` | `models/hsm/input/offshore/off_development_wells.csv` | AEO2025-Public-Release | yes |
| `offshore/off_discovery_coefficients.csv` | `models/hsm/input/offshore/off_discovery_coefficients.csv` | AEO2025-Public-Release | yes |
| `offshore/off_drill_depth.csv` | `models/hsm/input/offshore/off_drill_depth.csv` | AEO2025-Public-Release | yes |
| `offshore/off_drill_per_year.csv` | `models/hsm/input/offshore/off_drill_per_year.csv` | AEO2025-Public-Release | yes |
| `offshore/off_drilling_rig_availability_constraint.csv` | `models/hsm/input/offshore/off_drilling_rig_availability_constraint.csv` | AEO2025-Public-Release | yes |
| `offshore/off_exp_cost.csv` | `models/hsm/input/offshore/off_exp_cost.csv` | AEO2025-Public-Release | yes |
| `offshore/off_exp_success_rate.csv` | `models/hsm/input/offshore/off_exp_success_rate.csv` | AEO2025-Public-Release | yes |
| `offshore/off_field_availability.csv` | `models/hsm/input/offshore/off_field_availability.csv` | AEO2025-Public-Release | no |
| `offshore/off_field_size_classes.csv` | `models/hsm/input/offshore/off_field_size_classes.csv` | AEO2025-Public-Release | yes |
| `offshore/off_gas_ratio_and_cond.csv` | `models/hsm/input/offshore/off_gas_ratio_and_cond.csv` | AEO2025-Public-Release | yes |
| `offshore/off_mapping.csv` | `models/hsm/input/offshore/off_mapping.csv` | AEO2025-Public-Release | yes |
| `offshore/off_nfw_coefficients.csv` | `models/hsm/input/offshore/off_nfw_coefficients.csv` | AEO2025-Public-Release | yes |
| `offshore/off_operating_cost.csv` | `models/hsm/input/offshore/off_operating_cost.csv` | AEO2025-Public-Release | yes |
| `offshore/off_platform_abandonment.csv` | `models/hsm/input/offshore/off_platform_abandonment.csv` | AEO2025-Public-Release | yes |
| `offshore/off_platform_delays.csv` | `models/hsm/input/offshore/off_platform_delays.csv` | AEO2025-Public-Release | yes |
| `offshore/off_platform_drill_per_year.csv` | `models/hsm/input/offshore/off_platform_drill_per_year.csv` | AEO2025-Public-Release | yes |
| `offshore/off_platform_slots.csv` | `models/hsm/input/offshore/off_platform_slots.csv` | AEO2025-Public-Release | yes |
| `offshore/off_platform_structure_type.csv` | `models/hsm/input/offshore/off_platform_structure_type.csv` | AEO2025-Public-Release | yes |
| `offshore/off_prod_fac_cost_frac.csv` | `models/hsm/input/offshore/off_prod_fac_cost_frac.csv` | AEO2025-Public-Release | yes |
| `offshore/off_prod_profile.csv` | `models/hsm/input/offshore/off_prod_profile.csv` | AEO2025-Public-Release | yes |
| `offshore/off_producing_fields.csv` | `models/hsm/input/offshore/off_producing_fields.csv` | AEO2025-Public-Release | no |
| `offshore/off_royalty.csv` | `models/hsm/input/offshore/off_royalty.csv` | AEO2025-Public-Release | no |
| `offshore/off_transportation.csv` | `models/hsm/input/offshore/off_transportation.csv` | AEO2025-Public-Release | yes |
| `offshore/off_undiscovered_fields.csv` | `models/hsm/input/offshore/off_undiscovered_fields.csv` | AEO2025-Public-Release | yes |
| `offshore/off_undiscovered_production.csv` | `models/hsm/input/offshore/off_undiscovered_production.csv` | AEO2025-Public-Release | yes |
| `offshore/off_water_depth.csv` | `models/hsm/input/offshore/off_water_depth.csv` | AEO2025-Public-Release | yes |
| `on_setup.csv` | `models/hsm/input/on_setup.csv` | AEO2025-Public-Release | no |
| `onshore/on_capital_constraint_eq.csv` | `models/hsm/input/onshore/on_capital_constraint_eq.csv` | AEO2025-Public-Release | no |
| `onshore/on_ch4_emission_factors_basin.csv` | `models/hsm/input/onshore/on_ch4_emission_factors_basin.csv` | AEO2025-Public-Release | yes |
| `onshore/on_co2_pipe_seg_costs.csv` | `models/hsm/input/onshore/on_co2_pipe_seg_costs.csv` | AEO2025-Public-Release | yes |
| `onshore/on_co2_supply_cost_temp.csv` | `models/hsm/input/onshore/on_co2_supply_cost_temp.csv` | AEO2025-Public-Release | yes |
| `onshore/on_discovery_order.csv` | `models/hsm/input/onshore/on_discovery_order.csv` | AEO2025-Public-Release | yes |
| `onshore/on_drilling_costs.csv` | `models/hsm/input/onshore/on_drilling_costs.csv` | AEO2025-Public-Release | yes |
| `onshore/on_eor_other_costs.csv` | `models/hsm/input/onshore/on_eor_other_costs.csv` | AEO2025-Public-Release | yes |
| `onshore/on_footage_constraint_eq.csv` | `models/hsm/input/onshore/on_footage_constraint_eq.csv` | AEO2025-Public-Release | no |
| `onshore/on_future_wells_configuration.csv` | `models/hsm/input/onshore/configuration/on_future_wells_configuration.csv` | AEO2026-Public-Release | no |
| `onshore/on_legacy_co2_costs.csv` | `models/hsm/input/onshore/on_legacy_co2_costs.csv` | AEO2025-Public-Release | yes |
| `onshore/on_ngpl_costs.csv` | `models/hsm/input/onshore/on_ngpl_costs.csv` | AEO2025-Public-Release | yes |
| `onshore/on_old_process_code_conv.csv` | `models/hsm/input/onshore/on_old_process_code_conv.csv` | AEO2025-Public-Release | no |
| `onshore/on_override_switches.csv` | `models/hsm/input/onshore/configuration/on_override_switches.csv` | AEO2026-Public-Release | no |
| `onshore/on_projects_other_eor.csv` | `models/hsm/input/onshore/on_projects_other_eor.csv` | AEO2025-Public-Release | no |
| `onshore/on_projects_undiscovered.csv` | `models/hsm/input/onshore/projects/on_projects_undiscovered.csv` | AEO2026-Public-Release | no |
| `onshore/on_rig_constraint_eq.csv` | `models/hsm/input/onshore/on_rig_constraint_eq.csv` | AEO2025-Public-Release | no |
| `onshore/on_wells_per_rig.csv` | `models/hsm/input/onshore/on_wells_per_rig.csv` | AEO2025-Public-Release | no |
| `onshore/shale_gas_play_map.csv` | `models/hsm/input/onshore/configuration/shale_gas_play_map.csv` | AEO2026-Public-Release | no |
| `onshore/tight_oil_play_map.csv` | `models/hsm/input/onshore/configuration/tight_oil_play_map.csv` | AEO2026-Public-Release | no |
| `state_tax.csv` | `models/hsm/input/state_tax.csv` | AEO2025-Public-Release | yes |
| `steo/steo_dcrdwhp.csv` | `models/hsm/input/steo/steo_dcrdwhp.csv` | AEO2025-Public-Release | no |
| `steo/steo_ogngplbu.csv` | `models/hsm/input/steo/steo_ogngplbu.csv` | AEO2025-Public-Release | no |
| `steo/steo_ogngplet.csv` | `models/hsm/input/steo/steo_ogngplet.csv` | AEO2025-Public-Release | no |
| `steo/steo_ogngplis.csv` | `models/hsm/input/steo/steo_ogngplis.csv` | AEO2025-Public-Release | no |
| `steo/steo_ogngplpp.csv` | `models/hsm/input/steo/steo_ogngplpp.csv` | AEO2025-Public-Release | no |
| `steo/steo_ogngplpr.csv` | `models/hsm/input/steo/steo_ogngplpr.csv` | AEO2025-Public-Release | no |
| `steo/steo_ogngplprd.csv` | `models/hsm/input/steo/steo_ogngplprd.csv` | AEO2025-Public-Release | no |
| `steo/steo_owrite_ad_ng.csv` | `models/hsm/input/steo/steo_owrite_ad_ng.csv` | AEO2025-Public-Release | no |
| `steo/steo_owrite_can.csv` | `models/hsm/input/steo/steo_owrite_can.csv` | AEO2025-Public-Release | no |
| `steo/steo_owrite_na_ng.csv` | `models/hsm/input/steo/steo_owrite_na_ng.csv` | AEO2025-Public-Release | no |
| `steo/steo_owrite_ngpls.csv` | `models/hsm/input/steo/steo_owrite_ngpls.csv` | AEO2025-Public-Release | no |
| `steo/steo_owrite_rfqtdcrd.csv` | `models/hsm/input/steo/steo_owrite_rfqtdcrd.csv` | AEO2025-Public-Release | no |
| `steo/steo_rfqtdcrd.csv` | `models/hsm/input/steo/steo_rfqtdcrd.csv` | AEO2025-Public-Release | no |
| `steo/steo_togqshlgas.csv` | `models/hsm/input/steo/steo_togqshlgas.csv` | AEO2025-Public-Release | no |
| `steo/steo_togqshloil.csv` | `models/hsm/input/steo/steo_togqshloil.csv` | AEO2025-Public-Release | no |
| `steo_setup.csv` | `models/hsm/input/steo_setup.csv` | AEO2025-Public-Release | no |
