# Naming and data conventions

C-HSM follows C-NGMM's names and file conventions (`src/models/natural_gas/`, `input/natural_gas/`),
so that the two models call the same things by the same names.

## What C-HSM shares with C-NGMM

- **Cost tiers** are `cost_tier` with the values `low_cost`, `medium_cost`, `high_cost`, the names
  C-NGMM uses in `ng_supply_cost_tiers.csv`. C-HSM reads that file itself, so there is one copy.
- **Regions** are the nine census divisions spelled as in C-NGMM's `ng_region_data.csv`
  (`new_england`, …, `west_south_central`, …, `pacific`). C-HSM reads them from that file.
- **Output columns** are snake_case with the unit in the name: `capacity_bcf`, `production_bcf`,
  `gas_bcf`, `crude_mbbl`. Years are integers in a `year` column.
- **Canada outputs** name the Canadian region `canada_region` (1 = east, 2 = west); NEMS calls it
  `NUMCAN`. The rename happens when the results are written (`postprocessor.py`).
- **Code layout** follows C-NGMM: `hsm_config.py` (pydantic config for the `[hsm]` TOML section),
  `data.py` (readers), `hsm_model.py` (model object), `sequencer.py` (an
  `IntegratedModelSequencer`), `postprocessor.py` (result tables), `run_configs/basic_hsm_config.toml`,
  `tests/hsm/`, and the `pixi run hsm` task.

## Where C-HSM differs, on purpose

- **`input/hsm/` keeps the NEMS layout.** Most of it is NEMS input read by code adapted from NEMS,
  which expects NEMS's wide tables, its `setup.csv` lookup and its column names. Restating it in
  C-NEMS shape is a separate job, and it would change the files away from what EIA ships.
- **Inside the NEMS-derived code, NEMS names stay** (`rest_henry_hub`, `ogsmout_ogcnpprd`,
  `NUMCAN`, `hsm_vars`), so the code can still be read against NEMS `models/hsm/`
  (<https://github.com/EIAgov/NEMS>, release `AEO2025-Public-Release`).
- **`gas_type`** (`na` for non-associated, `ad` for associated-dissolved) has no C-NGMM counterpart.
  C-NGMM would sum over it, as it does over the cost tier.
- **The cost tier is not used by C-NGMM**: its `update_supply_capacity` ignores it and sums per region
  and year. C-HSM keeps it because its base capacities are defined per tier.

## If you extend it

Keep `cost_tier` as the name even though C-NGMM sums over it, and keep the unit suffixes on value
columns.
