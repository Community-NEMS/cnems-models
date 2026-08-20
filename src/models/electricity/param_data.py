"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/24/26

Class to hold parameter data and more advanced/indexing set constructs

Some param data is held in simple dictionary objects.  Others that need manipulation are
held in pandas dataframes.

"""

import logging
from collections.abc import Collection
from functools import singledispatchmethod

import pandas as pd
from pandas import DataFrame

from src.common.common_config import CommonConfig
from src.common.update_package import ElectricityPriceScaler, TransCostUpdate, UpdatePackage
from src.common.utilities import scale_load, scale_load_with_enduses
from src.models.electricity.data_ingestor import (
    TIME_BASED_DFS,
    FilterPackage,
    load_dataframes,
    load_param_data,
)
from src.models.electricity.elec_config import ElecConfig, LoadScaleMode, ReserveType
from src.models.electricity.model_sets import SCI, ModelSets
from src.models.electricity.param_utilities import add_season_index, avg_by_group, time_map

logger = logging.getLogger(__name__)


class ParamData:
    """Reads and shapes the electricity model's parameter data from the raw input files.

    Simple parameters are held as plain dicts; parameters that still need manipulation are held as
    DataFrames.
    """

    # constructed temporal dataframes
    weight_year: DataFrame
    weight_season: DataFrame
    weight_day: DataFrame
    weight_hour: DataFrame
    map_hour_day: DataFrame
    map_hour_season: DataFrame
    map_day_season: DataFrame

    # other constructed dataframes
    elec_load: DataFrame

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

    def __init__(self, common_config: CommonConfig, elec_config: ElecConfig, model_sets: ModelSets):
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
        load_df = self.build_load_dataframe()
        aggregated_load_df = self.aggregate_time(load_df)
        self.param_frames['elec_load'] = self.apply_hourly_weights(
            self.param_frames['weight_hour'], aggregated_load_df
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

        # augment cap_factor_vre with year.... ugh
        # TODO:  This should NOT be necessary as the data is not year-indexed.  Refactor
        df = pd.merge(
            self.param_frames['cap_factor_vre'].reset_index(),
            pd.DataFrame({'year': common_config.summary_years}),
            how='cross',
        )
        # re-sequence columns
        df = df[['region', 'tech', 'step', 'year', 'hour', 'value']]
        # set proper index
        self.param_frames['cap_factor_vre'] = df.set_index(list(df.columns)[:-1])

        # expand season columns to the appropriate rep-hours for the season where season is used
        # we can skip this if no international connections:
        # TODO:  Investigate why this is done.  It seems unnecessary bloat to expand with
        #        duplicate information.  Why not just index with [season]?
        if len(self.param_frames['tran_limit_cap_int']) > 0:
            tlci_cols = ['region', 'region_international', 'year', 'hour', 'capacity']
            tlgi_cols = ['region_international', 'step', 'year', 'hour', 'generation']
            self.param_frames['tran_limit_cap_int'] = self.create_hourly_params(
                self.param_frames['map_hour_season'],
                self.param_frames['tran_limit_cap_int'],
                tlci_cols,
                name='tran_limit_cap_int',
            )
            self.param_frames['tran_limit_gen_int'] = self.create_hourly_params(
                self.param_frames['map_hour_season'],
                self.param_frames['tran_limit_gen_int'],
                tlgi_cols,
                name='tran_limit_gen_int',
            )
        # do the same for interregional
        # we can skip this if there are no interregional connections
        if len(self.param_frames['tran_limit']) > 0:
            tli_cols = ['destination_region', 'source_region', 'year', 'hour', 'value']
            self.param_frames['tran_limit'] = self.create_hourly_params(
                self.param_frames['map_hour_season'],
                self.param_frames['tran_limit'],
                tli_cols,
                name='tran_limit',
            )
            logger.info(
                'Converted seasonal interregional travel param to hourly of size: %d',
                len(self.param_frames['tran_limit']),
            )

        # use the supply curve df BEFORE augmentation with seasons (below)
        # to populate other sets in ModelSets
        supply_curve_index_vals = [SCI(*t) for t in self.param_frames['supply_curve'].index]
        model_sets.build_sc_indexes(supply_curve_index_vals)

        # populate the trade indices based on gathered parameter data
        model_sets.build_international_trade_index(
            intl_capacity=self.param_frames['tran_limit_cap_int'],
            intl_gen_limit=self.param_frames['tran_limit_gen_int'],
        )

        # augment the supply curve dataframe w/ season x-product in temp var to enable x-product
        df = add_season_index(
            model_sets.cw_temporal, self.param_frames['supply_curve'].reset_index(), 4
        )
        augmented_supply_curve = df.set_index(list(df.columns)[:-1])

        # use the augmented supply_curve DF to make the capacity_credit df
        self.param_frames['capacity_credit'] = self.build_capacity_credit(
            augmented_supply_curve=augmented_supply_curve,
            hour_season_map=self.param_frames['map_hour_season'],
            cap_factor_vre=self.param_frames['cap_factor_vre'],
            hydro_cap_factor=self.param_frames['hydro_cap_factor'],
            tech_hydro=model_sets.tech_hydro,
        )

        # Load the remainder directly into dictionary objects
        for name in param_data:
            data = param_data.get(name, {})
            if not data:
                logger.warning(
                    'No data found for %s in the parameter data.  Using empty dict', name
                )
            self.param_dicts[name] = data

        # Convert the res-tech upper bound entries to Enum values for validation
        self.param_dicts['res_tech_upper_bound'] = ParamData._convert_reserve_types(
            self.param_dicts['res_tech_upper_bound']
        )

        # build the reserves set, using supply + the UB to be used as a filter in construction
        model_sets.build_reserves_index(
            supply_curve_index=supply_curve_index_vals,
            reserve_ub_limits=self.param_dicts['res_tech_upper_bound'],
        )

    def build_load_dataframe(self) -> DataFrame:
        """Build the load dataframe."""
        # TODO:  Research the functions called here and perhaps move them here as well
        # TODO:  Move the source data out of the "residential" folder to "common"??
        # TODO:  Verify that the Load is defined in all region x year x hour.
        #        Model assumes full set
        # TODO:  refactor this so it returns a DF for testing purposes

        res_dir = self.common_config.residential_data_path

        match self.elec_config.load_scale_mode:
            case LoadScaleMode.ANNUAL:
                # build it and filter by region
                df = scale_load(res_dir).reset_index(drop=True)
                df = self.filter_dataframe(df, 'region', self.model_sets.region_analyze)
            case LoadScaleMode.END_USE:
                # build routine uses filtered regions...
                df = scale_load_with_enduses(
                    res_dir,
                    regions=self.model_sets.region_analyze,
                ).reset_index(drop=True)
            case _:
                raise NotImplementedError(
                    f'load_scale_mode {self.elec_config.load_scale_mode} not implemented'
                )

        logger.info('Read/built in %d load elements', len(df))
        return df

    def build_temporal_maps(self):
        """Build the time-based dataframes."""
        self.param_frames['map_day_season'] = time_map(
            self.model_sets.cw_temporal, {'Map_day': 'day', 'Map_s': 'season'}
        ).set_index('day')
        self.param_frames['map_hour_day'] = time_map(
            self.model_sets.cw_temporal, {'Map_hour': 'hour', 'Map_day': 'day'}
        ).set_index('hour')
        self.param_frames['map_hour_season'] = time_map(
            self.model_sets.cw_temporal, {'Map_hour': 'hour', 'Map_s': 'season'}
        ).set_index('hour')

        self.param_frames['weight_year'] = pd.DataFrame.from_dict(
            data=self.model_sets.year_agg_weights, orient='index', columns=['weight_year']
        ).rename_axis('year')
        self.param_frames['weight_hour'] = time_map(
            self.model_sets.cw_temporal, {'Map_hour': 'hour', 'WeightHour': 'weight_hour'}
        ).set_index('hour')

        self.param_frames['weight_day'] = time_map(
            self.model_sets.cw_temporal, {'Map_day': 'day', 'WeightDay': 'weight_day'}
        ).set_index('day')

        # weights per season
        self.param_frames['weight_season'] = self.model_sets.cw_temporal[
            ['Map_s', 'Map_hour', 'WeightDay', 'WeightHour']
        ].drop_duplicates()
        self.param_frames['weight_season'].loc[:, 'weight_season'] = (
            self.param_frames['weight_season']['WeightDay']
            * self.param_frames['weight_season']['WeightHour']
        )
        self.param_frames['weight_season'] = (
            self.param_frames['weight_season']
            .drop(columns=['WeightDay', 'WeightHour', 'Map_hour'])
            .groupby(['Map_s'])
            .agg('sum')
            .reset_index()
            .rename(columns={'Map_s': 'season'})
        ).set_index('season')

    def aggregate_time(self, df: DataFrame):
        """Aggregate a time-based dataframe at yearly and hourly levels."""
        # average values in years/hours used

        if 'year' in df.columns:
            df = avg_by_group(df, 'year', self.model_sets.year_map_df)
        if 'hour' in df.columns:
            df = avg_by_group(df, 'hour', self.model_sets.cw_temporal[['hour', 'Map_hour']])
        return df

    def apply_hourly_weights(self, hour_weight: DataFrame, target: DataFrame) -> DataFrame:
        """Apply the hourly weights to the data in the target dataframe by matching hour weights."""
        # Update load to be the total demand in each time segment rather than the average
        df = pd.merge(target, hour_weight, how='left', on=['hour'])
        df['Load'] = df['Load'] * df['weight_hour']
        df = df.drop(columns=['weight_hour'])
        df = df.set_index(list(df.columns[:-1]))
        return df

    # supersedes param_utilities.capacity_credit_df(all_frames, setin)
    def build_capacity_credit(
        self,
        augmented_supply_curve: DataFrame,
        hour_season_map: DataFrame,
        cap_factor_vre: DataFrame,
        hydro_cap_factor: DataFrame,
        tech_hydro: Collection[str],
    ) -> DataFrame:
        """Builds the capacity credit dataframe.

        Parameters
        ----------
        augmented_supply_curve : DataFrame
            supply curve providing the (region, tech, step, year) basis to expand to hours
        hour_season_map : DataFrame
            crosswalk mapping each hour to its season
        cap_factor_vre : DataFrame
            capacity factors for the variable renewable techs
        hydro_cap_factor : DataFrame
            seasonal capacity factors for the hydro techs
        tech_hydro : Collection[str]
            techs treated as hydro, which draw their credit from ``hydro_cap_factor``

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
        ).rename(columns={'value': 'capacity_credit'})  # TODO:  This seems hoaky
        # df now has all rows in it.  We just need to correct entries for non-VRE
        # technologies and hydro techs

        # capacity credit = 1 for dispatchable technologies
        df['capacity_credit'] = df['capacity_credit'].fillna(1)  # everything non-VRE

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
            hydro_capacity[['region', 'hour', 'value']],
            how='left',
            on=['region', 'hour'],
        )

        hydro_mask = df['tech'].isin(tech_hydro)
        df.loc[hydro_mask, 'capacity_credit'] = df.loc[hydro_mask, 'value']

        # drop unnecessary columns
        df = df.drop(columns=['capacity', 'value'])

        # reorder columns
        df = df[['region', 'tech', 'step', 'year', 'hour', 'capacity_credit']]
        df = df.set_index(list(df.columns)[:-1])
        return df

    @singledispatchmethod
    def apply_update_package(self, update_package: UpdatePackage):
        """Apply the update package using single dispatch method."""
        raise NotImplementedError(
            f'Missing single dispatch method for type: {type(update_package)}'
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_update_package.register  # type: ignore[no-matching-overload]
    def _(self, electricity_price_scalar: ElectricityPriceScaler) -> None:
        """Scale the supply prices of the named techs by the scalar multiplier.

        Parameters
        ----------
        electricity_price_scalar : ElectricityPriceScaler
            Update package naming the techs to scale and the multiplier to apply.

        Notes
        -----
        ``supply_price`` is indexed by ``(region, tech, step, year, season)``, so the tech
        filter is built from the index level rather than a column.  The frame is modified in
        place, in ``self.param_frames``.
        """
        prices = self.param_frames['supply_price']
        tech_mask = prices.index.get_level_values('tech').isin(electricity_price_scalar.techs)
        if not tech_mask.any():
            logger.warning(
                'No supply_price rows matched techs %s; prices unchanged',
                electricity_price_scalar.techs,
            )
            return
        prices.loc[tech_mask, :] *= electricity_price_scalar.scalar
        logger.info(
            'Scaled supply_price by factor %0.3f for %d of %d rows (techs %s)',
            electricity_price_scalar.scalar,
            tech_mask.sum(),
            len(prices),
            electricity_price_scalar.techs,
        )

    # pyrefly cannot type either form of singledispatchmethod.register against typeshed
    @apply_update_package.register  # type: ignore[no-matching-overload]
    def _(self, trans_cost_update: TransCostUpdate) -> None:
        """Merge an incoming transmission cost update into the ``tran_cost`` frame.

        Rows carried by the update replace the matching rows of ``tran_cost``; entries the
        update does not cover keep their loaded values and are reported as warnings.  Entries
        in the update beyond the held index (regions/years filtered out of this run) are
        ignored.

        Parameters
        ----------
        trans_cost_update : TransCostUpdate
            Update package holding a frame indexed like ``tran_cost``
            ``(destination_region, source_region, year)`` with a single ``cost`` column.

        Notes
        -----
        Retaining uncovered entries matters:  ``TranCost`` is a dense pyomo Param with no
        default, so a hole in the frame fails model construction.  Incoming values must be in
        the same units as the loaded frame, which the price hack in ``__init__`` scales by
        1000.
        """
        old = self.param_frames['tran_cost']
        new = trans_cost_update.elements
        if old.empty:
            logger.warning(
                'Held tran_cost is empty; taking all %d received rows without a coverage check',
                len(new),
            )
            self.param_frames['tran_cost'] = new
            return
        if set(new.columns) != set(old.columns):
            logger.warning(
                'Received tran_cost columns %s do not match the held columns %s',
                list(new.columns),
                list(old.columns),
            )
        missing = self._report_index_gaps(old.index, new.index, name='tran_cost')
        # align level names so combine_first matches on position, as the gap report does; the
        # reindex drops the overages that combine_first's index union would otherwise carry in
        new = new.rename_axis(old.index.names)
        self.param_frames['tran_cost'] = new.combine_first(old).reindex(old.index)[old.columns]
        logger.info(
            'Updated tran_cost:  %d of %d rows replaced, %d retained from the loaded data',
            len(old) - len(missing),
            len(old),
            len(missing),
        )

    @staticmethod
    def _report_index_gaps(
        old: pd.Index, new: pd.Index, name: str, max_report: int = 10
    ) -> pd.Index:
        """Report entries of a held index that newly received data fails to cover.

        Parameters
        ----------
        old : pd.Index
            Index of the currently held data.
        new : pd.Index
            Index of the newly received data.
        name : str
            Frame name, used in the log entries.
        max_report : int, default 10
            Maximum number of missing entries to name in the warning.

        Returns
        -------
        pd.Index
            Entries of ``old`` absent from ``new``; empty if coverage is complete.

        Raises
        ------
        ValueError
            If the indexes have a different number of levels, making them un-alignable.

        Notes
        -----
        Entries of ``new`` absent from ``old`` are overages and are logged at debug level only.
        Level *order* is the contract (see ``index_cols`` in ``param_sources.toml``), so
        differing level names are warned about and then compared by position.
        """
        if old.nlevels != new.nlevels:
            raise ValueError(
                f'Cannot compare the index of {name}:  held data has {old.nlevels} level(s), '
                f'received data has {new.nlevels}.'
            )
        if tuple(old.names) != tuple(new.names):
            logger.warning(
                'Index level names for %s differ:  held %s vs. received %s.  '
                'Comparing by level position',
                name,
                tuple(old.names),
                tuple(new.names),
            )
            new = new.set_names(old.names)
        missing = old.difference(new)
        if len(missing) > 0:
            logger.warning(
                'Received data for %s does not cover %d of %d held entries.  '
                'Missing (up to %d shown):  %s',
                name,
                len(missing),
                len(old),
                max_report,
                list(missing[:max_report]),
            )
        else:
            logger.info('Received data for %s covers all %d held entries', name, len(old))
        overage = len(new.difference(old))
        if overage > 0:
            logger.debug(
                'Received data for %s holds %d entries beyond the held index (ignored)',
                name,
                overage,
            )
        return missing

    @staticmethod
    def filter_dataframe(target: DataFrame, column: str, values: list[str]) -> DataFrame:
        """Filter a dataframe by a column and a list of values."""
        return target[target[column].isin(values)]

    @staticmethod
    def create_hourly_params(
        hour_season_map: DataFrame,
        target: DataFrame,
        new_col_sequence: list[str] | None,
        set_index: bool = True,
        name: str = '',
    ) -> DataFrame:
        """Expands params that are indexed by season to be indexed by hour."""
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

    @staticmethod
    def _convert_reserve_types(target: dict) -> dict[tuple[ReserveType, str], float]:
        """Convert strings to enums in this target dict."""
        res = {}
        for (restype, tech), value in target.items():
            try:
                res[ReserveType(restype), tech] = float(value)
            except ValueError:
                logger.error('Invalid reserve type %s for tech %s', restype, tech)
                raise
        return res
