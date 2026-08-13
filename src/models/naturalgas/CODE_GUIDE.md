# C-NGMM code guide

A walkthrough of the natural gas model, written for review. It complements the two docs:

- [README.md](README.md), the mathematical formulation, data files, and how this differs from
  NEMS NGMM. This guide tells you where each equation is implemented.
- [COUPLING.md](COUPLING.md), a proposed Gauss-Seidel interface to the electricity model.

All line numbers refer to the files as landed. If they drift, the section headings in
`ng_model.py` (banner comments of `###...###`) are stable anchors.

---

## 1. The four files, and what each is responsible for

| File | Lines | Responsibility | Depends on |
|---|---:|---|---|
| [data.py](data.py) | 712 | Read CSVs → plain Python dicts. No pyomo. | pandas |
| [ng_model.py](ng_model.py) | 1,769 | Build and solve the QP; expose a coupling API | `data.py`, pyomo |
| [../../integrator/ng_coupling.py](../../integrator/ng_coupling.py) | 379 | Translate between the electricity model and this one | neither model's internals |
| [../../../tests/naturalgas/test_ng_coupling.py](../../../tests/naturalgas/test_ng_coupling.py) | 147 | Tests using a stand-in electricity model | `ng_coupling.py` |

The dependency arrow only ever points one way: `data.py` knows nothing about pyomo, `ng_model.py`
knows nothing about electricity, and `ng_coupling.py` knows nothing about either model's internals
beyond a documented contract. That separation is what makes the coupling testable without building
a real electricity model.

---

## 2. Reading `data.py` (712 lines)

This file defines the shape of everything the model consumes.

### Structure

| Lines | What |
|---|---|
| 56 | `_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / 'input' / 'naturalgas'` |
| 63-260 | `_*_FALLBACK` constants, the values used when a CSV is absent |
| 261-276 | `_csv()`, the loader primitive |
| 277-675 | One `load_*` function per input file |
| 676+ | `load_all()`, the single entry point, returns one dict |

### The `parents[3]` line (56)

`data.py` sits at `src/models/naturalgas/data.py`, so `parents[3]` walks up
`naturalgas → models → src → <repo root>`, giving `<repo root>/input/naturalgas`. **This is why
the file could be dropped into this repo unchanged**, `src/models/electricity/` sits at exactly
the same depth. Move the file one level and this silently resolves to the wrong directory, at
which point every load falls back and the model still runs, producing plausible-but-wrong numbers.

### The forgiving loader (261-276)

`_csv()` returns `None` when a file is missing rather than raising. Every `load_*` then falls back
to its constant and logs a warning. This is a deliberate design choice with a real trade-off:

- **Upside:** an incomplete input set degrades *visibly*, you get warnings, not a crash, and the
  model still runs so you can bisect what's missing.
- **Downside:** a typo'd filename looks exactly like an intentionally-absent file. The six
  fallback warnings on every run right now can shows you can ignore that channel.

Ten files load from CSV in this distribution; six fall back. The INFO lines (`SUPPLY_COST_TIERS
loaded from CSV (9 regions)`) are the positive confirmation, worth reading more than the warnings.



---

## 3. Reading `ng_model.py` (1,769 lines)

### Top-level map

| Lines | Section |
|---|---|
| 1-54 | Module docstring |
| 55-95 | Imports; `GI` namedtuple; **`_NG_DATA = load_all()` runs at import time** |
| 96-356 | Reference data: regions, labels, supply cost tiers, LNG export tables |
| 357-413 | `resolve_regions` (365), `project_demand` (387) |
| 415-485 | Supply-curve breakpoint maths: `_supply_qbase` (421), `_supply_pbase` (448) |
| 491-1119 | `class NGModel`, `__init__` is 518-1119, the entire formulation |
| 1121-1364 | The coupling API, eight methods |
| 1369-1454 | `solve()` |
| 1456-1610 | `_extract_*` result tables |
| 1611-1669 | `report()` |
| 1674-1769 | CLI |

**Note line 94:** `_NG_DATA = _load_ng_data()` executes on *import*, not on model construction.
That's why the fallback warnings appear before anything else, and why suppressing them requires
configuring logging before the import.

### 3.1 The supply curve, the least obvious part (415-485)

Each region-year has an anchor `(Q0, P0)`. Six breakpoints are built around it, three below and
three above, from two shape vectors and a vector of elasticities.

**`_supply_qbase` (421)**, quantities are cumulative products of the volume factors. Note the two
branches: `k ≤ 3` multiplies *upward* from index `k-1` to 3 (so breakpoint 1 is the furthest below
the anchor), while `k > 3` multiplies from 0 up to `k-3`. Getting this backwards silently inverts
the curve.

**`_supply_pbase` (448)**, read this docstring in full; it's the most important comment in the
file. NGMM's published AEO2025 formula is

```
PBASE_step = P0 × ∏ (1 ± CRV_step) / ELAS_step
```

which divides by an elasticity below 1 and is **non-monotonic** with the default elasticities,
verified in the docstring with a West South Central case where `PBASE_2 = 4.36 > PBASE_3 = 3.59`,
i.e. prices *fall* as you move up the curve. This model uses the elasticity-correct form

```
PBASE_step = P0 × ∏ (1 ± CRV_step / ELAS_step)
```

derived from ε = (dQ/Q)/(dP/P). **This is a deliberate, documented departure from the published
NGMM specification**, likely worth reviewing with the AIMMS code.

### 3.2 NGMM vocabulary, step, break, cost tier

The model uses NGMM's own words. Three distinct things, three distinct names:

| Concept | Count | Name in code | NGMM equivalent |
|---|---:|---|---|
| elastic piece carrying volume | 5 | `steps` (`step1`…`step5`), `sstep` | **`SSTEP`** (Eq 7, Eq 8) |
| endpoint bounding those pieces | 6 | `supply_breaks`, `SUPPLY_BREAK_IDS` | index of **`QBASE`/`PBASE`** |
| input aggregation tier | 3 | `SUPPLY_COST_TIERS`, `cost_tier` column | **none, NGMM has no such concept** |

Six breaks bound five steps, so every loop over steps reads break `k` and break `k+1`.


### 3.3 Breakpoints vs segments, the off-by-one that bites

A prime marks a breakpoint. **Six breakpoints, five segments.** A parameter indexed by breakpoints
(`QBASE`, `PBASE`) has one more entry per region-year than a variable indexed by segments
(`sstep`). The same pattern holds for tariffs (7 breaks / 6 segs) and LNG (4 breaks / 3 segs).
Every objective loop reads `k` and `k+1` for exactly this reason.

### 3.4 Sets and parameters (585-801)

Sets at 585-590. Parameters follow in curve order:

| Line | Param | Note |
|---|---|---|
| 629, 630 | `Q0`, `P0` | **mutable**, `update_supply_capacity` rewrites them |
| 653, 655 | `QBASE`, `PBASE` | derived from the anchor via the helpers above |
| 662 | `QMIN` | committed production floor (NGMM Eq 8) |
| 695, 697 | `QTAR`, `PTAR` | tariff curve, from utilisation breakpoints × capacity |
| 726, 728 | `QLNG`, `PLNG` | LNG export demand curve |
| 747-762 | loss fractions | distribution, intrastate, storage |
| 769 | `demand` | **mutable**, this is what the electricity model writes into |
| 778 | `bcf_to_mmbtu = 1e3` | the objective scaling factor, applied uniformly |
| 781 | `canada_supply` | **mutable** |

The mutable ones are exactly the coupling surface. Everything else is fixed at construction.

### 3.5 Variables and expressions (804-875)

| Line | Name | Meaning |
|---|---|---|
| 804 | `sstep[r,k,y]` | production on supply segment k |
| 820 | `production_total` | *Expression*, Σ over segments, NGMM Eq 8 |
| 824 | `lng_export_step` | LNG exports on demand segment m |
| 836 | `tar_step[o,d,j,y]` | pipeline flow on tariff segment j |
| 842 | `pipe_flow` | *Expression*, Σ over segments |
| 845 | `lng_import` | backstop imports, note this is **imports**, not exports |
| 852 | `var_demand` | balancing slack, demand side only |
| 868 | `unserved` | **subset runs only**, see below |

`production_total`, `pipe_flow`, and `lng_export_demand` are `Expression`, not `Var`. That's why
segment indices vanish from the market balance: each Expression collapses one index.

### 3.6 `demand_balance`, the price-forming constraint (961-997)

The central constraint, and the one constraint to read line by line. The comment block at 942-960
maps every term to its NGMM equation. Sources on the left, uses on the right:

```
production × (1 − intrastate_loss) sector demand
+ lng_import + distribution_loss × (res + comm)
+ canada_supply = + plant_fuel_frac × total demand
+ Σ pipe_in × (1 − pipe_loss) + Σ pipe_out
+ stor_withdraw × (1 − storage_loss) + stor_inject
+ unserved (subset only) + lng_export_demand
                                            + var_demand
```

Three things to notice:

1. **Losses apply asymmetrically.** Inbound pipe flow and storage withdrawal are derated; outbound
   flow and injection are not, the loss is on delivery.
2. **`var_demand` is on the uses side**, so it can absorb surplus but never cover a shortfall.
   That's why subsets need `unserved` as a separate supply-side term.
3. **The dual of this equality is the regional gas price.** It's what crosses into the electricity
   model. NGMM forms its price at a hub balance; this model has one merged layer, so the single
   dual plays that role.

`is_region_subset` (979) gates `unserved` into the supply side. For the full nine-region model the
term is a literal `0.0` and the variable is never created, which is why variable counts differ
between a subset and a full run.

### 3.7 The objective (999-1119)

Minimise `total_cost = costs − LNG consumer surplus`. NGMM *maximises* surplus; this minimises the
negative so the integrator's convergence checks (which expect a positive scalar) still work. The
comment at 1004-1008 states this explicitly. **The objective is legitimately negative**, about
−371.8 M at full resolution, because the LNG surplus term dominates. A negative objective is not
a bug here.

Six blocks, each an area under a piecewise-linear curve:

| Lines | Block | Form |
|---|---|---|
| 1023-1037 | producer cost | `Σ (PBASE_k·q + ½·slope·q²)·β` ← **the QP term** |
| 1040 | gathering | linear |
| 1046 | LNG backstop import | linear |
| 1052-1065 | transport | `Σ (PTAR_j·f + ½·slope·f²)·β` ← **QP** |
| 1068 | storage opex | linear |
| 1082-1095 | LNG consumer surplus | `Σ (PLNG_m·x + ½·slope·x²)·β`, **subtracted** |

The `if width_v <= 1e-9: continue` guard appears in all three quadratic blocks. It skips
zero-width segments, preventing a division by zero in regions with no capacity of a given type,
e.g. Pacific LNG exports are 0 in 2025, so all its `QLNG` breakpoints coincide.

Note the slopes are computed with `value(...)` at **construction** time, so they're numeric
constants in the expression, not pyomo objects. That's what makes this a QP with a constant
Hessian rather than a general nonlinear program.

`UNSERVED_PENALTY` (1105) is ~1000 $/MMBtu, roughly 100× any plausible gas price, so the backstop
is never economic. When a subset is short, the dual comes back *at the penalty level*,
a signal rather than an opaque infeasibility.

### 3.8 The coupling API (1121-1364)

Eight methods. These are the entire surface the Gauss-Seidel loop drives.

| Line | Method | Direction | Notes |
|---|---|---|---|
| 1123 | `set_reference_prices` | in | Call **once**, after the first solve |
| 1139 | `update_demand_from_price` | internal | `demand = base × (price/ref)^elasticity` |
| 1193 | `update_demand` | in | Electricity writes gas burn here |
| 1224 | `update_canada_supply` | in | |
| 1241 | `update_supply_capacity` | in | Rebuilds QBASE/PBASE from a new Q0 |
| 1311 | `poll_gas_price` | out | **the duals** |
| 1336 | `poll_total_gas_demand` | out | |
| 1351 | `attach_results` |, | |

Two details that matter for review:

**`update_demand_from_price` (1139) is a no-op until `set_reference_prices` is called**, it
returns immediately at 1164-1165 if `_ref_prices` is empty. Silent by design, but it means
forgetting the setup call disables price-responsive demand with no warning.

**Under-relaxation is implemented identically in three places** (1184-1186, 1219-1221, and in
`ng_coupling.update_ng_fuel_adj`): `new = α·new + (1−α)·current`. All three must be damped or the
loop can oscillate through the undamped one.

### 3.9 `solve()` (1369-1454)

The candidate ordering at 1403 must not be reordered:

```python
candidates = ['appsi_gurobi', 'gurobi_direct', 'gurobi', 'highs', 'appsi_highs']
```

`highs` precedes `appsi_highs` **deliberately**. The model is a convex QP; `appsi_highs` calls
`generate_standard_repn(quadratic=False)` and raises `DegreeError` on any quadratic objective
(still true in pyomo 6.10.1). A Gurobi-free environment must land on `highs`, the
new-generation interface that builds a Hessian. Confirm with `--tee`: HiGHS reports
`1476 Hessian nonzeros`.

Two consequences worth noting in review: `opt.solve(m)` at 1440 passes no `tee`, and the `results`
object is used for the termination check and then discarded, so neither solver output nor
solver-reported timing is available to callers.

---

## 4. Reading `ng_coupling.py` (379 lines)

The translation layer. It knows the *contract* both models satisfy, not their internals, which is
why the tests can exercise it against a stand-in electricity model.

| Lines | What |
|---|---|
| 42-46 | `NG_GAS_TECHS = {3, 4}` and heat rates `{3: 9.51, 4: 7.12}` MMBtu/MWh |
| 48-93 | `_ROLE_SET_NAMES`, `load_ng_region_map` |
| 97-112 | `_model_role_sets`, collect the electricity model's sets by role |
| 115-204 | **`resolve_generation_index`**, the defensive core |
| 207-237 | `check_coupling_contract` |
| 244-~300 | `poll_ng_gas_demand`, electricity → gas |
| ~305-360 | `update_ng_fuel_adj`, gas → electricity |

### 4.1 Why index discovery exists (115-204)

Electricity models in this lineage disagree on the order of the `generation_total` index. Both
orderings are **five-tuples of the same types**, so unpacking positionally against the wrong one
raises no error, it binds `tech` to a region id, filters on regions instead of technologies, and
returns a plausible but wrong answer. This is the failure mode the whole module is built around.

Two strategies, in order:

1. **Declared constituent sets** (139-152). If the index is a pyomo set product, read the ordered
   constituent set names and match them to roles by name. Exact when available.
2. **Value membership** (154-204). Sample up to 400 index tuples; for each position, find which
   roles' sets contain *every* sampled value. Positions matching exactly one role are assigned,
   then the rest fall out by elimination (the `while changed` loop at 186-195).

If it still can't resolve unambiguously it **raises** (197-202) rather than guessing. That choice
is the point: a wrong guess is silent, an exception is not. Do not replace this with a hard-coded
unpack.

### 4.2 The contract check (207-237)

Validates four attributes up front, `generation_total`, `WeightDay`, `MapHourDay`, and a
**mutable** `NGFuelAdj`, and specifically checks `adj.mutable` at 227-229, because a non-mutable
Param would accept the declaration and then fail on the first write mid-iteration. Each missing
item produces an actionable message naming *why* it's needed. Returns the resolved index order for
the transfer functions to reuse, so discovery runs once per session rather than once per call.

### 4.3 The two transfer functions

**`poll_ng_gas_demand` (244)**, electricity → gas. Day-weights generation for techs 3 and 4,
converts to Bcf, aggregates electricity regions onto gas regions. The unit chain is spelled out at
250-251:

```
Bcf = GWh × MMBtu/MWh × 1000 MWh/GWh / 1e6 MMBtu/Bcf
    = GWh × MMBtu/MWh / 1000
```

Electricity regions absent from the crosswalk are collected into `skipped_regions` and warned
about, **read that warning**, because a crosswalk that matches nothing returns an empty dict and
the coupling silently transmits zero.

**`update_ng_fuel_adj` (~305)**, gas → electricity. The adjustment is a *delta against a
reference*:

```
adj = (ng_prices[gi] − base_ng_prices[gi]) × heat_rate × 1000.0
```

Two consequences: `NGFuelAdj` must be `within=pyo.Reals` (it goes negative whenever gas is cheaper
than the reference), and `base_ng_prices` must come from the **first** gas solve and never be
reassigned, recapturing it each iteration makes the delta identically zero, silently disabling
the coupling while the run converges immediately and looks healthy.

### 4.4 The tests (`tests/naturalgas/test_ng_coupling.py`, 147 lines)

14 tests against a minimal stand-in electricity model, so they run in ~7 s with no real model
build. The two that matter most exercise **both index orderings** and assert they give the same
answer, and a crosswalk that matches nothing. Read these as the executable specification of the
contract, they're the fastest feedback loop in the whole gas codebase.

---

## 5. Data flow, end to end

```
input/naturalgas/*.csv
        │
        ▼ data.py load_all() ← 10 load, 6 fall back
   _NG_DATA dict (executes at ng_model import)
        │
        ▼ module constants: SUPPLY_COST_TIERS, LNG_IMPORT, ...
        │
        ▼ NGModel.__init__
   anchors (Q0,P0) ──► _supply_qbase/_supply_pbase ──► QBASE/PBASE breakpoints
        │ │
        ▼ ▼
   Sets ──► Params ──► Vars ──► Constraints ──► Objective (quadratic)
        │
        ▼ solve() → 'highs' → optimal
        │
        ├──► duals of demand_balance ──► poll_gas_price() ──► $/MMBtu by (region, year)
        └──► _extract_* ──► results_production / flows / prices / storage / balance
                    │
                    ▼ report() ──► console summary + 5 CSVs
```

---
