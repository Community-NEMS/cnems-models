# electricity_light dataset

A small, string-indexed development dataset derived from `input/electricity` (the full numeric
dataset). It exists to exercise non-numeric region/technology names and to keep development runs
fast; it is driven by `run_configs/reduced_elec_config.toml`. Like the full dataset it is in MW,
MWh and USD, with each file's units carried in its value column name.
