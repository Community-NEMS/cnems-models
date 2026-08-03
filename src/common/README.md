# Integration

### Table of Contents
- [Introduction](#introduction)
- [Model Overview](#model-overview)
- [Prepare Data](#prepare-data)
- [Code Documentation](#code-documentation)

## Introduction


## Model Overview


## Prepare Data

### Run Configuration

The majority of the options for integrating and solving modules rely upon a configuration file located in the common folder: run_config.toml. This file contains settings shared across modules as well as settings specific to each module.

#### Module Settings

These switches specify whether a module will be included in a run.

|Switch | Description   | Values     |
|:----- | :-----------: | :--------- |
| electricity | electricity module | **false** = Off <br> **true** = On|
| hydrogen | hydrogen module |  **false** = Off <br> **true** = On|
| residential | residential module |  **false** = Off <br> **true** = On|

#### Temporal Settings

The model runs a number of representative days (self-looping) with a number of periods (hour-several hours) per representative day. Any number of aggregated representative days can be used, but there must be at least 1 per season. The representative days and hours use a weighted average of inputs where necessary.

| Temporality | File / Setting | Description |
| :---------- | :--- | :---------- |
| Hours | cw_hr.csv | **Contains the representative hour mapping crosswalk** <br> <li> Index_hour represent each hour in a 24 hour period <br> <li> Map_hour is the representative hour number each Index_hour is being mapped to. Hours must be in chronological order. <br> <li> Map_hour 1 can wrap around from 24 to 1. |
| Days | cw_s_day.csv | **Contains the representative hour mapping crosswalk** <br> <li> Index_day is the day number of the year: 1-365 <br> <li> Map_s is the season mapping of these days. *Note: Do not change this unless you need to make larger changes to the code because input files are based on the seasons* <br> <li> Map_day is the representative day that this Index_day is mapped to. Representative days are created using weighted averages of Index_day. *Note: You cannot have a representative day span multiple seasons.* |
| Years | summary_years | **Contains the representative year mapping** <br> <li> A list of the years being run, set in the `[common]` section of the run config, e.g., `summary_years = [2025, 2030]` <br> <li> If **aggregate_years** in the toml file is also turned on, the representative year will be an average of all of those before it (until the previous selected year). This also properly weights the years. Otherwise, it only runs the years that are listed.

#### Spatial Settings
This model currently has 25 regions. Turning on and off certain regions allows only specific regions to be run.

| Regionality | File / Setting | Description |
| :---------- | :--- | :---------- |
|Regions | region_filter | **Contains the representative region mapping** <br> <li> A list of the regions being run, set in the `[elec_config]` section of the run config, e.g., `region_filter = ["7"]`.  Omit it (or leave it null) to run all regions.

*Note: Hydrogen data currently has hydrogen production in Region 7 (only) for demonstration.  Simple modifications to the data could expand this.  Most of the instantiations of the Hydrogen model use the 'single_region' data folder in the H2 module*

#### Model-Specific Settings

For more details about the module-specific settings provided in the configuration file, navigate to the model READMEs (e.g. [Integrator](/src/integrator/README.md), [Electricity](/src/models/electricity/README.md), [Residential](/src/models/residential/README.md), [Hydrogen](/src/models/hydrogen/README.md)).

## Code Documentation

Navigate to the docs README for details on code documentation, as well as instructions for locally compliling Sphinx to generate an html version of the documenation.

[Code Documentation](/docs/README.md)
