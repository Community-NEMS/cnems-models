# electricity_light dataset

A small, string-indexed development dataset derived from `input/electricity` (the full numeric
dataset). It exists to exercise non-numeric region/technology names and to keep development runs
fast; it is driven by `run_configs/reduced_elec_config.toml`. Like the full dataset it is in MW,
MWh and USD, with each file's units carried in its value column name.

## Provenance

Added in commit `77d7264` (2026-07-21) with no script retained. The mapping below was recovered on
2026-09-02 by exact value matching between the light CSVs and `input/electricity/parameters`, and
re-verified on 2026-10-06 after the MW unit change and file renames: applying it to the light
files reproduces the upstream values exactly for every row that has an upstream counterpart
(`capacity_factor_vre` 52,560 rows, `generation_cost` 648, `available_capacity` 162, `fom_cost`
27, `hydro_capacity_factor` 12, `tran_cost` 54, `tran_cost_intl` 72, `supply_limit_intl` 96,
`tran_limit` 48 of 144, plus `storage_efficiency` and `storage_duration` — zero difference on all
of them).

## Region mapping

| `input/electricity` | `input/electricity_light` |
| ------------------- | ------------------------- |
| 7                   | CA                        |
| 9                   | NY                        |
| 3                   | TX                        |
| 29                  | Mexico (international)    |

## Technology mapping

| `input/electricity` | `input/electricity_light` | upstream technology (see `src/models/electricity/README.md`) |
| ------------------- | ------------------------- | ------------------------------------------------------------ |
| 1                   | NG_Fired_Plant            | Coal Steam                                                   |
| 5                   | H2_Fired_Plant            | Hydrogen Turbine                                             |
| 10_seasonal         | Hydro_Plant_seasonal      | Hydroelectric Generation, seasonal budget                    |
| 10_regular          | Hydro_Plant_regular       | Hydroelectric Generation, hourly capacity factor             |
| 11                  | Battery_Storage           | Pumped Hydroelectric Storage                                 |
| 15_utility          | Solar_utility             | Solar, utility-scale                                         |
| 15_end_use          | Solar_end_use             | Solar, end-use                                               |

Steps, seasons, and hours pass through unchanged. Years are subset to 2030-2035 wherever a year
column exists.

## Caveats

- **The names are aspirational, not faithful to the upstream legend.** `NG_Fired_Plant` carries
  tech 1 (Coal Steam) values, and `Battery_Storage` carries tech 11 (Pumped Hydroelectric Storage)
  values — 0.83 efficiency and 12 h storage duration, where the real battery is tech 12 at 0.85 /
  4 h.
  Treat the names as labels for this dataset, not as claims about the underlying technology.
- **Transmission data is partly invented.** Only 48 of 144 `tran_limit` rows have an upstream
  counterpart, and no `tran_limit_cap_intl` row does — the full dataset links only region 23 to region
  29, while this one links all of CA/NY/TX to Mexico. Those files were authored, not converted.
- **No capacity expansion.** There is no `capital_cost.csv` here, so `capacity_expansion = true` fails
  while building `capacity_balance`; the reduced config runs with expansion off.

## Converting another file from `input/electricity/parameters`

Filter the full CSV to the mapped regions and technologies (and to 2030-2035 where the file has a
year column), then substitute the names. `fom_cost.csv` was converted this way:

```python
import pandas as pd

RMAP = {'7': 'CA', '3': 'TX', '9': 'NY'}
TMAP = {
    '1': 'NG_Fired_Plant',
    '5': 'H2_Fired_Plant',
    '10_seasonal': 'Hydro_Plant_seasonal',
    '10_regular': 'Hydro_Plant_regular',
    '11': 'Battery_Storage',
    '15_utility': 'Solar_utility',
    '15_end_use': 'Solar_end_use',
}

full = pd.read_csv('input/electricity/parameters/fom_cost.csv', dtype={'region': str, 'tech': str})
out = full[full.region.isin(RMAP) & full.tech.isin(TMAP)].copy()
out['region'] = pd.Categorical(out.region.map(RMAP), ['CA', 'TX', 'NY'], ordered=True)
out['tech'] = pd.Categorical(out.tech.map(TMAP), list(TMAP.values()), ordered=True)
out.sort_values(['tech', 'region', 'step']).to_csv(
    'input/electricity_light/parameters/fom_cost.csv', index=False
)
```

Row order is cosmetic — `data_ingestor.py` indexes by the `index_cols` declared in
`param_sources.toml` — but tech/region/step ordering matches the other files here.
