# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This repo is a fork of EIA's [Project BlueSky](https://www.eia.gov/totalenergy/data/bluesky/) prototype
(an open-source energy systems modeling framework), being reworked by the current maintainer
under the internal name "C-NEMS Project" (see file headers). The upstream prototype had three sectoral
modules (electricity, hydrogen, residential) combined via an integrator; **this fork has removed the
hydrogen and residential modules and is focused solely on the electricity module**, run in standalone
mode. Don't assume the upstream root `README.md` (which still describes all three modules and multiple
run modes) reflects the current code — large parts of it are aspirational/stale here.

### Configuration

All current code uses the pydantic config system: `src/common/common_config.py` (`CommonConfig`) plus
`src/models/electricity/elec_config.py` (`ElecConfig`), loaded from a TOML with `[common]` and
`[elec_config]` sections via `CommonConfig.from_toml(path)` (or `parse_config_file(path)`), which returns
`(common_config, remainder_dict)`. Enums (`RunMode`, `ModelType`, `ExpansionLearningType`,
`LoadScaleMode`, `ReserveType`) replace the old int switches. See `run_configs/*.toml` and
`tests/electric/basic_elec_config.toml`. A parallel legacy config system still exists — see
"Ignore / do not touch" below.

## Commands

The project is managed by the `pixi` build manager:

```bash
# Run all tests (with coverage; writes coverage.xml, then prints a coverage report)
pixi run test

# Lint / type check (ruff check . + pyrefly check src)
pixi run lint

# Format (ruff format + ruff check --fix, plus prettier on YAML)
pixi run format

# Run a single test file / test — enter the env, then call pytest directly
pixi shell
pytest tests/electric/test_basic_run.py -q
pytest tests/electric/test_basic_run.py::test_linear_learning -q

# Run the model standalone (new config path; __main__ runs the run_configs/*.toml scenarios)
pixi shell
python main.py
```

- ruff config lives in `pyproject.toml`: line-length 100, single quotes.
- pytest-xdist runs tests in parallel by default (`addopts = "-n auto"`); pass `-n0` or
  `-p no:xdist` to force serial execution when debugging a misbehaving test or when you need
  ordered output.
- Test logs are written to `tests/logs/testlog.log` (configured in `pyproject.toml`).
- pre-commit (`prek`) has `no-commit-to-branch` enabled: don't commit directly to `main` — branch
  and open a PR.

## Architecture

### Flow for the electricity model (current, tested path)

1. `CommonConfig.from_toml(path)` parses the `[common]` section into a `CommonConfig`, returning
   `(common_config, remainder_dict)`. The remaining sections are parsed separately, e.g.
   `ElecConfig(**remainder.pop('elec_config'))`.
2. `src/models/electricity/model_sets.py::ModelSets(common_config, elec_config)` builds the model's sets
   (regions, techs, temporal sets, sparse index sets) from `common_config`/`elec_config` and raw property
   files loaded via `data_ingestor.py`.
3. `src/models/electricity/param_data.py::ParamData(common_config, elec_config, model_sets)` reads and
   shapes the parameter data (supply curves, capacity factors, costs, transmission limits, etc.). Simple
   params are kept as plain dicts; params still needing manipulation are kept as DataFrames. Some helpers
   still live in `preprocessor.py` (`add_season_index`, `avg_by_group`, `time_map`).
4. `sequencer.py::ElectricitySequencer` implements the `IntegratedModelSequencer` ABC
   (`src/common/integrated_model_sequencer.py`: `build_model` / `update_model` / `solve_model` /
   `full_postprocess` / `iteration_postprocess`). `solve_model` returns an `IterationStatus`. Tests build
   models by calling `ElectricitySequencer().build_model(common_config, elec_config)` directly.
5. `sequencer.py::run_elec_model(common_config, elec_config, solve=True)` is a thin wrapper over the
   sequencer: build → (optionally) solve → postprocess, returning the `PowerModel`.

### Electricity model structure

- `elec_config.py` — pydantic `ElecConfig` (`region_filter`, switches for `regional_exchange` /
  `capacity_expansion` / `reserve_margin_required` / `spinning_reserve_required` / `ramping_required`,
  `expansion_learning_type`, `load_scale_mode`) plus supporting enums.
- `model_sets.py` — `ModelSets`: regional, temporal, and technology-based sets, including many sparse
  index sets (e.g. `generation_total_index`, `capacity_builds_index`, `trade_interregional_index`) built
  by crossing raw data against switches. `src/models/electricity/README.md` is the full
  sets/params/variables/constraints reference (includes the LaTeX formulation of the objective and
  constraints) — read it before modifying model formulation, not just the code.
- `param_data.py` — `ParamData`: loads/shapes parameters. `data_ingestor.py` does the raw CSV reads.
- Input CSVs are declared, not hard-coded: `param_sources.toml` / `property_sources.toml` are parsed by
  `param_source_loader.py` / `property_source_loader.py` into the `PARAM_SOURCES` / `PROPERTY_SOURCES`
  dicts in `data_ingestor.py`. To add an input file, add a TOML entry (`key`, `filename`, `index_cols`,
  `value_col`, `required`) rather than editing loader code. `index_cols` ordering matters — it drives
  MultiIndex construction and downstream column-order expectations.
- `electricity_model.py` — `PowerModel`: the pyomo formulation (objective + constraints); features are
  conditionally added based on `ElecConfig` switches (e.g. trade constraints only exist if
  `regional_exchange=True`).
- `sequencer.py` — build/solve orchestration described above, plus the linear-learning iteration loop.
- `postprocessor.py`, `validators.py`, `utilities.py` — output shaping and result sanity checks.
- `analysis_tools/` (top-level package) — `model_diagnostics.py`, `transmission_network.py`, `viewer.py`.
- Input data lives under `input/electricity/` and `input/electricity/cem_inputs/`
  (regionally/technology-indexed CSVs for costs, transmission, supply curves, etc.).
- Do not alter the formulation of the math model (constraints and objective functions) in electricity
  model code without explicitly asking to do so.

### Temporal/spatial resolution

Representative days/hours (not full 8760h) drive the model's time dimension, configurable via
`temporal_resolution` in `CommonConfig` (`"default"`, `"d8h12"`, `"d4h24"`, or custom crosswalks — see
`src/integrator/README.md` for the temporal mapping crosswalk format). Regions are filtered via
`ElecConfig.region_filter`. Year aggregation (`aggregate_years`/`aggregate_start_year` in `CommonConfig`)
lets a run represent multiple actual years with one solved representative year, weighted accordingly.

### Tests

`tests/electric/conftest.py` provides four fixtures: `config_set` (a `(CommonConfig, ElecConfig)` pair
built from `tests/electric/basic_elec_config.toml`), `learning_config_set` (the single-region
linear-learning micro dataset under `tests/electric/test_data_linear_learning_test/`), and
`unsolved_model` / `solved_model`, which add a `PowerModel` built via `ElectricitySequencer`.

`analysis_tools/model_diagnostics.py` has helpers (`gather_set_data`, `gather_var_data`,
`gather_param_data`, `gather_constraint_data`, `breakdown_obj_elements`, `capacity_inspector`,
`load_inspector`) for inspecting a solved/unsolved `PowerModel` instance — reach for these instead of
writing new inspection code when debugging a test.

`test_basic_run.py` locks in expected objective value / variable count / constraint count for several
feature combinations (baseline, exchange, expansion, ramping, reserve margin) against values captured
from a legacy run; if you intentionally change model formulation, these expected values need updating and
the change should be justified (see the module docstring's caveat that captured values are *assumed*
correct, not verified against an independent source).

## Ignore / do not touch

- `old_docs/` — outdated; ignore completely until it is updated.
- **Legacy config + Dash GUI stack**: `src/common/config_setup.py` (`Config_settings`) and
  `src/integrator/{unified,gaussseidel,runner}.py`. These still use the old flat-TOML config style.
  Don't build on them and don't fix breakage in them. (`src/integrator/utilities.py` is *not* legacy —
  `select_solver` and `create_temporal_mapping` are live dependencies of the electricity path.)
- `src/common/model.py::Model` — dead as a base class. `PowerModel` is already a direct
  `pyo.ConcreteModel` subclass (`class PowerModel(pyo.ConcreteModel, IntegratedModel)`). The one
  remaining import is `src/models/electricity/utilities.py`; do not add new ones or use its methods.

## Creating and editing rules

- For any functions/methods that are updated or created, document them with NumPy-style docstrings.  Be concise,
  expect that the reader is an experienced user of the code.
- Prefer logging to `logging.info` or `logging.debug` rather than `print` for significant events/debug info.
- When writing tests, use pytest structure including fixtures and parametrization.  See
  `tests/electric/test_basic_run.py` for examples.
- Make minimal changes -- do not refactor unrelated code without asking first.
- When editing or adding code, use type hints.  The project is poorly type-hinted and this will help.
