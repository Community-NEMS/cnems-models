# C-CDM evaluation

What C-CDM can be used for now, what it cannot, and how far it is from EIA's Commercial Demand
Module. Equations are in `docs/models/cdm.md`; sources in `PROVENANCE.md`.

## Current status

C-CDM runs on its own (`pixi run cdm`) for the AEO2026 Counterfactual Baseline and High Electricity
Demand cases, 2025 to 2050. It is not coupled to any other C-NEMS model; `COUPLING.md` says what
coupling would take.

At the reference prices, the end uses it scales to AEO2026 equal their Table 5 national totals,
and the other-uses rows add up to Table 5 by construction (building part, non-building part and
combined heat and power). Servers are modeled, not scaled, and come within 0.1 to 4.3 percent of
Table 5 "Data Center Servers" in both cases:

| Case | 2025 | 2030 | 2040 | 2050 |
|---|---|---|---|---|
| Counterfactual Baseline | 0.9994 | 0.9898 | 0.9744 | 0.9568 |
| High Electricity Demand | 0.9994 | 0.9899 | 0.9747 | 0.9575 |

(ratio of C-CDM to AEO2026). The tests in `tests/cdm/` pin these ratios and the result files.

Base year. NEMS's 2018 floorspace and intensities (`kflspc.txt`, `kintens.txt`) give 96.5 billion
square feet and 967 TWh of electricity, against CBECS 2018's 96.4 billion square feet (Table B1,
row 4) and 1,196 billion kWh (Table E5, row 5). By end use, in TWh (CBECS: Table E5, row 5):

| End use | NEMS 2018 intensities | CBECS 2018 |
|---|---|---|
| Heating | 69.3 | 70 |
| Cooling | 180.6 | 170 |
| Water heating | 23.5 | 24 |
| Ventilation | 218.1 | 213 |
| Cooking | 25.8 | 26 |
| Lighting | 206.8 | 208 |
| Refrigeration | 107.5 | 108 |
| Servers, computers and office equipment | 111.6 | 93 (computing 79, office equipment 14) |
| Other | 23.5 | 284 |
| Total | 966.8 | 1,196 |

NEMS's "other" is small because NEMS models most miscellaneous load explicitly instead
(`docs/models/cdm.md`, section 5). Cooling and ventilation are 10.6 and 5.1 TWh above CBECS, and
computing 18.6 TWh above; the reasons are not established here.

Division split. C-CDM divides each end use among divisions with NEMS's 2018 floorspace shares and
intensities. Against CBECS 2018 (Table C13, rows 47 to 58, electricity; Table C23, rows 46 to 57,
natural gas), each division's share of national electricity is within 0.8 percentage points, both
in NEMS's 2018 inputs and in C-CDM's 2025 results. For natural gas, NEMS's 2018 inputs match CBECS
to within 1 trillion Btu in eight divisions; in East South Central they give 103 trillion Btu
against CBECS's 126, a 4.5 percent share against 5.5 percent. We should think about updating this as necessary.

## Caveats on use

1. C-CDM is not an independent projection of commercial demand. Most end uses follow AEO2026's
   national totals; what C-CDM models is servers, other uses in buildings, the split by division
   and building type, and the response to prices away from the reference path.
2. The data center increment in cooling, ventilation, computers and office equipment, and other
   uses is NEMS's decomposition: a share of each building type, from a cubic EIA marks "TODO -
   update coefficients", times NEMS's multipliers. The share reaches 0.578 by 2050. The increment
   is the part of each end use the multiplier accounts for (eqs. 8 and 15); it is neither all load
   inside data centers nor the change a run without data centers would give. NEMS represents
   standalone data centers inside the "other" building type, which holds 61 percent of the data
   center total in 2050 in the Counterfactual Baseline and 68 percent in High Electricity Demand.
3. Boundary. LBNL's 2024 United States Data Center Energy Usage Report counts whole-facility
   electricity (servers, storage, network and infrastructure, through PUE; PDF pp. 7, 16, 40):
   about 76 TWh in 2018 and 176 TWh in 2023 (p. 7), and about 325 to 580 TWh in 2028 (p. 8).
   C-CDM's servers are IT load only (105 TWh in 2025); servers plus the increment in the four
   services is 134 TWh in 2025 and 229 TWh in 2030 in the Counterfactual Baseline. The two are not
   the same quantity, and why C-CDM's total is below LBNL's 2023 figure is not established here.
4. About two-thirds of AEO2026's commercial other-uses electricity (1,332 of 1,949 trillion Btu in
   2025) and nine-tenths of its other-uses natural gas lie outside the building model: uses
   outside buildings, combined heat and power, and the benchmarking residual NEMS books as
   non-building use. C-CDM keeps them as `non_building` and `combined_heat_power` rows. Their
   split by division is placeholder for now (building-consumption shares; NEMS uses historical
   state data and population).
5. Division shares within each building type are held at 2018 values, and the 2018 intensities
   are used as weights in every year. NEMS's equipment choice, shell efficiency and stock turnover
   move divisions and building types differently; C-CDM does not model them.
6. The price response compares prices with a reference path for the same year; NEMS compares with
   a fixed base-year price. The elasticities come from `ksdela.txt`, last modified in 2000 by its
   header; rows 8 and 9 of that file still carry the old office equipment labels and apply to
   servers and to computers and office equipment.
7. NEMS benchmarks consumption to EIA's Short-Term Energy Outlook and books the difference as
   non-building use (`comm.f:9155`, `9283`); C-CDM's non-building rows, the remainder of AEO2026
   other uses, carry it.
8. In the High Electricity Demand case NEMS ramps the "other" building's multipliers for its major
   services and for computers and office equipment, not other uses, in over the first years of
   the projection; C-CDM applies the full multiplier in every year, so its cooling, ventilation,
   and computers and office equipment increments for that building type are larger than NEMS's
   in those years.
9. The services output index NEMS applies to unexplained other-uses electricity comes from its
   macroeconomic module and has no input file; C-CDM sets it to 1 (placeholder for now). It
   scales 30 to 40 trillion Btu a year in the Counterfactual Baseline and nothing in the High
   Electricity Demand case.
10. Electricity results are gross end use. Purchased electricity also needs electric vehicle
    charging and on-site generation for own use, which C-CDM takes from AEO2026 rather than
    modeling.
11. Distribution transformers. NEMS shares them over national purchased electricity, which also
    counts non-building use and vehicle charging, so only part lands in buildings; C-CDM puts all
    of it in buildings (an assumption). An estimate with C-CDM's same-year totals, holding its
    shares among buildings fixed, puts building other uses higher and the non-building row lower
    by about 39 TBtu in 2025 and 60 TBtu in 2050 in the Counterfactual Baseline, and the data
    center total higher by 0.3 and 3.7 TWh (`docs/models/cdm.md`, section 5.1).

## Before others rely on it

- Review by the C-NEMS team.
- A coupling design (`COUPLING.md` lists what it has to cover), including how a price-taking
  demand model sits in the control loop.

## How far it is from EIA's Commercial Demand Module

| NEMS component | C-CDM |
|---|---|
| Floorspace stock model (new construction, survival) | AEO2026 floorspace by building type; 2018 division shares |
| Service demand from 2018 intensities | Same intensities, used as weights for scaled end uses |
| Equipment choice, efficiency, shell | Not modeled; scaled to AEO2026 totals |
| Servers (intensity times index) | Same equation, same inputs |
| Data center share and multipliers | Same equation, same inputs; High Electricity Demand ramp not modeled |
| Explicit miscellaneous electric loads | Same loads, indices and building eligibility; transformers shared by same-year electricity, all of them to buildings |
| Non-building use and benchmarking | Taken as the remainder of AEO2026 other uses |
| Distributed generation and CHP | CHP natural gas input from AEO2026 Table 22; generation not modeled |
| Short-run price response | Same form and elasticities; reference path instead of base-year price |
| Distillate and minor fuels | Not modeled (distillate enters only the other-uses fuel split) |

## Most detailed parts of the model

The server equation, the data center share and multipliers, and the explicit miscellaneous
loads follow the cited `comm.f` lines, with the departures listed above, and every input is traced
to a NEMS file or an AEO2026 row. `build_inputs.py` rebuilds every input from its source.
