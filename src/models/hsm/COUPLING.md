# Coupling C-HSM to C-NGMM

**Status.** C-HSM is not yet wired into a coupled run. On the gas side, C-NGMM already has the
method that would receive C-HSM's capacity (`NGModel.update_supply_capacity`), but no runner calls
it, and both sequencers raise `NotImplementedError` from `update_model`. This page sets out what
each side offers and how a Gauss-Seidel loop would join them. Line numbers on the C-NGMM side are for
c-nems `main` at `0c79a67`.

## What C-HSM offers

| Call | Direction | What it takes or gives |
|---|---|---|
| `HSMSequencer().build_model(common_config, hsm_config)` | build | an `HSMModel`; reads C-NGMM's regions and cost tiers; `common_config.make_scenario_dir()` must have been called |
| `HSMModel.update_prices(henry_hub, brent)` | in | `{year: price}` in **1987 \$** (\$/MMBtu, \$/bbl); an empty dict means the reference path; otherwise every year 2023–2050 is required |
| `HSMModel.run()` | run | sets up Canada, then runs 2023–2050 in order |
| `HSMModel.poll_us_natgas_capacity()` | out | `{(region, cost_tier, gas_type, year): BCF}`, every calendar year |
| `HSMModel.poll_canada_natgas_production()` | out | DataFrame indexed `(NUMCAN, year)`, BCF |
| `model.module.canada_na_reference`, `model.module.canada_export_share` | out | Canada's NA production at reference prices, and the share of extra production that reaches the US |

## What C-NGMM offers (`src/models/natural_gas/ng_model.py` on `main`)

| Method | Line | What it does |
|---|---|---|
| `update_supply_capacity(capacity_updates, alpha)` | :1208 | takes `{(region, cost_tier, year): BCF}`, ignores the cost tier and sums per `(region, year)`, blends with the current `q0` by `alpha`, sets `q0` and the production floor, and rebuilds the supply curve |
| `update_canada_supply(supply)` | :1127 | sets the Canadian gas arriving in each **US** region: keys are `GI(region, year)` named tuples (`GI` at :102), unknown regions are skipped; it starts at 0 everywhere |
| `poll_gas_price()` | :1294 | regional prices, $/MMBtu, the duals of the market balance |
| `set_reference_prices`, `update_demand`, `update_demand_from_price` | :1018, :1096, :1034 | the demand side of the exchange |

C-NGMM runs on representative years (2025 to 2050 in steps of 5); C-HSM runs every calendar year.

## One Gauss-Seidel iteration

Not shipped as code; the runner belongs in c-nems.

```python
import pandas as pd

common_config.make_scenario_dir()  # once per run, before any model is built
model = HSMSequencer().build_model(common_config, hsm_config)  # reference Henry Hub path
model.module.setup_canada()
canada_start = {
    k: v.copy(deep=True)
    for k, v in vars(model.module.canada).items()
    if isinstance(v, pd.DataFrame)
}  # Canada's state after setup

for it in range(max_iter):
    # 1. C-HSM on the previous iteration's gas price (iteration 0: the reference path)
    for k, v in canada_start.items():  # put Canada back
        setattr(model.module.canada, k, v.copy(deep=True))
    model.module.reset_canada()  # clears US results too
    if it > 0:
        model.update_prices(henry_hub_1987_from(prices), {})  # every year 2023-2050
    for year in model.years:
        model.module.run_year(year)

    # 2. Hand capacity to C-NGMM: one number per (region, gas year)
    capacity = model.poll_us_natgas_capacity()  # (region, tier, type, year)
    totals = {}
    for (region, _tier, _type, year), bcf in capacity.items():
        if year in gas_years:
            totals[(region, year)] = totals.get((region, year), 0.0) + bcf
    gas_model.update_supply_capacity(
        {(r, 'total', y): v for (r, y), v in totals.items()}, alpha=1.0
    )

    # 3. Electricity and gas exchange, solve C-NGMM, read its prices
    ...
    prices = gas_model.poll_gas_price()
```

`model.run()` calls `setup_canada()` itself, which is why the loop calls `setup_canada()` once and
`run_year` directly. Tested: with the restore above, a second sweep over the years gives results
identical to the first. `reset_canada()` on its own does not restore Canada's tables, and a second
sweep then fails with `KeyError: 'year'`.

## Points to settle before wiring

- **Price basis.** C-HSM takes 1987 \$ and converts to real 2023 \$ inside, with a fixed deflator
  (`module.py:474`). C-NGMM's prices must be converted to 1987 $ exactly once, in the adapter
  (`henry_hub_1987_from` above), with the same deflator table (`_GDP_DEFLATOR`, `module.py:44`).
  Converting with each year's deflator on the way in and the fixed 2023 one on the way out would
  scale a real price by `deflator[2023] / deflator[year]`.
- **Which C-NGMM price stands for Henry Hub.** C-NGMM has regional prices, not a Henry Hub price.
  West South Central, where Henry Hub is, is the natural stand-in. Its representative years must be
  filled in to every calendar year 2023–2050 (C-HSM rejects gaps).
- **Canada.** Canada's new wells do not respond to the Henry Hub price C-HSM is given (`EVALUATION.md`
  §4), so a coupled gas price will not move Canadian supply. If Canada is sent to C-NGMM at all, send
  `export_share × (NA production − reference)`, so Canada adds nothing at reference prices: net
  imports from Canada are already in C-NGMM's own data, and sending gross production would count
  them twice. The adapter has to turn this into C-NGMM's keys: C-HSM gives Canada by `NUMCAN` (1 =
  east, 2 = west) and calendar year, while `update_canada_supply` wants `GI(region, year)` for US
  regions and C-NGMM's years. C-NGMM has no pipeline links from Canada, so which US regions receive
  the gas, and in what shares, is a choice to make.
- **Damping.** `update_supply_capacity` blends by `alpha` and quietly raises any value below 1.0 BCF.
  Damping is better done by the runner, with `alpha = 1` in the call; at `alpha = 1` the call is
  idempotent (the same input twice gives the same model).
- **The adapter should refuse bad input**: a gas year with no C-HSM year, or a region C-NGMM does not
  have, should raise rather than be skipped.
- **The well-level engine.** With `onshore_engine_file` set, every sweep over the years runs the
  engine, which takes about two minutes today (`ENGINE_PLAN.md`, step 4). Couple the reduced form
  first.
- **The first step is not neutral region by region.** At reference prices, national capacity matches
  C-NGMM's anchors to within about 1.5% a year, but West North Central moves +18.7% in 2030 (the
  calibration residual). That is the first step of the loop, not a sign of instability.

## What to test when wiring

1. The adapter's `(region, year)` totals equal the sum over cost tier and gas type of
   `tests/hsm/expected/hsm_us_gas_capacity.csv` for each gas year (54 values).
2. After `update_supply_capacity`: `q0` equals the totals, the production floor and the first supply
   breakpoint follow from `q0`, `p0` is unchanged, and the same input twice gives the same model.
3. Direction through the loop: Henry Hub +10% raises capacity everywhere and lowers the gas price.
4. The coupled result does not depend on where the loop started (run from a cold start and from a
   primed one and compare) before any claim of convergence.
