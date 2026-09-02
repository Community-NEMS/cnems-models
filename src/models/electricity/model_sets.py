"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/23/26

Collection of sets used by model.
- Direct reads of "property" files
- Generated temporal sets
- crossed sets from basic data and parameter elements
"""

import logging
from collections import defaultdict, namedtuple
from collections.abc import Collection, Iterable

import pandas as pd
from pandas import DataFrame

from src.common.common_config import CommonConfig
from src.integrator.utilities import create_temporal_mapping
from src.models.electricity.data_ingestor import load_property_data
from src.models.electricity.elec_config import ElecConfig, ReserveType

logger = logging.getLogger(__name__)

SCI = namedtuple('SCI', ['region', 'tech', 'step', 'year'])
"""a fixed index type for supply curve entries"""


class ModelSets:
    """Generates an initial batch of sets that are used to solve electricity model.

    Sets include:

    - Scenario descriptor and model switches
    - Regional sets
    - Temporal sets
    - Technology type sets
    - Supply curve step sets
    - Other.
    """

    capacity_index: list[tuple]
    """The MASTER capacity index, augmented with Hour"""
    capacity_hydro_ub_index: list[tuple]
    generation_demand_index: defaultdict
    generation_dispatchable_ub_index: list[tuple]
    generation_hour_index: defaultdict
    generation_hydro_ub_index: list[tuple]
    generation_index: list[tuple]
    generation_ramp_index: list[tuple]
    generation_vre_ub_index: list[tuple]
    h2_generation_hour_index: defaultdict
    h2_generation_index: list[tuple]
    hydro_seasonal_step_index: defaultdict
    """(region, tech, year) -> the supply curve steps of each seasonal-hydro tech"""
    international_trade_index: list[tuple]
    ramp_first_hour_balance_index: list[tuple]
    ramp_most_hours_balance_index: list[tuple]
    reserves_procurement_index: list[tuple]
    retirement_index: list[tuple]
    storage_demand_index: defaultdict
    storage_first_hour_balance_index: list[tuple]
    storage_hour_index: defaultdict
    storage_index: list[tuple]
    storage_most_hours_balance_index: list[tuple]

    def __init__(self, common_config: CommonConfig, elec_config: ElecConfig):

        # load the bulk of the data from csv property files
        set_data = load_property_data(common_config.common_data_path)

        # break up the tech data into its constituents
        td = set_data['tech_data']
        self.tech = td['tech']
        self.tech_conv = td['T_conv']
        self.tech_re = td['T_re']
        self.tech_wind = td['T_wind']
        self.tech_solar = td['T_solar']
        self.tech_h2 = td['T_h2']
        self.tech_disp = td['T_disp']
        self.tech_gen = td['T_gen']
        self.tech_stor = td['T_stor']
        self.tech_vre = td['T_vre']
        self.tech_hydro = td['T_hydro']
        # T_hydro is the union; the two subsets below decide which hydro bound applies.  Seasonal
        # hydro is limited by a per-season energy budget (capacity_hydro_ub), regular hydro by an
        # hourly capacity factor (generation_hydro_ub).  These replace what used to be a hard-coded
        # supply-curve step number.
        self.tech_hydro_seasonal = td['T_hydro_seasonal']
        self.tech_hydro_regular = td['T_hydro_regular']

        self.tech_retires = set_data['retireable_techs']['retires']
        self.tech_builds = set_data['buildable_techs']['builds']

        # break up the region data into its constituents
        rd = set_data['region_data']
        self.region = rd['region']
        self.region_domestic = rd['domestic']
        self.region_analyze = (
            elec_config.region_filter if elec_config.region_filter else self.region_domestic
        )
        self.region_international = rd['international']

        # # Load Setting
        # self.load_scalar = common_config.scale_load

        # Temporal Sets
        self.cw_temporal: pd.DataFrame = create_temporal_mapping(
            common_config.temporal_resolution
        )  # TODO:  review this creator

        # Temporal Sets - Years
        # if aggregation is commanded make a year map and weighting map, else create a trivial map
        if common_config.aggregate_years:
            self.year_map = ModelSets._create_year_map(
                agg_years=common_config.summary_years,
                # pyrefly: ignore[bad-argument-type]  - CommonConfig validates that
                # aggregate_start_year is set whenever aggregate_years is True
                start_year=common_config.aggregate_start_year,
            )
            self.year_agg_weights = ModelSets._create_year_agg_weights(self.year_map)
        else:
            # the "year_map" is just a trivial mapping of the selected years onto themselves
            self.year_map = {y: y for y in common_config.summary_years}
            self.year_agg_weights = dict.fromkeys(common_config.summary_years, 1)

        # the year-map is used as a df in some spots, so add it here
        self.year_map_df = pd.DataFrame(self.year_map.items(), columns=['year', 'Map_year'])

        # Temporal Sets - Seasons and Days

        self.season = list(self.cw_temporal['Map_s'].unique())
        self.num_days = self.cw_temporal['Map_day'].max()
        self.day = range(1, self.num_days + 1)

        # Temporal Sets - Hours
        # number of time periods in a day
        self.num_hr_day = int(
            self.cw_temporal['Map_hour'].max() / self.cw_temporal['Map_day'].max()
        )
        # Number of time periods the model solves for: days x number of periods per day
        self.hour = range(1, self.num_days * self.num_hr_day + 1)
        # First time period of the day and all time periods that are not the first hour.
        # dev note: at num_hr_day == 1 (the d4h1/d8h1 crosswalks) every hour is a first hour,
        #           so hour_most is empty and the *_most_hours_balance constraints get no rows.
        #           The *_first_hour_balance rules then wrap an hour onto itself, collapsing to
        #           `efficiency * inflow == outflow` and `ramp_up == ramp_down`.
        self.hour_first = range(1, self.num_days * self.num_hr_day + 1, self.num_hr_day)
        # sorted() because set difference does not iterate in ascending order for every
        # (total hours, hours-per-day) pair -- e.g. 8 hours at 2/day yields [8, 2, 4, 6]
        self.hour_most = sorted(set(self.hour) - set(self.hour_first))

        # Misc Inputs
        self.step = range(1, 5)

    def build_reserves_index(
        self,
        supply_curve_index: Iterable[SCI],
        reserve_ub_limits: dict[tuple[ReserveType, str], float],
    ):
        """Build reserve index and use the upper bound limit param to enforce sparsity."""
        self.reserves_procurement_index = sorted(
            (idx.region, reserve_type, idx.tech, idx.step, idx.year, hour)
            for reserve_type in ReserveType
            for idx in supply_curve_index
            for hour in self.hour
            if reserve_ub_limits.get((reserve_type, idx.tech), 0.0) > 0.0
        )
        """sparse set of viable [r, ReserveType, tech, step, year, hour] where ub > 0.0"""
        if not self.reserves_procurement_index:
            logger.warning('reserves_procurement_index is empty')

    def build_sc_indexes(self, supply_curve_index: Iterable[SCI]):
        """Use the supply curve to build the index sets.

        Built by crossing techs with hours and supply curve steps.
        """
        self.capacity_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year) for idx in supply_curve_index
        )
        if not self.capacity_index:
            logger.warning('capacity_index is empty')
        logger.info(f'Built Capacity Index of size: {len(self.capacity_index)}')

        # TODO:  This structure limits possible retirements to only things currently in
        #        supply_curve_index.  Review.
        self.retirement_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year)
            for idx in supply_curve_index
            if (idx.tech, idx.step) in self.tech_retires
        )
        if not self.retirement_index:
            logger.warning('retirement_index is empty')

        self.generation_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr)
            for idx in supply_curve_index
            if idx.tech in self.tech_gen
            for hr in self.hour
        )
        if not self.generation_index:
            logger.warning('generation_index is empty')

        self.h2_generation_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr)
            for idx in supply_curve_index
            if idx.tech in self.tech_h2
            for hr in self.hour
        )
        if not self.h2_generation_index:
            logger.warning('h2_generation_index is empty')

        self.storage_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr)
            for idx in supply_curve_index
            if idx.tech in self.tech_stor
            for hr in self.hour
        )
        if not self.storage_index:
            logger.warning('storage_index is empty')

        # build the companion indexed set bases while we have by-name access to the fields...
        # TODO:  the need for hourly-indexing is under review.  these may "go away"
        # TODO:  refactor these below....all could be done in one pass through the supply curve
        self.generation_hour_index = defaultdict(list)
        for hr in self.hour:
            for idx in supply_curve_index:
                if idx.tech in self.tech_gen:
                    self.generation_hour_index[hr].append(
                        (idx.region, idx.tech, idx.step, idx.year)
                    )
        if not self.generation_hour_index:
            logger.warning('generation_hour_index is empty')

        self.storage_hour_index = defaultdict(list)
        for hr in self.hour:
            for idx in supply_curve_index:
                if idx.tech in self.tech_stor:
                    self.storage_hour_index[hr].append((idx.region, idx.tech, idx.step, idx.year))
        if not self.storage_hour_index:
            logger.warning('storage_hour_index is empty')

        self.h2_generation_hour_index = defaultdict(list)
        for hr in self.hour:
            for idx in supply_curve_index:
                if idx.tech in self.tech_h2:
                    self.h2_generation_hour_index[hr].append(
                        (idx.region, idx.tech, idx.step, idx.year)
                    )
        if not self.h2_generation_hour_index:
            logger.warning('h2_generation_hour_index is empty')

        self.generation_demand_index = defaultdict(list)
        for hr in self.hour:
            for idx in supply_curve_index:
                if idx.tech in self.tech_gen:
                    self.generation_demand_index[idx.region, idx.year, hr].append(
                        (idx.tech, idx.step)
                    )
        if not self.generation_demand_index:
            logger.warning('generation_demand_index is empty')

        self.storage_demand_index = defaultdict(list)
        for hr in self.hour:
            for idx in supply_curve_index:
                if idx.tech in self.tech_stor:
                    self.storage_demand_index[idx.region, idx.year, hr].append((idx.tech, idx.step))
        if not self.storage_demand_index:
            logger.warning('storage_demand_index is empty')

        # build the unique hour sets for ramping/storage first + other 23 hours
        self.ramp_most_hours_balance_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr_most)
            for idx in supply_curve_index
            if idx.tech in self.tech_conv
            for hr_most in self.hour_most
        )
        if not self.ramp_most_hours_balance_index:
            logger.warning('ramp_most_hours_balance_index is empty')
        self.ramp_first_hour_balance_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr_first)
            for idx in supply_curve_index
            if idx.tech in self.tech_conv
            for hr_first in self.hour_first
        )
        if not self.ramp_first_hour_balance_index:
            logger.warning('ramp_first_hour_balance_index is empty')
        self.storage_most_hours_balance_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr_most)
            for idx in supply_curve_index
            if idx.tech in self.tech_stor
            for hr_most in self.hour_most
        )
        if not self.storage_most_hours_balance_index:
            logger.warning('storage_most_hours_balance_index is empty')
        self.storage_first_hour_balance_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr_first)
            for idx in supply_curve_index
            if idx.tech in self.tech_stor
            for hr_first in self.hour_first
        )
        if not self.storage_first_hour_balance_index:
            logger.warning('storage_first_hour_balance_index is empty')
        self.generation_hydro_ub_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hr)
            for idx in supply_curve_index
            if idx.tech in self.tech_hydro_regular
            for hr in self.hour
        )
        if not self.generation_hydro_ub_index:
            logger.warning('generation_hydro_ub_index is empty')
        # note: step is deliberately absent -- the seasonal energy budget applies to the tech's
        # whole supply curve, so capacity_hydro_ub sums over hydro_seasonal_step_index instead.
        self.capacity_hydro_ub_index = sorted(
            {
                (idx.region, idx.tech, idx.year, season)
                for idx in supply_curve_index
                if idx.tech in self.tech_hydro_seasonal
                for season in self.season
            }
        )
        if not self.capacity_hydro_ub_index:
            logger.warning('capacity_hydro_ub_index is empty')
        self.hydro_seasonal_step_index = defaultdict(list)
        for idx in supply_curve_index:
            if idx.tech in self.tech_hydro_seasonal:
                self.hydro_seasonal_step_index[idx.region, idx.tech, idx.year].append(idx.step)

        self.generation_ramp_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hour)
            for idx in supply_curve_index
            if idx.tech in self.tech_conv
            for hour in self.hour
        )
        if not self.generation_ramp_index:
            logger.warning('generation_ramp_index is empty')
        self.generation_dispatchable_ub_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hour)
            for idx in supply_curve_index
            if idx.tech in self.tech_disp
            for hour in self.hour
        )
        if not self.generation_dispatchable_ub_index:
            logger.warning('generation_dispatchable_ub_index is empty')
        self.generation_vre_ub_index = sorted(
            (idx.region, idx.tech, idx.step, idx.year, hour)
            for idx in supply_curve_index
            if idx.tech in self.tech_vre
            for hour in self.hour
        )
        if not self.generation_vre_ub_index:
            logger.warning('generation_vre_ub_index is empty')

    def build_international_trade_index(
        self, intl_capacity: DataFrame, intl_gen_limit: DataFrame
    ) -> list[tuple]:
        """Create the valid international trading index.

        Relies on the capacity and the step from generation limits.
        """
        df = (
            pd.merge(
                intl_capacity.reset_index(),
                intl_gen_limit.reset_index(),
                on=['region_international', 'year', 'hour'],
                how='inner',
            )
            .drop(columns=['generation'])
            .set_index(['region', 'region_international', 'step', 'year', 'hour'])
        )
        res = list(df.index)
        self.international_trade_index = res
        return res

    @staticmethod
    def _create_year_map(agg_years: Collection[int], start_year: int) -> dict:
        """Creates a dictionary of year mappings."""
        if not agg_years:
            logger.error('No years provided for aggregation.')
            raise ValueError('No years provided for aggregation.')
        if start_year > min(agg_years):
            logger.warning(
                'Start year is greater than the minimum year in the aggregation years. '
                'This may result in unexpected behavior.'
            )
        agg_years = sorted(agg_years)  # ensure years are sorted:  cheap insurance!
        res = {}
        idx = 0
        current_year = start_year
        while idx < len(agg_years):
            if current_year <= agg_years[idx]:
                res[current_year] = agg_years[idx]
                current_year += 1
            else:
                idx += 1
        return res

    @staticmethod
    def _create_year_agg_weights(year_map: dict[int, int]) -> dict[int, int]:
        """Creates a dictionary of year aggregation weights."""
        weights = {}
        for agg_year in year_map.values():
            weights[agg_year] = weights.get(agg_year, 0) + 1
        return weights
