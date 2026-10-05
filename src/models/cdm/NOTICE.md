# Notice

C-CDM, the commercial demand model of the C-NEMS project, is distributed under the Apache License,
Version 2.0, the license of the c-nems repository (`LICENSE` at the repository root).

C-CDM uses data from, and does the job of part of, the National Energy Modeling System (NEMS),
developed by the U.S. Energy Information Administration (EIA) and published at
<https://github.com/EIAgov/NEMS>. EIA releases its projects under the Apache License, Version 2.0,
unless a repository says otherwise ("All EIA projects will be released under Apache 2.0, unless
otherwise specifically noted within the project repository", <https://github.com/EIAgov>).

## What comes from NEMS

Code: none. NEMS's Commercial Demand Module is Fortran (`source/comm.f`). `commercial.py` does the
job of its service demand and consumption calculations and cites the lines it follows; it is
written for C-CDM.

Data, from `input/cdm/` of the NEMS repository, release tag `AEO2026-Public-Release`. The files are
not shipped unchanged: `build_inputs.py` reads them and writes tidy tables, each with a header
naming the file and lines read.

| C-CDM table | NEMS file | What it holds |
|---|---|---|
| `nems_floorspace_2018.csv` | `kflspc.txt` | 2018 floorspace by building type and division (from CBECS 2018) |
| `nems_eui_2018.csv` | `kintens.txt` | 2018 energy use intensity by division, building type, end use and fuel |
| `nems_dc_multipliers.csv` | `kmels.txt`, `kmels_higheldmd.txt` | Data center intensity multipliers |
| `nems_server_index.csv` | `kmels.txt`, `kmels_higheldmd.txt` | Server annual energy index |
| `nems_mel_share.csv` | `kmels.txt`, `kmels_higheldmd.txt` | Share of miscellaneous load modeled explicitly |
| `nems_mel_base.csv` | `kmels.txt` | Base-year load of 20 explicit miscellaneous electric loads |
| `nems_mel_index.csv` | `kmels.txt`, `kmels_higheldmd.txt` | Annual index of each explicit load |
| `nems_price_elasticity.csv` | `ksdela.txt` | Short-run price elasticity of service demand |

## What comes from AEO2026

`aeo2026_floorspace.csv`, `aeo2026_end_use.csv` and `aeo2026_prices.csv` hold rows of EIA's Annual
Energy Outlook 2026 (Tables 3 and 5, and supplemental Table 22), identified by EIA series code.
They are published results of NEMS runs `cb2026.d021826b` (Counterfactual Baseline) and
`higheldmd.d021526a` (High Electricity Demand), downloaded from EIA's AEO2026 tables page,
<https://www.eia.gov/outlooks/aeo/tables_ref.php>.
