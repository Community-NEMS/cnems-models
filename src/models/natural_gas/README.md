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

## Formulation and data reference

The notation, objective, constraints, supply curve, price formation, data files, regional
subsetting and the coupling interface are documented in
[docs/models/natural_gas.md](../../../docs/models/natural_gas.md).
