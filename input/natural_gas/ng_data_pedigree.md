# Natural gas input data pedigree

Provenance, units, and construction notes for the CSV inputs in `input/natural_gas/`.
These notes were previously carried as `#` comment rows at the top of each CSV and were
moved here so the files are plain, comment-free CSV; the readers in
`src/models/natural_gas/data.py` and `src/integrator/ng_coupling.py` still parse with
`comment='#'`, so re-adding a note inline remains harmless but is discouraged.

## `elec_to_ng_region_map.csv`

Crosswalk from the 25 electricity model regions to the 9 census-division natural gas
regions. Carried no provenance notes.

## `ng_base_demand.csv`

- Base-year (2025) NG demand by region/sector -- scaled to AEO2026 sectoral consumption
  (Table 62), regional shape preserved.

## `ng_demand_elasticity.csv`

- Own-price short-run demand elasticities by end-use sector (negative = demand falls when
  price rises).
- Source: EIA NEMS NGMM documentation; EIA STEO econometric estimates; Brown & Yucel 2008.

## `ng_demand_growth.csv`

- Annual NG demand growth by sector -- AEO2026 sectoral CAGR (Table 62).

## `ng_lng_export.csv`

- Net natural gas exports (LNG + pipeline) = AEO2026 production - consumption
  (Table 59/62), distributed across export hubs.

## `ng_lng_import.csv`

- LNG import terminals (coastal regions only) — high-cost backstop supply.
- `capacity_bcf`: re-gasification capacity [BCF/yr].
- `cost_per_mmbtu`: delivered import cost [$/MMBtu].
- Source: EIA AEO 2023; FERC terminal capacity filings.

## `ng_pipeline_arcs.csv`

- Interstate natural gas pipeline arcs (directed; each physical pipe = two rows).
- `capacity_bcf`: aggregate nameplate capacity [BCF/yr] per direction.
- `tariff_per_mmbtu`: FERC-approved transportation tariff [$/MMBtu].
- Source: EIA Compendium of Interstate Natural Gas Pipelines 2022; FERC Form 2.

## `ng_scalars.csv`

- Scalar parameters for the natural gas model.
- Source: as noted per parameter (the `source` column carries per-row provenance).

## `ng_storage.csv`

- Underground natural gas storage by census division.
- `working_cap_bcf`: working gas capacity [BCF].
- `inject_cap_bcf_yr`: max seasonal injection rate [BCF/yr].
- `withdraw_cap_bcf_yr`: max seasonal withdrawal rate [BCF/yr].
- Source: EIA Form EIA-191M aggregate by census division (2022).

## `ng_supply_anchors.csv`

- NG supply-curve anchor path (Phase 1.3) -- per-(region,year) multipliers on the static
  (Q0,P0) anchors; from AEO2026 Table 59 HSM production + regional supply prices,
  normalized to 2025. Built by `harness/build_ng_anchor_path.py`.

## `ng_supply_cost_tiers.csv`

- Natural gas supply cost tiers -- calibrated to AEO2026 HSM
  (`harness/build_ng_hsm_calibration.py`). capacity = peak HSM production x2.0; costs
  bracket AEO regional supply price.
