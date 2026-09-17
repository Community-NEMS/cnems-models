# C-NEMS Models

## Background

C-NEMS is a collection of energy models that can be run stand-alone or together as an
integrated construct. Each model covers one sector of the energy system and shares a common
pydantic configuration layer and run sequencer interface.

## Getting Started

The environment is managed with [pixi](https://pixi.sh); `pixi install` builds it from the
pinned lock file, after which `pixi shell` followed by `python main.py` runs the scenarios
declared in `run_configs/`.

## Project Structure

Model code lives under `src/models`, with shared configuration and sequencing machinery in
`src/common` and cross-model coupling in `src/integrator`. Input CSVs sit under `input/`,
run configuration TOMLs under `run_configs/`, and post-run inspection helpers in
`analysis_tools/`.

## Models

- [Electricity](models/electricity.md) — capacity expansion and dispatch.
- [Natural Gas](models/natural_gas.md) — C-NGMM, a multi-regional gas market model.
- [Magic](models/magic.md) — a small model used for development testing.
