# C-NGMM

C-NGMM is a natural gas market model in Python and Pyomo, patterned on the formulation of EIA's
Natural Gas Market Module (NGMM) in the National Energy Modeling System.

Throughout this document and the code, **C-NGMM** names this model and **NGMM** names EIA's
module in NEMS. Equation numbers of the form "NGMM Eq 7" cite the source documentation,
*The Natural Gas Market Module of the National Energy Modeling System*, AEO2025, July 2025.

C-NGMM is a quadratic program. It clears a multi-regional gas market over production,
interregional pipeline flow, storage, and LNG trade against sectoral demand, and produces
regional prices as the duals of the market-balance constraint. It runs standalone or couples to
an electricity model.

## Quick start

The environment is managed with pixi; dependencies are declared in `pyproject.toml`.

```bash
pixi install
pixi run ng          # full run: 9 regions, 6 years
pixi run ng-smoke    # 3 regions, 2 years
pixi run test # coupling tests
```

Or call the sequencer directly inside the environment:

```bash
pixi run python -m src.models.natural_gas.sequencer
```

`ng_model.py` builds the model but has no `__main__` block, so it cannot be run with `-m`, and
there are no command-line flags. Everything is set in `run_configs/basic_ng_config.toml`:

| Setting | Key |
|---|---|
| years | `summary_years` under `[common]` |
| region subset | `region_filter` under `[natural_gas]` |
| output location | `output_path` and `scenario_name` under `[common]` |

To force a solver, call `NGSequencer.solve_model(solver_name='highs')` rather than letting it
probe. `'highs'` and `'appsi_highs'` are not interchangeable -- see the solver note below.

### Solvers (HiGHS default but with correct interface)

No commercial solver is required. The model is a convex **quadratic** program, which constrains
which Pyomo interface can carry it:

| Interface | Quadratic objective | Use for this model |
|---|---|---|
| `SolverFactory('highs')`, pyomo ≥ 6.10 | yes, builds a Hessian | **yes** |
| `SolverFactory('appsi_highs')` | **no**, `generate_standard_repn(quadratic=False)`, raises `DegreeError` | no |
| Gurobi interfaces | yes | optional, faster |

`solve()` tries `appsi_gurobi, gurobi_direct, gurobi, highs, appsi_highs` in that order, so a
Gurobi-free environment lands on `highs` rather than the interface that raises. **Verified with
pyomo 6.10.1 + highspy 1.15.1 and no Gurobi present:**

```
objective  -385,180,372,245.78     Gurobi agrees to ~5e-11 relative
prices     all 54 regional prices identical to Gurobi to 4 decimals
tests      14 passed
```

The price agreement is the one that matters for coupling, since the regional prices are the duals
that cross the interface. **Do not pin pyomo below 6.10**, earlier versions have no HiGHS
interface that accepts a quadratic objective.

## At a glance

| | |
|---|---|
| Formulation | Convex quadratic program |
| Regions | 9 U.S. census divisions on a directed pipeline arc network |
| Years | 6 representative years in 5-year increments, 2025-2050 (configurable) |
| Sectors | residential, commercial, industrial, electric power, transportation |
| Within-year time | none an annual-rate market for now, will move to seasons later |
| Size | 1,476 variables, 1,530 constraints at full resolution |
| Solver | any convex-QP solver Pyomo reaches; Gurobi preferred, HiGHS works |

---

# Mathematical formulation

## Notation

### Sets

| Symbol | Code | Description | Count |
|---|---|---|---|
| r | `regions` | census divisions, the market nodes | 9 |
| ℓ | `lng_regions` | divisions with LNG export capability, a subset of r | 3 |
| y | `year` | representative model years | 6 |
| s | `sectors` | demand sectors | 5 |
| o, d | `regions` | origin and destination of an arc |  |
| (o,d) | `arcs` | directed pipeline arcs | 26 |
| k | `steps` | supply-curve **steps** (NGMM `SSTEP`) | 5 |
| k′ | `supply_breaks` | supply-curve **breakpoints** | 6 |
| j | `tariff_segs` | tariff-curve segments | 6 |
| j′ | `tariff_breaks` | tariff-curve breakpoints | 7 |
| m | `lng_segs` | LNG demand-curve segments | 3 |
| m′ | `lng_breaks` | LNG demand-curve breakpoints | 4 |

**A prime always marks a breakpoint set.** Breakpoints define a piecewise-linear curve; segments
lie between them, so a curve with *n* breakpoints has *n*−1 segments, and segment *k* spans
breakpoints *k* and *k*+1. The two are not interchangeable, a parameter indexed k′ has one more
entry per region-year than a variable indexed k.

### Parameters

| Symbol | Code | Description | Units |
|---|---|---|---|
| Q0 | `Q0[r,y]` | expected production anchor | Bcf/yr |
| P0 | `P0[r,y]` | expected supply price at Q0 | $/MMBtu |
| QBASE | `QBASE[r,k′,y]` | supply-curve quantity breakpoints | Bcf/yr |
| PBASE | `PBASE[r,k′,y]` | supply-curve price breakpoints | $/MMBtu |
| D | `demand[r,s,y]` | sectoral demand | Bcf/yr |
| CAN | `canada_supply[r,y]` | Canadian pipeline imports | Bcf/yr |
| QTAR, PTAR | `QTAR[o,d,j′,y]`, `PTAR[…]` | tariff-curve breakpoints | Bcf/yr, $/MMBtu |
| QLNG, PLNG | `QLNG[ℓ,m′,y]`, `PLNG[…]` | LNG export demand-curve breakpoints | Bcf/yr, $/MMBtu |
| γ | `gathering_charge[r]` | gathering and processing charge | $/MMBtu |
| c_LNG | `lng_cost[r]` | backstop LNG import cost | $/MMBtu |
| K_LNG | `lng_capacity[r]` | backstop LNG import capacity | Bcf/yr |
| S_w, S_i, S_x | `storage_working_cap[r]`, `storage_inject_cap[r]`, `storage_withdraw_cap[r]` | storage working gas and rate limits | Bcf, Bcf/yr |
| θ | `storage_opex` | storage operating cost | $/MMBtu |
| λ_pipe | `pipe_loss[o,d]` | pipeline transport loss | fraction |
| λ_intra | `intrastate_loss[r]` | loss on production within the region | fraction |
| λ_stor | `storage_loss[r]` | loss on storage withdrawal | fraction |
| λ_dist | `distribution_loss[r]` | loss on residential/commercial delivery | fraction |
| φ | `plant_fuel_frac[r]` | lease and plant fuel as a share of demand | fraction |
| ε_s | `demand_price_elasticity[s]` | own-price demand elasticity by sector |, |
| β | `bcf_to_mmbtu` | BCF -> MMBtu, `mmbtu_per_bcf` = 1.036e6 | MMBtu/BCF |

### Decision variables

All are non-negative.

| Symbol | Code | Description | Units |
|---|---|---|---|
| q | `sstep[r,k,y]` | production on supply segment *k* | Bcf/yr |
| f | `tar_step[o,d,j,y]` | pipeline flow on tariff segment *j* | Bcf/yr |
| x | `lng_export_step[ℓ,m,y]` | LNG exports on demand segment *m* | Bcf/yr |
| i | `lng_import[r,y]` | backstop LNG imports | Bcf/yr |
| inj | `stor_inject[r,y]` | storage injection | Bcf/yr |
| wd | `stor_withdraw[r,y]` | storage withdrawal | Bcf/yr |
| v | `var_demand[r,y]` | balancing slack, demand side | Bcf/yr |
| u | `unserved[r,y]` | unmet demand, **subset runs only** | Bcf/yr |

### Derived expressions

Each collapses a segment index, which is why segment symbols do not appear in the market balance:

```
production_total[r,y]     = q_min[r,y] + Σ_k  sstep[r,k,y]
pipe_flow[o,d,y]          = Σ_j  tar_step[o,d,j,y]
lng_export_demand[r,y]    = Σ_m  lng_export_step[r,m,y]      (zero for r ∉ ℓ)
```

## Objective

Minimize total system cost net of LNG consumer surplus, which is equivalent to maximizing total
surplus:

```
min   C_prod + C_gath + C_lngimp + C_trans + C_stor − S_lng
```

Each curve-based term is the area under its piecewise-linear curve. For the supply block, the
slope of segment *k* is

```
σ_k = ( PBASE[r,k+1,y] − PBASE[r,k,y] ) / ( QBASE[r,k+1,y] − QBASE[r,k,y] )
```

and integrating the segment gives a linear term plus a quadratic:

```
C_prod  = Σ_r Σ_y Σ_k ( PBASE[r,k,y]·q + ½ σ_k·q² ) · β          q = sstep[r,k,y]

C_gath  = Σ_r Σ_y  γ[r] · production_total[r,y] · β

C_lngimp= Σ_r Σ_y  c_LNG[r] · lng_import[r,y] · β

C_trans = Σ_(o,d) Σ_y Σ_j ( PTAR[o,d,j,y]·f + ½ τ_j·f² ) · β     τ_j = tariff slope

C_stor  = Σ_r Σ_y  θ · stor_inject[r,y] · β

S_lng   = Σ_ℓ Σ_y Σ_m ( PLNG[ℓ,m,y]·x + ½ π_m·x² ) · β           π_m = LNG demand slope
```

`S_lng` enters with a negative sign because the LNG export demand curve slopes downward and the
area beneath it is consumer surplus. Segments of zero width are skipped, which prevents division
by zero in regions with no capacity of a given type.

For a region subset, a penalty term is added:

```
+ Σ_r Σ_y  Π · unserved[r,y] · β        Π = 1000 $/MMBtu
```

Π is roughly 100× any plausible gas price, so the backstop is never economic and relieves only a
genuine shortfall.

## Constraints

**Market balance**: the price-forming relation, one per region-year:

```
production_total[r,y]·(1 − λ_intra[r])
  + lng_import[r,y]
  + canada_supply[r,y]
  + Σ_(o,d)→r  pipe_flow[o,d,y]·(1 − λ_pipe[o,d])
  + stor_withdraw[r,y]·(1 − λ_stor[r])
  + unserved[r,y]                                    ← subset runs only
=
  Σ_s demand[r,s,y]
  + λ_dist[r]·( demand[r,residential,y] + demand[r,commercial,y] )
  + φ[r]·Σ_s demand[r,s,y]
  + Σ_r→(o,d)  pipe_flow[o,d,y]
  + stor_inject[r,y]
  + lng_export_demand[r,y]
  + var_demand[r,y]
```

**Supply segment cap**: production on a segment cannot exceed its width:

```
sstep[r,k,y]  ≤  QBASE[r,k+1,y] − QBASE[r,k,y]
```

**Tariff segment cap** and **LNG export segment cap**, identically:

```
tar_step[o,d,j,y]     ≤  QTAR[o,d,j+1,y] − QTAR[o,d,j,y]
lng_export_step[ℓ,m,y] ≤  QLNG[ℓ,m+1,y]  − QLNG[ℓ,m,y]
```

**LNG import capacity**: note this bounds *imports*, not exports, and is defined on all regions:

```
lng_import[r,y]  ≤  K_LNG[r]
```

**Storage rates and balance**: injection and withdrawal are rate-limited, and the annual cycle
closes:

```
stor_inject[r,y]   ≤  S_i[r]
stor_withdraw[r,y] ≤  S_x[r]
stor_inject[r,y]   =  stor_withdraw[r,y]
```

Model doesn't currently use storage, just a placeholder for future work. The equality makes storage an annual identity rather than a seasonal arbitrage. The model has no
season index, so there is no within-year window over which storage could arbitrage; adding
seasonality would replace this with a genuine seasonal balance.

## The supply curve

This is the least documented part of the model, and it is where the input's cost tiers stop existing
and NGMM's steps begin.

Each region-year has an anchor (Q0, P0), the expected production point and the price there. The
model builds **six breakpoints** around that anchor, three below and three above, from two shape
vectors and a vector of elasticities:

```
crv_below = [0.30, 0.15, 0.05]      volume drops below the anchor
crv_above = [0.05, 0.15, 0.30]      volume rises above it
elas      = [0.8, 0.7, 0.5, 0.3, 0.2]   supply elasticity per segment
```

Quantities are cumulative products of the volume factors:

```
QBASE_1 = Q0 · (1−c⁻₁)(1−c⁻₂)(1−c⁻₃)        QBASE_4 = Q0 · (1+c⁺₁)
QBASE_2 = Q0 · (1−c⁻₂)(1−c⁻₃)               QBASE_5 = Q0 · (1+c⁺₁)(1+c⁺₂)
QBASE_3 = Q0 · (1−c⁻₃)                      QBASE_6 = Q0 · (1+c⁺₁)(1+c⁺₂)(1+c⁺₃)
```

Prices follow from the elasticity definition. With ε = (dQ/Q)/(dP/P), a volume change of CRV
implies a price change of CRV/ε, so:

```
PBASE_k = P0 · ∏ ( 1 ± CRV_k / ELAS_k )
```

> **Implementation note.** The NGMM AEO2025 document writes this as
> `PBASE = P0 · ∏ (1 ± CRV)/ELAS`, dividing the whole factor by an elasticity below 1. That form
> is non-monotonic with the default elasticities, it produces prices that *fall* between adjacent
> steps below Q0. This model uses the elasticity-correct form above, which is consistent with the
> in-segment price relation `P(Q) = PBASE·(1 + (1/ELAS)·(Q−QBASE)/QBASE)`. The published
> form is preserved in the docstring of `supply_pbase` in `data.py`.

Elasticities decline across segments (0.8 → 0.2), so the curve steepens as production rises above
the anchor, supply becomes progressively harder to expand.

## Price formation

Regional gas prices are the **duals of the market-balance constraint**, retrieved through
`poll_gas_price()` and returned in $/MMBtu keyed by `(region, year)`. Because the balance is an
equality and the objective is a true surplus integral, the dual is the marginal cost of an
additional unit delivered into that region, the competitive clearing price.

A consequence worth knowing, stated carefully because the obvious version of it is wrong.
Shipping one unit from `o` delivers only `1 - pipe_loss[o,d]` units to `d`, and transport cost is
piecewise over `tariff_segs`, so adjacent prices do **not** simply differ by the tariff. For an
arc carrying positive flow on the interior of an active segment:

```
(1 - pipe_loss[o,d]) * p_d - p_o = marginal transport cost on that segment
```

At zero flow this is an inequality rather than an equality, and at a segment breakpoint the
marginal value lies between the slopes of the two adjacent segments. The simpler
`p_d - p_o = tariff` holds only in the zero-loss, constant-tariff case, which is not this model.

---

# Data files

All inputs are UTF-8 CSV in `../../../input/natural_gas/`. Provenance, units and construction
notes live in `ng_data_pedigree.md` alongside them.

**Every file below is required.** `data.py` holds no fallback constants: a missing, unreadable,
malformed or empty input raises `ValueError` naming the file and the directory it looked in, so
the values a run solves on are always the values on disk. The two exceptions are described under
*Optional override files* below.

| File | Key columns | Feeds |
|---|---|---|
| `ng_supply_cost_tiers.csv` | region, cost_tier, capacity_bcf, cost_per_mmbtu | Q0 and P0 anchors |
| `ng_supply_anchors.csv` | region, year, q0_mult, p0_mult | year-varying path for Q0, P0 |
| `ng_base_demand.csv` | region, sector, demand_bcf_2025 | base-year demand |
| `ng_demand_growth.csv` | sector, annual_growth_rate | demand projection |
| `ng_demand_elasticity.csv` | sector, own_price_elasticity | price-responsive demand |
| `ng_pipeline_arcs.csv` | origin, destination, capacity_bcf, tariff_per_mmbtu | the arc network |
| `ng_storage.csv` | region, working_cap_bcf, inject_cap_bcf_yr, withdraw_cap_bcf_yr | storage limits |
| `ng_lng_export.csv` | region, year, demand_bcf | LNG export demand curve anchor |
| `ng_lng_import.csv` | region, capacity_bcf, cost_per_mmbtu | backstop imports |
| `ng_scalars.csv` | parameter, value, units, source | storage opex, LNG world price, defaults |
| `elec_to_ng_region_map.csv` | elec_region, ng_region | crosswalk for electricity coupling |

## Cost tiers in the input are not NGMM steps

`ng_supply_cost_tiers.csv` describes each region with a three-tier cost structure:

```csv
region,cost_tier,capacity_bcf,cost_per_mmbtu
new_england,low_cost,22.5,2.33
new_england,medium_cost,17.5,3.26
new_england,high_cost,...
```

**These tiers have no counterpart in NGMM.** NGMM's supply dimension is `(suptype, qps)`, supply
type by supply region, where `suptype` separates associated-dissolved from nonassociated gas, and
its elastic pieces are *steps* (`SSTEP`, Eq 8). The three tiers here are purely a feature of this
input format, and they stop existing the moment the model is built: they collapse into a single
anchor point,

```
Q0[r] = Σ over tiers of capacity_bcf              (total capacity)
P0[r] = Σ (capacity · cost) / Σ capacity          (quantity-weighted average cost)
```

and the five NGMM steps are then constructed around that anchor. So `sstep[r,'step3',y]` has no
relationship to `high_cost` in the CSV, one is a model step, the other an input aggregation tier.

The model uses NGMM's vocabulary throughout: a **step** carries volume (five of them, `step1` …
`step5`, indexing `sstep`), and a **break** is one of the six endpoints bounding those steps
(indexing `QBASE`/`PBASE`). Six breaks bound five steps.

`ng_supply_anchors.csv` then multiplies the anchor by year so it can follow a projected path:

```
Q0[r,y] = Q0[r] · q0_mult[r,y]        P0[r,y] = P0[r] · p0_mult[r,y]
```

with multipliers normalized to 1.0 in the base year. Missing entries default to 1.0, giving the
static anchor.

## The other two curves

**Pipeline tariff curve.** Defined by utilisation breakpoints and tariff multipliers rather than
absolute quantities:

```
util_break  = [0.0, 0.2, 0.6, 0.8, 0.95, 1.0, 1.4]
tariff_mult = [0.4, 0.55, 0.75, 0.95, 1.5, 3.0, 3.5]
```

`QTAR[o,d,j′,y] = util_break[j′] · capacity_bcf` and
`PTAR[o,d,j′,y] = tariff_mult[j′] · tariff_per_mmbtu`. The tariff rises steeply as an arc
approaches full utilisation, 0.4× the reference tariff when nearly empty, 3.0× at 100%. The final
breakpoint at 140% of rated capacity allows flow beyond nameplate at punitive cost, standing in
for capacity expansion that the model does not represent explicitly.

**LNG export demand curve.** Downward-sloping, anchored on a world price:

```
q_frac   = [0.0, 0.5, 0.85, 1.0]     fractions of the export demand in ng_lng_export.csv
p_factor = [2.0, 1.5, 1.1, 1.0]      multipliers on the world LNG price
```

So the first segment of export demand is willing to pay twice the world price and the last only
the world price itself. `world_price` comes from `ng_scalars.csv`.

## Optional override files

Two files, and only two, may be absent. Their values live in `ng_scalars.csv`, which is required,
so the file would carry only the exceptions:

```
ng_losses.csv      per-region override of distribution_loss, intrastate_loss,
                   storage_loss and plant_fuel_frac
ng_pipe_loss.csv   per-arc override of pipe_fuel_loss
```

Neither ships. Their absence logs at INFO, not as a warning, and the scalars apply everywhere.
`ng_losses.csv` overrides per region *and* per column, so a file naming one region and one column
leaves everything else on its scalar.

A healthy run is quiet: one `... loaded from CSV` line per input, plus two INFO lines noting the
absent override files. A `ValueError` naming a file is the system working.

---

# Regional subsetting

`--regions` runs a subset of the nine divisions. Only arcs **internal** to the subset are kept: an
arc with one endpoint outside has no counterparty balance constraint, and leaving it would let gas
appear from, or vanish into, a region the model no longer represents.

Dropping regions removes suppliers as well as consumers, so a net-importing subset would otherwise
be infeasible. `var_demand` sits on the demand side of the balance and can absorb surplus but never
cover a shortfall. Subset runs therefore carry the `unserved` variable described in the objective:
it stays at zero when the subset can supply itself, and any volume it takes is reported by region
and year. It is created **only** for a strict subset, so the full nine-region model is unaffected.

Results from a subset are not comparable to a full run, the omitted regions take their
production, demand, and trade with them. Subsets are for exercising mechanics and structure, not
for calibration.

# Coupling interface

`ng_model.py` exposes the methods another model uses to drive it:

| Method | Direction | Purpose |
|---|---|---|
| `update_demand` | in | write sectoral demand, e.g. electric-power gas burn |
| `update_supply_capacity` | in | rebuild QBASE/PBASE from new Q0 |
| `update_canada_supply` | in | set Canadian import volumes |
| `set_reference_prices` | in | capture the reference for elastic demand |
| `update_demand_from_price` | internal | apply own-price elasticities against that reference |
| `poll_gas_price` | out | regional prices, the balance duals, $/MMBtu |
| `poll_total_gas_demand` | out | total demand by region-year |

Because the model is a QP it carries no simplex basis, so a warm start is solution values plus
coupling parameters rather than a basis.

## Gauss-Seidel coupling to an electricity model

The electricity-side half of the exchange ships with this distribution:

```
src/integrator/ng_coupling.py            transfer functions and the contract check
tests/natural_gas/test_ng_coupling.py    contract tests (pytest)
```

`ng_coupling.py` provides `poll_ng_gas_demand` (electricity → gas burn by gas region),
`update_ng_fuel_adj` (gas price → electricity fuel-cost adjustment), `load_ng_region_map`, and
`check_coupling_contract`, which validates the electricity model up front rather than failing
mid-iteration.

**Index order is declared, not discovered.** `generation_total` is built in a fixed order,
`(region, tech, step, year, hour)`, at `src/models/electricity/model_sets.py`, so `ng_coupling.py`
declares it in `GENERATION_INDEX` rather than inferring it at runtime. Inference is not possible
here in any case: the index is a flat list of 5-tuples with no named constituent sets to read, and
regions and techs are both strings drawn from overlapping numerals, so value membership cannot
separate them. A test builds a real `PowerModel` and checks the declared positions against it.

The electricity model needs one addition: an `ng_fuel_adj` Param indexed exactly like
`supply_price`, declared `within=pyo.Reals, mutable=True`, added to the dispatch-cost term
alongside `supply_price`. Both are $/GWh, so no conversion is needed. See the `ng_coupling.py`
module docstring for the full contract and the failure modes that produce plausible-looking but
wrong results.

# Requirements

Declared in `pyproject.toml` and managed with pixi, `pixi install` is all that is needed.
Python 3.14, pyomo ≥ 6.10 (the floor for a QP-capable HiGHS interface), pandas, numpy, and
highspy. No commercial solver is required; see the solver table under *Quick start*.

# How this differs from NGMM in NEMS

The short answer: **the market economics are a faithful port; the network topology and the
endogenous capacity decisions are not.** The difference is *not* only seasonality and regionality.

Comparisons below are against *The Natural Gas Market Module of the National Energy Modeling
System*, AEO2025 documentation, July 2025. Equation numbers are NGMM's.

## The objective function, same formulation

NGMM's objective (its Eq 7) maximizes consumer plus producer surplus net of transport costs. Term
for term it is the same expression this model minimizes the negative of:

| Term | NGMM Eq 7 | This model |
|---|---|---|
| Supply curve area | `Σ PBASE·SSTEP + ½·SSTEP²·(ΔPBASE/ΔQBASE)` | identical |
| Pipeline tariff area | trapezoid area under the tariff curve | identical |
| Gathering charge | `Σ P_gath · FLOWS2H` | identical, on production |
| LNG export demand area | `Σ PLNG·LNG + ½·LNG²·(ΔPLNG/ΔQLNG)` | identical |
| Storage operating cost | not in Eq 7 | **added** |
| Backstop LNG import cost | not in Eq 7, imports are exogenous | **added** |

The two quadratic surplus integrals are algebraically the same expression, not merely analogous.
The two extra terms are additions on this side, both linear.

## The supply curve, same structure

Identical in construction: a stepwise piecewise-linear curve defined by `QBASE`/`PBASE`
breakpoints, with `SSTEP` volumes on each segment, a committed-production floor `QMIN`, and the
accounting identity of NGMM Eq 8:

```
PROD = Σ_step SSTEP + QMIN
```

That identity is `production_total[r,y]` here. Segment-range constraints (NGMM Eq 18-20) are the
same, and the breakpoints are built around an anchor by the same cumulative-product rule.

**One structural difference.** NGMM indexes supply by `(suptype, qps)`, supply type by supply
region, where `suptype` distinguishes **associated-dissolved from nonassociated** gas, so each
supply region carries two curves. This model carries **one aggregate curve per region**. Where a
supply module supplies the anchor, the NA/AD split is resolved before the QP sees it.

There is also a documented departure in the price-breakpoint formula, described under
*The supply curve* above: NGMM's published form is non-monotonic with the default elasticities, and
this model uses the elasticity-correct form.

## The constraints, different topology

This is the substantive difference, and it is not about resolution.

**NGMM is a three-layer network**: supply regions → hubs → demand regions, with a separate balance
at each layer.

| NGMM constraint | Eq | Role |
|---|---|---|
| Supply Mass Balance | 9 | production in a supply region = flow out to hubs |
| Flow Balance at Hubs | 11 | flow into a hub = flow out of it |
| Demand Mass Balance | 10 | flow from hubs into a demand region = consumption + distribution + storage + intrastate + plant fuel |

**This model has one layer.** Each census division is simultaneously supply node, hub, and demand
node, and a single `demand_balance[r,y]` merges all three equations. The pipeline network connects
divisions directly rather than connecting supply regions to hubs to demand regions.

That has consequences worth understanding: there is no separate hub price, no supply-region-to-hub
gathering leg distinct from the hub network, and no representation of a demand region served by
several hubs. The dual of the single balance is the regional price, playing the role NGMM's hub
balance dual plays for the Henry Hub spot price.

Remaining constraint correspondence:

| NGMM constraint | Here |
|---|---|
| Supply Accounting (8) | yes, `production_total` |
| LNG Export Demand Mass Balance (14) | yes, `lng_export_demand` + segment caps |
| Tariff Curve Quantity Balance | yes, `pipe_flow` = Σ segments |
| Storage Withdrawal / Injection Balance | yes in form, **annual not seasonal** |
| Supply / Hub / Demand mass balances (9, 10, 11) | **merged into one** |

## What is absent entirely

These are not simplifications of an existing constraint, the constraint does not exist here:

- **Endogenous pipeline capacity expansion.** NGMM builds capacity when volumes and prices warrant.
  Here capacity is fixed; the tariff curve's final breakpoint at 140% of rating allows flow beyond
  nameplate at punitive cost, standing in for expansion without representing it.
- **LNG export capacity build decisions.** NGMM decides liquefaction capacity by net present value
  against world LNG prices. Here export capacity is exogenous and only utilisation is solved.
- **Distributor tariff regressions.** NGMM regresses markups by sector and census division to
  produce citygate and delivered end-use prices. Here there are loss fractions and a plant-fuel
  share, and no end-use price formation.
- **Seasonal storage.** NGMM injects and withdraws across seasons against working-gas accounting.
  Here the annual identity `inject = withdraw` closes the cycle within the year, so storage cannot
  arbitrage, there is no within-year window for it to arbitrage across.
- Mexico, Alaska, renewable natural gas, supplemental supplies, and STEO benchmarking.

## Resolution differences

For completeness, the differences that *are* only resolution:

| Dimension | NGMM | This model |
|---|---|---|
| Within-year time | monthly, aggregated to 3 seasons for the power interface | annual rate |
| Projection years | every year | 6 representative years |
| Demand regions | Lower 48 at state level | 9 census divisions |
| Flow network | 11 flow regions over state-level nodes | 9 census divisions |
| Electric-power interface | 16 NNGEMM regions × 3 seasons | 9 census divisions, annual |
| Solver | CPLEX | any convex-QP solver via Pyomo |

## Summary

| Component | Verdict |
|---|---|
| Objective function | **Same formulation**, plus two linear cost terms |
| Supply curve | **Same structure**; one aggregate curve per region instead of NA/AD split |
| Network topology | **Different**, one layer instead of three |
| Capacity expansion, LNG builds, delivered prices | **Absent** |
| Storage | Same variables, annual rather than seasonal |
| Seasonality, regionality | Coarser |

So a fair description is: a faithful reduced-form implementation of NGMM's market-clearing
economics on a collapsed network, at coarser resolution, without the endogenous capacity and
delivered-price blocks. It is not a substitute for NGMM, and questions that depend on monthly or
state-level resolution, on capacity expansion, on delivered end-use price formation, or on Mexican
or Alaskan volumes are outside what it can answer.

# Status

Calibrated against EIA Annual Energy Outlook 2026: national production and Henry Hub within the
tolerances recorded in the project's calibration notes.
