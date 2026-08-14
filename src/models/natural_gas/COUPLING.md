# Coupling the gas model to an electricity model (Gauss-Seidel)

This describes what to add to an electricity model so it can be solved iteratively against the
natural gas model. Unified (single-optimization) coupling is out of scope here.

The gas side is already complete, `src/models/naturalgas/ng_model.py` exposes every method the
loop needs. The work is on the electricity side and in the loop itself.

---

## 1. The exchange

Two quantities cross the boundary each iteration:

| Direction | Quantity | Units | Function |
|---|---|---|---|
| electricity → gas | gas burn by gas-fired generation | Bcf/yr by gas region | `poll_ng_gas_demand` |
| gas → electricity | regional gas price as a fuel-cost adjustment | $/GWh | `update_ng_fuel_adj` |

This is the same shape as the electricity ↔ hydrogen coupling that already exists in models of
this lineage, `H2Price` /
`update_h2_prices` / `poll_h2_demand` is a working template sitting in the same files.

## 2. What to add to the electricity model

### 2.1 An `NGFuelAdj` parameter

Mirror the existing hydrogen price parameter exactly. If the electricity model has `H2Price`,
copy its declaration and change the technology set:

```python
self.NGFuelAdj = pyo.Param(
    self.region_analyze,
    self.tech,          # or a gas-tech subset, mirroring how H2Price uses tech_h2
    self.step,
    self.year,
    self.season,
    initialize=0.0, # zero until the first gas solve, see the note below
    within=pyo.Reals,   # NOT NonNegativeReals: this is a delta and may be negative
    mutable=True,       # required; it is written between solves
)
```

Two details that matter:

- **`within=pyo.Reals`.** `NGFuelAdj` carries the *difference* between the current gas price and
  a reference, so it is negative whenever gas is cheaper than the reference. Declaring it
  non-negative would silently clamp half the signal.
- **`initialize=0.0`.** A zero adjustment means "gas costs exactly what the electricity model
  already assumes", so the first electricity solve is unchanged from an uncoupled run. That is
  the property that makes a converged coupled run comparable to the calibrated standalone one.

### 2.2 An objective term

Add it to the dispatch cost alongside the existing fuel cost, mirroring the hydrogen term:

```python
+ sum(
    self.WeightYear[y]
    * self.NGFuelAdj[r, tech, step, y, season]
    * self.generation_total[r, tech, step, y, hr]
    for (r, tech, step, y) in self.GenHour_index[hr]
    if tech in NG_GAS_TECHS
)
```

Match the index order and the surrounding conventions of the model you are editing, including
whether the objective applies a discount factor. If the existing fuel-cost term carries one,
this term must carry the same one, or the two fuel costs are on different bases.

`NGFuelAdj` is already in $/GWh, the heat-rate conversion happens on the gas side in
`update_ng_fuel_adj`, so it multiplies generation directly with no further conversion.

### 2.3 Nothing else

No new sets, no new constraints. The coupling reads `generation_total`, `WeightDay`, and
`MapHourDay`, all of which already exist.

## 3. Wiring the loop

The gas model is constructed in `integrated` mode so its coupling parameters are mutable:

```python
from src.models.naturalgas.ng_model import NGModel, solve as ng_solve
from src.integrator.ng_coupling import (
    check_coupling_contract, load_ng_region_map,
    poll_ng_gas_demand, update_ng_fuel_adj,
)

# --- setup, once ---
ng_model   = NGModel(years=settings.years, mode='integrated')
elec_to_ng = load_ng_region_map()
gen_pos    = check_coupling_contract(elec_model)   # raises early if anything is missing

ng_solve(ng_model)
base_ng_prices = ng_model.poll_gas_price()          # the reference NGFuelAdj measures against
ng_model.set_reference_prices(base_ng_prices)
```

Then, inside the existing iteration loop, after the electricity solve:

```python
    # electricity -> gas
    ng_demand = poll_ng_gas_demand(elec_model, elec_to_ng, index_pos=gen_pos)
    ng_model.update_demand(ng_demand, alpha=alpha)

    # let the other gas sectors respond to last iteration's prices
    ng_model.update_demand_from_price(ng_prices, alpha=alpha)

    ng_solve(ng_model)
    ng_prices = ng_model.poll_gas_price()

    # gas -> electricity
    update_ng_fuel_adj(elec_model, ng_prices, elec_to_ng, base_ng_prices, alpha=alpha)
```

### Capture the reference exactly once

`base_ng_prices` must come from the **first** gas solve and never be reassigned. Recapturing it
each iteration makes the adjustment identically zero and silently disables the coupling, the run
converges immediately and looks healthy while transmitting nothing.

### Under-relaxation

`alpha` damps oscillation and belongs on every update: `update_demand`,
`update_demand_from_price`, and `update_ng_fuel_adj`. Gas price and gas-fired dispatch feed back
on each other strongly, so `alpha = 1.0` tends to oscillate. Start around 0.3 and raise it if
convergence is monotone.

### Convergence

Track the relative change in the gas objective and in the electricity objective between
iterations and stop when both fall below tolerance. Tracking only one can stop early while the
other is still moving.

## 4. Verify before trusting

Run these checks in order. Each catches a distinct failure that produces plausible-looking but
wrong numbers.

**1. The contract holds.** `check_coupling_contract(elec_model)` returns without raising. It
verifies `generation_total`, `WeightDay`, `MapHourDay`, and a mutable `NGFuelAdj` all exist, and
resolves the index order.

**2. Gas demand is the right magnitude.** Total polled gas demand should be within a few percent
of the electric-power sector's gas consumption in the gas model's own base-year data. An order of
magnitude off means a unit error; roughly right but consistently biased means a heat-rate or
tech-mapping error.

```python
polled = sum(poll_ng_gas_demand(elec_model, elec_to_ng, gen_pos).values())
print(f'polled electric-power gas demand: {polled:,.0f} Bcf/yr')
```

**3. Index order resolved correctly.** `resolve_generation_index` logs the mapping it found.
Confirm the region position really holds regions, a wrong resolution makes gas demand attach to
the wrong regions while the national total stays plausible.

**4. Zero adjustment reproduces the standalone electricity run.** Before the first gas update,
with `NGFuelAdj` all zero, the electricity objective must equal an uncoupled run's exactly. If it
does not, the new objective term is wrong.

**5. The coupling actually transmits.** After convergence, `NGFuelAdj` must not be uniformly
zero. All-zero means either the reference was recaptured (see above) or the region crosswalk
matched nothing.

## 5. Things to watch out for

**Index order.** Electricity models index order might change, `ng_coupling.py` discovers the order rather than assuming
it; do not replace that with a hard-coded unpack.

**Region identifier type.** Some models use string region ids, others integers.
`load_ng_region_map` returns a mapping keyed by both forms so either lookup succeeds. If the
electricity model's region ids are neither the strings nor the integers in
`elec_to_ng_region_map.csv`, the crosswalk silently matches nothing and gas demand comes back
empty, check 5 above catches this.

**Technology numbering.** `NG_GAS_TECHS = {3, 4}` and the heat rates assume 3 = gas combustion
turbine and 4 = gas combined cycle. Confirm against the technology table of the model you are
coupling to. Wrong numbering produces zero gas demand, or gas demand attributed to coal.

**Discounting asymmetry.** If the electricity objective discounts costs and the gas objective
does not, the two are on different bases and the coupled result is not a consistent equilibrium.
The gas model in this distribution applies no discount factor.

**Regional coverage.** Electricity regions with no entry in the crosswalk are skipped with a
warning, and their gas burn never reaches the gas model. Read that warning rather than filtering
it out.

**The wrong HiGHS interface.** The gas model is a convex quadratic program. Pyomo's
`appsi_highs` interface calls `generate_standard_repn(quadratic=False)` and therefore raises
`DegreeError` on any quadratic objective, still true in pyomo 6.10.1. Use
`SolverFactory('highs')`, the new-generation interface available from pyomo 6.10, which builds a
Hessian and handles it. `ng_model.solve()` already orders its candidates so a Gurobi-free
environment lands on `highs`; if you call a solver explicitly in the loop, pass `'highs'` and not
`'appsi_highs'`.

Note that the **electricity** model is typically a linear program, so it is unaffected by this and
either interface works for it. Only the gas solve is constrained.

## 6. Solver expectations

Verified with pyomo 6.10.1 + highspy 1.15.1, no Gurobi installed:

| | HiGHS | Gurobi |
|---|---|---|
| Gas objective | −371,795,726.1060 | −371,795,726.0855 |
| Regional prices (54) | identical to 4 decimals | reference |
| Coupling tests | 14 passed | 14 passed |

The price agreement matters more than the objective agreement here: the regional prices are the
duals that cross the interface, so a solver that agreed on the objective but not the duals would
give a different coupled equilibrium. These agree on both.
