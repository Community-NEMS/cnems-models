# Evaluation of C-HSM as released

What the model is, whether it is worth using, what to fix before others rely on it, and how far it
is from EIA's HSM. File and line references are to `src/models/hsm/`.

## 1. Current Status

C-HSM is a price-elasticity supply model with a fitted level, not a resource-economics model.

- **US non-associated (NA) gas capacity** by region, cost tier and year is a sum over gas types
  (conventional, tight, shale, coalbed methane) of base capacity × (wellhead price / 2.50)^ε ×
  a technology trend, times (1 − AD share), times a per-region calibration `k (1 + g)^x exp(c x²)`
  (`us_gas.py:567`, `:765`, `:761`). The low-cost tier also declines with the number of years since
  2023, to no less than half. Capacity in a year depends only on that year's price; the US side has
  no memory of earlier years.
- **US associated-dissolved (AD) gas** is a share of the capacity at the *reference* gas price,
  scaled by Brent against its reference with separate elasticities for rises and falls
  (`us_gas.py:745-770`). It does not respond to the gas price.
- **Canada** is NEMS's Canada submodule from the AEO2025 release, adapted: new wells from a
  polynomial in a benchmark Henry Hub price and a Brent term, spread over later years by decline
  profiles, so Canada carries a drilling lag. **It responds to Brent but not to the Henry Hub price
  C-HSM is given** (§4). This is accepted as it is.
- **Crude and existing-well gas** are a decline report of wells already producing. No new drilling,
  no discovery. Reported, not fed into anything.
- An **optional well-level engine** (`us_onshore.py`, `OnshoreEngine`) drills against fixed project
  well limits, ranks projects by a simple cash flow test and produces by vintage. It is off by
  default, can be switched on through the run config, and is covered by tests; it is not ready to be the default.
  `ENGINE_PLAN.md` has what it shows today, three known bugs and the steps to finish it.

The calibration is fitted so that capacity at the AEO2026 reference Henry Hub path matches
C-NGMM's own AEO2026-derived supply anchors. **At reference prices, US capacity is therefore the
anchors plus a fit residual** (for the regional totals, not for each tier and gas type). What C-HSM
adds is (a) the price response, (b) the Brent and AD channel, and (c) Canada.

## 2. Caveats on use

As a fast, deterministic, transparent supply-response layer for
coupled electricity and gas runs, and for scenarios where an elasticity response is an acceptable
stand-in, it is good. It runs in about 2 seconds and reproduces its expected output. The direction
checks hold: Henry Hub +10% raises capacity in all 252 region-years, and Brent +50% raises the AD
share (2025–2050) in West South Central (0.247 to 0.285), Mountain (0.320 to 0.367) and West North
Central (0.853 to 0.878). The interface to a gas market model is two dictionaries, and C-NGMM's
supply anchors match the calibration targets exactly. It is also the right first step for C-NEMS:
the smallest thing that runs and reproduces.

It is not a hydrocarbon supply module in the NEMS sense. By default it has no reserves, no drilling in
the US, no discovery, no offshore or Alaska response, and no crude forecast. Anyone comparing its
output with AEO supply tables should know that agreement at the reference prices is by construction.

The fair summary: a coupling-ready supply response, not validated supply forecasting. That will need to be built out.

## 3. Before others rely on it

Settled for this release:

- **Canada** stays as it is (§4).
- **NEMS input vintage** stays as it is: a mix of the AEO2025 and AEO2026 releases
  (`input/hsm/hsm_data_pedigree.md`).
- **Unread inputs** are left out; `INPUTS_PLAN.md` says how to add them back.
- **The well-level engine** is kept, switchable and tested, with a plan (`ENGINE_PLAN.md`).
- **Licence and attribution**: Apache License 2.0, with the NEMS release cited in each NEMS-derived
  file.
- **Headers**: C-NEMS files carry the c-nems header (author, date); files taken from NEMS cite the
  NEMS release instead of an author.

Done in the code:

- **Bad inputs stop the run.** `HSMModel` rejects a Henry Hub path, a price update, or either
  reference path that misses a year or has a blank or non-finite price (`hsm_model.py`); the price
  file reader also rejects repeated years (`data.py`). A calibration file that cannot be read, lacks
  a column, has a bad value or misses a region raises (`us_gas.py:330`, `:481`). Engine settings must
  all be present, spelled right and numeric (`us_onshore.py:475`). File settings in the config must
  be names inside `input/hsm/` (`hsm_config.py`).
- **Cost tiers.** C-HSM reads C-NGMM's regions and cost tiers with C-NGMM's own loaders and checks
  that every region has all three tiers (`us_gas.py:481`), so the two models cannot drift apart.
- **Tests.** `tests/hsm/` checks all six results against the expected files, the input checks above,
  and the engine.

Still to do, most important first:

1. **One price basis.** C-HSM takes 1987 \$ and converts to real 2023 \$ inside with a fixed deflator
   (`module.py:474`). A coupled run must convert C-NGMM's prices to 1987 $ in exactly one place, with
   one deflator table (`COUPLING.md`).
2. **Move the key constants into input files**: `REGIONAL_BASIS` (`module.py:458`),
   `BASE_WELLHEAD_PRICE_PER_MMBTU = 2.50` and `BASE_OIL_PRICE_PER_BBL = 65` (`us_gas.py:89`, `:91`),
   the supply elasticities (`us_gas.py:67`) and the medium and high-cost gas type shares
   (`us_gas.py:116-119`). They are the model, and at the moment they are set by hand without a source.
3. **Remaining silent behaviour.** A supplied Brent below \$20/bbl (2023 $) is replaced by $65 in the
   US supply (`module.py:489`); a failed load of the existing-well data skips two outputs with a
   warning; a failed load of either NA/AD file switches the split off. The shipped inputs avoid all
   of these, but a user's inputs may not.
4. **Refit the calibration whenever C-NGMM's cost tiers or anchors change**, and look at the residuals.
   The log-quadratic cannot follow West North Central (+18.7% in 2030, −10.5% in 2045). A
   year-by-year multiplier would remove a residual that is the first thing a coupled run sees.
5. **Coupling to C-NGMM**: an adapter from C-HSM's capacity to C-NGMM's `q0`, a runner that owns
   damping, and a way to restore Canada's state between iterations (`COUPLING.md`). `reset_canada()`
   does not restore Canada's own tables.
6. **Deck calendar.** The existing-well outputs, and the engine, take the first column of the AEO2026
   project decks as calendar 2024; in the AEO2026 cycle it may be 2025 (`ENGINE_PLAN.md`, step 2).
7. **Crude.** If crude matters, this is not the module yet: follow `ENGINE_PLAN.md`, or leave crude
   out of any results.

## 4. Canada does not respond to the Henry Hub price

In `canada.py`, the new-well equation (`:362-381`) is the formula of the NEMS AEO2025 release: a
polynomial in the benchmark Henry Hub price from `canada/can_benchmark_prices.csv`, times a pipeline
price benchmark, times a calibration term that includes the Henry Hub benchmark again and the Brent
ratio. All the Henry Hub terms are fixed inputs, not the price C-HSM is given (details in
`PROVENANCE.md` §2.4). Running C-HSM with the Henry Hub path doubled leaves Canadian NA and AD
production exactly unchanged, while US capacity in 2040 rises 37%. So Canadian supply moves only with
Brent, and in a coupled run Canada is exogenous with respect to the gas price. (The AEO2026 release
of NEMS drops the Henry Hub benchmark and the pipeline price benchmark from this equation.)

## 5. How far it is from EIA's HSM

The NEMS column is from EIA's HSM documentation for AEO2026 and the NEMS source,
<https://github.com/EIAgov/NEMS>, `models/hsm/`.

| Aspect | EIA's HSM (NEMS, AEO2026) | C-HSM |
|---|---|---|
| Language and size | Python; about 1,400 kB of code in `models/hsm/` | Python; about 200 kB, of which about 70 kB is NEMS-derived (Canada, the helpers, the names) |
| US supply mechanism | Project-level discounted cash flow on discovered and undiscovered resources; drilling decided by profitability under rig and capital limits; production follows well production profiles | Elasticity of capacity to the year's price, times a fitted level; the optional engine has a simple version of drilling and cash flow |
| Dynamics | Path dependent: wells drilled earlier produce later | Reduced form: no memory of earlier prices; Canada and the engine: drilling lag |
| What it hands the gas market | Expected production by supply region and type | Capacity by census division, cost tier and gas type, summed to one anchor per region for C-NGMM |
| Oil | Full crude supply (onshore, offshore, Alaska, EOR) with associated gas | Existing-well crude report (the engine adds new wells); oil affects gas through the AD term |
| Gas types | NA by well type and AD, from project types | NA/AD by fixed shares and Brent elasticities (from project types in the engine) |
| Canada | Included in HSM | NEMS's AEO2025 Canada code, adapted; responds to Brent only |
| Offshore, Alaska | Explicit submodules | Not included (`INPUTS_PLAN.md`) |
| Near-term years | History overwrites and STEO forecasts | Not included (`INPUTS_PLAN.md`) |
| Calibration | To history and resource assessments; AEO projections are its output | Fitted so the reference case matches AEO2026-derived anchors |
| Prices | 1987 $ inside | 1987 $ in, real 2023 \$ inside |

C-HSM reproduces the level of a NEMS-like supply path and adds an elasticity around
it; NEMS works the level out from resources, drilling and economics. Where literature elasticities
(Newell et al. 2016) are acceptable and the question is how coupled markets behave, that is enough.
Where the question is depletion, drilling or crude, it is not yet.

## 6. Worth keeping

- The expected-output test means any change to the code or inputs shows up at once. Re-capture the
  expected files only on purpose.
- `PROVENANCE.md` and `input/hsm/hsm_data_pedigree.md` trace every number and file to NEMS, AEO or
  ourselves.
