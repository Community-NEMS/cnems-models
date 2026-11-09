"""
Created as part of the C-NEMS Project.

Written by:  baranov4
Contact:  baranov.4@osu.edu
Created on:  11/5/26

Configuration of the hourly load model (the ``[load_config]`` TOML section) and the lookup from the
project's scenario names to the NEMS AEO output folders.
"""

import csv
from logging import getLogger
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from definitions import PROJECT_ROOT
from src.common.common_config import ModelConfig

logger = getLogger(__name__)

SCENARIO_MAP_COLUMNS = ('scenario_name', 'nems_scenario')


def read_scenario_map(path: Path) -> dict[str, str]:
    """Read the project scenario -> NEMS AEO output folder map.

    Parameters
    ----------
    path : Path
        CSV with the columns ``scenario_name`` (the project's scenario name, as in ``[common]``)
        and ``nems_scenario`` (the folder name under the NEMS outputs directory, e.g. ``cb2026``).

    Returns
    -------
    dict of str to str
        ``{scenario_name: nems_scenario}``.

    Raises
    ------
    ValueError
        If a column is missing, or a project scenario name is listed twice.
    """
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        missing = [c for c in SCENARIO_MAP_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f'{path} needs the columns {list(SCENARIO_MAP_COLUMNS)}; missing {missing}'
            )
        mapping: dict[str, str] = {}
        for row in reader:
            name, nems = row['scenario_name'].strip(), row['nems_scenario'].strip()
            if name in mapping:
                raise ValueError(f'{path} lists the scenario {name!r} more than once')
            mapping[name] = nems
    return mapping


def nems_scenario_for(scenario_name: str, path: Path) -> str:
    """Look up the NEMS AEO output folder that belongs to a project scenario.

    Parameters
    ----------
    scenario_name : str
        The project scenario name (``CommonConfig.scenario_name``).
    path : Path
        The scenario map file, see :func:`read_scenario_map`.

    Returns
    -------
    str
        The NEMS scenario folder name, e.g. ``cb2026``.

    Raises
    ------
    ValueError
        If the scenario is not in the map; the message lists the scenarios that are.
    """
    mapping = read_scenario_map(path)
    if scenario_name not in mapping:
        raise ValueError(
            f'The load model has no NEMS scenario for the project scenario {scenario_name!r}.  '
            f'Scenarios in {path}: {sorted(mapping)}.  Add a line for it to that file, or give '
            'census_division_loads / emm_region_loads in [load_config] instead of nems_outputs_dir.'
        )
    return mapping[scenario_name]


class LoadConfig(ModelConfig):
    """Settings from the ``[load_config]`` TOML section, controlling the hourly load model.

    The projection years are the run's ``summary_years`` and the results go to the run's output
    folder, so neither is set here.  Relative paths are taken from the project root.  Exactly one
    source of annual loads is needed:

    - ``nems_outputs_dir``: NEMS AEO outputs by scenario,
      ``<dir>/<nems scenario>/<run>/restart.rlx``;
      the folder is found from the run's ``scenario_name`` with ``scenario_map``;
    - ``census_division_loads``: a census-division load table (``year,sector,euse,census,mwh``);
    - ``emm_region_loads``: an EMM-region load table (``year,region,sector,euse,mwh``).

    ``nems_outputs_dir`` may also be given next to a user file (``census_division_loads`` or
    ``emm_region_loads``); it then supplies only the on-site generation file.
    """

    tables_dir: Path = Path('input/load/tables')
    nems_outputs_dir: Path | None = None
    scenario_map: Path = Path('input/load/scenario_map.csv')
    census_division_loads: Path | None = None
    emm_region_loads: Path | None = None
    restart_values: Literal['mwh', 'mwh_nems'] = 'mwh'
    demand_overrides: dict[str, Path] = Field(default_factory=dict)
    method: Literal['basic', 'delta', 'both'] = 'delta'
    base_year: int | None = None
    base_freeze_year: int | None = None
    ulossadj_file: Path | None = None
    loss_adj: float | None = None
    renormalize: bool = True
    validate_inputs: bool = True
    hourly8760: bool = True
    save_profiles_864: bool = False
    calendar_year: int | None = None
    calendar_csv: Path | None = None
    load_dir: Path | None = Path('input/load')
    holidays: str | None = None
    peak_days_csv: Path | None = None

    @model_validator(mode='after')
    def check_paths(self):
        """Resolve the file settings against PROJECT_ROOT and check that they exist."""
        for name in ('tables_dir', 'nems_outputs_dir', 'load_dir'):
            path = getattr(self, name)
            if path is not None:
                setattr(self, name, PROJECT_ROOT / path)
                if not getattr(self, name).is_dir():
                    raise ValueError(f'{name} {getattr(self, name)} is not a directory')
        file_names = (
            'scenario_map',
            'census_division_loads',
            'emm_region_loads',
            'ulossadj_file',
            'calendar_csv',
            'peak_days_csv',
        )
        for name in file_names:
            path = getattr(self, name)
            if path is not None:
                setattr(self, name, PROJECT_ROOT / path)
                if not getattr(self, name).is_file():
                    raise ValueError(f'{name} {getattr(self, name)} is not a file')
        self.demand_overrides = {k: PROJECT_ROOT / v for k, v in self.demand_overrides.items()}
        for sector, path in self.demand_overrides.items():
            if not path.is_file():
                raise ValueError(f'demand_overrides for {sector!r}: {path} is not a file')
        if self.holidays is not None and self.holidays.lower() != 'federal':
            self.holidays = str(PROJECT_ROOT / self.holidays)
        return self

    @model_validator(mode='after')
    def check_switch_logic(self):
        """Reject combinations of settings that ``run`` would refuse anyway."""
        user = [
            n for n in ('census_division_loads', 'emm_region_loads') if getattr(self, n) is not None
        ]
        nems = [n for n in ('nems_outputs_dir',) if getattr(self, n) is not None]
        if len(user) > 1 or not (user or nems):
            raise ValueError(
                'give exactly one source of annual loads: nems_outputs_dir, census_division_loads '
                'or emm_region_loads (nems_outputs_dir may accompany a user file); '
                f'got {user + nems}'
            )
        if self.demand_overrides and self.emm_region_loads is not None:
            raise ValueError(
                'demand_overrides replace census-division sectors; do not use emm_region_loads'
            )
        if self.method != 'basic' and (self.base_year is None) == (self.base_freeze_year is None):
            raise ValueError('the delta method needs exactly one of base_year and base_freeze_year')
        if self.ulossadj_file is not None and self.loss_adj is not None:
            raise ValueError('give ulossadj_file or loss_adj, not both')
        return self
