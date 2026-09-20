# Solvers: selection, termination checks, and persistence

Everything here was verified against **pyomo 6.10.1** with **highspy 1.15.1** and no Gurobi
present; the probe source is `probe` at the bottom so it can be re-run on an upgrade.

> **Upgrade check — read first.** Pyomo has signalled it intends to revisit the solver
> interfaces for **7.0**: `pyomo.contrib.solver` is expected to become the mainline API and the
> legacy `pyomo.opt` / APPSI layers are candidates for removal or relocation. If
> `python -c "import pyomo; print(pyomo.version.version)"` reports **7.0 or later**, treat this
> whole file as unverified: re-run the probe, check whether `SolverFactory` still returns a
> `LegacySolverWrapper`, and whether `solve(load_solutions=False)` is still the way to defer a
> load. Fix the file before relying on it.

## Which HiGHS interface

Three names look plausible; only one is current.

| `SolverFactory` name | Class | Persistent | Use |
|---|---|---|---|
| `highs` | `pyomo.contrib.solver.solvers.highs.Highs` | yes | **this one** |
| `appsi_highs` | `pyomo.contrib.appsi.solvers.highs.Highs` | yes | superseded |
| — | there is no file-based/shell HiGHS interface | — | — |

`highs` is a rewrite of the APPSI interface, not a different family — same persistent-solver
surface (`set_instance`, `update`, `add_constraints`, `update_parameters`, `is_persistent`).

`appsi_highs` additionally calls `generate_standard_repn(quadratic=False)` internally, so it
raises `DegreeError` on any quadratic objective. That is why the natural gas model (a convex QP)
cannot use it, and why it is not in that model's solver probe list.

**Registration gotcha.** `pyomo.opt`'s factory has no HiGHS plugin registered until
`pyomo.environ` has been imported — it is the same singleton registry, but `pyomo.environ`'s
import is what loads the plugins:

```python
import pyomo.opt
print(sorted(n for n in pyomo.opt.SolverFactory if 'high' in n))   # []
import pyomo.environ
print(sorted(n for n in pyomo.opt.SolverFactory if 'high' in n))   # ['appsi_highs', 'highs']
```

A module that does `import pyomo.opt as pyo` and then `pyo.SolverFactory('highs')` works only by
accident of some other import having pulled in `pyomo.environ` first. Import `SolverFactory` from
`pyomo.environ` and the dependency is explicit. `src/integrator/utilities.py` does this.

## The pattern: check, then load

```python
from pyomo.opt import check_optimal_termination

results = opt.solve(model, load_solutions=False)
if not check_optimal_termination(results):
    logger.error('non-optimal solve: %s', results.solver.termination_condition)
    return IterationStatus.ERROR
model.solutions.load_from(results)          # variable values *and* duals/suffixes
```

`load_solutions=False` is not optional hygiene — without it the check is **unreachable**,
because `solve()` raises while trying to load a solution that does not exist:

```
highs        NoFeasibleSolutionError
appsi_highs  RuntimeError
```

The message names the fix but points at the native config (`opt.config.load_solutions=False`);
through `SolverFactory` the `load_solutions=False` keyword on `solve()` is the equivalent.

`pyo.check_optimal_termination` is the right check on **both** generations and needs no
version branch — its source explicitly forks on `hasattr(results, 'solution_status')`, reading
the new `SolutionStatus.optimal` when present and falling back to
`results.solver.status` + `termination_condition` otherwise.

Deferred loading costs nothing: duals still land in an `IMPORT` Suffix at `load_from`
(`dual[c] = 3.0` in the probe), so a model asking Gurobi for `QCPDual` loses nothing.

## If you use the native API instead

`pyomo.contrib.solver.common.factory.SolverFactory` (a *different* factory) returns the solver
object unwrapped, and `solve()` then returns a `Results` rather than a legacy results object.
There, **two** flags are needed, because non-optimal termination raises on its own:

```python
opt = Highs()
opt.config.load_solutions = False
opt.config.raise_exception_on_nonoptimal_result = False   # defaults True here
results = opt.solve(model)
if results.solution_status is SolutionStatus.optimal:
    results.solution_loader.load_vars()
    duals = results.solution_loader.get_duals()
```

```
load_solutions=False alone: NoOptimalSolutionError
both off: SolutionStatus.noSolution | TerminationCondition.provenInfeasible | incumbent: None
```

`LegacySolverWrapper.solve()` declares `raise_exception_on_nonoptimal_result: bool = False` in
its own signature, which is why the `SolverFactory` path needs only the one keyword. Pick one
style per call site and don't mix them — the two `solve()` calls return different result types.

## Persistence (what APPSI was for) is automatic

Hold the **same solver object** and pass the **same model object**, and the interface pushes
only the diff. `PersistentSolverMixin.solve` is literally:

```python
if model is not self._model:
    self.set_instance(model)      # first call: full build
else:
    self.update(timer=timer)      # later calls: incremental
```

```
solver cached the model: True
after param change, x = 5.0
```

So an iteration loop gets persistence for free as long as it does not rebuild the model or
re-create the solver inside the loop. `ElectricitySequencer.solve_model`'s linear-learning loop
relies on exactly this: it mutates expansion costs and re-solves.

All nine `config.auto_updates` flags default to `True`, which is correct but re-scans the model
each solve. Turning off the structural ones is the usual speedup **only** if the loop genuinely
does not add or remove components:

```python
opt.config.auto_updates.check_for_new_or_removed_constraints = False
opt.config.auto_updates.check_for_new_or_removed_vars = False
opt.config.auto_updates.update_constraints = False
```

A stale flag here does not error — it silently solves the wrong model.

## Porting from APPSI

| APPSI | Current interface |
|---|---|
| `config.load_solution` | `config.load_solutions` (plural) |
| `config.stream_solver` | `config.tee` |
| `config.mip_gap` | `config.rel_gap` / `config.abs_gap` |
| `opt.highs_options[k]` | `config.solver_options[k]`, or `opt.options[k]` on the wrapper |
| `opt.update_config.<flag>` | `config.auto_updates.<flag>` |
| `res.termination_condition == TerminationCondition.optimal` | `check_optimal_termination(res)` |
| `res.best_feasible_objective` | `res.incumbent_objective` |

## How this repo is wired

- `src/integrator/utilities.py::select_solver` returns `SolverFactory('highs')`, or `ipopt` when
  `nonlinear=True`. Its return type is `OptSolver | LegacySolverWrapper`: the current interface
  is **not** an `OptSolver` subclass, so annotating it as one is wrong.
- Callers that iterate should keep the returned object — see persistence above.
- `NGSequencer.solve_model` probes `appsi_gurobi, gurobi_direct, gurobi, highs` and stops at the
  first available, so a Gurobi-free environment lands on `highs`.
- Every solve site uses the check-then-load pattern: `simple_solve` / `simple_solve_no_opt` in
  `src/integrator/utilities.py`, `ElectricitySequencer.solve_model` (both the learning loop and
  the single-solve branch), and `NGSequencer.solve_model`.

## probe

```python
import pyomo.environ as pyo
from pyomo.contrib.solver.common.results import SolutionStatus
from pyomo.contrib.solver.solvers.highs import Highs


def mk(rhs=2.0, ub=10.0):
    m = pyo.ConcreteModel()
    m.p = pyo.Param(initialize=rhs, mutable=True)
    m.x = pyo.Var(bounds=(0, ub))
    m.c = pyo.Constraint(expr=m.x >= m.p)
    m.o = pyo.Objective(expr=3 * m.x)
    m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    return m


for name in ('highs', 'appsi_highs'):          # which class is behind the name
    o = pyo.SolverFactory(name)
    print(f'{name:12} {type(o).__mro__[2].__module__}.{type(o).__mro__[2].__name__}')

for name in ('highs', 'appsi_highs'):          # default solve() raises on infeasible
    try:
        pyo.SolverFactory(name).solve(mk(ub=1.0))
    except Exception as e:
        print(f'{name:12} {type(e).__name__}')

opt = pyo.SolverFactory('highs')               # check-then-load, incl. duals
res = opt.solve(mk(ub=1.0), load_solutions=False)
print('infeasible  check:', pyo.check_optimal_termination(res), '| tc:', res.solver.termination_condition)
m = mk()
res = opt.solve(m, load_solutions=False)
m.solutions.load_from(res)
print('after load_from  x =', pyo.value(m.x), ' dual[c] =', m.dual[m.c])

native = Highs()                               # native API needs both flags
native.config.load_solutions = False
try:
    native.solve(mk(ub=1.0))
except Exception as e:
    print('load_solutions=False alone:', type(e).__name__)
native.config.raise_exception_on_nonoptimal_result = False
r = native.solve(mk(ub=1.0))
print('both off:', r.solution_status, '|', r.termination_condition, '| incumbent:', r.incumbent_objective)

m = mk()                                       # persistence
opt = pyo.SolverFactory('highs')
opt.solve(m, load_solutions=False)
print('solver cached the model:', opt._model is m)
m.p.value = 5.0
res = opt.solve(m, load_solutions=False)
m.solutions.load_from(res)
print('after param change, x =', pyo.value(m.x))
```
