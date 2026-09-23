# C-HSM

C-HSM is the hydrocarbon supply model of C-NEMS, adapted from the Hydrocarbon Supply Module of EIA's
National Energy Modeling System (NEMS, <https://github.com/EIAgov/NEMS>, Apache License 2.0; see
`NOTICE.md`). It projects, for every calendar year from 2023 to 2050:

- US natural gas production capacity by census division, cost tier and gas type
  (non-associated or associated-dissolved), in response to the Henry Hub and Brent prices;
- Canadian gas production (non-associated and associated-dissolved);
- gas and crude oil from wells already producing in the base year.

The US part is a reduced form by default: capacity follows the price through supply elasticities, a
technology trend and a fitted calibration, rather than through drilling and project economics as in
NEMS. An optional well-level engine does model drilling; it is not ready to be the default
(`ENGINE_PLAN.md`). Canada is NEMS's own Canada submodule, adapted. `EVALUATION.md` says what the
model is good for and what it is not.

## Quick start

```bash
pixi run hsm                  # run C-HSM with run_configs/basic_hsm_config.toml
pixi run pytest tests/hsm     # check the results against tests/hsm/expected/
```

A run takes about 2 seconds. Results go to `output/<scenario_name>/hsm/` (`output/hsm_baseline/hsm/`
with the basic config), and the log to `output/<scenario_name>/run.log`. The tests take about 15
seconds; `CHSM_ENGINE_FULL_RUN=1 pixi run pytest tests/hsm -n0` adds a full engine run (about 2
minutes).

## Configuration

`run_configs/basic_hsm_config.toml`, section `[hsm]`, parsed by `hsm_config.py`:

| Key | Meaning |
|---|---|
| `input_path` | the C-HSM input folder, `input/hsm` |
| `ng_input_path` | C-NGMM's input folder, `input/natural_gas`. C-HSM reads the regions (`ng_region_data.csv`) and the base capacity of each cost tier (`ng_supply_cost_tiers.csv`) from here, so the two models always agree on them |
| `henry_hub_file` | Henry Hub price path in `input_path`, 1987 $/MMBtu, one row per year 2023–2050. Default `hh_reference_path.csv`; the basic config uses `henry_hub_path_aeo2026_1987usd.csv` |
| `brent_multiplier` | scales the Brent path for oil price scenarios (default 1.0) |
| `onshore_engine_file` | settings file in `input_path` for the optional well-level engine, normally `us_onshore_engine.csv`. Left out (the default), C-HSM runs the reduced form |
| `calibration_file` | the `(k, g, c)` calibration in `input_path`: `us_gas_calibration.csv` (default) for the reduced form, `us_gas_calibration_engine.csv` for the engine |

File settings must be names inside `input_path`. The `[common]` section is required, but C-HSM reads
only `output_path` and `scenario_name`. It always runs every year from 2023 to 2050 (set in
`input/hsm/setup.csv`).

## Files

| File | What it does |
|---|---|
| `sequencer.py` | `HSMSequencer`: builds the model, runs it, writes results; `main()` is `pixi run hsm` |
| `hsm_config.py` | `HSMConfig`, the `[hsm]` settings |
| `data.py` | reads the price paths, and the regions and cost tiers from C-NGMM's inputs |
| `hsm_model.py` | `HSMModel`: holds the model years, runs them, changes prices, returns results |
| `postprocessor.py` | writes the six result CSVs |
| `module.py` | `HSMModule`: runs one year at a time: prices, the Canada submodule, the US supply |
| `us_gas.py` | `USGasModule`: the reduced-form US gas supply |
| `us_onshore.py` | `OnshoreLegacy` (production of existing wells, for two outputs) and `OnshoreEngine` (the optional well-level engine) |
| `canada.py`, `common.py`, `names.py`, `submodule.py` | adapted from the NEMS HSM files of the same names (AEO2025 release): the Canada submodule, helpers, name constants and the submodule base class |
| `NOTICE.md` | what comes from NEMS, and under what licence |
| `PROVENANCE.md` | where every piece of code, every number in the code and every input comes from |
| `EVALUATION.md` | what the model is worth and what is left to do |
| `COUPLING.md` | how to couple C-HSM to C-NGMM |
| `ENGINE_PLAN.md` | the well-level engine: status, known bugs and the steps to finish it |
| `INPUTS_PLAN.md` | the NEMS inputs C-HSM does not use yet, and how to add them |
| `CONVENTIONS.md` | names shared with C-NGMM, and where C-HSM keeps NEMS names |

## Outputs

| File | Columns | Rows (basic config) |
|---|---|---|
| `hsm_us_gas_capacity.csv` | `region, cost_tier, gas_type, year, capacity_bcf` | 1,008: 9 regions × (3 `na` tiers + 1 `ad` row under `medium_cost`) × 28 years |
| `hsm_us_legacy_gas.csv` | `region, gas_type, year, gas_bcf` | existing wells only |
| `hsm_us_crude.csv` | `region, gas_type, year, crude_mbbl` | existing wells only (with the engine, existing plus new wells), thousand barrels |
| `hsm_canada_na_prod.csv`, `hsm_canada_ad_prod.csv` | `canada_region, year, production_bcf` | `canada_region` 1 = east, 2 = west |
| `hsm_canada_realized_na_prod.csv` | as above | a copy of the NA production; the same file in a standalone run |

`hsm_us_gas_capacity.csv` is production capacity, the quantity C-NGMM uses as its supply anchor
(summed over cost tier and gas type for each region and year).

## How the US supply works

For each region, cost tier and year (`us_gas.py`, reduced form):

1. Base capacity is the cost tier's capacity from C-NGMM, split across four gas types
   (conventional, tight, shale, coalbed methane) by fixed shares.
2. Each gas type responds to the regional wellhead price: `(price / 2.50) ^ elasticity`, with the
   price = Henry Hub + a regional basis, in real 2023 $.
3. A technology trend by gas type (from NEMS `on_tech_levers.csv`) raises capacity each year, and the
   low-cost tier declines by a regional rate (from NEMS dry-hole rates) to no less than half.
4. A regional share of capacity is associated-dissolved (AD) gas. It is valued at the reference gas
   price and scaled by Brent against its reference, so it follows oil, not gas.
5. A calibration `k (1 + g)^x exp(c x²)`, `x = year − 2025`, with `(k, g, c)` by region from
   `input/hsm/us_gas_calibration.csv`, makes capacity at the AEO2026 reference prices match C-NGMM's
   supply anchors. Refit it if C-NGMM's cost tiers or anchors change.

With the engine on, steps 1 to 4 are replaced by drilling on 3,181 NEMS projects, and the engine's
own calibration applies (`ENGINE_PLAN.md`).

## Units and years

- Prices come in as 1987 dollars: Henry Hub in \$/MMBtu, Brent in \$/bbl. Inside, they are
  converted to real 2023 $ with a fixed deflator. Passing nominal prices would over-stimulate supply.
- Years run in order, 2023 to 2050. The reduced-form US side has no memory of earlier prices;
  Canada and the engine carry state from year to year.
- A price path must cover every year. `HSMModel` rejects a price path or a price update that
  misses a year or has a blank or non-finite price, and checks both reference paths when it is
  built. Reading a price file also rejects a repeated year.
- A supplied Brent below \$20/bbl (real 2023 \$) is replaced by the $65 base oil price in the US
  supply. Canada uses the Brent price as given.
- Canada does not respond to the Henry Hub price, only to Brent: its well equation, as in the
  NEMS AEO2025 release, uses a benchmark Henry Hub price from `canada/can_benchmark_prices.csv`.

## Coupling

C-HSM is not yet wired into a coupled run (`update_model` raises `NotImplementedError`, as in
C-NGMM). The pieces for a Gauss-Seidel loop with C-NGMM are `HSMModel.update_prices`,
`HSMModel.poll_us_natgas_capacity` and C-NGMM's `NGModel.update_supply_capacity`. That method takes
`(region, cost_tier, year)` keys and sums over the cost tier, so C-HSM's gas type has to be summed
first (`COUPLING.md` shows how). Before re-running the years inside a loop, restore Canada's tables
from a copy taken right after `setup_canada()`: `reset_canada()` alone does not.

## Where the inputs come from

`input/hsm/` holds the 37 files C-HSM reads. `input/hsm/hsm_data_pedigree.md` lists each one: 27 are
NEMS inputs, unchanged, from the AEO2025 or AEO2026 NEMS release, and 10 were built or set for C-HSM.
A reduced-form run reads 26 of them, an engine run 36. The rest of the NEMS HSM input folder is not
included; `INPUTS_PLAN.md` says where each file is and how to add it. `PROVENANCE.md` traces the code
and every number in it.
