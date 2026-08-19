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

- Base-year (2025) NG demand by region/sector [BCF/yr] -- scaled to AEO2026 sectoral
  consumption (Table 62), regional shape preserved. 45 region-sector pairs.
- Sectors: `electric_power`, `industrial`, `residential`, `commercial`, `transportation`
  (the `DEMAND_SECTORS` list in `ng_model.py`).
- **Source (superseded, but still live in the regional shape):** EIA Natural Gas Annual 2022
  Tables 1-7, scaled to 2025 with AEO 2023 reference-case projections, values rounded to
  the nearest 5 BCF. Only the sectoral totals were re-scaled to AEO2026; the cross-region
  distribution is still the NGA 2022 shape.

## `ng_demand_elasticity.csv`

- Own-price short-run demand elasticities by end-use sector (negative = demand falls when
  price rises).
- **Source:** EIA NEMS NGMM documentation; EIA STEO econometric estimates; Brown & Yucel 2008.
- Consumed by `NGModel.update_demand_from_price()`, which applies
  `demand = base x (price/reference)^elasticity` to add price-responsive demand behaviour,
  matching NEMS NGMM's price-sensitive demand blocks. Intended to be driven once per
  iteration by the Gauss-Seidel / unified integrator; that loop is proposed in
  `COUPLING.md` but is not wired up yet, so nothing calls the method today.

## `ng_demand_growth.csv`

- Annual NG demand growth by sector -- AEO2026 sectoral CAGR (Table 62).
- Fractional per-year rates, negative for declining sectors (residential and commercial in
  the fallback series).

## `ng_lng_export.csv`

- Net natural gas exports (LNG + pipeline) = AEO2026 production - consumption
  (Table 59/62), distributed across export hubs.
- Units: BCF/yr. Values are given at breakpoint years only; `_interp_lng_export` in
  `ng_model.py` linearly interpolates between them and clamps outside the range.
- Modeling role: US LNG export is a major use of domestic supply (~14+ BCF/day in 2025).
  It enters as demand rather than as a netted-out supply term, because export contracts are
  long-term obligations that tighten the domestic supply-demand balance and raise domestic
  prices. (In the QP it is price-responsive on a downward-sloping curve, NGMM Eq 14.)
- Export hubs, and the terminals each row aggregates:
  - *West South Central*: Sabine Pass, Corpus Christi, Freeport, Calcasieu Pass, Cameron,
    Golden Pass (under construction), Plaquemines, Port Arthur (planned), Rio Grande (planned).
  - *South Atlantic*: Cove Point (MD), Elba Island (GA).
  - *Pacific*: Jordan Cove / Magnolia (proposed, Oregon / BC border), assumed to partially
    materialise after 2030.
- **Source (earlier lineage):** the hub assignment above and the superseded hardcoded
  fallback values in `data.py` came from EIA AEO 2025 LNG export projections plus DOE
  export-authorisation data.
  The current CSV is the AEO2026-derived series described in the first bullet; the hub
  breakdown carried over unchanged.

## `ng_lng_import.csv`

- LNG import terminals (coastal regions only) — high-cost backstop supply.
- `capacity_bcf`: re-gasification capacity [BCF/yr].
- `cost_per_mmbtu`: delivered import cost [$/MMBtu].
- **Source:** EIA AEO 2023; FERC terminal capacity filings.

## `ng_pipeline_arcs.csv`

- Interstate natural gas pipeline arcs (directed; each physical pipe = two rows).
  26 directed arcs, i.e. 13 physical corridors.
- `capacity_bcf`: maximum throughput [BCF/yr] per direction, built as aggregate nameplate
  capacity x 365 d.
- `tariff_per_mmbtu`: FERC-approved transportation tariff [$/MMBtu].
- **Source:** EIA Compendium of Interstate Natural Gas Pipelines 2022; FERC Form 2 tariff
  filings.

## `ng_scalars.csv`

- Scalar parameters for the natural gas model.
- **Source:** as noted per parameter (the `source` column carries per-row provenance).
- `storage_opex` = 0.18 $/MMBtu, charged on injected volume. **Source:** EIA average storage
  injection / withdrawal tariff.

## `ng_storage.csv`

- Underground natural gas storage by census division.
- `working_cap_bcf`: working gas capacity [BCF].
- `inject_cap_bcf_yr`: max seasonal injection rate [BCF/yr].
- `withdraw_cap_bcf_yr`: max seasonal withdrawal rate [BCF/yr].
- **Source:** EIA Form EIA-191M aggregate by census division (2022).
- On an annual time step the net storage change is constrained to zero (cyclical):
  `stor_inject[r,y] == stor_withdraw[r,y]`. Storage therefore moves nothing between years;
  it reaches the balance only through the storage loss fraction and the objective only
  through `storage_opex`.

## `ng_supply_anchors.csv`

- NG supply-curve anchor path (Phase 1.3) -- per-(region,year) multipliers on the static
  (Q0,P0) anchors; from AEO2026 Table 59 HSM production + regional supply prices,
  normalized to 2025. Built by `harness/build_ng_anchor_path.py`.

## `ng_supply_cost_tiers.csv`

- Natural gas supply cost tiers -- calibrated to AEO2026 HSM
  (`harness/build_ng_hsm_calibration.py`). capacity = peak HSM production x2.0; costs
  bracket AEO regional supply price.
