# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This repo is a fork of EIA's [Project BlueSky](https://www.eia.gov/totalenergy/data/bluesky/) prototype
(an open-source energy systems modeling framework), being reworked by the current maintainer
under the internal name "C-NEMS Project" (see file headers). The upstream prototype had three sectoral
modules (electricity, hydrogen, residential) combined via an integrator; **this fork has removed the
hydrogen and residential modules and is focused solely on the electricity module**, run in standalone
mode. Don't assume the upstream README (which still describes all three modules and multiple run modes)
reflects the current code — large parts of it are aspirational/stale here.

### Two config systems (mid-refactor) — important

The codebase currently has **two parallel configuration systems**, and code you touch may be on either
side of this line:

- **Legacy**: `src/common/config_setup.py` (`Config_settings`), driven by `main.py` / `app.py` (the Dash
  GUI) reading `src/common/run_config.toml`. This is the original BlueSky config style: one big flat
  TOML with switches for every module (electricity/hydrogen/residential), regions as int ranges, etc.
- **New (pydantic-based)**: `src/common/common_config.py` (`CommonConfig`) +
  `src/models/electricity/elec_config.py` (`ElecConfig`), loaded via `CommonConfig.from_toml(path)` from a
  TOML with `[common]` and `[elec_config]` sections (see `tests/electric/basic_elec_config.toml` and
  `run_configs/basic_elec_config.toml`). Enums (`RunMode`, `ModelType`, `ExpansionLearningType`,
  `LoadScaleMode`, `ReserveType`) replace the old int switches.

Ignore any breaking changes to the **Legacy** files listed above.  All new code should use the **New**
config system.

**`src/models/electricity/runner.py::run_elec_model` has already been migrated** to take
`(common_config: CommonConfig, elec_config: ElecConfig, solve=True)`. `main.py` / `app.py` /
`src/integrator/runner.py::run_elec_solo` have **not** been migrated and still call the old
`Config_settings`-based signature — running the model through `main.py` or the Dash app will currently
break for the electricity path. **All tests and active development use the new pydantic config system.**
When adding features, follow the new system unless you are specifically working on the
migration/main.py itself. `ACTIONS.md` tracks the broader refactor roadmap; `NOTES.md` tracks known data/
modeling concerns — check both for context before assuming something is a bug vs. a known issue.

## Commands

```bash
# Run all electricity tests
pytest tests/electric -q

# Run a single test file / test
pytest tests/electric/test_basic_run.py -q
pytest tests/electric/test_basic_run.py::test_linear_learning -q

# Lint / format (ruff config lives in pyproject.toml: line-length 100, single quotes)
ruff check .
ruff format .

# Run the model standalone (legacy path via Config_settings + run_config.toml; see caveat above)
python main.py --mode standalone

# Launch the Dash GUI (legacy path)
python run_dash_app.bat   # Windows; on mac/linux invoke the underlying python command directly
```

Test logs are written to `tests/logs/testlog.log` (configured in `pyproject.toml`).

## Architecture

### Flow for the electricity model (current, tested path)

1. `CommonConfig.from_toml(path)` parses the `[common]` section of a TOML config into a `CommonConfig`
   pydantic model, returning `(common_config, remainder_dict)`. The remaining TOML sections (e.g.
   `[elec_config]`) are parsed separately, e.g. `ElecConfig(**remainder.pop('elec_config'))`.
2. `src/models/electricity/model_sets.py::ModelSets(common_config, elec_config)` builds the model's sets
   (regions, techs, temporal sets, sparse index sets) from `common_config`/`elec_config` and raw property
   files loaded via `data_ingestor.py`.
3. `src/models/electricity/param_data.py::ParamData(common_config, elec_config, model_sets)` reads and
   shapes the parameter data (supply curves, capacity factors, costs, transmission limits, etc.),
   consolidating what used to be scattered across `preprocessor.py`. Simple params are kept as plain
   dicts; params still needing manipulation are kept as DataFrames.
4. `src/models/electricity/runner.py::build_elec_model(...)` instantiates
   `electricity_model.py::PowerModel` (a `pyomo.ConcreteModel` subclass defined in `src/common/model.py`)
   and populates it with sets/params/vars/constraints.
5. `solve_elec_model(...)` selects a solver (`src/integrator/utilities.py::select_solver`, defaults
   towards HiGHS via `highspy`) and solves. If `elec_config.expansion_learning_type == LINEAR`, it
   iterates externally (build → solve → update learning-curve costs → resolve) until capacity converges,
   rather than embedding the nonlinear learning curve directly in the LP.

### `docs` folder and README.md files
- The `docs` folder is outdated and should be completely ignored for not until we update it later.
- All README.md files in the repo are not currently up to date and should be ignored in favor of code 
  comments and inspections
### `src/common/model.py::Model`

A `pyo.ConcreteModel` base class shared by sectoral models. 

Ignore this file and do not use any of the methods defined here.  We are trying to remove this middle class and 
we expect the electricity model to be a direct sublcass of `pyomo.ConcreteModel`.

### Electricity model structure

- `elec_config.py` — pydantic `ElecConfig` (regions filter, switches for trade/expansion/reserve
  margin/ramping/reserves, learning type, load-scale mode) plus supporting enums.
- `model_sets.py` — `ModelSets`: regional, temporal, and technology-based sets, including many sparse
  index sets (e.g. `generation_total_index`, `capacity_builds_index`, `trade_interregional_index`) built
  by crossing raw data against switches. See `src/models/electricity/README.md` for the full
  sets/params/variables/constraints reference (includes LaTeX formulation of the objective and
  constraints) — read that file before modifying model formulation, not just the code.
- `param_data.py` — `ParamData`: loads/shapes parameters (`data_ingestor.py` does the raw CSV reads).
- `electricity_model.py` — `PowerModel`: the actual pyomo formulation (objective + constraints), ~1900
  lines; features are conditionally added based on `ElecConfig` switches (e.g. trade constraints only
  exist if `regional_exchange=True`).
- `runner.py` — build/solve orchestration described above, plus the linear-learning iteration loop
  (`init_old_cap`, `set_new_cap`, `update_cost`, `cost_learning_func`).
- `postprocessor.py`, `validators.py`, `utilities.py` — output shaping and result sanity checks.
- Input data lives under `input/electricity/cem_inputs/` (regionally/technology-indexed CSVs for costs,
  transmission, supply curves, etc.) — see helper scripts in that directory
  (`fix_integer_cols.py`, `missing_trans_cost_detector.py`, `missing_vre_up_detector.py`) for
  ad hoc data QA.
- Do not alter the formulation of the math model (constraints and objective functions) in electricity model code
  without explicity asking to do so.

### Temporal/spatial resolution

Representative days/hours (not full 8760h) drive the model's time dimension, configurable via
`temporal_resolution` in `CommonConfig` (`"default"`, `"d8h12"`, `"d4h24"`, or custom crosswalks — see
`src/integrator/README.md` for the temporal mapping crosswalk format). Regions are filtered via
`ElecConfig.region_filter`. Year aggregation (`aggregate_years`/`aggregate_start_year` in `CommonConfig`)
lets a run represent multiple actual years with one solved representative year, weighted accordingly.

### Tests

`tests/electric/conftest.py` provides two key fixtures built on `tests/electric/basic_elec_config.toml`:
`config_set` (a `(CommonConfig, ElecConfig)` pair) and `unsolved_model` (adds an unsolved `PowerModel` via
`run_elec_model(..., solve=False)`). `tests/model_diagnostics.py` has helpers
(`gather_set_data`, `gather_var_data`, `gather_param_data`, `gather_constraint_data`,
`breakdown_obj_elements`, `capacity_inspector`, `load_inspector`) for inspecting a solved/unsolved
`PowerModel` instance — reach for these instead of writing new inspection code when debugging a test.
`test_basic_run.py` locks in expected objective value / variable count / constraint count for several
feature combinations (baseline, exchange, expansion, ramping, reserve margin) against values captured
from a legacy run; if you intentionally change model formulation, these expected values need updating and
the change should be justified (see the module docstring's caveat that captured values are *assumed*
correct, not verified against an independent source).

### Creating and editing rules

- For any functions/methods that are updated or created, document them with NumPy-style docstrings.  Be concise, 
  expect that the reader is an experienced user of the code.
- Pefer logging to `logging.info` or `logging.debug` rather than `print` for significant events/debug info.
- When writing tests, use pytest structure including fixtures and parametrization.  See `tests/electric/test_basic_run.py` for examples.
- Make minimal changes -- do not refactor unrelated code without asking first.
- When editing or adding code, use type hints.  The project is poorly type-hinted and this will help.