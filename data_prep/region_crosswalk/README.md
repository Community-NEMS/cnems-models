# Region-crosswalk workproduct (reference only)

These files are workproduct retained for reference. They are not maintained and are not used in
a model run.

They built the population-weighted electricity ↔ natural gas region crosswalks that ship with the
model:

- `build_region_weights.py` regenerates the crosswalks from the natural gas model's 1:1
  electricity-to-gas region map and 2020 Census state populations.
- `elec_to_ng_region_map_weighted.csv` and `elec_region_state_coverage.csv` are its intermediate
  products: the 1:1 map with population weights added, and the state-level allocation that every
  weight is derived from.

The work was AI designed and executed in mid-2026. It is kept as provenance for the shipped
crosswalks, and as a pattern to follow should other model regionalities need pairing. The
crosswalk documentation describes the method, the columns, and the known caveats.
