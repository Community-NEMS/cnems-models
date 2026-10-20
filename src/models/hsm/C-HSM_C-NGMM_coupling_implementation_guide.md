# C-HSM / C-NGMM coupling: implementation guide

Targets: `main` at `01af482` and `hsm` at `ede057f` (both as of 2026-10-20).

Companion patch: `patches/coupling_main-01af482_hsm-ede057f.patch`, applied after step 1. It holds every code change in steps 2 to 9, except the new tests in step 9 and the optional pixi tasks in step 8. It passes `ruff check`, `ruff format --check` and `pyrefly` on the changed files, and its modules import; it has not been run. It replaces `A_jacobi_on_main.patch` and `B_gauss_seidel_branch.patch`, which target commits that `main` has since replaced.

## Contents

In plain language (below), then the detailed sections:

1. Summary
2. The exchange
3. Order and stopping rule
4. Implementation steps
5. Settings
6. Limits and open decisions

## In plain language

This section restates the steps in plain terms and points only to code that exists in `main` and `hsm` today. Each step names the detailed step in section 4 that carries it out.

### What the two models do

C-HSM (hsm branch) estimates how much gas the US can produce in each region, year by year from 2023 to 2050, for a given gas price. C-NGMM (main) finds the gas price and production that balance supply and demand in nine regions for selected years (2025, 2030 and so on). C-NGMM builds its supply curve around one number per region and year: the expected production level, called Q0. To couple them, C-HSM passes its production capacity to C-NGMM and C-NGMM passes a gas price back. They repeat this until the numbers stop changing.

### What already exists

- C-HSM reports capacity (`poll_us_natgas_capacity`) and accepts new prices (`update_prices`), both in `src/models/hsm/hsm_model.py`.
- C-NGMM rebuilds its supply curve from a new Q0 (`update_supply_capacity`) and reports prices (`poll_gas_price`), both in `src/models/natural_gas/ng_model.py`.
- Main has a system for passing data between models, in `src/common/update_package/`. Each kind of data travels in a labeled container. Each model has a writer that fills containers and a reader that unpacks them. The electricity and gas models use this today.
- Main has two loop drivers. Jacobi runs all models at once (`src/integrator/jacobi/`). Gauss-Seidel runs them one after another (`src/integrator/gauss_seidel/`).
- The two models already use the same nine regions. C-HSM reads C-NGMM's region list (`load_ng_supply_side` in `src/models/hsm/data.py`), so no translation between regions is needed.

### The steps

1. Bring C-HSM onto main. Merge the hsm branch into a new branch from main. One file conflicts: the list of model names in `src/common/models_modes.py`. Keep main's list and add C-HSM to it. (Detailed: step 1.)

2. Bring C-HSM up to main's rules for models. Main now expects each model to give a display name and its list of regions. Each model's runner must also have a reader, a writer and a status. C-HSM's runner (`src/models/hsm/sequencer.py`) predates these rules and calls a logging helper that main removed. Update it the way the gas runner (`src/models/natural_gas/sequencer.py`) does it. The C-HSM tests in `tests/hsm` should pass afterwards. (Detailed: step 3.)

3. Define two new containers beside the existing ones in `src/common/update_package/update_package.py`:
   - A capacity container, from C-HSM to C-NGMM, with one number per region and year.
   - A price container, from C-NGMM to C-HSM, with one price per year.

   Each container rejects blanks, negative numbers and zero prices. (Detailed: step 2.)

4. C-HSM fills the capacity container. For each region and each of C-NGMM's years, it adds up its capacity across cost tiers and gas types. (Detailed: step 3.)

5. C-NGMM unpacks the capacity container and sets Q0 to it. There are two cases:
   - When C-NGMM is about to be built (Jacobi rebuilds it every round), the reader changes the starting production numbers in its input data so the new model starts with the received Q0.
   - When C-NGMM is already built (Gauss-Seidel keeps it), the reader calls `update_supply_capacity`. That function already redraws the supply curve around the new Q0 and keeps the price anchor.

   In both cases, a container that misses a region or year is rejected. The check already exists (`check_region_year_keys`). (Detailed: step 4.)

6. C-NGMM fills the price container. It takes the West South Central price for each year from `poll_gas_price`. Henry Hub sits in West South Central, so that price stands in for Henry Hub. (Detailed: step 4.)

7. C-HSM turns that price into the Henry Hub price path it runs on. C-HSM needs a price for every year from 2023 to 2050, in 1987 dollars. The reader divides C-NGMM's price by the AEO2026 reference price for the same year. Both reference prices come from `input/hsm/henry_hub_path_aeo2026_1987usd.csv`, which has the reference path in 2025 and in 1987 dollars. The reader draws straight lines to fill the years in between, then scales the 1987-dollar path by that ratio. When C-NGMM's price equals the reference, C-HSM runs on the reference path. C-HSM starts fresh from its inputs each round; a run takes about 2 seconds. (Detailed: step 3.)

8. Add C-HSM to the loop drivers. (Detailed: steps 5 and 6.)
   - Add its `[hsm]` settings section to the table in `src/integrator/config_sections.py`.
   - Give it a line in the Jacobi driver.
   - Give it first place in the Gauss-Seidel order: C-HSM, then electricity, then gas. The Gauss-Seidel driver accepts only electricity with gas today. It also needs to accept gas with C-HSM, with or without electricity.
   - Add the two container names to the exchange lists in `jacobi_config.toml` and `gs_config.toml`.
   - Leave C-HSM out when a config says `['all']` unless it is named, as main already does for the test model MAGIC. This keeps the current run configs working.

9. Fix the stopping rule. Main stops when each model's objective value stops changing (`src/integrator/convergence.py`). C-HSM has no objective, and C-NGMM's objective barely moves while Q0 moves. Add a second check: stop only when the capacity and price numbers also change by less than half a percent between rounds. (Detailed: step 7.)

10. Add run configs for the two coupled cases: C-HSM with gas, and C-HSM with gas and electricity. Copy `run_configs/gs_compare.toml` and add an `[hsm]` section. Do not filter C-NGMM's regions in these runs. C-HSM always reports all nine regions, and C-NGMM rejects regions it does not hold. (Detailed: step 8.)

11. Test the result. (Detailed: step 9.)
   - At the reference price, the capacity C-HSM sends matches its saved results in `tests/hsm/expected/hsm_us_gas_capacity.csv`.
   - Sending the same numbers twice gives the same model.
   - A higher gas price raises capacity, and the higher capacity lowers the gas price.
   - Jacobi and Gauss-Seidel end at the same numbers.
   - Runs without C-HSM give the same results as today.

### Open questions for the team

- Dollar year: we assume C-NGMM's prices are in 2025 dollars. No input file says so.
- West South Central: should its price stand in for Henry Hub?
- Associated gas: the part of C-HSM's capacity that comes from oil wells follows the oil price, not the gas price. It still sits inside the single Q0.
- Calibration: at its own starting Q0, C-NGMM's price sits below the AEO Henry Hub price. The first round therefore lowers C-HSM's capacity. That is a question about C-NGMM's input numbers, not about the link.

Section 6 covers these and the other limits in more detail.

## 1. Summary

Two update packages cross each iteration, one each way. Nothing else crosses.

- C-HSM sends US gas production capacity by region and summary year (`HSMSupplyCapacityPackage`). C-NGMM sets its supply anchor Q0 to it.
- C-NGMM sends its solved price in West South Central by summary year (`NGHubPricePackage`). C-HSM turns it into the Henry Hub path it runs on.

Both packages use the framework on `main`: registered package types, config-selected exchanges (`ExchangeSpec`), and writers with `builders`. C-NGMM receives through two handlers, one on its loaded data before a build and one on a built model. C-HSM has one handler, on its loaded data (`HSMData`); its `update_model` applies it and rebuilds the model. C-HSM runs under the Gauss-Seidel iterator (C-HSM, then electricity, then C-NGMM) and under the Jacobi iterator. A run stops when the objectives and the two exchanged packages have settled.

## 2. The exchange

### 2.1 Shared indexes and units

| Item | Rule |
|---|---|
| Regions | The nine census divisions, with C-NGMM's ids (`ng_region_data.csv`, domestic regions). C-HSM reads them through `load_ng_supply_side` (`src/models/hsm/data.py`), so no crosswalk is needed. |
| Years | C-NGMM solves `[common] summary_years`. C-HSM runs every year from 2023 to 2050 (`input/hsm/setup.csv`) and reports the summary years. A summary year outside 2023 to 2050 is an error. |
| Quantities | Bcf/yr on both sides. |
| Prices | C-NGMM: USD/MMBtu in the dollar year `PRICE_DOLLAR_YEAR` (2025). C-HSM's Henry Hub input: 1987 USD/MMBtu. |

### 2.2 C-HSM to C-NGMM: capacity sets Q0

| Item | Value |
|---|---|
| Package | `HSMSupplyCapacityPackage`; sender `ModelType.HSM`; receiver `ModelType.NATURAL_GAS` |
| Source | `HSMModel.poll_us_natgas_capacity()`: `{(region, cost_tier, gas_type, year): Bcf/yr}`. `cost_tier` is `low_cost`, `medium_cost` or `high_cost`; `gas_type` is `na` or `ad`, and `ad` is booked under `medium_cost`. With the NA/AD split off, the key is `(region, cost_tier, year)`. |
| Transform | `Q(r, y)` = sum over `cost_tier` and `gas_type` of the capacity of region `r` in summary year `y` (`HSMUpdateWriter.capacity_package`) |
| Frame | index `['region', 'year']` (`HSM_CAPACITY_INDEX`), one column `capacity_bcf` (`HSM_CAPACITY_VALUE`); 9 regions x number of summary years |
| Target | `Q0[r, y]` (`NGModel.q0`), replaced by `Q(r, y)`, no blending |
| Before a build (Jacobi) | `NGUpdateReader` sets `data['supply_anchors'][(r, y)] = (Q(r, y) / tier total(r), p0_mult)`, where the tier total is the sum of `capacity_bcf` over the region's rows of `ng_supply_cost_tiers.csv`. The model then builds with `Q0 = Q`. `validate_all` runs after the reader, so `validate_supply_anchors` checks the received Q0 too. |
| On a built model (Gauss-Seidel) | `NGModelUpdateReader` calls `NGModel.update_supply_capacity({(r, 'total', y): Q}, alpha=1.0)` |
| Parameters rebuilt from Q0 (both paths) | `q_min = f_min x Q0` (`supply_curve_qmin_fraction`, 0.20, `ng_scalars.csv`); `q_base[r, k, y]`, k = 1..6, with `q_base[r, 1, y] = q_min` (`_supply_qbase_at`); `p_base[r, k, y]` around the unchanged P0; `supply_capacity` (segment widths); `prod_cost_intercept` and `prod_cost_slope`. `production_total` and `committed_cost` read `q_base[r, 1, y]` and follow. |
| Kept | `P0[r, y]` (`NGModel.p0`: capacity-weighted tier cost x `p0_mult`), the curve shape (`ng_supply_curve_shape.csv`), the floor fraction, `canada_supply` |
| Checks | Package: index names, duplicate rows, the column, missing, negative or infinite values. Readers: `check_region_year_keys` (the received entries must equal C-NGMM's regions x years); `0 < Q < 1` Bcf is rejected (`MIN_POSITIVE_Q0`); before a build, a positive value for a region with no tier capacity is rejected. |

Values with `0 < Q < 1` Bcf are rejected because segments that narrow give slopes that dwarf the rest of the QP, and HiGHS has crashed on Q0 values near 1e-8 Bcf. Zero is allowed: the region then produces nothing.

### 2.3 C-NGMM to C-HSM: hub price sets the Henry Hub path

| Item | Value |
|---|---|
| Package | `NGHubPricePackage`; sender `ModelType.NATURAL_GAS`; receiver `ModelType.HSM` |
| Source | `NGModel.poll_gas_price()`: `abs(dual(demand_balance[r, y])) / bcf_to_mmbtu`, USD/MMBtu |
| Selection | `r = HUB_REGION = 'west_south_central'`, every summary year (`NGUpdateWriter.hub_price_package`) |
| Frame | index `['year']` (`NG_HUB_PRICE_INDEX`), one column `price` (`NG_HUB_PRICE_VALUE`); fields `hub_region` and `dollar_year = PRICE_DOLLAR_YEAR` |
| Target | The Henry Hub path `H(y)`, 1987 USD/MMBtu, every year 2023 to 2050: `HSMData.henry_hub_1987`, passed to `HSMModel(henry_hub_prices=...)`, which C-HSM holds as `module.rest_henry_hub` |
| Reference path | `[hsm] coupling_reference_file` (default `henry_hub_path_aeo2026_1987usd.csv`): `Href87(y)` in column `hh_1987_usd_per_mmbtu` and `HrefUSD(y)` in column `hh_<dollar year>usd_per_mmbtu` |
| Checks | Package: index, empty frame, duplicate years, the column, missing, infinite or non-positive prices (`poll_gas_price` reports a missing dual as 0.0). Writer: C-NGMM must carry `HUB_REGION`. Reader: `dollar_year` must equal `[hsm] coupling_reference_dollar_year`, and each package year needs a reference price. `HSMModel`: the path needs a finite price in every model year. |

Transform in `HSMUpdateReader` (`src/models/hsm/update_reader.py`):

```
R(t) = P_WSC(t) / HrefUSD(t)        at each summary year t, both in the package's dollar year
R(y) = fill_ratio(R, 2023..2050)    linear between summary years, flat before the first and after the last
H(y) = Href87(y) x R(y)             every year 2023..2050
```

At reference prices `R = 1` and `H = Href87`. The adapter applies no deflator, so C-HSM's 1987 USD convention stays inside C-HSM.

Inside C-HSM, with no code change, the regional wellhead price is `H(y) x deflator(2023) + REGIONAL_BASIS(r)` in 2023 USD (`module.py`, `run_year`), and non-associated capacity responds to it. In the reduced form, associated-dissolved capacity follows Brent and does not respond to `H`; with the well-level engine on (`onshore_engine_file`), the engine uses the gas price path for every project, associated-dissolved wells included.

### 2.4 Held fixed

- C-NGMM: P0 and `p0_mult`; the curve shape and the floor fraction; `canada_supply` (zero); demand, other than the electricity burn when electricity runs.
- C-HSM: the Brent path; the `(k, g, c)` calibration; the elasticities; `REGIONAL_BASIS`; Canada, which C-HSM computes and does not send.

## 3. Order and stopping rule

Gauss-Seidel (`GS_ORDER`): C-HSM, then electricity, then C-NGMM, over the selected models. The accepted model sets (`GS_RUNS`) are `{electricity, natural_gas}`, `{hsm, natural_gas}` and `{hsm, electricity, natural_gas}`. In iteration 1, C-HSM runs on its starting path (`[hsm] henry_hub_file`) and C-NGMM runs on that iteration's capacity. From iteration 2, C-HSM runs on the last hub price. C-HSM rebuilds from its held data on each update, and the new model starts from a reset Canada state; a C-HSM run takes 2 s (`src/models/hsm/README.md`).

Jacobi: every model runs in parallel on the packages of the previous iteration. In iteration 1, C-HSM runs on its starting path and C-NGMM on its own anchors.

No damping: both readers replace values (`alpha = 1`). The loop gain between Q0 and the hub price was 0.15 to 0.17 in prototype runs, so the error shrinks by that factor each Gauss-Seidel iteration and by its square root each Jacobi iteration.

Stop rule: both tests must pass in the same iteration.

1. `ConvergenceTracker` (on `main`): relative objective change below `epsilon` for `convergence_iterations` iterations. C-HSM has no objective and is left out.
2. `ExchangeTracker` (new, `src/integrator/convergence.py`): for each package type in `exchange_stop_packages` that the run carries, the largest relative change over its entries stays below `exchange_epsilon` for `convergence_iterations` iterations. Values in `jacobi_config.toml` and `gs_config.toml`: `HSMSupplyCapacityPackage` and `NGHubPricePackage`, 0.005. A run without C-HSM tracks nothing and stops as before.

The second test is needed because the C-NGMM objective moves little while Q0 moves: in a prototype Jacobi run, the objective changed by 0.02% in an iteration where Q0 moved 19%.

## 4. Implementation steps

Commands are for Windows Command Prompt, run from the repository root.

### Step 1. Merge `hsm` into a branch from `main`

```
git fetch origin
git switch -c hsm-ngmm-coupling origin/main
git merge origin/hsm
```

One file conflicts: `src/common/models_modes.py`. Resolve it to:

```python
class ModelType(Enum):
    """The individual models available to a run."""

    ALL = 'all'  # indicator implying all models
    ELECTRICITY = 'electricity'
    NATURAL_GAS = 'natural_gas'
    HSM = 'hsm'
    MAGIC = 'magic'  # for testing/dev
```

Drop `INTEGRATOR` from `hsm`: nothing uses it, and `resolve_models_to_run` would treat it as a model. These merge without conflict: `pyproject.toml` (pixi task `hsm`, pyrefly excludes for the C-HSM engine files), `.pre-commit-config.yaml` (large-file exclude for `input/hsm/onshore/on_projects_producing_oil.csv`) and `zensical.toml` (docs nav).

```
git add src\common\models_modes.py
git commit
```

The merged branch does not run C-HSM yet: `HSMModel` lacks the `label` and `regions` properties that `IntegratedModel` now requires, `HSMSequencer` predates the reader and writer interface, and `sequencer.py` imports `setup_logger`, which `main` removed. Step 3 fixes these.

To take the companion patch instead of steps 2 to 9:

```
git apply path\to\coupling_main-01af482_hsm-ede057f.patch
```

### Step 2. Add the two packages

File: `src/common/update_package/update_package.py`. Follow *Adding a Package Type* in `docs/run_modes/integrated.md`.

```python
HSM_CAPACITY_INDEX = REGION_YEAR_INDEX
HSM_CAPACITY_VALUE = 'capacity_bcf'


@register_package
@dataclass(frozen=True)
class HSMSupplyCapacityPackage(UpdatePackage):
    sender: ClassVar[ModelType] = ModelType.HSM
    elements: pd.DataFrame
    receivers: tuple[ModelType, ...] = (ModelType.NATURAL_GAS,)
    label: str = 'HSM Capacity'

    @property
    def size(self) -> int:
        return len(self.elements)

    def __post_init__(self) -> None:
        _check_region_year_frame(self, self.elements, HSM_CAPACITY_VALUE)
        if not np.isfinite(self.elements[HSM_CAPACITY_VALUE].to_numpy(dtype=float)).all():
            raise ValueError(f'{type(self).__name__} requires finite values in {HSM_CAPACITY_VALUE!r}.')


NG_HUB_PRICE_INDEX = ['year']
NG_HUB_PRICE_VALUE = 'price'


@register_package
@dataclass(frozen=True)
class NGHubPricePackage(UpdatePackage):
    sender: ClassVar[ModelType] = ModelType.NATURAL_GAS
    elements: pd.DataFrame
    hub_region: str
    dollar_year: int
    receivers: tuple[ModelType, ...] = (ModelType.HSM,)
    label: str = 'Hub Price'

    @property
    def size(self) -> int:
        return len(self.elements)

    def __post_init__(self) -> None:
        # raise ValueError unless: index names == NG_HUB_PRICE_INDEX; at least one row; no
        # duplicate years; NG_HUB_PRICE_VALUE present; every price finite and > 0
        ...
```

Add `import numpy as np`. Export the four constants and two classes from `src/common/update_package/__init__.py`, and keep `__all__` in the order the list has now: upper-case constants, then classes, then functions, each group alphabetical.

### Step 3. C-HSM side

`src/models/hsm/hsm_config.py`:

- `class HSMConfig(ModelConfig)` (from `src.common.common_config`), in place of `BaseModel`; drop the unused import.
- New fields `coupling_reference_file: str = 'henry_hub_path_aeo2026_1987usd.csv'` and `coupling_reference_dollar_year: int = 2025`, documented in the class docstring.
- Add `coupling_reference_file` to the file loop in `check_paths`.

`src/models/hsm/hsm_model.py`:

- `label` returns `'Hydrocarbon Supply'`.
- `regions` returns `frozenset(str(r) for r in self.module.us_gas.regions)`.
- New keyword `summary_years: Sequence[int] = ()`. Raise `ValueError` if a summary year is not in `self.years`; store the sorted list as `self.summary_years`. The keyword default keeps the existing tests' constructor calls valid.

New `src/models/hsm/update_reader.py`:

```python
@dataclass
class HSMData:
    regions: list[str]
    supply_cost_tiers: dict[str, list[tuple[float, float]]]
    henry_hub_1987: dict[int, float]   # the path C-HSM runs on, every model year
    reference_1987: dict[int, float]   # reference path, 1987 USD
    reference_usd: dict[int, float]    # same path, reference_dollar_year USD
    reference_dollar_year: int


def load_reference_pair(path: Path, dollar_year: int) -> tuple[dict[int, float], dict[int, float]]:
    # load_price_path(path, HENRY_HUB_COLUMNS, ...) and
    # load_price_path(path, (f'hh_{dollar_year}usd_per_mmbtu',), ...); same years in both


def fill_ratio(ratio: dict[int, float], years: Sequence[int]) -> dict[int, float]:
    # linear between known years, held at the first and last known values outside them


class HSMUpdateReader(UpdatePackageReader[HSMData]):
    @singledispatchmethod
    def apply_package(self, update_package: UpdatePackage, data: HSMData) -> None:  # type: ignore[bad-override]
        raise NotImplementedError(...)

    @apply_package.register  # type: ignore[no-matching-overload]
    def _(self, update_package: NGHubPricePackage, data: HSMData) -> None:
        if update_package.dollar_year != data.reference_dollar_year:
            raise ValueError(...)
        received = update_package.elements[NG_HUB_PRICE_VALUE]
        prices = {int(y): float(p) for y, p in zip(received.index.to_list(), received.to_list(), strict=True)}
        # raise ValueError for a year without a reference price
        ratio = {y: p / data.reference_usd[y] for y, p in prices.items()}
        years = sorted(data.reference_1987)
        filled = fill_ratio(ratio, years)
        data.henry_hub_1987 = {y: data.reference_1987[y] * filled[y] for y in years}
```

The handler recomputes the path from the reference path each time, so applying a package twice gives the same path.

New `src/models/hsm/update_writer.py`:

```python
class HSMUpdateWriter(UpdatePackageWriter[HSMModel]):
    @property
    def builders(self) -> Mapping[type[UpdatePackage], Callable[[HSMModel], UpdatePackage]]:
        return {HSMSupplyCapacityPackage: self.capacity_package}

    def capacity_package(self, model: HSMModel) -> HSMSupplyCapacityPackage:
        totals = {(r, y): 0.0 for r in model.regions for y in model.summary_years}
        for (region, *_, year), bcf in model.poll_us_natgas_capacity().items():
            key = (str(region), int(year))
            if key in totals:
                totals[key] += float(bcf)
        # raise ValueError if model.summary_years is empty or a (region, year) got no capacity
        series = pd.Series(totals, name=HSM_CAPACITY_VALUE).sort_index()
        series.index = series.index.set_names(HSM_CAPACITY_INDEX)
        return HSMSupplyCapacityPackage(elements=series.to_frame(), source=ModelType.HSM)
```

`src/models/hsm/sequencer.py`:

- `class HSMSequencer(IntegratedModelSequencer[HSMModel, HSMConfig, HSMData])`.
- `__init__(self, exchange: ExchangeSpec | None = None)`: `exchange = exchange if exchange is not None else ExchangeSpec.empty()`, as `NGSequencer` does, so a standalone `HSMSequencer()` writes nothing; then `self._reader = HSMUpdateReader()`, `self._writer = HSMUpdateWriter(exchange.outbound_for(ModelType.HSM), exchange.crosswalks)`, `self._last_status = None` and `self._data = None`.
- Properties `reader`, `writer` and `last_status`.
- `build_model(common_config, model_config, update_packages=None)`: build `HSMData` from `load_ng_supply_side`, `load_henry_hub_path(input_path / henry_hub_file)` and `load_reference_pair(input_path / coupling_reference_file, coupling_reference_dollar_year)`; apply `self._reader.read(update_packages, data)`; keep the data; build `HSMModel(..., henry_hub_prices=data.henry_hub_1987, summary_years=common_config.summary_years)`.
- `update_model(update_packages)`: apply the packages to the held `HSMData`, then build a new `HSMModel` from it. C-HSM has no solver to keep warm, and a new model starts from a reset Canada state.
- `solve_model()`: run, set `self._last_status = IterationStatus.USABLE`, return `(ModelType.HSM, IterationStatus.USABLE)`.
- `get_objective_value()` returns `None`.
- `main()`: replace `setup_logger(common_config)` with `setup_control_loop_logging(common_config.output_folder / 'run.log')` from `src.common.log_setup`, and unpack `_, status = sequencer.solve_model()`.

`tests/hsm/test_hsm_basic_run.py`: unpack the tuple, `_, status = sequencer.solve_model()`.

Gate: `pytest tests/hsm -q` passes.

New modules get the header docstring from `CLAUDE.md` (author, contact, date, and a `Written with:` line when a model helped).

### Step 4. C-NGMM side

`src/models/natural_gas/constants.py`:

```python
HUB_REGION = 'west_south_central'
PRICE_DOLLAR_YEAR = 2025
```

`src/models/natural_gas/update_writer.py`: add `NGHubPricePackage: self.hub_price_package` to `builders`.

```python
def hub_price_package(self, model: NGModel) -> NGHubPricePackage:
    if HUB_REGION not in model.regions:
        raise ValueError(...)
    prices = pd.Series(
        {int(gi.year): price for gi, price in model.poll_gas_price().items() if gi.region == HUB_REGION},
        name=NG_HUB_PRICE_VALUE,
    ).sort_index()
    prices.index = prices.index.set_names(NG_HUB_PRICE_INDEX)
    return NGHubPricePackage(
        elements=prices.to_frame(),
        hub_region=HUB_REGION,
        dollar_year=PRICE_DOLLAR_YEAR,
        source=ModelType.NATURAL_GAS,
    )
```

`src/models/natural_gas/update_reader.py`: a constant, a helper and two handlers.

```python
MIN_POSITIVE_Q0 = 1.0  # Bcf


def _received_capacity(update_package: HSMSupplyCapacityPackage) -> dict[tuple[str, int], float]:
    # {(region, year): Bcf}; raise ValueError for any 0 < value < MIN_POSITIVE_Q0


# NGUpdateReader, before a build
@apply_package.register  # type: ignore[no-matching-overload]
def _(self, update_package: HSMSupplyCapacityPackage, data: NGData) -> None:
    check_region_year_keys(
        update_package.elements.index, data['regions_analyze'], data['years'], name='C-HSM capacity'
    )
    anchors = data['supply_anchors']
    for (region, year), bcf in _received_capacity(update_package).items():
        total = sum(cap for cap, _ in data['supply_cost_tiers'][region])
        if total == 0.0:
            if bcf > 0.0:
                raise ValueError(...)  # no tier capacity to scale
            continue
        p0_mult = anchors.get((region, year), (1.0, 1.0))[1]
        anchors[(region, year)] = (bcf / total, p0_mult)


# NGModelUpdateReader, on a built model
@apply_package.register  # type: ignore[no-matching-overload]
def _(self, update_package: HSMSupplyCapacityPackage, data: NGModel) -> None:
    check_region_year_keys(update_package.elements.index, data.regions, data.year, name='C-HSM capacity')
    values = _received_capacity(update_package)
    data.update_supply_capacity({(r, 'total', y): bcf for (r, y), bcf in values.items()}, alpha=1.0)
```

`update_supply_capacity` with `alpha=1.0` writes the same model whether a package arrives once or twice. If damping is needed, it belongs in the iterator, not in this call.

### Step 5. Register C-HSM as a model

`src/common/models_modes.py`: `ALL` leaves C-HSM out unless it is named, as it does MAGIC. Configs that use `['all']` (`gs_compare.toml`, `jacobi_small.toml`) have no `[hsm]` section and keep running as before.

```python
NAMED_ONLY: frozenset[ModelType] = frozenset({ModelType.MAGIC, ModelType.HSM})


def resolve_models_to_run(models: Sequence[ModelType]) -> list[ModelType]:
    if ModelType.ALL not in models:
        return list(models)
    excluded = {ModelType.ALL} | (NAMED_ONLY - set(models))
    return [model for model in ModelType if model not in excluded]
```

`src/integrator/config_sections.py`: add `ModelType.HSM: ('hsm', HSMConfig)` to `CONFIG_SECTIONS`, and a check that both iterators call from `_process_configs`:

```python
def check_model_configs(configs: Mapping[ModelType, ModelConfig]) -> None:
    ng_config = configs.get(ModelType.NATURAL_GAS)
    if (
        ModelType.HSM in configs
        and isinstance(ng_config, NGConfig)
        and ng_config.region_filter is not None
    ):
        raise ValueError(...)  # C-HSM reports every domestic region; drop [natural_gas] region_filter
```

`main.py`, `_run_standalone`: add

```python
case ModelType.HSM:
    runs.append((model_type, HSMConfig(**remainder['hsm']), HSMSequencer()))
```

### Step 6. Iterators

Jacobi, `src/integrator/jacobi/jacobi_iterator.py`:

- `driver`: a `case ModelType.HSM` arm that checks `isinstance(iter_call.model_config, HSMConfig)` and returns `HSMSequencer(iter_call.exchange).full_run(...)`, as the other arms do.
- `_process_configs`: call `check_model_configs(configs)` before returning.
- `DEFAULT_CIRCUIT` includes `ModelType.HSM` with no change (it lists every model, alphabetical by enum name).
- `jacobi_config.toml`: add `"HSMSupplyCapacityPackage"` and `"NGHubPricePackage"` to `exchanges`.

Gauss-Seidel, `src/integrator/gauss_seidel/gs_iterator.py`:

```python
GS_ORDER: tuple[ModelType, ...] = (ModelType.HSM, ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
GS_RUNS: tuple[frozenset[ModelType], ...] = (
    frozenset({ModelType.ELECTRICITY, ModelType.NATURAL_GAS}),
    frozenset({ModelType.HSM, ModelType.NATURAL_GAS}),
    frozenset({ModelType.HSM, ModelType.ELECTRICITY, ModelType.NATURAL_GAS}),
)
SEQUENCERS: dict[ModelType, Callable[[ExchangeSpec], IntegratedModelSequencer[Any, Any, Any]]] = {
    ModelType.HSM: HSMSequencer,
    ModelType.ELECTRICITY: ElectricitySequencer,
    ModelType.NATURAL_GAS: NGSequencer,
}
```

- `_process_configs`: require `frozenset(resolve_models_to_run(...))` to be in `GS_RUNS`; build the configs in `GS_ORDER` order for the selected models; call `check_model_configs(configs)`.
- `run`: `order = tuple(configs)`; replace every `GS_ORDER` in the method with `order` (exchange spec, sequencers, logs, builds, monitor, routing loop); build sequencers as `{model: SEQUENCERS[model](exchange) for model in order}`.
- `exchange_summary` and `format_exchange` (optional): add national C-HSM capacity by year and the hub price by year to the line printed under each iteration.
- `gs_config.toml`: add `"HSMSupplyCapacityPackage"` and `"NGHubPricePackage"` to `exchanges`.

Typing note: `SEQUENCERS` is typed as callables that take an `ExchangeSpec`; typed as `type[IntegratedModelSequencer]`, pyrefly reports the call as `object.__init__` taking one argument too many.

### Step 7. Stop test on exchanged values

`src/integrator/convergence.py`:

- `largest_relative_change(previous, current)`: the largest per-entry `relative_change` between two indexed series; `inf` if the entries differ.
- `ExchangeTracker(epsilon, required_iterations, package_names)`, with `update(results) -> bool`. For each result in `ALLOW_OUTBOUND_UPDATES`, for each package whose class name is tracked, read the one-column `elements` frame and compare it with the previous one. The first values start no streak. A change below `epsilon` adds to the type's streak; any other change resets it. It returns `True` when every tracked type's streak has reached `required_iterations`, and `True` when nothing is tracked.

`src/common/iterative_sequencer.py`, `IterativeSequencerConfig`: two fields without defaults, so the TOMLs stay the single source:

```python
exchange_stop_packages: list[str]
exchange_epsilon: Annotated[float, Field(gt=0, le=1)]
```

Both `jacobi_config.toml` and `gs_config.toml`:

```toml
exchange_stop_packages = ["HSMSupplyCapacityPackage", "NGHubPricePackage"]
exchange_epsilon = 0.005
```

In each iterator's `run`, track the packages in `exchange_stop_packages` that the run carries, and update both trackers every iteration:

```python
exchange_tracker = ExchangeTracker(
    self.config.exchange_epsilon,
    self.config.convergence_iterations,
    set(self.config.exchange_stop_packages) & {t.__name__ for t in exchange.expected},
)
...
objectives_settled = tracker.update(results)
exchanges_settled = exchange_tracker.update(results)
converged = objectives_settled and exchanges_settled
log_progress(...)
log_exchange_progress(exchange_tracker, iteration, logger)
```

`log_exchange_progress` goes in `src/integrator/bookeeping_utilities.py` beside `log_progress`; it logs each tracked type's last change. In `tests/integrator/test_jacobi_convergence.py`, add the two keys to the `VALID` dict.

### Step 8. Run configs

| File | Mode | `models_to_run` |
|---|---|---|
| `run_configs/gs_hsm_ng.toml` | `integrated gs` | `['natural_gas', 'hsm']` |
| `run_configs/jacobi_hsm_ng.toml` | `integrated jacobi` | `['natural_gas', 'hsm']` |
| `run_configs/gs_hsm_elec_ng.toml` | `integrated gs` | `['all', 'hsm']` (`gs_compare.toml` plus C-HSM) |

The first two use all six summary years (2025 to 2050 by 5). None sets a `[natural_gas] region_filter`. Each has this section:

```toml
[hsm]
input_path = 'input/hsm'
ng_input_path = 'input/natural_gas'
henry_hub_file = 'henry_hub_path_aeo2026_1987usd.csv'
coupling_reference_file = 'henry_hub_path_aeo2026_1987usd.csv'
coupling_reference_dollar_year = 2025
```

Optional pixi tasks in `pyproject.toml`, beside `gs` and `jacobi`: `gs-hsm` for `python main.py run_configs/gs_hsm_ng.toml` and `jacobi-hsm` for `python main.py run_configs/jacobi_hsm_ng.toml`.

### Step 9. Tests

Existing tests to change (in the patch):

- `tests/common/test_exchange.py`: add `HSM` to the `all_models` case, with `NG: {NGPricePackage, NGHubPricePackage}` and `HSM: {HSMSupplyCapacityPackage}`. Without it the case logs an exclusion warning and fails.
- `tests/hsm/test_hsm_basic_run.py`: the tuple from `solve_model` (step 3).
- `tests/integrator/test_jacobi_convergence.py`: the two config keys (step 7).

New tests (not in the patch):

1. `resolve_models_to_run`: `['all']` leaves out C-HSM; `['all', 'hsm']` includes it.
2. `ExchangeSpec.build` for `{HSM, NG}`: HSM writes `HSMSupplyCapacityPackage`, NG writes `NGHubPricePackage`, and the electricity packages are excluded with one warning.
3. Package checks: `HSMSupplyCapacityPackage` rejects wrong index names, duplicate rows, NaN, negative and infinite values; `NGHubPricePackage` rejects an empty frame, duplicate years, NaN, infinite, zero and negative prices.
4. C-NGMM pre-build handler: the built model's `q0` equals the package and `p0` is unchanged; a missing or extra `(region, year)` raises; `0 < Q < 1` raises.
5. C-NGMM built-model handler: after the update, `q0`, `q_min`, `q_base`, `p_base`, `supply_capacity`, `prod_cost_intercept` and `prod_cost_slope` equal those of a model built with the same package; applying the package twice gives the same values.
6. Kept solver: solving the updated built model gives the prices of a rebuild, as `tests/integrator/test_model_updates.py` does for demand.
7. `fill_ratio`: values at known years, linear between them, flat outside them.
8. C-HSM reader: a package at reference prices gives `H = Href87` in every year; a package at 1.1 x reference gives `1.1 x Href87`; a different `dollar_year` raises; a year without a reference price raises.
9. C-HSM writer: at reference prices, the package equals `tests/hsm/expected/hsm_us_gas_capacity.csv` summed over `cost_tier` and `gas_type` at the summary years (54 values for six years); a summary year outside 2023 to 2050 raises at build.
10. Direction: a hub price 10% above reference raises C-HSM's non-associated capacity in every region, and the resulting capacity lowers C-NGMM's West South Central price.
11. Iterators: Gauss-Seidel rejects `{hsm, electricity}`; the three-model run solves in the order C-HSM, electricity, C-NGMM; a `[natural_gas] region_filter` with C-HSM raises in both iterators.
12. `ExchangeTracker`: first values start no streak; a type is stable after `required_iterations` changes below `epsilon`; changed entries give `inf`; untracked types are ignored; nothing tracked returns `True`.
13. Integration (slow): `gs_hsm_ng.toml` ends `CONVERGED`; Jacobi and Gauss-Seidel reach the same Q0 and hub price within `exchange_epsilon`; a cold start and a start primed with a converged hub price reach the same point; the existing electricity and gas tests pass unchanged.

### Step 10. Docs

- `docs/run_modes/integrated.md`: the `[hsm]` section and `['all', 'hsm']`; C-HSM in the Gauss-Seidel order and model sets; the two new settings in the iterator settings table; the stop rule; the two new values under each Gauss-Seidel iteration.
- `docs/models/hsm.md`: a short coupling section with the exchange in section 2 of this guide.
- `src/models/hsm/COUPLING.md`: replace it with a pointer to the docs; it describes the design before this implementation.
- `docs/models/natural_gas.md`: Q0 can come from C-HSM in an integrated run.

### Step 11. Checks before the pull request

```
pixi run lint
pixi run test
pixi run hsm
pixi shell
python main.py run_configs\gs_hsm_ng.toml
python main.py run_configs\jacobi_hsm_ng.toml
python main.py run_configs\gs_hsm_elec_ng.toml
```

`pixi run lint` runs `ruff check .` and pyrefly. `CLAUDE.md` asks for no commits to `main` (the `no-commit-to-branch` hook in `.pre-commit-config.yaml` is commented out); open a pull request from `hsm-ngmm-coupling`.

## 5. Settings

| Setting | Where | Value | Meaning |
|---|---|---|---|
| `HUB_REGION` | `src/models/natural_gas/constants.py` | `'west_south_central'` | C-NGMM region whose price stands in for Henry Hub |
| `PRICE_DOLLAR_YEAR` | same | `2025` | Dollar year of C-NGMM prices |
| `MIN_POSITIVE_Q0` | `src/models/natural_gas/update_reader.py` | `1.0` | Smallest positive Q0 a package may set, Bcf/yr |
| `henry_hub_file` | `[hsm]` | `henry_hub_path_aeo2026_1987usd.csv` in the new run configs (`HSMConfig` default: `hh_reference_path.csv`) | Starting Henry Hub path, 1987 USD/MMBtu |
| `coupling_reference_file` | `[hsm]` | `henry_hub_path_aeo2026_1987usd.csv` | Reference path the hub price is indexed to |
| `coupling_reference_dollar_year` | `[hsm]` | `2025` | Dollar year of its `hh_<year>usd_per_mmbtu` column; must equal `PRICE_DOLLAR_YEAR` |
| `exchanges` | `jacobi_config.toml`, `gs_config.toml` | adds `HSMSupplyCapacityPackage`, `NGHubPricePackage` | Packages a run may carry |
| `exchange_stop_packages` | same | `["HSMSupplyCapacityPackage", "NGHubPricePackage"]` | Packages whose values must settle |
| `exchange_epsilon` | same | `0.005` | Their tolerance, largest relative change |
| `models_to_run` | `[common]` | `['natural_gas', 'hsm']` or `['all', 'hsm']` | `'all'` leaves C-HSM out |

## 6. Limits and open decisions

- Dollar year. Nothing in `input/natural_gas` states the dollar year of C-NGMM prices; the inputs come from AEO2026, which reports 2025 USD. Set `PRICE_DOLLAR_YEAR` once `ng_data_pedigree.md` declares it, and give the reference file a matching `hh_<year>usd_per_mmbtu` column.
- Hub stand-in. West South Central stands in for Henry Hub, and C-HSM adds its fixed regional basis to that one price.
- One Q0. Non-associated and associated-dissolved capacity enter one Q0 per region. In the reduced form, associated-dissolved capacity follows Brent, so part of Q0 does not respond to the gas price.
- Region filters. C-HSM reports all nine regions, so a run with C-HSM cannot filter C-NGMM's regions. Supporting a filter means sending the regions C-NGMM holds and checking closure as `region_crosswalk.py` does.
- Anchor growth. `q0_mult` in `ng_supply_anchors.csv` carries AEO's production growth. In a coupled run, C-HSM's capacity replaces `q0_mult`, and `p0_mult` stays.
- Calibration. C-HSM's `(k, g, c)` reproduces C-NGMM's anchors at AEO2026 prices, but standalone C-NGMM clears below AEO2026 Henry Hub at those anchors. The first exchange lowers C-HSM's capacity, and the coupled Q0 settles below the anchors. This is a calibration question for the C-NGMM anchors, separate from the coupling code.
- Canada. C-HSM computes Canadian production and does not send it. Eq. 30 in `docs/models/hsm.md` gives the form for a later round, into `canada_supply`; it needs a choice of receiving divisions.
- Engine. With `onshore_engine_file` set, each C-HSM run takes 2 minutes, every iteration. Couple the reduced form first.
- Standalone C-HSM. A standalone config now needs `summary_years` inside 2023 to 2050.
- Missing packages. `ExchangeSpec.expected` is not yet checked against the packages an iteration produces (a TODO on `main`). A package with missing entries fails the receiver's coverage check, but a package that never arrives goes undetected: the receiver keeps its own data or the values it last received. For a tracked type, `ExchangeTracker` then cannot report convergence.
