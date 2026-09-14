# Electricity Model

## Introduction

**The electricity model is formulated as a least-cost optimization problem to meet electricity demand with generation
from multiple technology options**. It includes both electricity dispatch and the option for capacity expansion. Users
can also specify various power sector operations they would like to represent, for example, capabilities for
representing capacity reserves, operating reserves, and ramping constraints, as well as others that will be described in
more detail below.

**The model has both temporal and spatial flexibility**, which can be specified by the user depending on the purpose of
a study. The temporal flexibility includes the ability to specify both the years and the time segments within a year,
with the finest granularity available being 8760 hours annually. The 8760 hours within a year can be aggregated up by
hours within a day as well as days within a season. If a user is running the model for more than one year, the user can
choose how non-modeled intervals between years are aggregated. In terms of spatial flexibility, users can specify which
regions they want to run and if regions have the capability to trade with one another.

**The model uses linear optimization by default, but if running the model with capacity expansion, a user has the option
of representing cost reductions using a nonlinear technology learning function**. This function can be modeled
endogenously, by either turning the problem into a nonlinear program, or by running successive iterations of linear
programs over a fixed nonlinear learning function. Users can also specify which technology options are allowed to expand
or retire.

After completing a run, the module will return datasets in the output directory for the variables, parameters, sets, and
constraints. These datasets will be stored in the output directory at the top level. The viewer within the output
directory includes options for reviewing electricity model variable results. If running the model in standalone mode,
the model will also produce a graphic showing the distribution of electricity prices within the output directory.

## Prepare Data

The data needed for the electricity model is stored within the input directory in the capacity expansion model e.g.,
cem_input subdirectory. The inputs include regionally indexed and technology indexed input assumptions data for items
like transmission and technology costs and operations. In addition, there is supply curve price and quantity data that
provides the fuel cost assumptions for each power technology.

The data is prepared by `model_sets.py` and `param_data.py`, with the raw CSV reads handled by `data_ingestor.py` (a few
helpers remain in `preprocessor.py`).
`ModelSets` creates the sets for the model from the configuration data. Sets are organized into regional sets, temporal
sets, and technology-based sets. Next
`ParamData` reads in all of the input data within the cem_inputs directory and processes it into the format needed for
the PowerModel based on the spatial and temporal settings specified. Both are passed to the PowerModel for further
processing.

Features are toggled and crosswalks (cw) selected through the run configuration TOML files
in [run_configs](/run_configs). Each file has a `[common]` section parsed into `CommonConfig` and an `[elec_config]`
section parsed into
`ElecConfig`.

### Feature Settings

The `[elec_config]` section contains the main settings through which features for the electricity module can be toggled:

| Setting                   | Description                   | Values                                                                                                                |                                                                                           Notes                                                                                           |
|:--------------------------|:------------------------------|:----------------------------------------------------------------------------------------------------------------------|:-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
| regional_exchange         | Interregional trade           | **false** = Off <br> **true** = On                                                                                    |                                                                                                                                                                                           |
| capacity_expansion        | Capacity expansion/retirement | **false** = Off <br> **true** = On                                                                                    |  Note the file build_data.csv also contains settings of which technologies are available to expand. retire_data.csv contains which technologies have the option to economically retire.   |
| reserve_margin_required   | Reserve margin requirement    | **false** = Off <br> **true** = On                                                                                    |                                                    Requires capacity_expansion; the combination is rejected by ElecConfig validation.                                                     |
| ramping_required          | Maximum ramping constraint    | **false** = Off <br> **true** = On                                                                                    |                                                                                                                                                                                           |
| spinning_reserve_required | Operating reserve requirement | **false** = Off <br> **true** = On                                                                                    |                                            Enables all three reserve products in ReserveType (spinning, regulation, flex), not just spinning.                                             |
| expansion_learning_type   | Technology cost learning      | **disabled** = Exogenous learning <br> **linear** = Iterative linear learning <br> **nonlinear** = Nonlinear learning | The method of which technology costs decrease as more capacity is built. Any value other than disabled requires capacity_expansion; the combination is rejected by ElecConfig validation. |

The `[common]` section holds settings that are not specific to the electricity module:

| Setting             | Description         | Values                                                                                                                 |                                                 Notes                                                 |
|:--------------------|:--------------------|:-----------------------------------------------------------------------------------------------------------------------|:-----------------------------------------------------------------------------------------------------:|
| aggregate_years     | Aggregate years     | **false** = Only runs the selected years <br> **true** = Aggregates all unselected years into subsequent selected year |        Aggregates based on the years listed in summary_years.  Requires aggregate_start_year.         |
| temporal_resolution | Temporal resolution | **default**, **d8h12**, **d4h24**, or a custom crosswalk name                                                          | Selects the representative day/hour mapping.  See [the integrator README](/src/integrator/README.md). |
| summary_years       | Years to run        | list of years, e.g. `[2025, 2030]`                                                                                     |                                The years the model solves and reports.                                |

### Technology Settings

The model contains 15 technologies (tech) in its initial layout. Users could change the technology assignments and add
more technology types or remove technology types, but any changes to the code would require updates to the corresponding
input data. The technologies represented include:
<br> 1.)    Coal Steam
<br> 2.)    Oil Steam
<br> 3.)    Natural Gas Single-Cycle Combustion Turbine
<br> 4.)    Natural Gas Combined-Cycle
<br> 5.)    Hydrogen Turbine
<br> 6.)    Nuclear
<br> 7.)    Biomass
<br> 8.)    Geothermal
<br> 9.)    Municipal-Solid-Waste
<br> 10.)    Hydroelectric Generation
<br> 11.)    Pumped Hydroelectric Storage
<br> 12.)    Battery Energy Storage
<br> 13.)    Wind, Offshore
<br> 14.)    Wind, Onshore
<br> 15.)    Solar (step 1 = utility-scale; step 2 = end-use)

The technologies (tech) are also combined into group based on the applicability of different constraints. These groups
are defined in tech_data.csv within the input/electricity directory and includes:

* T_conv: conventional
* T_re: renewable energy
* T_hydro: hydroelectric
* T_stor: storage
* T_vre: variable renewable energy
* T_wind: wind
* T_solar: solar
* T_h2: hydrogen
* T_disp: dispatchable
* T_gen: generating

When capacity_expansion is turned on, a user can select which technologies they want to have expansion and retirement
capabilities. Turning these switches on allows for builds and/or retirements of a given technology and supply curve
step. These files are located in the input/electricity directory and are declared in property_sources.toml.

| Data column | Description                                                                                   | Values                                                       |                                 Notes                                 |
|:------------|:---------------------------------------------------------------------------------------------:|:-------------------------------------------------------------|:---------------------------------------------------------------------:|
| builds      | Contains switches for technologies and supply curve steps where capacity is allowed to build  | **0** = Not Allowed to Build <br> **1** = Allowed to Build   |  Switches contained in build_data.csv (property key buildable_techs)  |
| retires     | Contains switches for technologies and supply curve steps where capacity is allowed to retire | **0** = Not Allowed to Retire <br> **1** = Allowed to Retire | Switches contained in retire_data.csv (property key retireable_techs) |

## Formulation

The sets, parameters, variables, objective and constraints are documented in
[docs/models/electricity.md](../../../docs/models/electricity.md).
