"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/24/26

A hand-computable "two of everything" electricity model built without any CSV files.

``MiniModelData`` holds the raw payloads the electricity loaders would otherwise read from disk
(property sets, tech attributes, the temporal crosswalk, the parameter dicts, and the load).
``patch_loaders`` swaps those loaders out so the *real* ``ModelSets`` / ``ParamData`` /
``validate_all`` / ``ElectricitySequencer`` pipeline runs on it.

The base data is deliberately bare:  two domestic regions (R1, R2), two techs (Gas, Solar), two
years, two representative days (one per season) of two hours each, with unit time weights.  Only
Gas has capacity and costs; Solar is declared but carries no data.  Tests augment the base with
the editing helpers (``add_tech``, ``add_rows``, ``set_buildable``, ``set_load``, ...) before
building.
"""

import copy
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.common.common_config import CommonConfig
from src.common.season import Season
from src.models.electricity.data_ingestor import PARAM_SOURCES, PROPERTY_SOURCES
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.param_data import ParamData

TECH_FLAGS = frozenset(PROPERTY_SOURCES['tech'].property_cols) - {'tech'}
"""the valid membership flags for ``MiniModelData.add_tech``"""

_DEFAULT_SWITCHES: dict[str, Any] = {
    'regional_exchange': False,
    'capacity_expansion': False,
    'expansion_learning_type': 'disabled',
    'reserve_margin_required': False,
    'spinning_reserve_required': False,
    'ramping_required': False,
    'load_scale_mode': 'annual',
}


@dataclass
class MiniModelData:
    """Raw loader payloads for a mini electricity model, plus helpers to edit them consistently.

    Attributes mirror the loader outputs:  ``properties`` <- ``load_property_data``,
    ``attributes`` <- ``load_attribute_data``, ``cw_temporal`` <- ``create_temporal_mapping``,
    ``params`` <- ``load_param_data`` (one dict per ``PARAM_SOURCES`` key, keyed by its
    ``index_cols``), and ``load`` <- ``ParamData.build_load_dataframe``.
    """

    regions: list[str]
    years: list[int]
    seasons: list[Season]
    hours: list[int]
    properties: dict[str, dict[str, list]]
    attributes: dict[str, dict[str, dict]]
    cw_temporal: pd.DataFrame
    params: dict[str, dict[tuple, float]] = field(
        default_factory=lambda: {k: {} for k in PARAM_SOURCES}
    )
    load: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=['region', 'year', 'hour', 'Load'])
    )

    @classmethod
    def default(cls, day_weight: float = 1.0, hour_weight: float = 1.0) -> MiniModelData:
        """Build the bare two-region / two-tech / two-year / 2x2-hour base.

        Gas:  100 MW existing in both regions and years, $30/MWh, $10/MW-yr FOM.  Solar:
        declared (vre, solar utility) only.  Load:  80 MW in every region, year and hour.

        Parameters
        ----------
        day_weight : float
            ``WeightDay`` for both representative days.
        hour_weight : float
            ``WeightHour`` for every representative hour.

        Returns
        -------
        MiniModelData
        """
        seasons = [Season('1', 1), Season('2', 2)]
        hours_per_day = 2
        # a "4-hour year":  each actual hour is its own representative hour
        cw = pd.DataFrame(
            [
                {
                    'Map_s': seasons[day - 1],
                    'Map_day': day,
                    'WeightDay': day_weight,
                    'Map_hour': (day - 1) * hours_per_day + h,
                    'WeightHour': hour_weight,
                }
                for day in (1, 2)
                for h in range(1, hours_per_day + 1)
            ]
        )
        cw['hour'] = cw['Map_hour']

        data = cls(
            regions=['R1', 'R2'],
            years=[2030, 2031],
            seasons=seasons,
            hours=list(cw['hour']),
            properties={
                'tech': {col: [] for col in PROPERTY_SOURCES['tech'].property_cols},
                'tech_build': {'is_buildable': []},
                'tech_retire': {'is_retirable': []},
                'region': {
                    'region': ['R1', 'R2'],
                    'is_domestic': ['R1', 'R2'],
                    'is_international': [],
                },
            },
            attributes={
                'tech': {col: {} for col in PROPERTY_SOURCES['tech'].attribute_cols},
                'region': {'label': {'R1': 'R1', 'R2': 'R2'}},
            },
            cw_temporal=cw,
        )
        data.add_tech('Gas', flags=['is_conventional', 'is_dispatchable', 'is_generator'])
        data.add_tech(
            'Solar',
            flags=['is_renewable', 'is_vre', 'is_solar', 'is_solar_utility', 'is_generator'],
        )
        data.add_capacity('Gas', 100.0)
        data.add_operating_costs('Gas', generation_cost=30.0, fom_cost=10.0)
        # required table; inert without hydro techs
        data.add_rows(
            'capacity_factor_seasonal',
            {(r, str(s)): 0.5 for r, s in data.every('region', 'season')},
        )
        for region in data.regions:
            data.set_load(region, dict.fromkeys(data.hours, 80.0))
        return data

    # ---------------------------------------------------------------- editing helpers

    def every(self, *dims: str) -> Iterator[tuple]:
        """Cross the named dimensions (``'region'``, ``'year'``, ``'season'``, ``'hour'``).

        Parameters
        ----------
        *dims : str
            dimension names, in the order wanted in each yielded tuple.

        Returns
        -------
        Iterator[tuple]
        """
        lookup = {
            'region': self.regions,
            'year': self.years,
            'season': self.seasons,
            'hour': self.hours,
        }
        return product(*(lookup[d] for d in dims))

    def add_tech(
        self,
        name: str,
        flags: Iterable[str],
        steps: Iterable[int] = (1,),
        buildable: Iterable[int] = (),
        retirable: Iterable[int] = (),
    ) -> MiniModelData:
        """Declare a tech, its set memberships, steps, and build/retire eligibility.

        Parameters
        ----------
        name : str
            tech name.
        flags : Iterable[str]
            ``tech.csv`` membership columns that are True for it (e.g. ``'is_storage'``).
        steps : Iterable[int]
            its valid supply curve steps.
        buildable, retirable : Iterable[int]
            the steps that are buildable / retirable.

        Returns
        -------
        MiniModelData
            self, for chaining.

        Raises
        ------
        ValueError
            on an unknown flag or a duplicate tech.
        """
        flags = set(flags)
        if unknown := flags - TECH_FLAGS:
            raise ValueError(f'unknown tech flags {sorted(unknown)}; valid: {sorted(TECH_FLAGS)}')
        tech_props = self.properties['tech']
        if name in tech_props['tech']:
            raise ValueError(f'tech {name!r} already declared')
        steps = sorted(steps)
        tech_props['tech'].append(name)
        for flag in flags:
            tech_props[flag].append(name)
        attrs = self.attributes['tech']
        attrs['steps'][name] = '/'.join(map(str, steps))
        attrs['label'][name] = name
        attrs['abbreviation'][name] = name[:6].upper()
        attrs['color'][name] = '#000000'
        for step in buildable:
            self.set_buildable(name, step)
        for step in retirable:
            self.set_retirable(name, step)
        return self

    def set_buildable(self, tech: str, step: int, value: bool = True) -> MiniModelData:
        """Add (or with ``value=False`` remove) ``(tech, step)`` in the buildable set."""
        return self._set_membership('tech_build', 'is_buildable', (tech, step), value)

    def set_retirable(self, tech: str, step: int, value: bool = True) -> MiniModelData:
        """Add (or with ``value=False`` remove) ``(tech, step)`` in the retirable set."""
        return self._set_membership('tech_retire', 'is_retirable', (tech, step), value)

    def add_rows(self, key: str, rows: Mapping[tuple, float]) -> MiniModelData:
        """Add/overwrite rows of a parameter source.

        Parameters
        ----------
        key : str
            a ``PARAM_SOURCES`` key.
        rows : Mapping[tuple, float]
            keys ordered as that source's ``index_cols``; seasons by name (``str``), as in the
            csv files.

        Returns
        -------
        MiniModelData
            self, for chaining.

        Raises
        ------
        KeyError
            on an unknown param key.
        ValueError
            on a row key whose length does not match ``index_cols``.
        """
        index_cols = PARAM_SOURCES[key].index_cols
        for idx in rows:
            if len(idx) != len(index_cols):
                raise ValueError(f'{key} row {idx} does not match index_cols {index_cols}')
        self.params[key].update({idx: float(v) for idx, v in rows.items()})
        return self

    def add_capacity(
        self,
        tech: str,
        mw: float,
        step: int = 1,
        regions: Iterable[str] | None = None,
        years: Iterable[int] | None = None,
    ) -> MiniModelData:
        """Add existing (``available_capacity``) MW for a tech step.

        Pair with ``add_operating_costs``:  ``validate_all`` requires a ``generation_cost`` for
        every existing capacity row.

        Parameters
        ----------
        tech : str
        mw : float
        step : int
        regions, years : Iterable, optional
            where/when the capacity exists; all regions / years when omitted.

        Returns
        -------
        MiniModelData
            self, for chaining.
        """
        regions = self.regions if regions is None else list(regions)
        years = self.years if years is None else list(years)
        return self.add_rows(
            'available_capacity', {(r, tech, step, y): mw for r in regions for y in years}
        )

    def add_operating_costs(
        self,
        tech: str,
        generation_cost: float,
        fom_cost: float,
        step: int = 1,
        regions: Iterable[str] | None = None,
    ) -> MiniModelData:
        """Add ``generation_cost`` (every year and season) and ``fom_cost`` for a tech step.

        Every location in the capacity index (existing *or* buildable) needs both.

        Parameters
        ----------
        tech : str
        generation_cost : float
            $/MWh, applied to every year and season.
        fom_cost : float
            $/MW-yr.
        step : int
        regions : Iterable[str], optional
            all regions when omitted.

        Returns
        -------
        MiniModelData
            self, for chaining.
        """
        regions = self.regions if regions is None else list(regions)
        self.add_rows(
            'generation_cost',
            {
                (r, tech, step, y, str(s)): generation_cost
                for r in regions
                for y, s in self.every('year', 'season')
            },
        )
        return self.add_rows('fom_cost', {(r, tech, step): fom_cost for r in regions})

    def drop_rows(self, key: str, rows: Iterable[tuple]) -> MiniModelData:
        """Remove rows (which must exist) from a parameter source."""
        for idx in rows:
            del self.params[key][idx]
        return self

    def set_load(
        self, region: str, by_hour: Mapping[int, float], years: Iterable[int] | None = None
    ) -> MiniModelData:
        """Set the (pre-weighting) load for a region, replacing any existing rows it overlaps.

        Parameters
        ----------
        region : str
        by_hour : Mapping[int, float]
            hour -> MW.
        years : Iterable[int], optional
            years to apply to; all years when omitted.

        Returns
        -------
        MiniModelData
            self, for chaining.
        """
        years = list(years) if years is not None else self.years
        new = pd.DataFrame(
            [(region, y, hr, float(mw)) for y in years for hr, mw in by_hour.items()],
            columns=['region', 'year', 'hour', 'Load'],
        )
        keys = ['region', 'year', 'hour']
        if self.load.empty:
            self.load = new
        else:
            kept = self.load.merge(new[keys], on=keys, how='left', indicator=True)
            kept = kept[kept['_merge'] == 'left_only'].drop(columns='_merge')
            self.load = pd.concat([kept, new], ignore_index=True)
        self.load = self.load.astype({'year': int, 'hour': int}).sort_values(keys)
        return self

    def _set_membership(self, source: str, prop: str, idx: tuple, value: bool) -> MiniModelData:
        """Toggle ``idx`` in the ``properties[source][prop]`` membership list."""
        members = self.properties[source][prop]
        if value and idx not in members:
            members.append(idx)
        elif not value and idx in members:
            members.remove(idx)
        return self


def patch_loaders(monkeypatch: pytest.MonkeyPatch, data: MiniModelData) -> None:
    """Point the electricity loaders at ``data`` instead of the filesystem.

    Each call hands out a deep copy, as ``ParamData`` consumes (pops) what it is given.  The
    patches read ``data`` lazily, so edits made after patching are still picked up.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
    data : MiniModelData
    """
    monkeypatch.setattr(
        'src.models.electricity.model_sets.load_property_data',
        lambda _path: copy.deepcopy(data.properties),
    )
    monkeypatch.setattr(
        'src.models.electricity.model_sets.load_attribute_data',
        lambda _path: copy.deepcopy(data.attributes),
    )
    monkeypatch.setattr(
        'src.models.electricity.model_sets.create_temporal_mapping',
        lambda _resolution: data.cw_temporal.copy(),
    )
    monkeypatch.setattr(
        'src.models.electricity.param_data.load_param_data',
        lambda input_dir, param_filter=None: copy.deepcopy(data.params),
    )
    monkeypatch.setattr(ParamData, 'build_load_dataframe', lambda self: data.load.copy())


def mini_configs(
    root: Path, years: list[int], **elec_switches: Any
) -> tuple[CommonConfig, ElecConfig]:
    """Build a (CommonConfig, ElecConfig) pair for the mini model.

    Every path points under ``root`` (an empty directory), so a read that escapes the patched
    loaders fails loudly.

    Parameters
    ----------
    root : Path
        empty scratch directory (e.g. pytest's ``tmp_path``).
    years : list[int]
        summary years.
    **elec_switches
        ``ElecConfig`` overrides (e.g. ``capacity_expansion=True``).

    Returns
    -------
    tuple[CommonConfig, ElecConfig]
    """
    for sub in ('data', 'residential', 'params', 'output'):
        (root / sub).mkdir(exist_ok=True)
    common_config = CommonConfig(
        mode='standalone',
        models_to_run=['electricity'],
        common_data_path=root / 'data',
        residential_data_path=root / 'residential',
        output_path=root / 'output',
        scenario_name='mini_model',
        temporal_resolution='mini',
        aggregate_years=False,
        aggregate_start_year=None,
        summary_years=years,
    )
    elec_config = ElecConfig(input_path=root / 'params', **(_DEFAULT_SWITCHES | elec_switches))
    return common_config, elec_config
