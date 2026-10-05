# C-CDM, the commercial demand model

C-CDM projects commercial-sector electricity and natural gas use in the nine census divisions, by
building type and end use, for every year from 2025 to 2050. Data centers are its focus: server
electricity is modeled the way EIA's National Energy Modeling System (NEMS) models it, and so is
the extra cooling, ventilation, office equipment and other load NEMS attributes to data centers in
each building type. It follows one of two AEO2026 cases and responds to electricity and natural
gas prices.

The equations are in `docs/models/cdm.md`. Where every number comes from is in `PROVENANCE.md`,
what the model can and cannot be used for in `EVALUATION.md`, and how it would connect to the
other C-NEMS models in `COUPLING.md`.

## Quick start

```bash
pixi run cdm
```

This runs `run_configs/basic_cdm_config.toml` and writes the results to `output/cdm_baseline/cdm/`
(a later run writes to `output/cdm_baseline_1/cdm/`, and so on, so earlier results are kept).
C-CDM runs on its own. It has no member in `ModelType`, so `python main.py` with this config runs
nothing. To run another config:

```bash
pixi run python -m src.models.cdm.sequencer run_configs/<your_config>.toml
```

The tests are in `tests/cdm/` (`pixi run pytest tests/cdm`).

## Configuration

The `[common]` section is the usual one; C-CDM reads only `output_path` and `scenario_name`. The
`[cdm]` section:

| Setting | Default | Meaning |
|---|---|---|
| `input_path` | (required) | Folder of input tables, `input/cdm` |
| `case` | `reference` | AEO2026 case to follow: `reference` (the Counterfactual Baseline) or `high_electricity_demand` |
| `electricity_price_scale` | 1.0 | Multiplier on the reference electricity price path |
| `natural_gas_price_scale` | 1.0 | Multiplier on the reference natural gas price path |

At scales of 1 the end uses C-CDM scales to AEO2026 equal their AEO2026 national totals, and
servers follow NEMS's method (within 0.1 to 4.3 percent of AEO2026). A scale of 1.1 raises that
fuel's price by 10 percent in every division and year; demand falls through the price response.

## Files

| File | Content |
|---|---|
| `commercial.py` | The equations: floorspace, data center share and multipliers, servers, scaled end uses, other uses, non-building rows, price response |
| `data.py` | Reads and checks the input tables |
| `cdm_model.py` | The model object: inputs, reference and current prices, results |
| `cdm_config.py` | The `[cdm]` settings |
| `sequencer.py` | Builds, runs and reports the model; `main()` for `pixi run cdm` |
| `postprocessor.py` | Writes the result tables |
| `build_inputs.py` | Builds `input/cdm/` from the NEMS input files and the AEO2026 tables; not run by the model |

## Outputs

Written to `<output_path>/<scenario_name>/cdm/` (the scenario folder gets `_1`, `_2`, ... if it
already exists):

| File | Content | Unit |
|---|---|---|
| `cdm_electricity.csv` | Gross end-use electricity by division, building type, end use and year; building types include `non_building` | TWh |
| `cdm_natural_gas.csv` | Natural gas by division, building type, end use and year; building types include `non_building` and `combined_heat_power` | trillion Btu |
| `cdm_data_centers.csv` | Servers and the data center increment in cooling, ventilation, computers and office equipment, and other uses, by division, building type and year | TWh |
| `cdm_summary.csv` | National totals beside the AEO2026 rows they follow or check against | trillion Btu |

`cdm_data_centers.csv` is a subset of `cdm_electricity.csv`; adding the two double counts.
Electricity is gross end use: purchased electricity adds electric vehicle charging at commercial
locations and subtracts on-site generation for own use, which `cdm_summary.csv` reports from
AEO2026 Table 5.

## How it works

1. Floorspace by building type comes from AEO2026 Table 22 and is shared to divisions as in 2018.
2. Servers are modeled: 2018 server intensity times floorspace times NEMS's server energy index.
3. Heating, cooling, water heating, ventilation, cooking, lighting, refrigeration and computers
   and office equipment are scaled to the AEO2026 Table 5 national totals, shared by 2018
   intensity, floorspace and NEMS's data center multiplier.
4. Other uses in buildings are built as NEMS builds them, from 20 explicit miscellaneous loads and
   an unexplained remainder. The rest of the AEO2026 other-uses row, which EIA says includes uses
   outside buildings and combined heat and power, is kept as separate `non_building` and
   `combined_heat_power` rows.
5. Every result except the combined heat and power row is scaled by a price factor: current
   over reference prices for this year and the two before, raised to NEMS's short-run
   elasticities.

## Units and years

Annual, 2025 to 2050. Energy in trillion Btu inside the model; electricity results in TWh (3,412
Btu per kWh). Prices in 2025 USD per million Btu, as in AEO2026 Table 3. Floorspace in million
square feet (AEO2026 publishes billion square feet).

## Where the inputs come from

Every table in `input/cdm/` carries a `#` header naming its source, the lines or series codes read,
the units and any assumption, and `input/cdm/cdm_data_pedigree.md` lists them all. The NEMS tables
come from the CDM input files of the `AEO2026-Public-Release` tag of
<https://github.com/EIAgov/NEMS>; the AEO2026 tables from EIA's published Annual Energy Outlook
2026 (Tables 3, 5 and 22), downloaded from <https://www.eia.gov/outlooks/aeo/tables_ref.php>. To rebuild them:

```bash
pixi exec --spec python=3.12 --spec openpyxl -- python src/models/cdm/build_inputs.py --reference <year-by-year workbook, Counterfactual Baseline> --high-demand <year-by-year workbook, High Electricity Demand> --supplement <supplemental Table 22 workbook>
```
