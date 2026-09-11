# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

This repo is a large-scale energy model comprising sub-models under `src/models`, each intended to run
stand-alone or in an integrated construct. Only the electricity model is currently in the repo and
tested; the integration elements in `src/integrator` are in-work and partly unmaintained.

### Configuration

All current code uses the pydantic config system: `src/common/common_config.py` (`CommonConfig`) plus a
model-specific configuration such as `src/models/electricity/elec_config.py` (`ElecConfig`), loaded from
a TOML with `[common]` and `[elec_config]` sections via `CommonConfig.from_toml(path)` (or
`parse_config_file(path)`), which returns `(common_config, remainder_dict)`. Enums (`RunMode`,
`ModelType`, `ExpansionLearningType`, `LoadScaleMode`, `ReserveType`) are used to control
modes/configuration options. See `run_configs/*.toml` and `tests/electric/basic_elec_config.toml` for
working examples.

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
   still live in `param_utilities.py` (`add_season_index`, `avg_by_group`, `time_map`).
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
- `postprocessor.py` (variable extraction / CSV export), `validators.py` (pyomo domain checks),
  `param_utilities.py` (param reshaping helpers), `utilities.py` (`annual_count` only).
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

- `old_docs/` — a deliberately preserved reference archive (the BlueSky prototype PDF and its
  images) left over from the deleted Sphinx tree. Not maintained and not slated for update;
  don't cite it as current, and don't propose deleting it.
- `src/integrator/{unified,gaussseidel}.py` — do not build on these and do not fix breakage in them.
  They import the deleted `src.common.config_setup` and no longer import at all.
  (`src/integrator/utilities.py` is *not* legacy — `select_solver` and `create_temporal_mapping`
  are live dependencies of the electricity path.)
- **Dash GUI stack**: `app.py`, `run_dash_app.bat`, `src/common/config_gui.py` — unmaintained;
  don't build on them.

## Creating and editing rules

- For any functions/methods that are updated or created, document them with NumPy-style docstrings.  Be concise,
  expect that the reader is an experienced user of the code.
- Prefer logging to `logging.info` or `logging.debug` rather than `print` for significant events/debug info.
- When writing tests, use pytest structure including fixtures and parametrization.  See
  `tests/electric/test_basic_run.py` for examples.
- Make minimal changes -- do not refactor unrelated code without asking first.
- When editing or adding code, use type hints.  The project is poorly type-hinted and this will help.
- New modules get the standard header docstring (`Created as part of the C-NEMS Project.` plus
  author/contact/date) — copy the form from an existing file such as `param_source_loader.py`.
