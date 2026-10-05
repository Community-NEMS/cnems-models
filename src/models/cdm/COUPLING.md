# Coupling C-CDM to the other C-NEMS models

C-CDM runs on its own. This note says what it offers a coupled run and what a coupling would have
to add. Nothing here is implemented.

## What C-CDM offers

- Commercial electricity by census division, building type, end use and year, and the data center
  part of it (servers plus the increment in four services), in `cdm_electricity.csv` and
  `cdm_data_centers.csv`, or in `CDMModel.results` after `run()`.
- Commercial natural gas the same way, in `cdm_natural_gas.csv`.
- `CDMModel.set_reference_prices(electricity, natural_gas)`: the baseline price path, set once
  before the first run, by `{(region, year): 2025 USD per MMBtu}`.
- `CDMModel.update_prices(electricity, natural_gas)`: current prices, same keys. Demand responds
  to the ratio of current to reference prices (`docs/models/cdm.md`, eq. 19).

## What a coupling would add

1. A sequencer. `IntegratedModelSequencer.solve_model` returns a `(ModelType, IterationStatus)`
   pair, and `ModelType` has no C-CDM member. Adding one changes `ModelType.ALL` and the default
   circuit of the control loop, so it is a decision for the repository, not for this model.
2. Prices. C-NGMM reports regional natural gas prices (`poll_gas_price`) and the electricity model
   reports wholesale prices; neither is a delivered commercial price. Setting the reference path
   to each model's own baseline (`set_reference_prices`) and sending its prices
   (`update_prices`) makes C-CDM respond to the ratio, which assumes delivered prices move in
   proportion to the coupled model's prices. With delivery charges that stay fixed on top of a
   wholesale price, that overstates the change in the delivered electricity price. Keeping the
   AEO2026 reference path and sending it plus each model's price change, in the same units and
   dollar year, assumes instead that the change passes through in full and the other charges stay
   fixed. Either way the reference path must not be reset between iterations.
3. Regions. C-CDM uses the nine census divisions, as C-NGMM does. The electricity model's regions
   differ and need a mapping.
4. Load shape. C-CDM gives annual energy. The electricity model needs load by representative day
   and hour; a shape for commercial load, and a flatter one for data centers, would have to come
   from another source.
5. Purchased electricity. C-CDM's electricity is gross end use. Purchased load adds electric
   vehicle charging at commercial locations and subtracts on-site generation for own use (AEO2026
   Table 5 rows, reported in `cdm_summary.csv`).
6. No double counting. If the electricity model's load already includes commercial or data
   center demand, the coupled value must replace that part, not add to it. The data center
   columns are inside the electricity totals; carving them out as their own load means
   subtracting exactly what is reassigned.
