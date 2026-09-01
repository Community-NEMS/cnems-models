# Indexed Sets and Params: sparse vs dense

Everything here was verified against **pyomo 6.10.1** with runnable probes; the probe
source is included so it can be re-run on an upgrade.

## The core asymmetry

`Param` and `Set` treat a missing index completely differently, and `Param` changes
behavior based on whether a `default` was supplied.

### Param *with* a default is dense

```python
m = pyo.ConcreteModel()
m.i = pyo.Set(initialize=['a', 'b'])
m.j = pyo.Set(initialize=[1, 2])  # declared space = 4
m.p = pyo.Param(m.i, m.j, initialize={('a', 1): 10.0, ('b', 2): 20.0}, default=0.0)
```

```
declared index space : 4
len(m.p)             : 4
list(m.p)            : [('a', 1), ('a', 2), ('b', 1), ('b', 2)]
m.p['a', 2] (unset)  : 0.0
sum over iteration   : 30.0
sum over declared    : 30.0
```

Supplying `default=` makes the Param behave as if fully populated. Iterating it covers
the **whole declared index space**, not just the initialized keys.

Practical consequence: iterating `for idx in m.p` and looping the declared sets by hand
give identical results, so there is no need to defensively expand the index yourself. Do
not assume "has a default" implies "is sparse" — it is the opposite.

### Param *without* a default is sparse

```python
m.q = pyo.Param(m.i, m.j, initialize={('a', 1): 10.0})
```

```
no-default Param, len: 1
  unset lookup -> ValueError
```

Note the exception type: `ValueError`, not `KeyError`.

**This is the preferred shape in this project.** Add `default=` only where the default
value genuinely means something for that quantity. Where there is no sensible default, or
where the param is only ever meant to be reached through a sparse index set, omit it and
let a stray lookup raise.

The reasoning is that a `default` silences exactly the signal you want. Without one, an
index that should never have been asked for fails loudly at build time; with one, it
returns a believable zero and the mistake surfaces later as a wrong objective value —
if at all. Deciding "0.0 is the right answer for a missing supply price" is a modeling
claim, and usually a false one.

Examples in `electricity_model.py`, both already annotated in place:

| Param | Shape | Why |
|---|---|---|
| `supply_price` | no default | *"A missing price value (sparse set) will cause fail w/o a default value here, which is OK as it probably indicates a true error."* — the intended pattern |
| `elec_load` | `default=0.0` | deliberate: zero load is meaningful, and it lets the code iterate `(r, y, hr)` confidently |
| `cap_factor_vre` | `default=0.0` | deliberate: *"the indexing set is larger than the upper bound limit from the data"* |

When a default is being added purely to stop construction from failing, the real fix is
almost always a correctly sparse index set rather than a fabricated value.

### Set is always sparse, and has no default

```python
SPARSE = {('a', 1): ['x', 'y']}  # 1 of the 4 declared indices
m.s = pyo.Set(m.letter, m.number, initialize=SPARSE)
```

```
construction succeeded, len(m.s) = 1  (declared index space = 4)
m.s['a', 1] (present) = ['x', 'y']
m.s['b', 2] (missing) -> KeyError: ('b', 2)
```

Sparse initialization is **fine** — construction succeeds and the populated index returns
its members. What fails is *reaching for an index the data doesn't cover*. Pyomo
re-invokes the initializer for an unconstructed index, and a plain dict's `__getitem__`
raises there.

Trying to paper over it with a `default` does not work:

```
Set default= -> ValueError: Unexpected keyword options found while constructing 'IndexedSet': default
```

## Every `initialize=` form for an indexed Set

Same 4-index declared space, same sparse data, `within=m.tw * m.st`:

| `initialize=` | len after construct | present index | missing index |
|---|---|---|---|
| plain `dict` | 1 | `['x', 'y']` | `KeyError` |
| `defaultdict(list, ...)` | 1 | `['x', 'y']` | `[]` |
| rule / `lambda` with `.get()` | 4 | `['x', 'y']` | `[]` |
| a list (e.g. `[]`) | 4 | `[]` | `[]` |
| `sorted(some_dict)` | — | `ValueError` on `dimen` | — |

Notes on each:

- **plain dict** — correct whenever every consumer only visits populated keys.
- **defaultdict** — sparse construction plus a lazy empty fallback. Both the pyomo Set and
  the backing dict grow as absent indices are touched, so the dict you passed in is
  mutated as a side effect of constraint construction.
- **rule function** — `lambda m, *index: ...`. Constructs the full declared space up
  front. Most explicit, no mutation, but pays for every index whether or not it has data.
- **a bare list/iterable** — applied as the members of *every* index. This is rarely what
  you want for per-index data, but it is why an empty list "works": it produces a fully
  dense set of empty sets.
- **`sorted(some_dict)`** — a bug, not a form. `sorted()` on a dict yields its *keys*, so
  this is the previous row with the index tuples as members:
  `ValueError: The value=('7', 2025, 1) has dimension 3 and is not valid for Set s['7',2025,1] which has dimen=2`.
  When the dict is empty it degrades to `sorted({}) == []` and the bug hides completely.

## Where this bit us

`electricity_model.py` built `wind_reserves` / `solar_reserves` (then named
`WindSetReserves` / `SolarSetReserves`) with `initialize=sorted(wind_idx)`. On the current dataset no VRE tech survives the reserve
upper-bound sparsity filter, so `wind_idx` is empty, `sorted({})` is `[]`, and the model
constructed 576 empty sets and behaved correctly by accident. With any nonzero VRE
reserve bound it would have raised the `dimen` ValueError above.

The first fix attempt — a plain dict comprehension — then failed the *other* way:

```
Rule failed when generating expression for Constraint reserve_requirement_reg_lb with index ('7', 2025, 1)
KeyError: ('7', 2025, 1)
```

because `reserve_requirement_reg_lb` and `reserve_requirement_flex_lb` are declared over
`self.elec_load.index_set()` — all 576 `(region, year, hour)` — and index `wind_reserves`
unconditionally. Sparse data, dense consumer. The landed fix wraps the sorted mapping in
a `defaultdict(list)`, and says so in a comment above `wind_members` / `solar_members`.

By contrast `reserves_procurement_index` passes a sparse dict directly and is fine,
because its consumers only iterate keys it populates.

## Domain validation with `within=`

`within=` is enforced per member set on an indexed Set, and it does real work:

```python
pyo.Set(probe.i, within=ReserveType, initialize={1: ['not-a-reserve-type'], 2: []})
# ValueError: Cannot add value not-a-reserve-type to Set s[1].
#   The value is not in the domain {ReserveType.FLEX, ReserveType.SPINNING, ReserveType.REGULATION}
```

A plain Python `Enum` class is accepted both as a `within=` domain and as an index set;
pyomo iterates it into the set of its **members**. Because `ReserveType.REGULATION` and
the string `'regulation'` are not equal, storing values in an index while comparing
against members yields silently-always-false comparisons. Pick one representation and
enforce it with a `validate=` callback — see
`src/models/electricity/validators.py::reserve_procurement_check`.

## Probe source

Two throwaway scripts reproduce everything above. Keep them in the scratchpad, not the
repo.

```python
# sparse Set: construction vs access
from collections import defaultdict
import pyomo.environ as pyo

LETTERS, NUMBERS = ['a', 'b'], [1, 2]  # declared space = 4
SPARSE = {('a', 1): ['x', 'y']}  # data for 1 of them


def build(initialize):
    m = pyo.ConcreteModel()
    m.letter = pyo.Set(initialize=LETTERS)
    m.number = pyo.Set(initialize=NUMBERS)
    m.s = pyo.Set(m.letter, m.number, initialize=initialize)
    return m


for label, init in (
    ('plain dict', SPARSE),
    ('defaultdict', defaultdict(list, SPARSE)),
    ('rule fn', lambda _m, a, b: sorted(SPARSE.get((a, b), ()))),
    ('bare list', []),
):
    m = build(init)
    built = len(m.s)  # measure BEFORE touching a missing index
    try:
        missing = sorted(m.s['b', 2])
    except Exception as exc:
        missing = type(exc).__name__
    print(f'{label:<12} built={built}  missing={missing}  after={len(m.s)}')
```

```
plain dict   built=1  missing=KeyError  after=2
defaultdict  built=1  missing=[]        after=2
rule fn      built=4  missing=[]        after=4
bare list    built=4  missing=[]        after=4
```

Measure `len()` before the access or the numbers mislead: touching a missing index grows
the component in the sparse cases — and it grows even when the lookup *raised*, since
pyomo creates the member set before asking the initializer to populate it.

```python
# Param: default makes it dense
import pyomo.environ as pyo

m = pyo.ConcreteModel()
m.i = pyo.Set(initialize=['a', 'b'])
m.j = pyo.Set(initialize=[1, 2])
m.p = pyo.Param(m.i, m.j, initialize={('a', 1): 10.0, ('b', 2): 20.0}, default=0.0)
m.q = pyo.Param(m.i, m.j, initialize={('a', 1): 10.0})

print(len(m.p), sorted(m.p))  # 4, all four keys
print(len(m.q))  # 1
```
