# Electricity ↔ natural gas region weights

Population-based weights bridging the 25 EIA Electricity Market Module (EMM) regions and
the 9 natural gas regions (US Census divisions).

## Files

| File | Rows sum to 1.0 over | Purpose |
|---|---|---|
| `workproduct/elec_to_ng_region_map_weighted.csv` | each `ng_region` | The 1:1 map plus a `weight` column: each electricity region's share of its assigned gas region's population. Drop-in superset of `../../natural_gas/elec_to_ng_region_map.csv` — same rows, same order. |
| `elec_to_ng_crosswalk.csv` | each `elec_region` | Fractional elec → gas. Splits an electricity region across every census division it actually touches. |
| `ng_to_elec_crosswalk.csv` | each `ng_region` | Fractional gas → elec. Splits a census division across every electricity region that actually touches it. |
| `workproduct/elec_region_state_coverage.csv` | each `state` | Provenance: the state-level allocation everything above is derived from. |

`workproduct/build_region_weights.py` regenerates all four from scratch. Run it from anywhere;
it resolves paths relative to itself, reading `../../../natural_gas/elec_to_ng_region_map.csv`,
writing the two crosswalks the model reads into this directory and the weighted map and
coverage file, which are intermediate products, into `workproduct/`.

All four CSVs are plain header-plus-data with **no comment lines** — they parse with a bare
`pd.read_csv(...)`, no `comment='#'` needed. Their provenance lives here instead: every
`weight` and `state_pop_share` in this directory is derived from 2020 Census state
populations allocated across electricity regions, by the method below.

### Columns

| Column | Meaning |
|---|---|
| `elec_region` | EMM region ID, 1–25, matching `../../natural_gas/elec_to_ng_region_map.csv`. The ID is the join key; `elec_region_name` is descriptive only. |
| `ng_region` | Census division name (`new_england`, `middle_atlantic`, …). |
| `weight` | Fraction of population, on the basis given by the "Rows sum to 1.0 over" column above. Always 4 decimals. |
| `state_pop_share` | *(coverage file)* Share of that state's 2020 Census population assigned to that electricity region. Sums to 1.0 across regions for each of the 48 contiguous states + DC. |
| `pop_thousands` | *(coverage file)* `state_pop_share` × state population, in thousands — the cell population the weights are aggregated from. |

Rows whose weight rounds below 0.0005 are dropped from the two fractional crosswalks, so a
region absent from a division simply has no row rather than a zero.

Use the weighted map to disaggregate a gas-region quantity down to electricity regions
under the existing 1:1 assignment. Use the two crosswalks when the 1:1 assignment itself is
the thing you don't trust — they are the two margins of one population matrix, so
round-tripping a quantity through both conserves totals.

## Extensive vs. intensive values in code

`src/integrator/region_crosswalk.py` reads these files. Its registry keys each file by the
ordered `(source, destination)` pair of `ModelType` it *allocates* for, i.e. the file whose
rows sum to 1.0 per **source** region:

| Registry key | File |
|---|---|
| `(NATURAL_GAS, ELECTRICITY)` | `ng_to_elec_crosswalk.csv` |
| `(ELECTRICITY, NATURAL_GAS)` | `elec_to_ng_crosswalk.csv` |

`crosswalk_values(values, source, destination, kind)` then picks the file by what kind of
value is crossing:

- **`QuantityKind.EXTENSIVE`** (demand, volume, capacity): a source region's total is *spread*
  over its destination regions, so the weights must sum to 1.0 per source. Uses the
  `(source, destination)` entry directly. Totals are conserved.
- **`QuantityKind.INTENSIVE`** (price, rate, share): each destination region is the *weighted
  average* of the source regions it touches, so the weights must sum to 1.0 per destination.
  Uses the `(destination, source)` entry, the other margin of the same matrix. If only some
  source regions carry a value (a region-filtered run), the average is renormalised over the
  ones present and a warning names the destinations with partial coverage.

Sending a price through the extensive path is the classic mistake: a region wholly inside one
gas division would receive only its population share of that division's price.

### Worked example: one gas region, two electricity regions

Gas region `G` is covered by electricity regions `A` (70% of `G`'s population) and `B` (30%),
and both `A` and `B` lie wholly inside `G`. The two margins of that matrix are:

| `ng_to_elec` (sums to 1 per gas region) | | `elec_to_ng` (sums to 1 per elec region) | |
|---|---|---|---|
| `G -> A` | 0.7 | `A -> G` | 1.0 |
| `G -> B` | 0.3 | `B -> G` | 1.0 |

**Quantity, extensive.** `G` consumes 100 Bcf. Crosswalking `NATURAL_GAS -> ELECTRICITY`
with `EXTENSIVE` uses `ng_to_elec`:

    A = 0.7 x 100 = 70 Bcf
    B = 0.3 x 100 = 30 Bcf        total 100 Bcf, conserved

**Price, intensive.** Gas in `G` costs 3.00 $/MMBtu. The same direction with `INTENSIVE`
uses `elec_to_ng`:

    A = 1.0 x 3.00 = 3.00 $/MMBtu
    B = 1.0 x 3.00 = 3.00 $/MMBtu   everyone inside G pays G's price

Had the price gone through the extensive weights, `A` would read 2.10 and `B` 0.90, which is
not a price of anything.

If `B` instead straddled `G` (60%) and a second gas region `H` (40%) priced at 5.00 $/MMBtu,
its `elec_to_ng` row would be `B -> G 0.6, B -> H 0.4` and the intensive result becomes
`B = 0.6 x 3.00 + 0.4 x 5.00 = 3.80 $/MMBtu`. With `H` absent from the run, the average
renormalises to `B = 3.00` and the partial-coverage warning fires. The unit tests in
`tests/integrator/test_region_crosswalk.py` pin both behaviours on the real files.

## Method

Census divisions are exact unions of states, so a single **electricity region × state**
population matrix determines both directions. Each state's 2020 Census resident population
is split across the EMM regions covering it (shares per state sum to 1.0; the build asserts
this, and fails loudly if a state is unallocated). Cell populations then aggregate to
either margin. Weights are rounded to 4 decimals by largest remainder, so every group sums
to exactly 1.0000 as written rather than merely within tolerance.

Region boundaries follow the EIA EMM map,
<https://www.eia.gov/outlooks/aeo/pdf/nerc_map.pdf>, using each region's NERC/ISO subregion
and geographic name (TRE = Texas, MISW = Upper Mississippi Valley, PJMW = Ohio Valley,
BASN = Great Basin, and so on).

Alaska and Hawaii are excluded — Pacific-division states with no EMM region, being separate
interconnects. Pacific weights therefore describe the modeled footprint, not the full
division.

## How well does the 1:1 map hold up?

Population-weighted, the 1:1 assignment places **89.1%** of population in the right census
division. No region is assigned to a division holding a minority of its population, so
every line in the source map is defensible as a first choice. The weak ones:

| Electricity region | Assigned gas region | Population actually there | Where the rest goes |
|---|---|---|---|
| MISW (MISO West) | west_north_central | **58.2%** | Wisconsin (40.7%) is east_north_central |
| PJMW (PJM West) | east_north_central | **61.1%** | western PA, WV, KY — spans four divisions |
| PJME (PJM East) | middle_atlantic | **67.7%** | MD/DE/DC (32.3%) are south_atlantic |
| MISC (MISO Central) | east_north_central | 76.9% | eastern Missouri |
| SRSE (SERC Southeast) | south_atlantic | 77.3% | Alabama, eastern Mississippi |
| MISS (MISO South) | west_south_central | 81.3% | Mississippi |
| NWPP (Northwest) | pacific | 86.1% | Idaho, western Montana |

MISW, PJMW and PJME are the three worth using the fractional crosswalks for. PJMW is the
most genuinely split region in the set — it straddles middle_atlantic, east_north_central,
south_atlantic and east_south_central with no division holding two-thirds of it.

Fifteen of the 25 regions sit entirely inside one census division and are unaffected by the
choice of method: ISNE, NYCW, NYUP, PJMC, PJMD, SRCA, SPPC, SPPN, TRE, CANO, CASO, RMRG,
BASN, MISE, FRCC.

## Caveats

State shares are round numbers (0.85, 0.55, …) reflecting approximate service-territory
geography, not measured sub-state populations. The EMM map itself states that "exact
regional boundaries do not necessarily correspond to state borders," so a state-level
allocation is the natural resolution limit of this approach.

The largest remaining judgment calls are states split across several RTOs, where the split
is estimated rather than looked up:

- **Missouri** (MISC / SPPC / SRCE, 45/45/10) — divided between Ameren, Evergy and TVA.
- **Kentucky** (SRCE / PJMW, 60/40) — LG&E-KU and TVA against EKPC and AEP Kentucky.
- **California** (CANO / CASO, 35/65) — the north/south split point is approximate.
- **Idaho / Wyoming** (NWPP vs BASN) — the Idaho Power and PacifiCorp footprints.

These are good enough for rough proportional allocation but are not a substitute for an
actual service-territory-to-county crosswalk. If a result turns out to be sensitive to any
of the four splits above, that is the place to invest in better data.
