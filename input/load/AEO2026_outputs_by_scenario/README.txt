Put the NEMS AEO2026 outputs here, one folder per NEMS scenario, in the layout of the NEMS repository
(output/aeo2026):

    AEO2026_outputs_by_scenario/
        cb2026/<run>/restart.rlx
        higheldmd/<run>/restart.rlx
        ...

The scenario folders are not tracked by git (large files, see .gitignore).  The load model finds the folder
of a run from the project's scenario_name with ../scenario_map.csv, e.g. baseline -> cb2026.
Files of about 130 bytes are Git LFS pointers: run 'git lfs pull' in the NEMS repository to get the real data.
