# C-NEMS Models

## Background

C-NEMS is a collection of energy models that can be run stand-alone or together as an
integrated construct. Each model covers one sector of the energy system and shares a common
pydantic configuration layer and run sequencer interface.

## Getting Started

The environment is managed with [pixi](https://pixi.sh); `pixi install` builds it from the
pinned lock file, after which `pixi shell` followed by `python -m cnems` runs the scenarios
declared in `run_configs/`.

## Project Structure

Model code is the `cnems` package under `src/` (a src layout, installed editable by
pixi). It holds the sectoral models (`models`), shared configuration and sequencing machinery
(`common`), and cross-model coupling (`integrator`); `cnems.main` is the command-line entry
point behind `python -m cnems` and the `cnems` command. Input CSVs sit under `input/`, run
configuration TOMLs under `run_configs/`, and `cnems.tools` holds what builds on the models:
`tools.analysis` (post-run inspection helpers) and `tools.interfaces` (the Dash output viewer and
the unmaintained Model Runner).

## Run Modes

- [Standalone Runs](run_modes/standalone.md): one model is built, solved, and reported through
  its sequencer. The RPS feasibility run is described there too.
- [Integrated Runs](run_modes/integrated.md): models are solved together with Jacobi iteration
  and exchange results through update packages.

## Models

- [Electricity](models/electricity.md) — capacity expansion and dispatch, with
  [Regional Portfolio Standards](models/electricity_rps.md).
- [Natural Gas](models/natural_gas.md) — C-NGMM, a multi-regional gas market model.
- [Magic](models/magic.md) — a small model used for development testing.
- [Magic HSM](models/magic_hsm.md) — a test model for the coupling with C-NGMM.
