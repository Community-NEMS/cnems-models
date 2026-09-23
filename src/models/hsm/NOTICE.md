# Notice

C-HSM, the hydrocarbon supply model of the C-NEMS project, is distributed under the Apache License,
Version 2.0, the licence of the c-nems repository (`LICENSE` at the repository root).

C-HSM includes code and data from the National Energy Modeling System (NEMS), developed by the
U.S. Energy Information Administration (EIA) and published at <https://github.com/EIAgov/NEMS>.
EIA releases its projects under the Apache License, Version 2.0, unless a repository says otherwise
("All EIA projects will be released under Apache 2.0, unless otherwise specifically noted within the
project repository", <https://github.com/EIAgov>); the NEMS repository says nothing otherwise.

## What comes from NEMS

Code, from `models/hsm/` of the NEMS repository. Each file says at the top which release it comes
from and how it was changed.

| C-HSM file | NEMS file | NEMS release tag | Changes |
|---|---|---|---|
| `canada.py` | `canada.py` | `AEO2025-Public-Release` | modified (listed in the file) |
| `common.py` | `common.py` | `AEO2025-Public-Release` | modified (listed in the file) |
| `names.py` | `names.py` | `AEO2025-Public-Release` | names unchanged; reformatted |
| `submodule.py` | `submodule.py` | `AEO2025-Public-Release` | modified (listed in the file) |
| `us_onshore.py`, the drilling rules `calculate_price_adjustment`, `_base_well_count`, `on_next_wells` | `drilling_equations.py` | `AEO2026-Public-Release` | rewritten, keeping the NEMS constants |

`module.py` does the job of NEMS `module_unf.py` (`AEO2025-Public-Release`) and keeps its variable
names; its code is written for C-HSM. The other C-HSM files are C-NEMS code.

Data, from `models/hsm/input/` of the NEMS repository: 27 files in `input/hsm/`, unchanged, taken
from the `AEO2025-Public-Release` or `AEO2026-Public-Release` tag. `input/hsm/hsm_data_pedigree.md`
lists each one with its NEMS path and release.

C-HSM also uses results that EIA publishes in the *Annual Energy Outlook* (AEO2026 supplemental
tables, and AEO2025 side cases) to build some of its inputs; `input/hsm/hsm_data_pedigree.md` says
which.

## Changes

The NEMS-derived files were changed for C-HSM. The main changes, all described in the files:

- the NEMS restart file and pickle files are replaced by values kept in memory;
- imports point at `src.models.hsm`;
- diagnostic printing goes to the logger;
- file types are read from the text after the last dot of a file name;
- the files are formatted to the C-NEMS code style, and their comments and docstrings rewritten.
