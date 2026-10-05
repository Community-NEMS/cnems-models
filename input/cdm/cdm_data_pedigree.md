# C-CDM input data pedigree

## Summary

| Origin | Files |
|---|---|
| NEMS input, release tag `AEO2026-Public-Release` | 8 |
| AEO2026 published tables | 3 |
| Total | 11 |

Every file is a tidy CSV with a `#` header naming its source file, the lines or series codes read,
the units and any assumption. `src/models/cdm/build_inputs.py` builds them all from their sources;
the model only reads them. Both AEO2026 cases are in the same files, keyed by a `case` column
(`reference` for the Counterfactual Baseline, `high_electricity_demand`).

## Which NEMS release

The NEMS files come from `input/cdm/` of <https://github.com/EIAgov/NEMS> at tag
`AEO2026-Public-Release`, the release that produced AEO2026. The High Electricity Demand case uses
that release's `kmels_higheldmd.txt`, which differs from `kmels.txt` in the "other" building's
multipliers, the explicit miscellaneous share (1 for every building type) and the server index.

## Files built for C-CDM

The NEMS text files are not shipped as they are: the repository normalizes line endings and trailing
whitespace, which would rewrite them. `build_inputs.py` reads each one by its documented layout
(data from line 101, blocks in the file header's order) and writes only the values C-CDM uses. The
AEO2026 rows are found by EIA series code in the year-by-year workbooks of the two cases and the
supplemental workbook holding Table 22, never by position. The workbooks come from EIA's AEO2026
tables page, <https://www.eia.gov/outlooks/aeo/tables_ref.php> (`yearbyyear.xlsx`, `sup_rci.xlsx`,
and the High Electricity Demand case through the side-case tables linked there).

Known issue. The `kmels.txt` header gives the base-year loads of the explicit miscellaneous
electric loads in trillion Btu per square foot; the way `comm.f:3488-3528` scales them shows Btu
per square foot, and `nems_mel_base.csv` says so.

## Note for committing these files

All files are under 200 KB. They are plain text with Unix line endings and pass the repository's
whitespace hooks.

## All files

| File | Origin | Used by | Source or method |
|---|---|---|---|
| `aeo2026_end_use.csv` | AEO2026 result | `data.py`, `commercial.py`, `postprocessor.py` | Table 5 end-use rows and the rows that form purchased electricity, both cases, quadrillion Btu, by series code; CHP natural gas input from supplemental Table 22 (`CST000:dge_NaturalGas`, trillion Btu / 1000, Counterfactual Baseline used for both cases) |
| `aeo2026_floorspace.csv` | AEO2026 result | `commercial.py` (eq. 2) | Supplemental Table 22 floorspace by building type, Counterfactual Baseline, billion sqft, series `CST000:ca_*`; used for both cases |
| `aeo2026_prices.csv` | AEO2026 result | `cdm_model.py` (reference prices) | Table 3 commercial natural gas and electricity, both cases, 2025 USD per MMBtu, series `PRC000:ca_NaturalGas`, `PRC000:ca_Electricity` |
| `nems_dc_multipliers.csv` | NEMS input | `commercial.py` (eq. 4) | `kmels.txt`, `kmels_higheldmd.txt` lines 101-111 [dcf] |
| `nems_eui_2018.csv` | NEMS input | `commercial.py` (eqs. 5-7, 12-14) | `kintens.txt` lines 101 on, by division, building type, fuel (electricity, natural gas, distillate) and service |
| `nems_floorspace_2018.csv` | NEMS input | `commercial.py` (eq. 1) | `kflspc.txt` numeric lines 101-220, summed over 10 vintage cohorts |
| `nems_mel_base.csv` | NEMS input | `commercial.py` (eqs. 9-11) | `kmels.txt` lines 126-145 [MelsElQ] |
| `nems_mel_index.csv` | NEMS input | `commercial.py` (eqs. 9-11) | `kmels.txt`, `kmels_higheldmd.txt` lines 149-168 [MarketPenetrationMels], 2019-2050 |
| `nems_mel_share.csv` | NEMS input | `commercial.py` (eq. 12) | `kmels.txt`, `kmels_higheldmd.txt` line 115 [xplicitmiscshr] |
| `nems_price_elasticity.csv` | NEMS input | `commercial.py` (eq. 19) | `ksdela.txt` numeric lines 101-198, by division, service and fuel |
| `nems_server_index.csv` | NEMS input | `commercial.py` (eq. 5) | `kmels.txt`, `kmels_higheldmd.txt` line 119 [DataCtrServPenetration], 2019-2050 |
