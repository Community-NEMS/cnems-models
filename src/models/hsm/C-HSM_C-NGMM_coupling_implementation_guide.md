# C-HSM / C-NGMM coupling implementation guide



### 1. Summary


- C-HSM sends US gas production capacity by region and summary year. C-NGMM sets its supply anchor Q0 to it.
- C-NGMM sends its solved price in West South Central by summary year. C-HSM turns it into the Henry Hub path it runs on.

This can be implemented with the updated main branch and current hsm branch. 

### 2. What the two models do

C-HSM (hsm branch) estimates how much gas the US can produce in each region, year by year from 2023 to 2050, for a given gas price. C-NGMM (main) finds the gas price and production that balance supply and demand in nine regions for selected years (2025, 2030 and so on). C-NGMM builds its supply curve around one number per region and year: the expected production level, called Q0. To couple them, C-HSM passes its production capacity to C-NGMM and C-NGMM passes a gas price back. They repeat this until the numbers stop changing.

### 3. What already exists

- C-HSM reports capacity (`poll_us_natgas_capacity`) and accepts new prices (`update_prices`), both in `src/models/hsm/hsm_model.py`.
- C-NGMM rebuilds its supply curve from a new Q0 (`update_supply_capacity`) and reports prices (`poll_gas_price`), both in `src/models/natural_gas/ng_model.py`.
- Main has a system for passing data between models, in `src/common/update_package/`. Each kind of data travels in a labeled container. Each model has a writer that fills containers and a reader that unpacks them. The electricity and gas models use this today.
- Main has two loop drivers. Jacobi runs all models at once (`src/integrator/jacobi/`). Gauss-Seidel runs them one after another (`src/integrator/gauss_seidel/`).
- The two models already use the same nine regions. C-HSM reads C-NGMM's region list (`load_ng_supply_side` in `src/models/hsm/data.py`), so no translation between regions is needed.

### 4. Detailed Steps

| Item | Rule |
|---|---|
| Regions | The nine census divisions, with C-NGMM's ids (`ng_region_data.csv`, domestic regions). C-HSM reads them through `load_ng_supply_side` (`src/models/hsm/data.py`), so no crosswalk is needed. |
| Years | C-NGMM solves `[common] summary_years`. C-HSM runs every year from 2023 to 2050 (`input/hsm/setup.csv`) and reports the summary years.|
| Quantities | Bcf/yr on both sides. |
| Prices | C-NGMM: USD/MMBtu in the dollar year `PRICE_DOLLAR_YEAR` (2025). C-HSM's Henry Hub input: 1987 USD/MMBtu. |

1. Bring C-HSM onto main. Merge the hsm branch into a new branch from main. 

2. Bring C-HSM up to main's rules for models. Main now expects each model to give a display name and its list of regions. Each model's runner must also have a reader, a writer and a status. C-HSM's runner (`src/models/hsm/sequencer.py`) predates these rules and calls a logging helper that main removed. Update it the way the gas runner (`src/models/natural_gas/sequencer.py`) does it. The C-HSM tests in `tests/hsm` should pass afterwards.

3. Define two new containers beside the existing ones in `src/common/update_package/update_package.py`:
   - A capacity container, from C-HSM to C-NGMM, with one number per region and year.
   - A price container, from C-NGMM to C-HSM, with one price per year.

   Each container rejects blanks, negative numbers and zero prices. 

4. C-HSM fills the capacity container. For each region and each of C-NGMM's years, it adds up its capacity across cost tiers and gas types. 

5. C-NGMM unpacks the capacity container and sets Q0 to it. There are two cases:
   - When C-NGMM is about to be built (Jacobi rebuilds it every round), the reader changes the starting production numbers in its input data so the new model starts with the received Q0.
   - When C-NGMM is already built (Gauss-Seidel keeps it), the reader calls `update_supply_capacity`. That function already redraws the supply curve around the new Q0 and keeps the price anchor.

   In both cases, a container that misses a region or year is rejected. The check already exists (`check_region_year_keys`). 

6. C-NGMM fills the price container. It takes the West South Central price for each year from `poll_gas_price`. Henry Hub sits in West South Central, so that price stands in for Henry Hub. 

7. C-HSM turns that price into the Henry Hub price path it runs on. C-HSM needs a price for every year from 2023 to 2050, in 1987 dollars. The reader divides C-NGMM's price by the AEO2026 reference price for the same year. Both reference prices come from `input/hsm/henry_hub_path_aeo2026_1987usd.csv`, which has the reference path in 2025 and in 1987 dollars. The reader draws straight lines to fill the years in between, then scales the 1987-dollar path by that ratio. When C-NGMM's price equals the reference, C-HSM runs on the reference path. C-HSM starts fresh from its inputs each round; a run takes about 2 seconds.

8. Add C-HSM to the loop drivers, fix the stopping rule, add run configs for the two coupled cases, and add tests
