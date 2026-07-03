"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/24/26

Class to hold parameter data and more advanced/indexing set constructs

Some param data is held in simple dictionary objects.  Others that need manipulation are
held in pandas dataframes.

"""

import logging
from collections.abc import Collection
from pathlib import Path

import pandas as pd
from pandas import DataFrame

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.utilities import scale_load, scale_load_with_enduses
from src.models.electricity.data_ingestor import (
    TIME_BASED_DFS,
    FilterPackage,
    load_dataframes,
    load_param_data,
)
from src.models.electricity.elec_config import ElecConfig, LoadScaleMode
from src.models.electricity.model_sets import SCI, ModelSets
from src.models.electricity.preprocessor import add_season_index, avg_by_group, time_map

logger = logging.getLogger(__name__)


class ParamData:
    # constructed temporal dataframes
    weight_year: DataFrame
    weight_season: DataFrame
    weight_day: DataFrame
    weight_hour: DataFrame
    map_hour_day: DataFrame
    map_hour_season: DataFrame
    map_day_season: DataFrame

    # other constructed dataframes
    load: DataFrame

    # loaded frames and dicts
    param_frames: dict[str, DataFrame]
    param_dicts: dict[str, dict]

    # # complex Param Data
    # cap_cost : DataFrame
    # cap_factor_VRE : DataFrame
    # h2_price : DataFrame
    # load_df : DataFrame
    # supply_curve : DataFrame
    # supply_curve_learning : DataFrame
    # supply_price : DataFrame
    # tran_cost : DataFrame
    # tran_cost_int : DataFrame
    # tran_limit : DataFrame
    # tran_limit_cap_int : DataFrame
    # tran_limit_gen_int : DataFrame
    #
    # # basic Param Data
    # battery_efficiency : dict
    # cap_cost_initial : dict
    # cap_factor_vre : dict
    # fom_cost : dict
    # hours_to_buy : dict
    # hydro_cap_factor : dict
    # learning_rate : dict
    # ramp_down_cost : dict
    # ramp_rate : dict
    # ramp_up_cost : dict
    # reg_reserves_cost : dict
    # reserve_margin : dict
    # res_tech_upper_bound : dict

    def __init__(self, elec_config: ElecConfig, common_config: CommonConfig, model_sets: ModelSets):
        self.param_frames = {}
        self.param_dicts = {}

        # capture config items
        self.elec_config = elec_config
        self.common_config = common_config
        self.model_sets = model_sets

        # load all of the param data using a filter
        param_filter = FilterPackage(
            region_filter=elec_config.region_filter, year_filter=common_config.summary_years
        )
        param_data = load_param_data(input_dir=elec_config.input_path, param_filter=param_filter)
        logger.info('Read in %d parameter elements', len(param_data))

        # TEMP HACK:  Adjust prices by factor of x1000 in select params to match the old values
        # TODO:  Remove this segment and force this on the DATA!!!!
        names_to_adjust = [
            'supply_price',
            'tran_cost',
            'tran_cost_int',
            'reg_reserves_cost',
            'ramp_up_cost',
            'ramp_down_cost',
            'cap_cost',
            'cap_cost_initial',
        ]
        for name in names_to_adjust:
            param_data[name] = {k: v * 1000 for k, v in param_data[name].items()}
        # make the time-based dataframes
        # TODO:  refactor this?
        self.build_temporal_maps()

        # make the Load dataframe
        full_load_df = self.build_load_dataframe()
        full_load_df = self.aggregate_time(full_load_df)
        filtered_load_df = self.filter_dataframe(full_load_df, 'region', elec_config.region_filter)
        self.param_frames['Load'] = self.apply_hourly_weights(
            self.param_frames['WeightHour'], filtered_load_df
        )

        # Pluck out the time-based DF conversions and load them
        all_frames = load_dataframes(param_data=param_data, names_to_convert=TIME_BASED_DFS)
        for name, df in all_frames.items():
            param_data.pop(name)  # remove from param_data so we don't try to load it again below
            # aggregate the time in the dataframe
            df = self.aggregate_time(df)
            # set the index properly
            df = df.set_index(list(df.columns[:-1]))
            self.param_frames[name] = df

        # augment CapFactorVRE with year.... ugh
        # TODO:  This should NOT be necessary as the data is not year-indexed.  Refactor
        df = pd.merge(
            self.param_frames['cap_factor_vre'].reset_index(),
            pd.DataFrame({'year': common_config.summary_years}),
            how='cross',
        )
        # re-sequence columns
        df = df[['tech', 'year', 'region', 'step', 'hour', 'CapFactorVRE']]
        # set proper index
        self.param_frames['cap_factor_vre'] = df.set_index(list(df.columns)[:-1])

        # expand season columns to the appropriate rep-hours for the season where season is used

        TLCI_cols = ['region', 'region_international', 'year', 'hour', 'TranLimitCapInt']
        TLGI_cols = ['region_international', 'step', 'year', 'hour', 'TranLimitGenInt']
        self.param_frames['tran_limit_cap_int'] = self.create_hourly_params(
            self.param_frames['MapHourSeason'],
            self.param_frames['tran_limit_cap_int'],
            TLCI_cols,
            name='tran_limit_cap_int',
        )
        self.param_frames['tran_limit_gen_int'] = self.create_hourly_params(
            self.param_frames['MapHourSeason'],
            self.param_frames['tran_limit_gen_int'],
            TLGI_cols,
            name='tran_limit_gen_int',
        )
        # do the same for interregional
        TLI_cols = ['source_region', 'destination_region', 'year', 'hour', 'TranLimit']
        self.param_frames['tran_limit'] = self.create_hourly_params(
            self.param_frames['MapHourSeason'],
            self.param_frames['tran_limit'],
            TLI_cols,
            name='tran_limit',
        )
        logger.info(
            'Converted seasonal interregional travel param to hourly of size: %d',
            len(self.param_frames['tran_limit']),
        )

        # use the supply curve df BEFORE augmentation with seasons (below)
        # to populate other sets in ModelSets
        model_sets.build_sc_indexes(list(SCI(*t) for t in self.param_frames['supply_curve'].index))

        # populate the trade indices based on gathered parameter data
        model_sets.build_international_travel_index(
            intl_capacity=self.param_frames['tran_limit_cap_int'],
            intl_gen_limit=self.param_frames['tran_limit_gen_int'],
        )

        # augment the supply curve dataframe w/ season x-product
        # TODO:  It should NOT be necessary to augment this DF with the x-product of season.
        #        Review formulation.
        #        Likely done to allow comparison w/ price, which IS seasonal.
        df = add_season_index(
            model_sets.cw_temporal, self.param_frames['supply_curve'].reset_index(), 1
        )
        self.param_frames['supply_curve'] = df.set_index(list(df.columns)[:-1])

        # use the augmented supply_curve DF to make the capacity_credit df
        self.param_frames['capacity_credit'] = self.build_capacity_credit(
            augmented_supply_curve=self.param_frames['supply_curve'],
            hour_season_map=self.param_frames['MapHourSeason'],
            cap_factor_vre=self.param_frames['cap_factor_vre'],
            hydro_cap_factor=self.param_frames['hydro_cap_factor'],
            tech_hyrdo=model_sets.tech_hydro,
        )

        # Load the remainder directly into dictionary objects
        for name in param_data:
            data = param_data.get(name, {})
            if not data:
                logger.warning(
                    'No data found for %s in the parameter data.  Using empty dict', name
                )
            self.param_dicts[name] = data

    def build_load_dataframe(self) -> DataFrame:
        """Build the load dataframe"""

        # TODO:  Research the functions called here and perhaps move them here as well
        # TODO:  Move the source data out of the "residential" folder to "common"??
        # TODO:  Verify that the Load is defined in all region x year x hour.  Model assumes full set
        # TODO:  refactor this so it returns a DF for testing purposes

        res_dir = Path(PROJECT_ROOT, 'input', 'residential')
        if self.elec_config.load_scale_mode == LoadScaleMode.ANNUAL:
            df = scale_load(res_dir).reset_index(drop=True)
        elif self.elec_config.load_scale_mode == LoadScaleMode.END_USE:
            df = scale_load_with_enduses(res_dir).reset_index(drop=True)
        else:
            raise NotImplementedError(
                f'load_scale_mode {self.elec_config.load_scale_mode} not implemented'
            )

        logger.info('Read/built in %d load elements', len(df))
        return df

    def build_temporal_maps(self):
        """Build the time-based dataframes"""
        self.param_frames['MapDaySeason'] = time_map(
            self.model_sets.cw_temporal, {'Map_day': 'day', 'Map_s': 'season'}
        ).set_index('day')
        self.param_frames['MapHourDay'] = time_map(
            self.model_sets.cw_temporal, {'Map_hour': 'hour', 'Map_day': 'day'}
        ).set_index('hour')
        self.param_frames['MapHourSeason'] = time_map(
            self.model_sets.cw_temporal, {'Map_hour': 'hour', 'Map_s': 'season'}
        ).set_index('hour')

        self.param_frames['WeightYear'] = pd.DataFrame.from_dict(
            data=self.model_sets.year_agg_weights, orient='index', columns=['WeightYear']
        ).rename_axis('year')
        self.param_frames['WeightHour'] = time_map(
            self.model_sets.cw_temporal, {'Map_hour': 'hour', 'WeightHour': 'WeightHour'}
        ).set_index('hour')

        self.param_frames['WeightDay'] = time_map(
            self.model_sets.cw_temporal, {'Map_day': 'day', 'WeightDay': 'WeightDay'}
        ).set_index('day')

        # weights per season
        self.param_frames['WeightSeason'] = self.model_sets.cw_temporal[
            ['Map_s', 'Map_hour', 'WeightDay', 'WeightHour']
        ].drop_duplicates()
        self.param_frames['WeightSeason'].loc[:, 'WeightSeason'] = (
            self.param_frames['WeightSeason']['WeightDay']
            * self.param_frames['WeightSeason']['WeightHour']
        )
        self.param_frames['WeightSeason'] = (
            self.param_frames['WeightSeason']
            .drop(columns=['WeightDay', 'WeightHour', 'Map_hour'])
            .groupby(['Map_s'])
            .agg('sum')
            .reset_index()
            .rename(columns={'Map_s': 'season'})
        ).set_index('season')

    def aggregate_time(self, df: DataFrame):
        """Aggregate a time-based dataframe at yearly and hourly levels"""
        # average values in years/hours used

        if 'year' in df.columns:
            df = avg_by_group(df, 'year', self.model_sets.year_map_df)
        if 'hour' in df.columns:
            df = avg_by_group(df, 'hour', self.model_sets.cw_temporal[['hour', 'Map_hour']])
        return df

    def apply_hourly_weights(self, hour_weight: DataFrame, target: DataFrame) -> DataFrame:
        """Apply the hourly weights to the data in the target dataframe by matching hour weights"""
        # Update load to be the total demand in each time segment rather than the average
        df = pd.merge(target, hour_weight, how='left', on=['hour'])
        df['Load'] = df['Load'] * df['WeightHour']
        df = df.drop(columns=['WeightHour'])
        df = df.set_index(list(df.columns[:-1]))
        return df

    def build_capacity_credit(
        self,
        augmented_supply_curve: DataFrame,
        hour_season_map: DataFrame,
        cap_factor_vre: DataFrame,
        hydro_cap_factor: DataFrame,
        tech_hyrdo: Collection[str],
    ) -> DataFrame:
        # def capacitycredit_df(all_frames: dict[str, DataFrame], setin):
        """builds the capacity credit dataframe

        Parameters
        ----------

        Returns
        -------
        pd.DataFrame
            formatted capacity credit data frame
        """
        basis = self.create_hourly_params(
            target=augmented_supply_curve,
            hour_season_map=hour_season_map,
            new_col_sequence=None,
            set_index=False,
        )

        # capacity credit is hourly capacity factor for vre technologies
        df = pd.merge(
            basis,
            cap_factor_vre,
            how='left',
            on=['tech', 'year', 'region', 'step', 'hour'],
        ).rename(columns={'CapFactorVRE': 'CapacityCredit'})  # TODO:  This seems hoaky
        # df now has all rows in it.  We just need to correct entries for non-VRE technologies and hydro techs

        # capacity credit = 1 for dispatchable technologies
        df['CapacityCredit'] = df['CapacityCredit'].fillna(1)  # everything non-VRE

        # capacity credit is seasonal limit for hydro, map it out to hourly
        hydro_capacity = self.create_hourly_params(
            target=hydro_cap_factor,
            hour_season_map=hour_season_map,
            new_col_sequence=None,
            set_index=False,
        )

        # merge hydro capacity factors onto df by keys, then update only hydro technologies
        df = pd.merge(
            df,
            hydro_capacity[['region', 'hour', 'HydroCapFactor']],
            how='left',
            on=['region', 'hour'],
        )

        hydro_mask = df['tech'].isin(tech_hyrdo)
        df.loc[hydro_mask, 'CapacityCredit'] = df.loc[hydro_mask, 'HydroCapFactor']

        # drop unnecessary columns
        df = df.drop(columns=['SupplyCurve', 'HydroCapFactor'])

        # reorder columns
        df = df[['tech', 'year', 'region', 'step', 'hour', 'CapacityCredit']]
        df = df.set_index(list(df.columns)[:-1])
        return df

    @staticmethod
    def filter_dataframe(target: DataFrame, column: str, values: list[str]) -> DataFrame:
        """Filter a dataframe by a column and a list of values"""
        return target[target[column].isin(values)]

    @staticmethod
    def create_hourly_params(
        hour_season_map: DataFrame,
        target: DataFrame,
        new_col_sequence: list[str] | None,
        set_index: bool = True,
        name: str = '',
    ) -> DataFrame:
        """Expands params that are indexed by season to be indexed by hour"""
        orig_size = len(target)
        target = target.reset_index()
        map_no_index = hour_season_map.reset_index()
        df = pd.merge(target, map_no_index, on=['season'], how='left').drop(columns=['season'])
        if new_col_sequence:
            # filter/sort by provided column list
            df = df[new_col_sequence]
        new_size = len(df)
        logger.info(
            'Expanded df %s from %d to %d with seasonal -> hour conversion',
            name,
            orig_size,
            new_size,
        )
        if set_index:
            return df.set_index(list(df.columns[:-1]))
        return df
