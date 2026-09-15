# C-NEMS Models

The Community National Energy Modeling System (C-NEMS), an open source framework for United States energy
system analysis. C-NEMS is a clone and continuation of [Project BlueSky](https://github.com/EIAgov/BlueSky) outside of the U.S. federal government. This repository holds the model code and [C-NEMS-Inputs](https://github.com/Community-NEMS/cnems-inputs) holds the input data pipelines.

## Contents

- [Overview](#overview)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [Provenance](#provenance)
- [License](#license)

## Overview

C-NEMS, like NEMS, is a collection of energy modules that run standalone or together as an integrated
construct. Each module covers one energy sector or subsector, and all of them share a common pydantic configuration
layer and a run sequencer interface.

The project is being built sector by sector. Two models are currently in the repository.

| Model | Location | Scope |
|---|---|---|
| Electricity | `src/models/electricity` | Least cost dispatch, with optional capacity expansion, reserve margins, operating reserves, ramping, and interregional trade |
| Natural gas | `src/models/natural_gas` | C-NGMM, a regional natural gas market model |

`src/integrator` holds the machinery for coupling models to each other. It is in progress, and
parts of it are unmaintained.

**Maturity.** This is a research codebase under active development. The electricity model is the
most exercised and is covered by tests that pin expected objective values. Results are not
calibrated for real world interpretation, and several inputs are still being reconciled. Treat
outputs as development artifacts rather than actual projections.

## Getting started

The environment is managed with [pixi](https://pixi.sh), which builds it from a pinned lock file.

```sh
pixi install
```

Run the electricity model.

```sh
pixi run python main.py
```

That runs three scenarios in sequence, each named in the `__main__` block of `main.py` and
configured by a TOML under `run_configs`.

| Config | Input set | Regions | Trade | Expansion |
|---|---|---|---|---|
| `basic_elec_config.toml` | `input/electricity/cem_inputs` | 7, 8, 9 | off | off |
| `exchange_elec_config.toml` | `input/electricity/cem_inputs` | 7, 8, 9 | off | off |
| `reduced_elec_config.toml` | `input/electricity_light` | CA, NY, TX | on | off |

`exchange_elec_config.toml` currently differs from `basic_elec_config.toml` only in its scenario
name, so the two write separate output directories from the same settings.

Run the natural gas model.

```sh
pixi run ng
```

Other tasks worth knowing.

| Command | Does |
|---|---|
| `pixi run test` | Run the test suite with coverage |
| `pixi run lint` | Check style and types, without modifying files |
| `pixi run format` | Reformat code and auto fix lint issues |


Run configurations live in `run_configs` as TOML files, each with a `[common]` section and a
model specific section. `basic_elec_config.toml` is the smallest useful starting point.

## Repository layout

```
src/models/       one directory per sector model
src/common/       shared configuration and run sequencing
src/integrator/   cross model coupling, in progress
input/            model input CSVs
run_configs/      run configuration TOMLs
analysis_tools/   post run inspection helpers
docs/             documentation sources
tests/            test suite
```

## Documentation

Model documentation lives under `docs/`.

The formal formulation for each model, meaning its sets, parameters, variables and constraints,
currently lives alongside the code and is being migrated into `docs/`.

- [Electricity model](src/models/electricity/README.md)
- [Natural gas model](src/models/natural_gas/README.md)
- [Integrator](src/integrator/README.md)
- [Shared configuration and sequencing](src/common/README.md)
- [Analysis tools](analysis_tools/README.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Provenance

The U.S. Energy Information Administration's National Energy Modeling System has served for
decades as a principal policy neutral reference for United States energy projections. In 2022 EIA
began [Project BlueSky](https://www.eia.gov/totalenergy/data/bluesky/) to design a next generation
successor, and released a prototype.

EIA has since paused its own work on that effort. C-NEMS continues the line independently, as an
open source community project, following design principles established in the [BlueSky prototype](https://github.com/EIAgov/BlueSky).
This repository began as a clone of that prototype.

The prototype's own documentation is kept unmodified in `old_docs/` as a historical reference. It
describes the earlier codebase rather than this one, and should not be read as current.

## License

Apache License 2.0. See [LICENSE](LICENSE).

Developed by the C-NEMS team.
