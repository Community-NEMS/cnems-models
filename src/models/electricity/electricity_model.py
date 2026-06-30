"""Electricity Model, a pyomo optimization model of the electric power sector.

The class is organized by sections: settings, sets, parameters, variables, objective function,
constraints, plus additional misc support functions.
"""
###################################################################################################
# Setup

# Import packages
from logging import getLogger
import pyomo.environ as pyo
from pandas import DataFrame

from src.common.common_config import CommonConfig

# Import python modules
from src.common.model import Model
from src.models.electricity.constants import UNMET_LOAD_PRICE, STORAGE_LEVEL_COST, H2Heatrate
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType, ReserveType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData

# move to new file
from src.models.electricity.utilities import ElectricityMethods as em

# Establish logger
logger = getLogger(__name__)

###################################################################################################
# MODEL


class PowerModel(Model):
    """A PowerModel instance. Builds electricity pyomo model.

    Parameters
    ----------
    all_frames : dictionary of pd.DataFrames
        Contains all dataframes of inputs
    setA : Sets
        Contains all other non-dataframe inputs
    """

    def __init__(
        self,
        setA: ModelSets,
        param_data: ParamData,
        elec_config: ElecConfig,
        common_config: CommonConfig,
        *args,
        **kwargs,
    ):
        Model.__init__(self, *args, **kwargs)

        ###########################################################################################
        # Settings

        # TODO:  extract this burried constant
        self.sw_h2int = 0

        ###########################################################################################
        # TODO: Example future model concept
        # Note: the goal would be to eventually reorganize the preprocessor so that most data would
        # fit something similar to this example structure below.

        # def declare_set_and_param(name):
        #     """declare set and parameter based on data frame name
        #
        #     Parameters
        #     ----------
        #     name : str
        #         name of data frame to create into set and parameter
        #     """
        #     index_name = name + '_index'
        #     self.declare_set(index_name, all_frames[name])
        #     self.declare_param(name, getattr(self, index_name), all_frames[name])

        # self.declare_set_and_param('FOMCost')
        # self.declare_set_and_param('HydroCapFactor')

        ###########################################################################################
        # Sets

        # temporal sets
        self.hour = pyo.Set(initialize=setA.hour)
        self.day = pyo.Set(initialize=setA.day)
        self.season = pyo.Set(initialize=setA.season)
        self.year = pyo.Set(initialize=setA.year_map.values())

        # spatial sets
        self.region = pyo.Set(initialize=setA.region)
        self.region_int = pyo.Set(initialize=setA.region_international, within=self.region)
        self.region_dom = pyo.Set(initialize=setA.region_domestic, within=self.region)
        self.region_analyze = pyo.Set(initialize=elec_config.region_filter, within=self.region_dom)

        # technology sets
        self.tech = pyo.Set(initialize=setA.tech)
        self.tech_step = pyo.Set(initialize=setA.step)  # TODO:  come back to this
        self.tech_conv = pyo.Set(initialize=setA.tech_conv, within=self.tech)
        self.tech_re = pyo.Set(initialize=setA.tech_re, within=self.tech)
        self.tech_hydro = pyo.Set(initialize=setA.tech_hydro, within=self.tech)
        self.tech_stor = pyo.Set(initialize=setA.tech_stor, within=self.tech)
        self.tech_vre = pyo.Set(initialize=setA.tech_vre, within=self.tech)
        self.tech_wind = pyo.Set(initialize=setA.tech_wind, within=self.tech)
        self.tech_solar = pyo.Set(initialize=setA.tech_solar, within=self.tech)
        self.tech_h2 = pyo.Set(initialize=setA.tech_h2, within=self.tech)
        self.tech_disp = pyo.Set(initialize=setA.tech_disp, within=self.tech)
        self.tech_gen = pyo.Set(initialize=setA.tech_gen, within=self.tech)

        self.buildable_tech = pyo.Set(
            dimen=2, initialize=setA.tech_builds, within=self.tech * self.tech_step
        )
        self.retireable_tech = pyo.Set(
            dimen=2, initialize=setA.tech_retires, within=self.tech * self.tech_step
        )

        self.step = pyo.Set(
            initialize=range(1, 4)
        )  # TODO:  Temporary until we get the plan for step squared away

        # CONSTRAINT INDEXING SETS
        self.storage_most_hours_balance_index = pyo.Set(
            initialize=setA.storage_most_hours_balance_index
        )
        self.storage_first_hour_balance_index = pyo.Set(
            initialize=setA.storage_first_hour_balance_index
        )
        self.ramp_most_hours_balance_index = pyo.Set(initialize=setA.ramp_most_hours_balance_index)
        self.ramp_first_hour_balance_index = pyo.Set(initialize=setA.ramp_first_hour_balance_index)
        self.generation_hydro_ub_index = pyo.Set(initialize=setA.generation_hydro_ub_index)
        self.generation_dispatchable_ub_index = pyo.Set(
            initialize=setA.generation_dispatchable_up_index
        )
        self.generation_ramp_index = pyo.Set(initialize=setA.generation_ramp_index)
        self.capacity_hydro_ub_index = pyo.Set(initialize=setA.capacity_hydro_ub_index)
        self.reserves_procurement_index = pyo.Set(initialize=setA.reserves_procurement_index)
        self.generation_vre_ub_index = pyo.Set(initialize=setA.generation_vre_ub_index)
        ################# Indexed sets

        # make an indexed set of storage (tech, year, region, step) indexed by hour
        self.StorageHour_index = pyo.Set(self.hour, initialize=setA.storage_hour_index)
        # self.StorageHour_index.pprint()

        # Generation-eligible hours
        self.GenHour_index = pyo.Set(self.hour, initialize=setA.generation_hour_index)

        # Generation-eligible hours for H2 technologies
        self.H2GenHour_index = pyo.Set(self.hour, initialize=setA.h2_generation_hour_index)

        self.GenSetDemandBalance = pyo.Set(
            self.year, self.region_analyze, self.hour, initialize=setA.generation_demand_index
        )
        self.StorageSetDemandBalance = pyo.Set(
            self.year, self.region_analyze, self.hour, initialize=setA.storage_demand_index
        )

        # self.declare_set('hour', setA.hour)
        # self.declare_set('day', setA.day)
        # self.declare_set('season', setA.season)
        # self.declare_set('year', setA.year_map.values())  # values are the "mapped/onto" years

        # spatial sets
        # self.declare_set('region', elec_config.region_filter)
        # self.declare_set('region_int', setA.region_int)
        # self.declare_set('region_trade', setA.region_trade)
        # self.declare_set('region_int_trade', setA.region_int_trade)

        # Load sets
        # TODO:  Why is this needed?  If we are just enforcing at points where there is a Load,
        #        then, we can just use the Load parameter index
        # self.declare_set('demand_balance_index', all_frames['Load'])
        # TODO:  This should not be needed either.  It follows from above, that we can only have
        #        unmet load where there is Load, so the same Load parameter index should suffice
        # self.declare_set_with_sets('unmet_load_index', self.region, self.year, self.hour)

        # Supply price and quantity sets and subsets
        # self.declare_set('capacity_total_index', all_frames['SupplyCurve'])
        # self.declare_set('generation_total_index', setA.generation_total_index)
        # self.declare_set('generation_dispatchable_ub_index', setA.generation_dispatchable_ub_index)
        # self.declare_set('Storage_index', setA.Storage_index)
        # self.declare_set('H2Gen_index', setA.H2Gen_index)
        # self.declare_set('generation_hydro_ub_index', setA.generation_hydro_ub_index)

        # TODO:  Verify:  these ramp/stoarge 23 + 1 requirements probably do NOT need index sets.
        #        They are defined succinctly by the parameter itself
        # self.declare_set('ramp_most_hours_balance_index', setA.ramp_most_hours_balance_index)
        # self.declare_set('ramp_first_hour_balance_index', setA.ramp_first_hour_balance_index)
        # self.declare_set('storage_most_hours_balance_index', setA.storage_most_hours_balance_index)
        # self.declare_set('storage_first_hour_balance_index', setA.storage_first_hour_balance_index)

        # TODO:  This also should be just the param index values
        # self.declare_set('capacity_hydro_ub_index', setA.capacity_hydro_ub_index)

        # Other technology sets
        # TODO:  These should also just be the defined param index
        # self.declare_set('HydroCapFactor_index', all_frames['HydroCapFactor'])
        # self.declare_set('generation_vre_ub_index', all_frames['CapFactorVRE'])
        # self.declare_set('H2Price_index', all_frames['H2Price'])

        # These are now broken out in the ModelSets class:

        # for tss in setA.tech_subset_names:
        #     # create the technology subsets based on the tech_subsets input
        #     self.declare_set(tss, getattr(setA, tss))

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            pass
            # self.declare_set('capacity_builds_index', all_frames['CapCost'])
            # self.declare_set('FOMCost_index', all_frames['FOMCost'])
            # self.declare_set('Build_index', setA.Build_index)
            # self.declare_set('CapacityCredit_index', all_frames['CapacityCredit'])
            # self.declare_set('capacity_retirements_index', setA.capacity_retirements_index)

        # if capacity expansion and learning are on
        # this block of code demonstrates the application of the switch option,
        # but in general we found it easier to read if we continued to use if statements
        if elec_config.expansion_learning_type in {
            ExpansionLearningType.LINEAR,
            ExpansionLearningType.NONLINEAR,
        }:
            pass
            # self.declare_set(
            #     'LearningRate_index',
            #     all_frames['LearningRate'],
            #     switch=elec_config.capacity_expansion,
            # )
            # self.declare_set(
            #     'CapCostInitial_index',
            #     all_frames['CapCostInitial'],
            #     switch=elec_config.capacity_expansion,
            # )
            # self.declare_set(
            #     'SupplyCurveLearning_index',
            #     all_frames['SupplyCurveLearning'],
            #     switch=elec_config.capacity_expansion,
            # )

        # if trade operation is on
        # if elec_config.regional_exchange:
        #     self.declare_set('TranCost_index', all_frames['TranCost'])
        #     self.declare_set('TranLimit_index', all_frames['TranLimit'])
        #     self.declare_set('trade_interregional_index', setA.trade_interregional_index)
        #     self.declare_set('TranCostInt_index', all_frames['TranCostInt'])
        #     self.declare_set('TranLimitInt_index', all_frames['TranLimitGenInt'])
        #     self.declare_set('trade_interational_index', setA.trade_interational_index)
        #     self.declare_set('TranLineLimitInt_index', all_frames['TranLimitCapInt'])
        #
        # # if ramping requirements are on
        # if elec_config.ramping_required:
        #     self.declare_set('RampUpCost_index', all_frames['RampUpCost'])
        #     self.declare_set('RampRate_index', all_frames['RampRate'])
        #     self.declare_set('generation_ramp_index', setA.generation_ramp_index)
        #
        # # if operating reserve requirements are on
        # if elec_config.spinning_reserve_required:
        #     self.declare_set('restypes', setA.restypes)
        #     self.declare_set('reserves_procurement_index', setA.reserves_procurement_index)
        #     self.declare_set('RegReservesCost_index', all_frames['RegReservesCost'])
        #     self.declare_set('ResTechUpperBound_index', all_frames['ResTechUpperBound'])

        ###########################################################################################
        # Parameters

        # convenience renamings to get the dataframe/dict piece from the param data:
        all_frames = param_data.param_frames
        all_dicts = param_data.param_dicts

        # temporal parameters
        if common_config.aggregate_years:
            self.y0 = pyo.Param(initialize=common_config.aggregate_start_year)
        self.num_hr_day = pyo.Param(initialize=setA.num_hr_day)
        self.MapHourSeason = pyo.Param(self.hour, initialize=all_frames['MapHourSeason'])
        self.MapHourDay = pyo.Param(self.hour, initialize=all_frames['MapHourDay']['day'])

        self.WeightYear = pyo.Param(self.year, initialize=all_frames['WeightYear'])

        self.WeightHour = pyo.Param(self.hour, initialize=all_frames['WeightHour']['WeightHour'])
        self.WeightDay = pyo.Param(self.day, initialize=all_frames['WeightDay'])
        self.WeightSeason = pyo.Param(self.season, initialize=all_frames['WeightSeason'])

        # self.declare_param('y0', None, setA.start_year)
        # self.declare_param('num_hr_day', None, setA.num_hr_day)
        # self.declare_param('MapHourSeason', self.hour, all_frames['MapHourSeason'])
        # self.declare_param('MapHourDay', self.hour, all_frames['MapHourDay']['day'])
        # self.declare_param('WeightYear', self.year, all_frames['WeightYear'])
        # self.declare_param('WeightHour', self.hour, all_frames['WeightHour']['WeightHour'])
        # self.declare_param('WeightDay', self.day, all_frames['WeightDay'])
        # self.declare_param('WeightSeason', self.season, all_frames['WeightSeason'])

        # load and technology parameters
        # dev note:  set a default of 0.0 for all missing values, so that we can iterate over r, y, hr confidently
        self.Load = pyo.Param(
            self.region_analyze,
            self.year,
            self.hour,
            initialize=all_frames['Load'],
            within=pyo.NonNegativeReals,
            default=0.0,
        )

        self.UnmetLoadPenalty = pyo.Param(initialize=UNMET_LOAD_PRICE)

        # dev note: A missing price value (sparse set) will cause fail w/o a default value here,
        #           which is OK
        self.SupplyPrice = pyo.Param(
            self.region_analyze,
            self.season,
            self.tech,
            self.step,
            self.year,
            initialize=all_frames['supply_price'],
            within=pyo.NonNegativeReals,
        )

        # dev note: We do not supply a built index set here, so we should iterate over the
        #           param keys where needed
        self.SupplyCurve = pyo.Param(
            self.region_analyze,
            self.season,
            self.tech,
            self.step,
            self.year,
            initialize=all_frames['supply_curve'],
            within=pyo.NonNegativeReals,
        )
        # dev note:  a default of 0.0 is supplied because the indexing set is larger than the
        #            upper bound limit from the data
        self.CapFactorVRE = pyo.Param(
            self.tech_vre,
            self.year,
            self.region_analyze,
            self.step,
            self.hour,
            initialize=all_frames['cap_factor_vre'],
            within=pyo.NonNegativeReals,
            default=0.0,  # TODO:  Remove this hack when data is fixed.  Used now to "make it run for MIA values"
        )
        self.HydroCapFactor = pyo.Param(
            self.region_analyze,
            self.season,
            initialize=all_dicts['hydro_cap_factor'],
            within=pyo.NonNegativeReals,
        )
        self.BatteryEfficiency = pyo.Param(
            self.tech_stor, initialize=all_dicts['battery_efficiency'], within=pyo.NonNegativeReals
        )
        self.HourstoBuy = pyo.Param(
            self.tech_stor, initialize=all_dicts['hours_to_buy'], within=pyo.NonNegativeReals
        )
        self.H2Price = pyo.Param(
            self.region_analyze,
            self.season,
            self.tech_h2,
            self.step,
            self.year,
            initialize=all_frames['h2_price'],
            within=pyo.NonNegativeReals,
            mutable=True,
        )

        self.StorageLevelCost = pyo.Param(initialize=STORAGE_LEVEL_COST)

        self.H2Heatrate = pyo.Param(initialize=H2Heatrate)

        # self.declare_param('Load', self.demand_balance_index, all_frames['Load'], mutable=True)
        # self.declare_param('UnmetLoadPenalty', None, 500000)
        # self.declare_param('SupplyPrice', self.capacity_total_index, all_frames['SupplyPrice'])
        # self.declare_param('SupplyCurve', self.capacity_total_index, all_frames['SupplyCurve'])
        # self.declare_param('CapFactorVRE', self.generation_vre_ub_index, all_frames['CapFactorVRE'])
        # self.declare_param(
        #     'HydroCapFactor', self.HydroCapFactor_index, all_frames['HydroCapFactor']
        # )
        # self.declare_param('BatteryEfficiency', setA.T_stor, all_frames['BatteryEfficiency'])
        # self.declare_param('HourstoBuy', setA.T_stor, all_frames['HourstoBuy'])
        # self.declare_param('H2Price', self.H2Price_index, all_frames['H2Price'], mutable=True)
        # self.declare_param('StorageLevelCost', None, 0.00000001)
        # self.declare_param('H2Heatrate', None, setA.H2Heatrate)

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            self.declare_param('FOMCost', self.FOMCost_index, all_frames['FOMCost'])
            self.declare_param(
                'CapacityCredit', self.CapacityCredit_index, all_frames['CapacityCredit']
            )

            # if capacity expansion and learning are on
            if elec_config.expansion_learning_type is not ExpansionLearningType.DISABLED:
                self.declare_param(
                    'LearningRate', self.LearningRate_index, all_frames['LearningRate']
                )
                self.declare_param(
                    'CapCostInitial', self.CapCostInitial_index, all_frames['CapCostInitial']
                )
                self.declare_param(
                    'SupplyCurveLearning',
                    self.SupplyCurveLearning_index,
                    all_frames['SupplyCurveLearning'],
                )

            # if learning is not to be solved nonlinearly directly in the obj
            if elec_config.expansion_learning_type in {
                ExpansionLearningType.DISABLED,
                ExpansionLearningType.LINEAR,
            }:
                if elec_config.expansion_learning_type == ExpansionLearningType.DISABLED:
                    mute = False
                else:
                    mute = True
                self.declare_param(
                    'CapCostLearning',
                    self.capacity_builds_index,
                    all_frames['CapCost'],
                    mutable=mute,
                )

        # if trade operation is on
        if elec_config.regional_exchange:
            self.declare_param('TransLoss', None, setA.TransLoss)
            self.declare_param('TranCost', self.TranCost_index, all_frames['TranCost'])
            self.declare_param('TranLimit', self.TranLimit_index, all_frames['TranLimit'])
            self.declare_param('TranCostInt', self.TranCostInt_index, all_frames['TranCostInt'])
            self.declare_param(
                'TranLimitGenInt', self.TranLimitInt_index, all_frames['TranLimitGenInt']
            )
            self.declare_param(
                'TranLimitCapInt', self.TranLineLimitInt_index, all_frames['TranLimitCapInt']
            )

        # if reserve margin requirements are on
        if elec_config.reserve_margin_required:
            self.declare_param('ReserveMargin', self.region, all_frames['ReserveMargin'])

        # if ramping requirements are on
        if elec_config.ramping_required:
            self.declare_param('RampUpCost', self.RampUpCost_index, all_frames['RampUpCost'])
            self.declare_param('RampDownCost', self.RampUpCost_index, all_frames['RampDownCost'])
            self.declare_param('RampRate', self.RampRate_index, all_frames['RampRate'])

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.declare_param(
                'RegReservesCost', self.RegReservesCost_index, all_frames['RegReservesCost']
            )
            # TODO:  either declare the set of restypes or just use the enumeration ReserveType directly here
            self.declare_param(
                'ResTechUpperBound', self.ResTechUpperBound_index, all_frames['ResTechUpperBound']
            )

        ##########################
        # Cross-talk from H2 model
        self.FixedElecRequest = pyo.Param(
            self.region_analyze,
            self.year,
            domain=pyo.NonNegativeReals,
            initialize=0,
            mutable=True,
            doc='a known fixed request from H2',
        )
        self.var_elec_request = pyo.Var(
            self.region_analyze,
            self.year,
            domain=pyo.NonNegativeReals,
            initialize=0,
            doc='variable request from H2',
        )

        ###########################################################################################
        # TODO: Example future model concept
        # Note: the goal would be to eventually reorganize the preprocessor so that most data would
        # fit something similar to this example structure below.
        #
        # self.var_switch_dict = {
        #     'capacity_builds': elec_config.capacity_expansion,
        #     'capacity_retirements': elec_config.capacity_expansion,  # TODO:  this should be retirement capable?
        # }
        #
        # for var in self.var_switch_dict.keys():
        #     # self.declare_var(var, getattr(self, var + '_index'), switch=self.var_switch_dict[var])
        #     pass

        ###########################################################################################
        # Variables

        # Generation, capacity, and technology variables
        self.generation_total = pyo.Var(setA.generation_index, within=pyo.NonNegativeReals)
        """tech, year, region, step, hour"""
        self.unmet_load = pyo.Var(
            self.region_analyze, self.year, self.hour, within=pyo.NonNegativeReals
        )
        self.capacity_total = pyo.Var(setA.capacity_index, within=pyo.NonNegativeReals)
        """region, season, tech, step, year"""
        self.storage_inflow = pyo.Var(setA.storage_index, within=pyo.NonNegativeReals)
        self.storage_outflow = pyo.Var(setA.storage_index, within=pyo.NonNegativeReals)
        self.storage_level = pyo.Var(setA.storage_index, within=pyo.NonNegativeReals)

        # helper

        # self.declare_var('generation_total', self.generation_total_index, )
        # self.declare_var('unmet_load', self.unmet_load_index)
        # self.declare_var('capacity_total', self.capacity_total_index)
        # self.declare_var('storage_inflow', self.Storage_index)
        # self.declare_var('storage_outflow', self.Storage_index)
        # self.declare_var('storage_level', self.Storage_index)

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            self.declare_var('capacity_builds', self.capacity_builds_index)
            self.declare_var('capacity_retirements', self.capacity_retirements_index)

        # if trade operation is on
        if elec_config.regional_exchange:
            self.declare_var('trade_interregional', self.trade_interregional_index)
            self.declare_var('trade_international', self.trade_interational_index)

        # if reserve margin constraints are on
        if elec_config.reserve_margin_required:
            self.declare_var('storage_avail_cap', self.Storage_index)

        # if ramping requirements are on
        if elec_config.ramping_required:
            self.declare_var('generation_ramp_up', self.generation_ramp_index)
            self.declare_var('generation_ramp_down', self.generation_ramp_index)

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.declare_var('reserves_procurement', self.reserves_procurement_index)

        ###########################################################################################
        # Objective Function

        # dev note: These 3 indexed sets are created above with the SETS portion of the model
        #           without the 'rule'
        # self.populate_by_hour_sets = pyo.BuildAction(rule=em.populate_by_hour_sets_rule)

        def dispatch_cost(self):
            """Dispatch cost (e.g., variable O&M cost) component for the objective function.

            Returns
            -------
            int
                Dispatch cost
            """
            return sum(
                self.WeightDay[self.MapHourDay[hr]]
                * (
                    sum(
                        self.WeightYear[y]
                        * self.SupplyPrice[(r, season, tech, step, y)]
                        * self.generation_total[(tech, y, r, step, hr)]
                        for (tech, y, r, step) in self.GenHour_index[hr]
                    )
                    + sum(
                        self.WeightYear[y]
                        * (
                            0.5
                            * self.SupplyPrice[(r, season, tech, step, y)]
                            * (
                                self.storage_inflow[(tech, y, r, step, hr)]
                                + self.storage_outflow[(tech, y, r, step, hr)]
                            )
                            + (self.WeightHour[hr] * self.StorageLevelCost)
                            * self.storage_level[(tech, y, r, step, hr)]
                        )
                        for (tech, y, r, step) in self.StorageHour_index[hr]
                    )
                    # dimensional analysis for cost:
                    # $/kg * kg/Gwh * Gwh = $
                    # so we need 1/heatrate for kg/Gwh
                    + sum(
                        self.WeightYear[y]
                        * self.H2Price[r, season, tech, step, y]
                        / self.H2Heatrate
                        * self.generation_total[(tech, y, r, 1, hr)]
                        for (tech, y, r, step) in self.H2GenHour_index[hr]
                    )
                )
                for hr in self.hour
                if (season := self.MapHourSeason[hr])
            )

        self.dispatch_cost = pyo.Expression(expr=dispatch_cost)

        def unmet_load_cost(self):
            """Unmet load cost component for the objective function. Should equal zero.

            Returns
            -------
            int
                Unmet load cost
            """
            return sum(
                self.WeightDay[self.MapHourDay[hr]]
                * self.WeightYear[y]
                * self.unmet_load[(r, y, hr)]
                * self.UnmetLoadPenalty
                for (r, y, hr) in self.Load
                if r in self.region_analyze
            )

        self.unmet_load_cost = pyo.Expression(expr=unmet_load_cost)

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            # TODO: choosing summer for capacity, may want to revisit this, fix hard coded value
            def fixed_om_cost(self):
                """Fixed operation and maintenance (FOM) cost component for the objective function.

                Returns
                -------
                int
                    FOM cost component
                """
                return sum(
                    self.WeightYear[y]
                    * self.FOMCost[(r, tech, step)]
                    * self.capacity_total[(r, season, tech, step, y)]
                    for (r, season, tech, step, y) in self.capacity_total_index
                    if season == 2
                )

            self.fixed_om_cost = pyo.Expression(expr=fixed_om_cost)

            # nonlinear expansion costs
            if elec_config.expansion_learning_type == ExpansionLearningType.NONLINEAR:

                def capacity_expansion_cost(self):
                    """Capacity expansion cost component for the objective function if
                    learning switch is set to nonlinear option.

                    Returns
                    -------
                    int
                        Capacity expansion cost component (nonlinear learning)
                    """
                    return sum(
                        (
                            self.CapCostInitial[(r, tech, step)]
                            * (
                                (
                                    (
                                        self.SupplyCurveLearning[tech]
                                        + 0.0001 * (y - self.y0)
                                        + sum(
                                            sum(
                                                self.capacity_builds[(r, tech, year, step)]
                                                for year in self.year
                                                if year < y
                                            )
                                            for (r, t, step) in self.CapCostInitial_index
                                            if t == tech
                                        )
                                    )
                                    / self.SupplyCurveLearning[tech]
                                )
                                ** (-1.0 * self.LearningRate[tech])
                            )
                        )
                        * self.capacity_builds[(r, tech, y, step)]
                        for (r, tech, y, step) in self.capacity_builds_index
                    )

                self.capacity_expansion_cost = pyo.Expression(expr=capacity_expansion_cost)

            # linear expansion costs
            else:

                def capacity_expansion_cost(self):
                    """Capacity expansion cost component for the objective function if
                    learning switch is set to linear option.

                    Returns
                    -------
                    int
                        Capacity expansion cost component (linear learning)
                    """
                    return sum(
                        self.CapCostLearning[(r, tech, y, step)]
                        * self.capacity_builds[(r, tech, y, step)]
                        for (r, tech, y, step) in self.capacity_builds_index
                    )

                self.capacity_expansion_cost = pyo.Expression(expr=capacity_expansion_cost)

        # if trade operation is on
        if elec_config.regional_exchange:

            def trade_cost(self):
                """Interregional and international trade cost component for the objective function.

                Returns
                -------
                int
                    Interregional trade cost component
                """
                return sum(
                    self.WeightDay[self.MapHourDay[hr]]
                    * self.WeightYear[y]
                    * self.trade_interregional[(r, r1, y, hr)]
                    * self.TranCost[(r, r1, y)]
                    for (r, r1, y, hr) in self.trade_interregional_index
                ) + sum(
                    self.WeightDay[self.MapHourDay[hr]]
                    * self.WeightYear[y]
                    * self.trade_international[(r, R_int, y, step, hr)]
                    * self.TranCostInt[(r, R_int, step, y)]
                    for (r, R_int, y, step, hr) in self.trade_interational_index
                )

            self.trade_cost = pyo.Expression(expr=trade_cost)

        # if ramping requirements are on
        if elec_config.ramping_required:

            def ramp_cost(self):
                """Ramping cost component for the objective function.

                Returns
                -------
                int
                    Ramping cost component
                """
                return sum(
                    self.WeightDay[self.MapHourDay[hr]]
                    * self.WeightYear[y]
                    * (
                        self.generation_ramp_up[(T_conv, y, r, step, hr)] * self.RampUpCost[T_conv]
                        + self.generation_ramp_down[(T_conv, y, r, step, hr)]
                        * self.RampDownCost[T_conv]
                    )
                    for (T_conv, y, r, step, hr) in self.generation_ramp_index
                )

            self.ramp_cost = pyo.Expression(expr=ramp_cost)

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:

            def operating_reserves_cost(self):
                """Operating reserve cost component for the objective function.

                Returns
                -------
                int
                    Operating reserve cost component
                """
                return sum(
                    (self.RegReservesCost[tech] if restype == 'regulation' else 0.01)
                    * self.WeightDay[self.MapHourDay[hr]]
                    * self.WeightYear[y]
                    * self.reserves_procurement[(restype, tech, y, r, step, hr)]
                    for (restype, tech, y, r, step, hr) in self.reserves_procurement_index
                )

            self.operating_reserves_cost = pyo.Expression(expr=operating_reserves_cost)

        # Final Objective Function
        def electricity_objective_function(self):
            """Objective function, objective is to minimize costs to the electric power system.

            Returns
            -------
            int
                Objective function
            """
            return (
                self.dispatch_cost
                + self.unmet_load_cost
                + (self.ramp_cost if elec_config.ramping_required else 0)
                + (self.trade_cost if elec_config.regional_exchange else 0)
                + (
                    self.capacity_expansion_cost + self.fixed_om_cost
                    if elec_config.capacity_expansion
                    else 0
                )
                + (self.operating_reserves_cost if elec_config.spinning_reserve_required else 0)
            )

        self.total_cost = pyo.Objective(rule=electricity_objective_function, sense=pyo.minimize)

        ###########################################################################################
        # Constraints

        self.sw_trade = elec_config.regional_exchange  # TODO:  temporary fix as the rule needs this

        # below is handled in indexed set creation at top (still incomplete)
        # self.populate_demand_balance_sets = pyo.BuildAction(
        #     rule=em.populate_demand_balance_sets_rule
        # )

        # Property: ShadowPrice
        @self.Constraint(self.region_analyze, self.year, self.hour)
        def demand_balance(self, r, y, hr):
            """Demand balance constraint where Load <= Generation.

            Parameters
            ----------
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            hr : pyomo.core.base.set.OrderedScalarSet
                time segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Demand balance constraint
            """
            return self.Load[(r, y, hr)] <= sum(
                self.generation_total[(tech, y, r, step, hr)]
                for (tech, step) in self.GenSetDemandBalance[(y, r, hr)]
            ) + sum(
                self.storage_outflow[(tech, y, r, step, hr)]
                - self.storage_inflow[(tech, y, r, step, hr)]
                for (tech, step) in self.StorageSetDemandBalance[(y, r, hr)]
            ) + self.unmet_load[(r, y, hr)] + (
                sum(
                    self.trade_interregional[(r, r1, y, hr)] * (1 - self.TransLoss)
                    - self.trade_interregional[(r1, r, y, hr)]
                    for (r1) in self.TradeSetDemandBalance[(y, r, hr)]
                )
                if elec_config.regional_exchange and r in self.region_trade
                else 0
            ) + (
                sum(
                    self.trade_international[(r, R_int, y, step, hr)] * (1 - self.TransLoss)
                    for (R_int, step) in self.TradeCanSetDemandBalance[(y, r, hr)]
                )
                if (elec_config.regional_exchange and r in self.region_int_trade)
                else 0
            )

        # #First hour
        @self.Constraint(self.storage_first_hour_balance_index)
        def storage_first_hour_balance(self, T_stor, y, r, step, hr1):
            """Storage balance constraint for the first hour time-segment in each day-type where
            Storage level == Storage level (in final hour time-segment in current day-type)
                            + Storage inflow * Battery efficiency
                            - Storage outflow

            Parameters
            ----------
            T_stor : pyomo.core.base.set.OrderedScalarSet
                storage technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr1 : pyomo.core.base.set.OrderedScalarSet
                set containing first hour time-segment in each day-type

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage balance constraint for the first hour time-segment in each day-type
            """
            return (
                self.storage_level[(T_stor, y, r, step, hr1)]
                == self.storage_level[(T_stor, y, r, step, hr1 + self.num_hr_day - 1)]
                + self.BatteryEfficiency[T_stor] * self.storage_inflow[(T_stor, y, r, step, hr1)]
                - self.storage_outflow[(T_stor, y, r, step, hr1)]
            )

        # #Not first hour
        @self.Constraint(self.storage_most_hours_balance_index)
        def storage_most_hours_balance(self, T_stor, y, r, step, hr23):
            """Storage balance constraint for the time-segment in each day-type other than
            the first hour time-segment where
            Storage level == Storage level (in previous hour time-segment)
                            + Storage inflow * Battery efficiency
                            - Storage outflow

            Parameters
            ----------
            T_stor : pyomo.core.base.set.OrderedScalarSet
                storage technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr23 : pyomo.core.base.set.OrderedScalarSet
                set containing time-segment except first hour in each day-type

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage balance constraint for the time-segment in each day-type other than
            the first hour time-segment
            """
            return (
                self.storage_level[(T_stor, y, r, step, hr23)]
                == self.storage_level[(T_stor, y, r, step, hr23 - 1)]
                + self.BatteryEfficiency[T_stor] * self.storage_inflow[(T_stor, y, r, step, hr23)]
                - self.storage_outflow[(T_stor, y, r, step, hr23)]
            )

        self.populate_hydro_sets = pyo.BuildAction(rule=em.populate_hydro_sets_rule)

        @self.Constraint(self.capacity_hydro_ub_index)
        def capacity_hydro_ub(self, T_hydro, y, r, season):
            """hydroelectric generation seasonal upper bound where
            Hydo generation <= Hydo capacity * Hydro capacity factor

            Parameters
            ----------
            T_hydro : pyomo.core.base.set.OrderedScalarSet
                hydro technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            season : pyomo.core.base.set.OrderedScalarSet
                season set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                hydroelectric generation seasonal upper bound
            """
            return (
                sum(
                    self.generation_total[T_hydro, y, r, 1, hr]
                    * self.WeightDay[self.MapHourDay[hr]]
                    for hr in self.HourSeason_index[season]
                )
                <= self.capacity_total[(r, season, T_hydro, 1, y)]
                * self.HydroCapFactor[r, season]
                * self.WeightSeason[season]
            )

        @self.Constraint(self.generation_dispatchable_ub_index)
        def generation_dispatchable_ub(self, T_disp, y, r, step, hr):
            """Dispatchable generation upper bound where
            Dispatchable generation + reserve procurement <= capacity * capacity factor

            Parameters
            ----------
            T_disp : pyomo.core.base.set.OrderedScalarSet
                dispatchable technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr : pyomo.core.base.set.OrderedScalarSet
                time-segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Dispatchable generation upper bound
            """
            return (
                self.generation_total[(T_disp, y, r, step, hr)]
                + (
                    sum(
                        self.reserves_procurement[(restype, T_disp, y, r, step, hr)]
                        for restype in self.restypes
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[(r, self.MapHourSeason[hr], T_disp, step, y)]
                * self.WeightHour[hr]
            )

        @self.Constraint(self.generation_hydro_ub_index)
        def generation_hydro_ub(self, T_hydro, y, r, step, hr):
            """Hydroelectric generation upper bound where
            Hydroelectric generation + reserve procurement <= capacity * capacity factor

            Parameters
            ----------
            T_hydro : pyomo.core.base.set.OrderedScalarSet
                hydro technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr : pyomo.core.base.set.OrderedScalarSet
                time-segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Hydroelectric generation upper bound
            """
            return (
                self.generation_total[(T_hydro, y, r, step, hr)]
                + sum(
                    self.reserves_procurement[(restype, T_hydro, y, r, step, hr)]
                    for restype in self.restypes
                )
                if elec_config.spinning_reserve_required
                else 0
            ) <= self.capacity_total[
                (r, self.MapHourSeason[hr], T_hydro, step, y)
            ] * self.HydroCapFactor[(r, self.MapHourSeason[hr])] * self.WeightHour[hr]

        @self.Constraint(self.generation_vre_ub_index)
        def generation_vre_ub(self, T_vre, y, r, step, hr):
            """Intermittent generation upper bound where
            Intermittent generation + reserve procurement <= capacity * capacity factor

            Parameters
            ----------
            T_vre : pyomo.core.base.set.OrderedScalarSet
                intermittent technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr : pyomo.core.base.set.OrderedScalarSet
                time-segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                intermittent generation upper bound
            """
            return (
                self.generation_total[(T_vre, y, r, step, hr)]
                + (
                    sum(
                        self.reserves_procurement[(restype, T_vre, y, r, step, hr)]
                        for restype in self.restypes
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[(r, self.MapHourSeason[hr], T_vre, step, y)]
                * self.CapFactorVRE[(T_vre, y, r, step, hr)]
                * self.WeightHour[hr]
            )

        # TODO:  internalize this set from the inputs ?   maybe?
        @self.Constraint(setA.storage_index)
        def storage_inflow_ub(self, tech, y, r, step, hr):
            """Storage inflow upper bound where
            Storage inflow <= Storage Capacity

            Parameters
            ----------
            tech : pyomo.core.base.set.OrderedScalarSet
                technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr : pyomo.core.base.set.OrderedScalarSet
                time-segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage inflow upper bound
            """
            return (
                self.storage_inflow[(tech, y, r, step, hr)]
                <= self.capacity_total[(r, self.MapHourSeason[hr], tech, step, y)]
                * self.WeightHour[hr]
            )

        # TODO:  internalize this set from the inputs ?   maybe?

        # TODO check if it's only able to build in regions with existing capacity?
        @self.Constraint(setA.storage_index)
        def storage_outflow_ub(self, tech, y, r, step, hr):
            """Storage outflow upper bound where
            Storage outflow <= Storage Capacity

            Parameters
            ----------
            tech : pyomo.core.base.set.OrderedScalarSet
                technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr : pyomo.core.base.set.OrderedScalarSet
                time-segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage outflow upper bound
            """
            return (
                self.storage_outflow[(tech, y, r, step, hr)]
                + (
                    sum(
                        self.reserves_procurement[(restype, tech, y, r, step, hr)]
                        for restype in self.restypes
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[(r, self.MapHourSeason[hr], tech, step, y)]
                * self.WeightHour[hr]
            )

        # TODO:  internalize this set from the inputs ?   maybe?
        @self.Constraint(setA.storage_index)
        def storage_level_ub(self, tech, y, r, step, hr):
            """Storage level upper bound where
            Storage level <= Storage power capacity * storage energy capacity

            Parameters
            ----------
            tech : pyomo.core.base.set.OrderedScalarSet
                technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr : pyomo.core.base.set.OrderedScalarSet
                time-segment set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage level upper bound
            """
            return (
                self.storage_level[(tech, y, r, step, hr)]
                <= self.capacity_total[(r, self.MapHourSeason[hr], tech, step, y)]
                * self.HourstoBuy[(tech)]
            )

        # TODO:  internalize this set from the inputs ?   maybe?
        @self.Constraint(setA.capacity_index)
        def capacity_balance(self, r, season, tech, step, y):
            """Capacity Equality constraint where
            Capacity = Operating Capacity
                      + New Builds Capacity
                      - Retired Capacity

            Parameters
            ----------
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            season : pyomo.core.base.set.OrderedScalarSet
                season set
            tech : pyomo.core.base.set.OrderedScalarSet
                technology set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            y : pyomo.core.base.set.OrderedScalarSet
                year set

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Capacity Equality

            """
            return self.capacity_total[(r, season, tech, step, y)] == self.SupplyCurve[
                (r, season, tech, step, y)
            ] + (
                sum(self.capacity_builds[(r, tech, year, step)] for year in self.year if year <= y)
                if elec_config.capacity_expansion and (tech, step) in self.Build_index
                else 0
            ) - (
                sum(
                    self.capacity_retirements[(tech, year, r, step)]
                    for year in self.year
                    if year <= y
                )
                if elec_config.capacity_expansion
                and (tech, y, r, step) in self.capacity_retirements_index
                else 0
            )

        # if capacity expansion is on
        if elec_config.capacity_expansion:

            @self.Constraint(self.capacity_retirements_index)
            def capacity_retirements_ub(self, tech, y, r, step):
                """Retirement upper bound where
                Capacity Retired <= Operating Capacity
                                   + New Builds Capacity
                                   - Retired Capacity

                Parameters
                ----------
                tech : pyomo.core.base.set.OrderedScalarSet
                    technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Retirement upper bound
                """
                return self.capacity_retirements[(tech, y, r, step)] <= (
                    (
                        self.SupplyCurve[(r, 2, tech, step, y)]
                        if (r, 2, tech, step, y) in self.capacity_total_index
                        else 0
                    )
                    + (
                        sum(
                            self.capacity_builds[(r, tech, year, step)]
                            for year in self.year
                            if year < y
                        )
                        if (tech, step) in self.Build_index
                        else 0
                    )
                    - sum(
                        self.capacity_retirements[(tech, year, r, step)]
                        for year in self.year
                        if year < y
                    )
                )

        # if trade operation is on
        if elec_config.regional_exchange and len(self.TranLineLimitInt_index) != 0:
            self.populate_trade_sets = pyo.BuildAction(rule=em.populate_trade_sets_rule)

            @self.Constraint(self.TranLineLimitInt_index)
            def trade_interational_capacity_ub(self, r, R_int, y, hr):
                """International interregional trade upper bound where
                Interregional Trade <= Interregional Transmission Capabilities * Time

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                R_int : pyomo.core.base.set.OrderedScalarSet
                    international region set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    International interregional trade capacity upper bound
                """
                return (
                    sum(
                        self.trade_international[(r, R_int, y, c, hr)]
                        for c in self.TradeCanLineSetUpper[(r, R_int, y, hr)]
                    )
                    <= self.TranLimitCapInt[(r, R_int, y, hr)] * self.WeightHour[hr]
                )

            @self.Constraint(self.TranLimitInt_index)
            def trade_interational_generation_ub(self, R_int, step, y, hr):
                """International electricity supply upper bound where
                Interregional Trade <= Interregional Supply

                Parameters
                ----------
                R_int : pyomo.core.base.set.OrderedScalarSet
                    international region set
                step : pyomo.core.base.set.OrderedScalarSet
                    international trade supply curve step set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    International electricity supply upper bound
                """
                return (
                    sum(
                        self.trade_international[(r, R_int, y, step, hr)]
                        for r in self.TradeCanSetUpper[(R_int, y, step, hr)]
                    )
                    <= self.TranLimitGenInt[(R_int, step, y, hr)] * self.WeightHour[hr]
                )

            @self.Constraint(self.trade_interregional_index)
            def trade_domestic_ub(self, r, r1, y, hr):
                """Interregional trade upper bound where
                Interregional Trade <= Interregional Transmission Capabilities * Time

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                r1 : pyomo.core.base.set.OrderedScalarSet
                    region set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Interregional trade capacity upper bound
                """
                return (
                    self.trade_interregional[(r, r1, y, hr)]
                    <= self.TranLimit[(r, r1, self.MapHourSeason[hr], y)] * self.WeightHour[hr]
                )

        # if reserve margin requirements are on
        if elec_config.capacity_expansion and elec_config.reserve_margin_required:
            self.populate_RM_sets = pyo.BuildAction(rule=em.populate_RM_sets_rule)

            @self.Constraint(self.Load)
            def reserve_margin_lb(self, r, y, hr):
                """Reserve margin requirement where
                Load * Reserve Margin <= Capacity * Capacity Credit * Time

                # must meet reserve margin requirement
                # apply to every hour, a fraction above the final year's load
                # ReserveMarginReq <= sum(Max capacity in that hour)

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Reserve margin requirement
                """
                return self.Load[(r, y, hr)] * (1 + self.ReserveMargin[r]) <= self.WeightHour[
                    hr
                ] * sum(
                    (
                        self.CapacityCredit[(tech, y, r, step, hr)]
                        * (
                            self.storage_avail_cap[(tech, y, r, step, hr)]
                            if tech in self.T_stor
                            else self.capacity_total[(r, self.MapHourSeason[hr], tech, step, y)]
                        )
                    )
                    for (tech, step) in self.SupplyCurveRM[(y, r, self.MapHourSeason[hr])]
                )

            @self.Constraint(self.Storage_index)
            def reserve_margin_storage_avail_cap_ub(self, T_stor, y, r, step, hr):
                """Available storage power capacity for meeting reserve margin

                # ensure available capacity to meet RM for storage < power capacity

                Parameters
                ----------
                T_stor : pyomo.core.base.set.OrderedScalarSet
                    storage technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time-segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Available storage power capacity for meeting reserve margin
                """
                return (
                    self.storage_avail_cap[(T_stor, y, r, step, hr)]
                    <= self.capacity_total[(r, self.MapHourSeason[hr], T_stor, step, y)]
                )

            @self.Constraint(self.Storage_index)
            def reserve_margin_storage_avail_level_ub(self, T_stor, y, r, step, hr):
                """Available storage energy capacity for meeting reserve margin

                # ensure available capacity to meet RM for storage < existing SOC

                Parameters
                ----------
                T_stor : pyomo.core.base.set.OrderedScalarSet
                    storage technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time-segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Available storage energy capacity for meeting reserve margin
                """
                return (
                    self.storage_avail_cap[(T_stor, y, r, step, hr)]
                    <= self.storage_level[(T_stor, y, r, step, hr)]
                )

        # if ramping requirements are on
        if elec_config.ramping_required:

            @self.Constraint(self.ramp_first_hour_balance_index)
            def ramp_first_hour_balance(self, T_conv, y, r, step, hr1):
                """Ramp constraint for the first hour time-segment in each day-type where
                Generation == Generation (in final hour time-segment in current day-type)
                            + Ramp Up
                            - Ramp Down

                Parameters
                ----------
                T_conv : pyomo.core.base.set.OrderedScalarSet
                    conventional technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr1 : pyomo.core.base.set.OrderedScalarSet
                    set containing first hour time-segment in each day-type

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Ramp constraint for the first hour
                """
                return (
                    self.generation_total[(T_conv, y, r, step, hr1)]
                    == self.generation_total[(T_conv, y, r, step, hr1 + self.num_hr_day - 1)]
                    + self.generation_ramp_up[(T_conv, y, r, step, hr1)]
                    - self.generation_ramp_down[(T_conv, y, r, step, hr1)]
                )

            @self.Constraint(self.ramp_most_hours_balance_index)
            def ramp_most_hours_balance(self, T_conv, y, r, step, hr23):
                """Ramp constraint for the time-segment in each day-type other than
                the first hour time-segment where
                Generation == Generation (in previous hour time-segment)
                            + Ramp Up
                            - Ramp Down

                Parameters
                ----------
                T_conv : pyomo.core.base.set.OrderedScalarSet
                    conventional technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr23 : pyomo.core.base.set.OrderedScalarSet
                    set containing time-segment except first hour in each day-type

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Ramp constraint for the first hour
                """
                return (
                    self.generation_total[(T_conv, y, r, step, hr23)]
                    == self.generation_total[(T_conv, y, r, step, hr23 - 1)]
                    + self.generation_ramp_up[(T_conv, y, r, step, hr23)]
                    - self.generation_ramp_down[(T_conv, y, r, step, hr23)]
                )

            @self.Constraint(self.generation_ramp_index)
            def ramp_up_ub(self, T_conv, y, r, step, hr):
                """Ramp rate up upper constraint where
                Ramp Up <= Capaciry * Ramp Rate * Time

                Parameters
                ----------
                T_conv : pyomo.core.base.set.OrderedScalarSet
                    conventional technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Ramp rate up upper constraint
                """
                return (
                    self.generation_ramp_up[(T_conv, y, r, step, hr)]
                    <= self.WeightHour[hr]
                    * self.RampRate[T_conv]
                    * self.capacity_total[(r, self.MapHourSeason[hr], T_conv, step, y)]
                )

            @self.Constraint(self.generation_ramp_index)
            def ramp_down_ub(self, T_conv, y, r, step, hr):
                """Ramp rate down upper constraint where
                Ramp Up <= Capaciry * Ramp Rate * Time

                Parameters
                ----------
                T_conv : pyomo.core.base.set.OrderedScalarSet
                    conventional technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Ramp rate down upper constraint
                """
                return (
                    self.generation_ramp_down[(T_conv, y, r, step, hr)]
                    <= self.WeightHour[hr]
                    * self.RampRate[T_conv]
                    * self.capacity_total[(r, self.MapHourSeason[hr], T_conv, step, y)]
                )

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.populate_reserves_sets = pyo.BuildAction(rule=em.populate_reserves_sets_rule)

            @self.Constraint(self.Load)
            def reserve_requirement_spin_lb(self, r, y, hr):
                """Spinning reserve requirements (3% of load) where
                Spinning reserve procurement >= 0.03 * Load

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time-segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Spinning reserve requirements
                """
                return (
                    sum(
                        self.reserves_procurement[('spinning', tech, y, r, step, hr)]
                        for (tech, step) in self.ProcurementSetReserves[('spinning', r, y, hr)]
                    )
                    >= 0.03 * self.Load[(r, y, hr)]
                )

            @self.Constraint(self.Load)
            def reserve_requirement_reg_lb(self, r, y, hr):
                """Regulation Reserve Req (1% of load + 0.5% of wind gen + 0.3% of solar cap) where
                Reserves Requirement >= 0.01 * Load
                                      + 0.005 * Wind Gen
                                      + 0.003 * Solar Cap

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time-segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Regulation reserve requirement
                """
                return sum(
                    self.reserves_procurement[('regulation', tech, y, r, step, hr)]
                    for (tech, step) in self.ProcurementSetReserves[('regulation', r, y, hr)]
                ) >= 0.01 * self.Load[(r, y, hr)] + 0.005 * sum(
                    self.generation_total[(T_wind, y, r, step, hr)]
                    for (T_wind, step) in self.WindSetReserves[(y, r, hr)]
                ) + 0.003 * self.WeightHour[hr] * sum(
                    self.capacity_total[(r, self.MapHourSeason[hr], T_solar, step, y)]
                    for (T_solar, step) in self.SolarSetReserves[(y, r, hr)]
                )

            @self.Constraint(self.Load)
            def reserve_requirement_flex_lb(self, r, y, hr):
                """Flexible Reserve Requirement (10% of wind gen + 4% of solar cap) where
                Reserves Requirement >= 0.01 * Wind Gen
                                      + 0.04 * Solar Cap

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time-segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Flexible reserve requirement
                """
                return sum(
                    self.reserves_procurement[('flex', tech, y, r, step, hr)]
                    for (tech, step) in self.ProcurementSetReserves[('flex', r, y, hr)]
                ) >= +0.1 * sum(
                    self.generation_total[(T_wind, y, r, step, hr)]
                    for (T_wind, step) in self.WindSetReserves[(y, r, hr)]
                ) + 0.04 * self.WeightHour[hr] * sum(
                    self.capacity_total[(r, self.MapHourSeason[hr], T_solar, step, y)]
                    for (T_solar, step) in self.SolarSetReserves[(y, r, hr)]
                )

            # TODO:  Review this.  It operates on the x-product of tech x restype, yet many techs are
            #        not "reserve-able" so we could make the variable `reserve_procurement` more sparse
            #        and/or use defaults better.
            @self.Constraint(self.reserves_procurement_index)
            def reserve_procurement_ub(self, restypes, tech, y, r, step, hr):
                """Reserve Requirement Procurement Upper Bound where
                Reserve Procurement <= Capacity
                                    * Tech Reserve Contribution Share
                                    * Time

                Parameters
                ----------
                restypes : pyomo.core.base.set.OrderedScalarSet
                    reserve requirement type set
                tech : pyomo.core.base.set.OrderedScalarSet
                    technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr : pyomo.core.base.set.OrderedScalarSet
                    time segment set

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Reserve Requirement Procurement Upper Bound
                """
                return (
                    self.reserves_procurement[(restypes, tech, y, r, step, hr)]
                    <= self.ResTechUpperBound[(restypes, tech)]
                    * self.WeightHour[hr]
                    * self.capacity_total[(r, self.MapHourSeason[hr], tech, step, y)]
                )
