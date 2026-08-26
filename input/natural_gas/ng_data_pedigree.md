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
  iteration by an integrator, but no such loop is wired up yet, so nothing calls the method
  today.

## `ng_demand_growth.csv`

- Annual NG demand growth by sector -- AEO2026 sectoral CAGR (Table 62).
- Fractional per-year rates, negative for declining sectors (residential and commercial in
  the fallback series).

## `ng_gathering.csv`

- First-mile gathering charge per supply region, wellhead to regional hub
  (NGMM Eq 7, the `P^gath` term), in $/MMBtu.
- Higher in remote or steep-terrain regions (Mountain 0.35), lower on the Gulf Coast (0.15).
- **Source:** values as previously carried in `data.py`.

## `ng_lng_demand_curve.csv`

- LNG export demand curve (NGMM Fig 3.6), a linear curve in (Q, P) approximated by three
  steps: at `q_frac = 1.0` the price is the world LNG price, at `q_frac = 0` it is
  `lng_max_price_factor` times that.
- `q_frac` is a fraction of export capacity; `p_factor` multiplies the world price. The
  anchoring scalars `lng_world_price_per_mmbtu` and `lng_max_price_factor` live in
  `ng_scalars.csv`, not here.

## `ng_lng_export.csv`

- Net natural gas exports (LNG + pipeline) = AEO2026 production - consumption
  (Table 59/62), distributed across export hubs.
- Units: BCF/yr. Values are given at breakpoint years only; `interp_lng_export` in
  `data.py` linearly interpolates between them and clamps outside the range.
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

## `ng_losses.csv` — optional, not shipped

- The four loss fractions (NGMM Eq 10, 11) are single numbers applying to every region, so
  they live in `ng_scalars.csv` as `distribution_loss` (0.008, LDC
  unaccounted-for-gas on residential + commercial volumes at delivery),
  `intrastate_loss` (0.003, short-haul loss within a census division),
  `storage_loss` (0.005, fraction of cycled storage volume) and
  `plant_fuel_frac` (0.030, lease and processing-plant fuel use).
  **There is no `ng_losses.csv` in this repo and none is required.**
- The loader supports one purely as an *override*: add the file with a `region` column and any
  subset of `distribution_loss`, `intrastate_loss`, `storage_loss`, `plant_fuel_frac`.
- Overriding is per region **and** per column — the model resolves each value as
  `losses.get(region, {}).get(column, <scalar default>)` — so a file listing one region and
  one column leaves everything else on its scalar.

## `ng_pipe_loss.csv` — optional, not shipped

- Pipeline fuel loss (NGMM Eq 11, `f^pip`) is a single number applying to every corridor, so
  it lives in `ng_scalars.csv` as `pipe_fuel_loss` (0.005, ~0.5% per long-haul
  corridor). **There is no `ng_pipe_loss.csv` in this repo and none is required.**
- The loader supports one purely as a per-arc *override*: add the file with
  `origin,destination,loss_fraction` rows and only the arcs listed depart from the scalar.
  Arcs not listed, and the absence of the file entirely, both mean "use the scalar".
- This and `ng_losses.csv` are the only two optional inputs in `data.py`. Every other file
  listed here is required and raises if missing, because for those the absent file would mean
  the value existed nowhere on disk. For these two the value is in `ng_scalars.csv` either way.

## `ng_pipeline_arcs.csv`

- Interstate natural gas pipeline arcs (directed; each physical pipe = two rows).
  26 directed arcs, i.e. 13 physical corridors.
- `capacity_bcf`: maximum throughput [BCF/yr] per direction, built as aggregate nameplate
  capacity x 365 d.
- `tariff_per_mmbtu`: FERC-approved transportation tariff [$/MMBtu].
- **Source:** EIA Compendium of Interstate Natural Gas Pipelines 2022; FERC Form 2 tariff
  filings.

## `ng_region_data.csv`

- The model's regions and their display labels. **Required**: regions are definitional, so
  `load_region_data` raises rather than substituting a hardcoded list, and a model built on a
  region set that disagrees with the other input files is worse than one that refuses to build.
- `region`: identifier used as the key in every other file here.
- `domestic` / `international`: case-insensitive `True` places the region in that subset;
  anything else, conventionally `-`, does not. Mirrors the electricity model's convention.
- `covered_areas`: the states each census division spans. Documentation for the reader; it is
  read but not returned.
- `label`: display name used in reporting.
- `NGConfig.region_filter` narrows which of the domestic regions are analysed; it does not
  change this file.

## `ng_scalars.csv`

- Scalar parameters for the natural gas model.
- **Source:** as noted per parameter (the `source` column carries per-row provenance).
- `storage_opex` = 0.18 $/MMBtu, charged on injected volume. **Source:** EIA average storage
  injection / withdrawal tariff.
- The nine scalars listed in `_REQUIRED_QP_SCALARS` (`data.py`) are all required:
  `lng_world_price_per_mmbtu`, `lng_max_price_factor`, `pipe_fuel_loss`,
  `distribution_loss`, `intrastate_loss`, `plant_fuel_frac`, `storage_loss`,
  `supply_curve_qmin_fraction`, `mmbtu_per_bcf`. Dropping one raises rather than reverting to
  a built-in default, so add a row here before referencing a new scalar in the model.
- `mmbtu_per_bcf` = 1.036e6 is the physical BCF -> MMBtu conversion, from the EIA average heat
  content of natural gas delivered to consumers (~1036 Btu/cf). It multiplies every cost term
  in the objective, which is what makes `total_cost` dollar-denominated, and is divided back
  out of the demand-balance duals so prices come back as $/MMBtu.
- Five of them are the operative value for every region or arc, and can be overridden
  selectively by an optional file: `pipe_fuel_loss` by `ng_pipe_loss.csv`, and
  `distribution_loss` / `intrastate_loss` / `storage_loss` / `plant_fuel_frac` by
  `ng_losses.csv`. Neither override file ships, so the scalars apply everywhere.
- **Those five names match the override column names exactly.** Where a name can appear in
  two places it is the same name in both, so a column in `ng_losses.csv` is unambiguously the
  override of the scalar it shares a name with.

## `ng_sector_data.csv`

- The demand sectors, and the display label for each. **Required**, like the region file.
- `name`: sector key, used in `ng_base_demand.csv`, `ng_demand_elasticity.csv` and
  `ng_demand_growth.csv`.
- `label`: display name used in reporting.
- Every sector listed here must have an entry in `ng_demand_elasticity.csv`. `load_all` checks
  this and raises naming any that do not, because a sector with no elasticity would read as
  perfectly inelastic demand, which is a modelling statement rather than a default.

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

## `ng_supply_curve_shape.csv`

- NGMM elastic supply-curve shape (NGMM Eq 2-5): the CRV breakpoint multipliers and the
  per-segment elasticities used to build five steps around each region's `(Q0, P0)` anchor.
- `side` is `below` / `above` for the three CRV steps either side of the anchor, or the
  sentinel `elas` for the five segment elasticities, which are indexed 1-5 and so cannot
  share the 1-3 step numbering. Rows carrying a CRV leave `elas` blank and vice versa.
- **Source:** ELAS `[0.8, 0.7, 0.5, 0.3, 0.2]` per the AEO 2022 footnote; CRV values as
  previously carried in `data.py`.

## `ng_tariff_curve_shape.csv`

- Piecewise-linear pipeline tariff curve (NGMM Fig 3.5). Seven utilisation breakpoints define
  six segments; `tariff_mult` multiplies the flat base tariff from `ng_pipeline_arcs.csv`.
- The curve rises slowly to ~80% utilisation then sharply approaching 100%, which is how the
  NGMM hurdle-rate behaviour is encoded without a separate capacity-expansion QP. The
  breakpoint beyond 1.0 represents capacity that a expansion run could build.
