---
name: pyomo
description: Working with pyomo models — indexed Sets/Params/Vars, sparse vs dense index semantics, initializers, domain validation, and type-checking pyomo code. Use when building or debugging a pyomo formulation, when a KeyError/ValueError appears during model or constraint construction, when choosing an `initialize=` form, or when pyrefly/mypy reports spurious errors on pyomo access.
---

# Working with pyomo

This repo's electricity model (`src/models/electricity/electricity_model.py`) is a
`pyo.ConcreteModel` subclass with several hundred Sets, Params, Vars and Constraints.
Most bugs found in it so far have come from **index-space mismatches**: data that is
sparse being consumed by something that iterates a wider space. This skill records what
has been empirically verified about those semantics, so they don't have to be
rediscovered.

Verified against **pyomo 6.10.1**. Re-verify before trusting any of it on a different
version — pyomo's initializer machinery has changed across 6.x.

## Ground rule: prove it, don't recall it

Pyomo's behavior around sparsity, defaults and initializer types is genuinely
surprising, and plausible-sounding reasoning about it is frequently wrong. Several
claims in this file replaced an earlier confident-but-incorrect explanation.

Before asserting how pyomo behaves, write a ten-line `ConcreteModel` and run it. Put
throwaway probes in the scratchpad directory, not the repo. A toy model with a 2x2 index
space builds in milliseconds and settles most arguments outright.

## Reference files

Read on demand rather than inlining:

- `references/indexed-components.md` — sparse vs dense semantics for Set and Param, every
  `initialize=` form and what it does, domain validation, and the runnable proofs behind
  the table below.

## The one table to remember

Whether a missing index is an error or a default depends on the component *and* how it
was initialized:

| Component | Initialized with | Missing-index lookup |
|---|---|---|
| `Param(..., default=d)` | dict (sparse) | returns `d` — component is **dense**, `len()` == full index space |
| `Param(...)` no default | dict (sparse) | `ValueError` — component is **sparse** |
| `Set(...)` | plain dict (sparse) | **`KeyError`** |
| `Set(...)` | `defaultdict` (sparse) | empty set; component grows on access |
| `Set(...)` | rule / lambda | whatever the rule returns; constructs **densely** |
| `Set(...)` | a list/iterable | that same list becomes the members of **every** index |

### Project preference: don't reach for `default=`

Supply `default=` on a `Param` only when the default is **genuinely meaningful** for that
quantity. If a param has no sensible default, or should only ever be reached through a
sparse index set, leave it without one.

A `default` is not a safety net — it is a silencer. It converts "this index should never
have been asked for" into a plausible-looking zero, so an indexing bug shows up as a
quietly wrong objective instead of a `ValueError` at build time. Prefer the loud failure.

`supply_price` in `electricity_model.py` is the pattern to follow, and already says so:

```python
# dev note: A missing price value (sparse set) will cause fail w/o a default value here,
#           which is OK as it probably indicates a true error.
```

Only two params there carry a `default=`, and each states its reason in place:
`elec_load` (so `r, y, hr` can be iterated confidently, as all three should be defined)
and `cap_factor_vre` (the indexing set is wider than the data's upper-bound limit). Both
are deliberate. What to avoid is the *undocumented* default added to make construction
succeed — that is a smell, and the fix is usually a correctly sparse index set rather
than a fabricated value.

Two consequences that have each caused a real bug here:

1. **`Set` has no `default=`.** Passing one raises
   `ValueError: Unexpected keyword options found while constructing 'IndexedSet': default`.
   The fallback must come from the initializer object — a `defaultdict` or a rule.
2. **A sparse `Set` is only safe if its consumers are equally sparse.** Constraints
   declared over a wide index set (e.g. `self.elec_load.index_set()`) that reach into a
   narrowly-populated Set will `KeyError` during *constraint* construction — not at the
   `Set` declaration, which succeeds quietly.

## Reading the error location

The traceback tells you which of the two phases failed, and they mean different things:

- `Constructing component 's' from data=None failed` → the `Set`/`Param` declaration
  itself is bad (wrong `dimen`, value outside `within`, malformed initializer).
- `Rule failed when generating expression for Constraint <name> with index (...)` → the
  declaration was fine; a *constraint body* asked for something the data doesn't have.
  This is the signature of the sparse-data / dense-consumer mismatch.

## Type checking pyomo code

Pyomo is effectively untyped from a checker's point of view, and two patterns dominate:

- `pyomo.common.numeric_types.value` is annotated as returning `None`, which poisons
  every arithmetic expression downstream of it.
- Components attached at runtime (`self.Foo = pyo.Set(...)`) are seen as the base
  `Component` class, so indexing and iterating them look invalid.

This repo handles that three ways, in increasing order of bluntness. Prefer the least
blunt one that works:

1. Inline `# pyrefly: ignore[...]` with a rationale — the norm; see
   `src/integrator/utilities.py`, which carries several.
2. A local `m: Any = em` alias plus a typed `_val()` wrapper around `value()`, when a
   single function would otherwise need a dozen suppressions.
3. `project-excludes` in `pyproject.toml` under `[tool.pyrefly]` — reserved for files
   that are essentially all pyomo construction. Currently the electricity and natural
   gas model files (`electricity_model.py`, `ng_model.py`, `ng` `postprocessor.py`),
   plus the two unmaintained `src/integrator` modules, which are excluded for a
   different reason: they are not in upgrade scope.

Note that `pixi run lint-pyrefly` passes **no path argument** on purpose: naming files on
the command line makes pyrefly ignore `project-excludes`.

## Gotchas worth knowing

- `sorted(some_dict)` returns the **keys**. If you meant to sort the members of a
  dict-of-collections, you want `{k: sorted(v) for k, v in d.items()}`. Passing the
  former to an indexed `Set` silently makes every index share a member list of index
  tuples — which then fails `within` validation on `dimen`, or does nothing at all when
  the dict is empty.
- A plain Python `Enum` class works both as an index set and as a `within=` domain, and
  it really does validate. But `SomeEnum.MEMBER == 'member_value'` is `False`, so keep
  member-vs-value straight throughout — mixing them produces comparisons that are always
  false rather than errors.
- `value()` works on an `Objective`; use `.expr` when you need the expression itself
  (e.g. to hand to `generate_standard_repn`).
- Iterating a component yields index keys, not values: `for idx in m.Foo` then
  `value(m.Foo[idx])`.

## Growing this skill

Keep `SKILL.md` to things worth knowing *before* you start writing, and push detail into
`references/`. Subdivide when a section stops fitting on a screen or starts serving a
different task. Likely next splits:

- `references/type-checking.md` — when the pyrefly section outgrows the summary above.
- `references/solvers.md` — solver selection, `select_solver`, termination-condition
  handling, duals/suffixes.
- `references/debugging.md` — inspecting a built or solved model; this repo already has
  `analysis_tools/model_diagnostics.py` for that and it should be cross-referenced.

Every claim added here should be traceable to a probe that was actually run. If something
is believed but unverified, mark it as such rather than stating it flatly.
