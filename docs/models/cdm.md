# Commercial Demand

C-CDM projects commercial-sector electricity and natural gas use in the nine census divisions, by
building type and end use, for every year from 2025 to 2050. It follows one of two AEO2026 cases,
the Counterfactual Baseline (`reference`) or High Electricity Demand, and responds to electricity
and natural gas prices supplied by a user or another C-NEMS model. Data centers are its focus;
server electricity is modeled the way EIA's National Energy Modeling System (NEMS) models it, and
so is the extra cooling, ventilation, office equipment and other load that NEMS attributes to
data centers inside each building type.

C-CDM does the job of the Commercial Demand Module of NEMS (`source/comm.f`, release tag
`AEO2026-Public-Release`) but is much smaller. It takes floorspace by building type from AEO2026
instead of projecting the building stock, and it scales every end use other than servers and
other uses to the AEO2026 national total instead of choosing equipment. C-CDM is not an
optimization model; each year is a direct calculation, and years are linked only through the
lagged prices in the price response.

This page sets out every equation the model evaluates. Code references are to `src/models/cdm/`.
The origin of each number is in `src/models/cdm/PROVENANCE.md`, and what the model can and cannot
be used for is in `src/models/cdm/EVALUATION.md`.

EIA documents the NEMS module in Commercial Demand Module of the National Energy Modeling System:
Model Documentation 2025 (<https://www.eia.gov/outlooks/aeo/nems/documentation/commercial/pdf/CDM_AEO2025.pdf>).
Where an equation here has a counterpart in its Appendix B, the text cites it, for example as EIA
eq. B-30, and the last table under Notation lists them all. That document describes the AEO2025
release; where it differs from the AEO2026 code, C-CDM follows the code and the text says so.

| Part | Code | Results | Section |
|---|---|---|---|
| Floorspace | `floorspace` in `commercial.py` | (input to all) | 1 |
| Data center share and multipliers | `data_center_share`, `multiplier` | (input to 3-5) | 2 |
| Servers | `_servers` | `cdm_electricity.csv`, `cdm_data_centers.csv` | 3 |
| End uses scaled to AEO2026 | `_scaled` | `cdm_electricity.csv`, `cdm_natural_gas.csv` | 4 |
| Other uses | `explicit_mels`, `_other_uses`, `_non_building` | both, and `cdm_summary.csv` | 5 |
| Price response | `price_factor`, `_apply_prices` | all | 6 |
| Data center electricity | `report` in `postprocessor.py` | `cdm_data_centers.csv` | 7 |

## Notation

Multi-letter names in upright type ($\mathrm{el}$, $\mathrm{oth}$) are labels, not products.
Energy is in trillion Btu (TBtu) and floorspace in million square feet unless a symbol says
otherwise. A prime marks a second index over the same set. The "Eq." column gives the equation
that defines a calculated quantity.

### Sets and indices

| Symbol | Description | Members |
|---|---|---|
| $r \in ℛ$ | Census division | new_england, middle_atlantic, east_north_central, west_north_central, south_atlantic, east_south_central, west_south_central, mountain, pacific |
| $b \in ℬ$ | Building type | assembly, education, food_sales, food_service, health_care, lodging, large_office, small_office, mercantile_service, warehouse, other |
| $s \in 𝒮$ | End use (service) | heating, cooling, water_heating, ventilation, cooking, lighting, refrigeration, data_center_servers ($\mathrm{srv}$), computers_office_equipment, other_uses ($\mathrm{oth}$) |
| $f$ | Fuel | electricity ($\mathrm{el}$), natural gas ($\mathrm{ng}$); distillate ($\mathrm{ds}$) enters only eqs. 12-13 |
| $k \in 𝒦$ | Explicit miscellaneous electric load | 20 loads, from distribution transformers to televisions (`MELS` in `data.py`) |
| $ℬ_k \subseteq ℬ$ | Building types load $k$ is shared to | `MEL_BUILDINGS` in `commercial.py` |
| $ℬ^{\mathrm{dc}} \subseteq ℬ$ | Building types whose other uses NEMS scales for data centers | all but food_sales, food_service and mercantile_service |
| $y \in 𝒴$ | Year | 2025 to 2050 |

### Inputs

| Symbol | Code or file | Description | Unit |
|---|---|---|---|
| $K_{r,b}$ | `nems_floorspace_2018.csv` | 2018 floorspace (NEMS `kflspc.txt`, from CBECS 2018) | million sqft |
| $A_{b,y}$ | `aeo2026_floorspace.csv` | Floorspace by building type, AEO2026 Table 22 | billion sqft |
| $e_{r,b,s,f}$ | `nems_eui_2018.csv` | 2018 energy use intensity (NEMS `kintens.txt`) | thousand Btu per sqft |
| $c_{b,s}$ | `nems_dc_multipliers.csv` | Data center intensity multiplier (NEMS `kmels.txt`, by case) | none |
| $I_y$ | `nems_server_index.csv` | Server annual energy index, 2018 = 1 (NEMS `kmels.txt`, by case) | none |
| $x_b$ | `nems_mel_share.csv` | Share of miscellaneous load modeled explicitly (NEMS `kmels.txt`, by case) | none |
| $q_k$ | `nems_mel_base.csv` | Base-year load of explicit load $k$ per sqft of eligible floorspace | Btu per sqft |
| $\iota_{k,y}$ | `nems_mel_index.csv` | Annual index of explicit load $k$, 2018 = 1 | none |
| $\varepsilon_{r,s,f}$ | `nems_price_elasticity.csv` | Short-run price elasticity of service demand (NEMS `ksdela.txt`) | none |
| $Q_{s,f,y}$ | `aeo2026_end_use.csv` | Consumption by end use, AEO2026 Table 5, by case | quadrillion Btu |
| $Q_y^{\mathrm{chp}}$ | `aeo2026_end_use.csv` | Natural gas input to commercial CHP and distributed generation, AEO2026 Table 22 | quadrillion Btu |
| $P_{r,f,y}^{\mathrm{ref}}$ | `aeo2026_prices.csv`, or `set_reference_prices` | Reference price path, fixed before the first run | 2025 USD per MMBtu |
| $P_{r,f,y}$ | `update_prices`, or the config price scales | Current prices | 2025 USD per MMBtu |
| $w_j$ | `LAG_WEIGHTS` | Weights on this year's and the two previous years' prices, 0.50, 0.35, 0.15 (`comm.f:7807`) | none |
| $\sigma$ | `SERVICES_INDEX` | Services output index applied to unexplained other-uses electricity; 1, placeholder for now | none |

### Calculated quantities

| Symbol | Description | Units | Eq. |
|---|---|---|---|
| $\varphi_{r,b}$ | Division share of a building type's floorspace | none | 1 |
| $F_{r,b,y}$ | Floorspace | million sqft | 2 |
| $d_y$ | NEMS data center share | none | 3 |
| $m_{b,s,y}$ | Data center multiplier on service demand | none | 4 |
| $S_{r,b,y}$ | Server electricity | TBtu | 5 |
| $W_{r,b,s,f,y}$ | Weight of a division and building type in a national end use | billion Btu | 6 |
| $E_{r,b,s,f,y}$ | Consumption before the price response | TBtu | 7, 13, 14 |
| $\Delta_{r,b,s,y}$ | Data center increment in electricity | TBtu | 8, 15 |
| $M_{k,r,b,y}$ | Explicit miscellaneous electric load | TBtu | 9-11 |
| $T_{r,b,y}$ | Other-uses service demand | TBtu | 12 |
| $N_{f,y}$ | Non-building other uses, national | TBtu | 16, 17 |
| $\kappa_{r,s,f,y}$ | Price factor | none | 19 |
| $D_{r,b,y}$ | Data center electricity | TBtu | 21 |

### Counterparts in EIA's documentation

NEMS names are the variable names EIA's documentation uses.

| Eq. | NEMS quantity | EIA eq. | How C-CDM differs |
|---|---|---|---|
| 1, 2 | `CMTotalFlspc` | B-1 to B-6 | AEO2026 floorspace and 2018 division shares in place of the stock model |
| 3 | `DatCtrShare` | B-30 | Follows the code, which differs from B-30 (section 2) |
| 4 | factor in `ServDmdExBldg` and `NewServDmd` | B-31, B-32 | One multiplier for all floorspace, as in the code (section 2) |
| 5 | `ServDmdExBldg` and `NewServDmd`, service 8 | B-34, B-35 | The server end use and its index `DataCtrServPenetration` came in AEO2026, after the documentation |
| 6 | `ServDmdExBldg` and `NewServDmd` | B-18, B-25, B-31, B-34, B-35 | Consumption intensity in place of service demand intensity; no shell or equipment efficiency |
| 7 | none | none | Scaling to AEO2026 in place of equipment choice and consumption (B-54 to B-109) |
| 8, 15, 21 | none | none | C-CDM's own decomposition |
| 9 to 11 | `MiscElQ` | B-43, B-44 | B-44 shares every load by floorspace; the code shares transformers and off-road vehicles differently (section 5.1) |
| 12 | `ServDmdExBldg` and `NewServDmd`, service 10 | B-17, B-36, B-45 to B-47 | Services index on the electricity part only, as in the code (section 5.2) |
| 13, 14 | `EndUseConsump`, service 10 | B-108 | Fuel shares as set in the code, which the documentation does not show (section 5.2) |
| 16, 17 | `CMNonBldgUse` | B-133 to B-146 | Remainder of AEO2026 Table 5 in place of NEMS's state data and STEO benchmarking |
| 18 | none | none | Division split by building consumption, placeholder for now (section 5.3) |
| 19 | `KElast` | B-110 | Reference path in place of the 2018 price |
| 20 | `EndUseConsump` | B-111 | Price factor only, without the rebound terms (section 6) |

## 1. Floorspace

NEMS projects floorspace with a stock model of construction and survival (EIA eqs. B-1 to B-6);
C-CDM takes AEO2026's projection instead. AEO2026 publishes floorspace by building type but not by
division, so C-CDM shares each building type's total to divisions as in 2018:

$$
\varphi_{r,b} = \frac{K_{r,b}}{\sum_{r'} K_{r',b}} \qquad (1)
$$

$$
F_{r,b,y} = 1000\thinspace A_{b,y}\thinspace \varphi_{r,b} \qquad (2)
$$

Holding the 2018 division shares is an assumption. The same Table 22 floorspace (Counterfactual
Baseline) is used in both cases; AEO2026 Table 5 total floorspace differs between the cases by
under 0.01 percent.

## 2. Data center share and multipliers

NEMS treats data centers as a growing share of most building types and raises the service demand
of each building type by a multiplier on that share (`comm.f:3430-3434`; EIA eqs. B-30 and B-31):

$$
d_y = 0.000002\thinspace n^3 - 0.00002\thinspace n^2 + 0.0173\thinspace n + 0.001626, \qquad n = y - 2019 \qquad (3)
$$

The origin is 2019 because NEMS evaluates the cubic at the number of years since the year after
the CBECS year (`comm.f:474-481`). The share is 0.105 in 2025, 0.192 in 2030, 0.375 in 2040 and
0.578 in 2050. EIA's code marks the coefficients "TODO - update coefficients"; the share, and the
increments of section 4 and 5 that it drives, are NEMS's decomposition, not an independent
estimate.

EIA's documentation of the cubic (eq. B-30) differs from the code. It gives -0.000002 for the
squared term where the code has -0.00002, has no constant where the code adds 0.001626, and sets
the share to zero up to the last year benchmarked to EIA's Short-Term Energy Outlook, a condition
the code does not have. The code is the same at the AEO2025 and AEO2026 release tags. Eq. 3
follows the code; the documented form would give 0.594 in 2050.

$$
m_{b,s,y} =
\begin{cases}
1 & \text{if } s = \mathrm{oth} \text{ and } b \notin ℬ^{\mathrm{dc}} \\
1 - d_y + c_{b,s}\thinspace d_y & \text{otherwise}
\end{cases}
\qquad (4)
$$

NEMS applies the multiplier to every building type and service, except other uses in food sales,
food service and mercantile/service (the other-uses branches at `comm.f:3851-3857` and the seven
like it). The multiplier for mercantile/service other uses in `kmels.txt` is 1.3 but is never
used. Eq. 4 is the factor of EIA's eq. B-31. EIA's eq. B-32 documents it in new floorspace for
large-office cooling only; the code applies it to new and surviving floorspace of every building
type (`comm.f:3600-3604`, `3625-3628`), and C-CDM, which does not separate the two, applies it to
all floorspace. With the shipped inputs $c_{b,s} > 1$ only for cooling, ventilation, computers and
office equipment, and other uses:

| Building type | Cooling | Ventilation | Computers and office equipment | Other uses |
|---|---|---|---|---|
| assembly | 2.1 | 1.9 | 10.6 | 1.8 |
| education | 1.0 | 1.0 | 1.4 | 1.0 |
| food_sales | 1.0 | 1.0 | 1.7 | 1.0 |
| lodging | 1.0 | 1.4 | 8.7 | 1.0 |
| small_office | 1.0 | 1.3 | 2.3 | 1.0 |
| mercantile_service | 1.2 | 1.4 | 2.6 | 1.3 (not applied) |
| warehouse | 1.4 | 2.6 | 5.2 | 1.4 |
| other, reference | 2.9 | 1.8 | 2.4 | 2.2 |
| other, high electricity demand | 8.7 | 2.4 | 8.0 | 2.5 |

All other entries are 1. NEMS represents standalone data centers inside the "other" building
type (`kmels.txt:96-97`), which is why its cooling and other-uses multipliers are the largest.

In the High Electricity Demand case NEMS also ramps the "other" building's multipliers in over the
first years of the projection (`ApplyRamp`, 1 in `kparm_higheldmd.txt:110` and 0 in
`kparm.txt:110`; `comm.f:3572-3598`, `3689-3714`). The ramp covers that building's major
services, among them cooling and ventilation, and computers and office equipment, but not other
uses (`comm.f:4492-4498`); in its middle years it also scales services whose multiplier is 1,
such as heating and lighting, below their base. C-CDM does not model the ramp; it applies eq. 4
in every year of both cases.

## 3. Servers

Server electricity is modeled, not scaled. NEMS multiplies the 2018 server intensity by
floorspace and by the server energy index (`comm.f:3667-3679`). This is the form of EIA's
eqs. B-34 and B-35, the demand of a minor service with its base-year efficiency indexed to one,
times an index. Servers became an end use of their own in AEO2026, so EIA's 2025 documentation has
no server equation; in the AEO2025 release, service 8 is office PCs (EIA eq. B-37):

$$
S_{r,b,y} = \frac{e_{r,b,\mathrm{srv},\mathrm{el}}\thinspace F_{r,b,y}\thinspace I_y}{1000} \qquad (5)
$$

The server multiplier is 1, so eq. 4 does not change servers. In the results and in the sums
below, servers are the electricity of end use $\mathrm{srv}$: $E_{r,b,\mathrm{srv},\mathrm{el},y} = S_{r,b,y}$. National $\sum_{r,b} S_{r,b,y}$ is
compared with AEO2026 Table 5 "Data Center Servers", which C-CDM does not use:

| Case | 2025 | 2030 | 2040 | 2050 |
|---|---|---|---|---|
| Counterfactual Baseline | 0.9994 | 0.9898 | 0.9744 | 0.9568 |
| High Electricity Demand | 0.9994 | 0.9899 | 0.9747 | 0.9575 |

The ratios are not tuned. C-CDM holds 2018 division shares, and NEMS applies its price response
to servers; its benchmarking to EIA's Short-Term Energy Outlook goes to non-building use
(`comm.f:9155`, `9283`), not to the server total it forms (`comm.f:8640-8660`). What causes the
drift is not established.

## 4. End uses scaled to AEO2026

Heating, cooling, water heating, ventilation, cooking, lighting, refrigeration and computers and
office equipment (electricity), and heating, cooling, water heating and cooking (natural gas),
follow the AEO2026 national totals. Each division and building type gets a weight from its 2018
intensity, floorspace and multiplier, the service demand of EIA's eqs. B-18 and B-25 (B-34 and B-35
for computers and office equipment) with the factor of B-31, but with consumption intensity in
place of service demand intensity, since C-CDM has no equipment efficiencies:

$$
W_{r,b,s,f,y} = e_{r,b,s,f}\thinspace F_{r,b,y}\thinspace m_{b,s,y} \qquad (6)
$$

$$
E_{r,b,s,f,y} = 1000\thinspace Q_{s,f,y}\thinspace \frac{W_{r,b,s,f,y}}{\sum_{r',b'} W_{r',b',s,f,y}} \qquad (7)
$$

Eq. 7 has no counterpart in NEMS, where equipment choice and efficiency turn service demand into
consumption (EIA eqs. B-54 to B-109). If every weight of an end use is zero in a year, C-CDM
shares it by floorspace instead (an assumption); with the shipped inputs this never happens. Using
NEMS's 2018 intensities as weights for later years is an assumption: NEMS's equipment
choice and shell efficiency move each division and building type differently, and C-CDM does not
model them. The data center increment is the part of eq. 7 that the multiplier added:

$$
\Delta_{r,b,s,y} = \frac{m_{b,s,y} - 1}{m_{b,s,y}}\thinspace E_{r,b,s,\mathrm{el},y} \qquad (8)
$$

It is $E - E/m$ with the normalization of eq. 7 held fixed, so it splits the allocated
electricity the way NEMS's multipliers do. It is neither all the load inside data
centers nor the difference a run without data centers would give, and it stays inside $E$.

## 5. Other uses

AEO2026 Table 5 "Other Uses" holds more than building load. EIA lists in it combined heat and
power, minor uses of natural gas and distillate, and uses outside buildings such as municipal water
services and telecommunications. NEMS adds its benchmarking residual to the same non-building
use (`comm.f:9035-9303`). C-CDM therefore builds the building part the NEMS way, without scaling,
and keeps the rest in separate rows.

### 5.1 Explicit miscellaneous electric loads

NEMS models 20 miscellaneous electric loads explicitly (`comm.f:3488-3528`). Each is a base-year
load per square foot, an annual index and the floorspace of the building types it is shared to
(the select case at `comm.f:3795-4506`). EIA writes this as a national load on the eligible
floorspace (eq. B-43), shared back to building types by floorspace (eq. B-44); the eligible
floorspace cancels, which gives

$$
M_{k,r,b,y} = \frac{\iota_{k,y}\thinspace q_k\thinspace F_{r,b,y}}{10^6}, \qquad b \in ℬ_k \qquad (9)
$$

and zero for other building types. Shredders are the one load NEMS computes for a building type
and then leaves out of its sum: they do not count in assembly or education (`comm.f:3829-3835`,
`3896-3902`). The `kmels.txt` header gives $q_k$ in trillion Btu per square
foot, but the scaling in `comm.f:3488-3528` shows Btu per square foot. EIA's eq. B-44 shares
every load by floorspace, but in the code two loads are shared differently. Distribution
transformers are shared by electricity use. NEMS shares them by each
building type's electricity in the previous year over national commercial purchased electricity,
which also counts non-building use and vehicle charging (`comm.f:3798-3801`, `8770-8772`,
`9302-9303`, `9458-9467`), so only part of the national load lands in buildings. C-CDM shares
all of it by the same year's electricity in every other service, $U_{r,b,y} = \sum_{s \neq \mathrm{oth}} E_{r,b,s,\mathrm{el},y}$
(an assumption):

$$
M_{\mathrm{xf},r,b,y} = \frac{\iota_{\mathrm{xf},y}\thinspace q_{\mathrm{xf}}}{10^6}\thinspace \Big(\sum_{r',b'} F_{r',b',y}\Big)\thinspace \frac{U_{r,b,y}}{\sum_{r',b'} U_{r',b',y}} \qquad (10)
$$

An estimate of the national difference, using C-CDM's same-year totals in place of NEMS's
previous-year ones and holding C-CDM's shares among buildings fixed: NEMS's rule would put 64 to
73 percent of the national transformer load in buildings. C-CDM's building other uses are higher,
and its non-building row (eq. 16) lower, by about 39 TBtu in 2025 and 60 TBtu in 2050 in the
Counterfactual Baseline (40 and 56 TBtu in High Electricity Demand); the data center total of
section 7 is higher by 0.3 TWh in 2025 and 3.7 TWh in 2050 (0.4 and 4.8 TWh).

Off-road electric vehicles are sized on 60 percent of warehouse and 40 percent of other
floorspace, then shared once over the other building types and once more over warehouses
(`comm.f:3502-3504`, `3806`, `4378`), as NEMS does; NEMS shares the warehouse part over surviving
warehouse floorspace, C-CDM over all of it (an assumption):

$$
M_{\mathrm{ev},r,b,y} = \frac{\iota_{\mathrm{ev},y}\thinspace q_{\mathrm{ev}}}{10^6}\thinspace \big(0.6\thinspace F_y^{\mathrm{wh}} + 0.4\thinspace F_y^{\mathrm{nw}}\big)\thinspace \frac{F_{r,b,y}}{F_y^{g(b)}} \qquad (11)
$$

where $F_y^{\mathrm{wh}}$ and $F_y^{\mathrm{nw}}$ are national warehouse and non-warehouse
floorspace and $g(b)$ picks the one that contains $b$. NEMS shares large video boards and
warehouse robots over surviving floorspace only; C-CDM uses all floorspace (an assumption).

### 5.2 Other uses in buildings

NEMS's other-uses service demand adds the unexplained electricity, the natural gas and distillate
parts and the explicit loads (`comm.f:3729-3745`, `3837-3841`; EIA eqs. B-17, B-36 and B-45 to
B-47). EIA's eq. B-36 writes the services index and the explicit share as multiplying all three
fuels; the code applies them to the electricity part only, and eq. 12 follows the code:

$$
T_{r,b,y} = \frac{\big((1 - x_b)\thinspace \sigma\thinspace e_{r,b,\mathrm{oth},\mathrm{el}} + e_{r,b,\mathrm{oth},\mathrm{ng}} + e_{r,b,\mathrm{oth},\mathrm{ds}}\big)\thinspace F_{r,b,y}}{1000} + \sum_{k} M_{k,r,b,y} \qquad (12)
$$

NEMS scales the whole of $T$ by the multiplier but keeps natural gas and distillate at their base
intensity, so electricity takes the rest (`comm.f:7448-7457`). This is EIA's eq. B-108 with the
fuel shares the code sets for other uses, which the documentation does not show:

$$
E_{r,b,\mathrm{oth},\mathrm{el},y} = m_{b,\mathrm{oth},y}\thinspace T_{r,b,y} - \frac{\big(e_{r,b,\mathrm{oth},\mathrm{ng}} + e_{r,b,\mathrm{oth},\mathrm{ds}}\big)\thinspace F_{r,b,y}}{1000} \qquad (13)
$$

$$
E_{r,b,\mathrm{oth},\mathrm{ng},y} = \frac{e_{r,b,\mathrm{oth},\mathrm{ng}}\thinspace F_{r,b,y}}{1000} \qquad (14)
$$

The whole data center increment in other uses is electricity:

$$
\Delta_{r,b,\mathrm{oth},y} = \big(m_{b,\mathrm{oth},y} - 1\big)\thinspace T_{r,b,y} \qquad (15)
$$

The services index $\sigma$ comes from NEMS's macroeconomic module (`comm.f:1566`), which has no
input file; C-CDM sets it to 1 (placeholder for now). It scales only the unexplained part, which
is zero in the High Electricity Demand case because $x_b = 1$ there.

### 5.3 Non-building uses and combined heat and power

NEMS estimates non-building use from state energy data and EIA's Short-Term Energy Outlook (EIA
eqs. B-133 to B-146). C-CDM instead keeps the rest of the AEO2026 row out of the building types:

$$
N_{\mathrm{el},y} = 1000\thinspace Q_{\mathrm{oth},\mathrm{el},y} - \sum_{r,b} E_{r,b,\mathrm{oth},\mathrm{el},y} \qquad (16)
$$

$$
N_{\mathrm{ng},y} = 1000\thinspace Q_{\mathrm{oth},\mathrm{ng},y} - \sum_{r,b} E_{r,b,\mathrm{oth},\mathrm{ng},y} - 1000\thinspace Q_y^{\mathrm{chp}} \qquad (17)
$$

Each national row, and the CHP row $1000\thinspace Q_y^{\mathrm{chp}}$, is shared to divisions by
the division's share of C-CDM building consumption of the same fuel:

$$
N_{r,f,y} = N_{f,y}\thinspace \frac{\sum_{b,s} E_{r,b,s,f,y}}{\sum_{r',b,s} E_{r',b,s,f,y}} \qquad (18)
$$

NEMS shares these uses with historical state data and population, which its public inputs do
not carry, so the division values of eq. 18 are placeholder for now. They are written as building
types `non_building` and `combined_heat_power` (natural gas only), service `other_uses`, so at
the reference prices the national total of every end use other than servers equals AEO2026 Table 5. With the shipped
inputs, in the Counterfactual Baseline:

| Year | Electricity, buildings | Electricity, non-building | Natural gas, buildings | Natural gas, non-building | Natural gas, CHP |
|---|---|---|---|---|---|
| 2025 | 617 | 1,332 | 102 | 1,049 | 117 |
| 2050 | 820 | 1,425 | 131 | 883 | 159 |

(TBtu.) About two-thirds of AEO2026's commercial other-uses electricity is outside the building
model.

## 6. Price response

Demand responds to the ratio of current to reference prices over three years. NEMS uses the same
form (EIA eq. B-110) but compares each price with a fixed base-year price, the 2018 price
(`comm.f:8922-8953`); C-CDM compares it with the reference path for the same year, so the factor
is 1 at the reference prices:

$$
\kappa_{r,s,f,y} = \prod_{j=0}^{2} \left(\frac{P_{r,f,y-j}}{P_{r,f,y-j}^{\mathrm{ref}}}\right)^{\varepsilon_{r,s,f}\thinspace w_j} \qquad (19)
$$

A year before 2025 has no price and contributes 1, as NEMS's lags at or before its elasticity
base year do (`comm.f:8948-8949`). Every result is multiplied by its factor:

$$
\hat{E}_{r,b,s,f,y} = \kappa_{r,s,f,y}\thinspace E_{r,b,s,f,y} \qquad (20)
$$

with $\hat{S}$, $\hat{\Delta}$ and the non-building rows scaled the same way (the non-building rows
use the other-uses factor, as NEMS uses the other-uses elasticity for its non-building use,
`comm.f:9098`). The CHP row is not price-responsive in C-CDM. Eq. 20 is EIA's eq. B-111 without
its rebound terms: NEMS also raises consumption of the first six end uses as equipment becomes
more efficient, and of heating and cooling as shells improve (`comm.f:8149-8179`); C-CDM models
neither efficiency change. The reference path defaults to the
AEO2026 commercial price of the case (Table 3) in every division. A coupled model may replace it
with its own baseline once, before the first run (`set_reference_prices`), and must never reset
it between iterations (`src/models/cdm/COUPLING.md`, item 2).

## 7. Data center electricity

$$
D_{r,b,y} = \hat{S}_{r,b,y} + \sum_{s} \hat{\Delta}_{r,b,s,y} \qquad (21)
$$

$D$ is a subset of the electricity results, not an extra load. Terawatt-hours are TBtu divided by
3.412 (3,412 Btu per kWh, as NEMS uses at `comm.f:12669-12678`). With the shipped inputs, in TWh:

| Case | Year | Servers | Cooling | Ventilation | Computers and office equipment | Other uses | Total |
|---|---|---|---|---|---|---|---|
| Counterfactual Baseline | 2025 | 104.8 | 8.7 | 7.1 | 8.8 | 4.7 | 134.1 |
| Counterfactual Baseline | 2050 | 426.4 | 54.9 | 45.2 | 21.0 | 36.4 | 583.8 |
| High Electricity Demand | 2025 | 104.8 | 20.3 | 8.0 | 12.0 | 5.2 | 150.2 |
| High Electricity Demand | 2050 | 783.9 | 143.5 | 50.9 | 31.8 | 43.6 | 1,053.7 |

## 8. Results

Results are written to `<output folder>/cdm/`.

| File | Content | Equations |
|---|---|---|
| `cdm_electricity.csv` | Gross end-use electricity by division, building type (with `non_building`), end use and year, TWh | 5, 7, 13, 16, 18, 20 |
| `cdm_natural_gas.csv` | Natural gas by division, building type (with `non_building` and `combined_heat_power`), end use and year, TBtu | 7, 14, 17, 18, 20 |
| `cdm_data_centers.csv` | Servers, the increment in each of the four services, and their total, by division, building type and year, TWh | 5, 8, 15, 21 |
| `cdm_summary.csv` | National totals beside the AEO2026 rows they follow or check against, TBtu, with purchased electricity formed from gross end use, Table 5 electric vehicle charging and on-site generation for own use | all |

Electricity is gross end use. Purchased electricity at commercial locations adds Table 5
"Purchased Electricity for Electric Vehicle Charging" and subtracts "On-site Generation for Own
Use"; C-CDM models neither and reports both from AEO2026 in the summary.
