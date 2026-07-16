"""Electricity Model, a pyomo optimization model of the electric power sector.

The class is organized by sections: settings, sets, parameters, variables, objective function,
constraints, plus additional misc support functions.
"""

from collections import defaultdict
from logging import getLogger

import pyomo.environ as pyo

from src.common.common_config import CommonConfig
from src.models.electricity.constants import (
    REGULATION_RESERVE_PROPORTION,
    SOLAR_FLEX_RESERVE_PROPORTION,
    SOLAR_REGULATION_RESERVE_PROPORTION,
    SPINNING_RESERVE_DEFAULT_COST,
    SPINNING_RESERVE_PROPORTION,
    STORAGE_LEVEL_COST,
    TRANSMISSION_LOSS_FACTOR,
    UNMET_LOAD_PRICE,
    WIND_FLEX_RESERVE_PROPORTION,
    WIND_REGULATION_RESERVE_PROPORTION,
    H2Heatrate,
)
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType, ReserveType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.validators import region_check

# Establish logger
logger = getLogger(__name__)


class PowerModel(pyo.ConcreteModel):
    """A PowerModel instance."""

    def __init__(
        self,
        setA: ModelSets,
        param_data: ParamData,
        elec_config: ElecConfig,
        common_config: CommonConfig,
        *args,
        **kwargs,
    ):
        pyo.ConcreteModel.__init__(self, *args, **kwargs)

        # Sets

        # temporal sets
        self.hour = pyo.Set(initialize=setA.hour)
        self.hour_first = pyo.Set(initialize=setA.hour1, within=self.hour)
        self.hour_most = pyo.Set(initialize=setA.hour23, within=self.hour)
        self.day = pyo.Set(initialize=setA.day)
        self.season = pyo.Set(initialize=setA.season)
        self.year = pyo.Set(initialize=setA.year_map.values())

        # spatial sets
        self.region = pyo.Set(initialize=setA.region, validate=region_check)
        self.region_int = pyo.Set(initialize=setA.region_international, within=self.region)
        self.region_dom = pyo.Set(initialize=setA.region_domestic, within=self.region)
        self.region_analyze = pyo.Set(initialize=setA.region_analyze, within=self.region_dom)

        # technology sets
        self.tech = pyo.Set(initialize=setA.tech)
        self.step = pyo.Set(initialize=setA.step)  # TODO:  come back to this
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
        self.tech_buildable = pyo.Set(
            dimen=2, initialize=setA.tech_builds, within=self.tech * self.step
        )
        self.tech_retireable = pyo.Set(
            dimen=2, initialize=setA.tech_retires, within=self.tech * self.step
        )

        # CONSTRAINT INDEXING SETS
        self.storage_most_hours_balance_index = pyo.Set(
            initialize=setA.storage_most_hours_balance_index,
            within=self.tech_stor * self.year * self.region_analyze * self.step * self.hour_most,
        )
        self.storage_first_hour_balance_index = pyo.Set(
            initialize=setA.storage_first_hour_balance_index,
            within=self.tech_stor * self.year * self.region_analyze * self.step * self.hour_first,
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

        # Derivative reserve indexing sets...  # TODO:  a little clunky here.  Move to companion file as was done before?
        idx = defaultdict(list)
        wind_idx = defaultdict(list)
        solar_idx = defaultdict(list)
        for res_type, tech, year, region, step, hour in setA.reserves_procurement_index:
            idx[res_type, region, year, hour].append((tech, step))
            if tech in self.tech_wind:
                wind_idx[year, region, hour].append((tech, step))
            elif tech in self.tech_solar:
                solar_idx[year, region, hour].append((tech, step))

        # TODO:  Rename these 3 sets...they are all VRE... can we be more clear?
        self.ProcurementSetReserves = pyo.Set(
            set(t.value for t in ReserveType),
            self.region_analyze,
            self.year,
            self.hour,
            within=self.tech * self.step,
            initialize=idx,
        )
        self.WindSetReserves = pyo.Set(
            self.year,
            self.region_analyze,
            self.hour,
            within=self.tech_wind * self.step,
            initialize=wind_idx,
        )
        self.SolarSetReserves = pyo.Set(
            self.year,
            self.region_analyze,
            self.hour,
            within=self.tech_solar * self.step,
            initialize=solar_idx,
        )

        self.generation_vre_ub_index = pyo.Set(initialize=setA.generation_vre_ub_index)

        # international trade indices
        self.international_trade_index = pyo.Set(
            dimen=5,
            initialize=setA.international_trade_index,
            within=self.region_analyze * self.region_int * self.year * self.step * self.hour,
        )

        ################# Indexed sets

        # make an indexed set of storage (tech, year, region, step) indexed by hour
        self.StorageHour_index = pyo.Set(
            self.hour,
            initialize=setA.storage_hour_index,
            within=self.tech_stor * self.year * self.region_analyze * self.step,
        )

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

        # make a set of capacity sources indexed by year, region, season
        idx = defaultdict(list)
        for region, season, tech, step, year in setA.capacity_index:
            idx[year, region, season].append((tech, step))
        self.capacity_sources = pyo.Set(
            self.year,
            self.region_analyze,
            self.season,
            within=self.tech * self.step,
            initialize=idx,
        )

        # if capacity expansion is on
        if elec_config.capacity_expansion:

            def retireable(m, tech, _, __, step):
                """verify that the combination of tech-step is in the eligible set"""
                return (tech, step) in m.tech_retireable

            self.capacity_retirements_index = pyo.Set(
                dimen=4,
                within=self.tech * self.year * self.region_analyze * self.step,
                initialize=setA.retirement_index,
                validate=retireable,
            )

        # if capacity expansion and learning are on
        # this block of code demonstrates the application of the switch option,
        # but in general we found it easier to read if we continued to use if statements
        if elec_config.expansion_learning_type in {
            ExpansionLearningType.LINEAR,
            ExpansionLearningType.NONLINEAR,
        }:
            # TODO:  revisit this after no-learning working
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

        ###########################################################################################
        # Parameters

        # conveniences to get the dataframe/dict pieces from the param data:
        all_frames = param_data.param_frames
        all_dicts = param_data.param_dicts

        # temporal parameters
        self.y0_learning = pyo.Param(
            initialize=common_config.aggregate_start_year
        )  # TODO:  Separate this concept from aggregation
        self.num_hr_day = pyo.Param(initialize=setA.num_hr_day)
        # TODO:  Consider making these mappings just dictionaries.  They don't really "fit the mold"
        #        of a *numeric* parameter.  They are just simple LUTs
        self.MapHourSeason = pyo.Param(
            self.hour, initialize=all_frames['MapHourSeason'], within=pyo.Any
        )
        self.MapHourDay = pyo.Param(
            self.hour, initialize=all_frames['MapHourDay']['day'], within=pyo.Any
        )

        self.WeightYear = pyo.Param(self.year, initialize=all_frames['WeightYear'])

        self.WeightHour = pyo.Param(self.hour, initialize=all_frames['WeightHour']['WeightHour'])
        self.WeightDay = pyo.Param(self.day, initialize=all_frames['WeightDay'])
        self.WeightSeason = pyo.Param(self.season, initialize=all_frames['WeightSeason'])

        # load and technology parameters
        # dev note:  set a default of 0.0 for all missing values,
        #            so that we can iterate over r, y, hr confidently as they should all be defined
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
            default=0.0,  # TODO:  Needed to "make it work" with MIA values.  Decide if that is intended...
        )
        self.HydroCapFactor = pyo.Param(
            self.region_analyze,
            self.season,
            initialize=all_frames['hydro_cap_factor'],
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

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            self.FOMCost = pyo.Param(
                self.region_analyze, self.tech, self.step, initialize=all_dicts['fom_cost']
            )
            self.CapacityCredit = pyo.Param(
                self.tech,
                self.year,
                self.region_analyze,
                self.step,
                self.hour,
                initialize=all_frames['capacity_credit'],
            )

            # if capacity expansion and learning are on
            if elec_config.expansion_learning_type is not ExpansionLearningType.DISABLED:
                self.LearningRate = pyo.Param(self.tech, initialize=all_dicts['learning_rate'])
                self.CapCostInitial = pyo.Param(
                    self.region_analyze,
                    self.tech,
                    self.step,
                    initialize=all_dicts['cap_cost_initial'],
                )
                self.SupplyCurveLearning = pyo.Param(
                    self.tech, initialize=all_dicts['supply_curve_learning']
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
                self.CapCostLearning = pyo.Param(  # TODO:  Shouldn't this be named just "CapCost"?  regardless of learning?
                    self.region,
                    self.tech,
                    self.year,
                    self.step,
                    initialize=all_frames['cap_cost'],
                    mutable=mute,
                )

        # if trade operation is on
        if elec_config.regional_exchange:
            self.TranCost = pyo.Param(
                self.region_analyze,
                self.region_analyze,
                self.year,
                initialize=all_frames['tran_cost'],
            )
            self.TranLimit = pyo.Param(
                self.region_analyze,
                self.region_analyze,
                self.year,
                self.hour,
                initialize=all_frames['tran_limit'],
            )
            partners = defaultdict(list)
            for source_region, destination_region, year, hour in all_frames['tran_limit'].index:
                partners[year, source_region, hour].append(destination_region)
            self.regional_partners = pyo.Set(
                self.year,
                self.region_analyze,
                self.hour,
                initialize=lambda m, y, r, h: partners.get((y, r, h), []),
            )
            self.TranCostInt = pyo.Param(
                self.region_analyze,
                self.region_int,
                self.step,
                self.year,
                initialize=all_frames['tran_cost_int'],
            )
            self.TranLimitGenInt = pyo.Param(
                self.region_int,
                self.step,
                self.year,
                self.hour,
                initialize=all_frames['tran_limit_gen_int'],
            )
            self.TranLimitCapInt = pyo.Param(
                self.region_analyze,
                self.region_int,
                self.year,
                self.hour,
                initialize=all_frames['tran_limit_cap_int'],
            )
            # use the index to create reverse-lookup to make index of intl regions that are
            # connected to a domestic region
            partners = defaultdict(list)
            for region, region_int, year, step, hour in self.international_trade_index:
                partners[year, region, hour].append((region_int, step))
            self.international_partners = pyo.Set(
                self.year,
                self.region_analyze,
                self.hour,
                initialize=lambda m, y, r, h: partners.get((y, r, h), []),
            )

            # index of what steps are available by international connection
            viable_steps = defaultdict(list)
            for region, region_int, year, step, hour in self.international_trade_index:
                viable_steps[region, region_int, year, hour].append(step)
            self.viable_international_steps = pyo.Set(
                self.region_analyze,
                self.region_int,
                self.year,
                self.hour,
                initialize=lambda m, ra, ri, y, h: viable_steps.get((ra, ri, y, h), []),
            )
            domestic_destinations = defaultdict(list)
            for region, region_int, year, step, hour in self.international_trade_index:
                domestic_destinations[region_int, year, hour].append(region)
            # note: this does NOT depend on step, but there could be non-viable hours
            self.domestic_destinations = pyo.Set(
                self.region_int,
                self.year,
                self.hour,
                initialize=lambda m, r, y, h: domestic_destinations.get((r, y, h), []),
            )

        # if reserve margin requirements are on
        if elec_config.reserve_margin_required:
            self.ReserveMargin = pyo.Param(
                self.region_analyze, initialize=all_dicts['reserve_margin']
            )

        # if ramping requirements are on
        if elec_config.ramping_required:
            self.RampUpCost = pyo.Param(self.tech_conv, initialize=all_dicts['ramp_up_cost'])
            self.RampDownCost = pyo.Param(self.tech_conv, initialize=all_dicts['ramp_down_cost'])
            self.RampRate = pyo.Param(self.tech_conv, initialize=all_dicts['ramp_rate'])

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.RegReservesCost = pyo.Param(self.tech, initialize=all_dicts['reg_reserves_cost'])
            # note:  The data is cast to cover all combinations of ReserveType and Tech with 0's as appropriate
            self.ResTechUpperBound = pyo.Param(
                set(rt.value for rt in ReserveType),
                self.tech,
                initialize=all_dicts['res_tech_upper_bound'],
            )

        ##########################
        # Cross-talk from H2 model  # preserved as basis for expansion/ideas...?
        # TODO:  Extract these...not used
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

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            # TODO:  Review this creation of var index from parameter keys.  Done in a few spots, seems like best plan.
            # TODO:  Refactor the order of indices in these params.  They are same names, different order.
            self.capacity_builds = pyo.Var(
                list(self.CapCostLearning.keys()), within=pyo.NonNegativeReals
            )
            self.capacity_retirements = pyo.Var(
                self.capacity_retirements_index, within=pyo.NonNegativeReals
            )

        # if trade operation is on
        if elec_config.regional_exchange:
            # Interregional trade is limited by the TranLimit, so we can index with it
            self.trade_interregional = pyo.Var(
                list(self.TranLimit.keys()),
                within=pyo.NonNegativeReals,
            )
            self.trade_international = pyo.Var(
                self.international_trade_index,
                within=pyo.NonNegativeReals,
            )

        # if reserve margin constraints are on
        if elec_config.reserve_margin_required:
            self.storage_avail_cap = pyo.Var(setA.storage_index, within=pyo.NonNegativeReals)
            # self.declare_var('storage_avail_cap', self.Storage_index)

        # if ramping requirements are on
        if elec_config.ramping_required:
            self.generation_ramp_up = pyo.Var(
                self.generation_ramp_index, within=pyo.NonNegativeReals
            )
            self.generation_ramp_down = pyo.Var(
                self.generation_ramp_index, within=pyo.NonNegativeReals
            )
            # self.declare_var('generation_ramp_up', self.generation_ramp_index)
            # self.declare_var('generation_ramp_down', self.generation_ramp_index)

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.reserves_procurement = pyo.Var(
                self.reserves_procurement_index, within=pyo.NonNegativeReals
            )
            # self.declare_var('reserves_procurement', self.reserves_procurement_index)

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
                        * self.SupplyPrice[r, season, tech, step, y]
                        * self.generation_total[tech, y, r, step, hr]
                        for (tech, y, r, step) in self.GenHour_index[hr]
                    )
                    + sum(
                        self.WeightYear[y]
                        * (
                            0.5
                            * self.SupplyPrice[r, season, tech, step, y]
                            * (
                                self.storage_inflow[tech, y, r, step, hr]
                                + self.storage_outflow[tech, y, r, step, hr]
                            )
                            + (self.WeightHour[hr] * self.StorageLevelCost)
                            * self.storage_level[tech, y, r, step, hr]
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
                        * self.generation_total[tech, y, r, 1, hr]  # TODO:  Why the hardcode "1"?
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
                * self.unmet_load[r, y, hr]
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
                    * self.FOMCost[r, tech, step]
                    * self.capacity_total[r, season, tech, step, y]
                    for (r, season, tech, step, y) in self.capacity_total
                    if season
                    == '2'  # TODO:  hard coded summer.  Remove after simplifying capacity to non-seasonal
                )

            self.fixed_om_cost = pyo.Expression(expr=fixed_om_cost)

            # nonlinear expansion costs
            if elec_config.expansion_learning_type == ExpansionLearningType.NONLINEAR:
                # TODO:  Review after no-learning is done
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
                            self.CapCostInitial[r, tech, step]
                            * (
                                (
                                    (
                                        self.SupplyCurveLearning[tech]
                                        + 0.0001
                                        * (
                                            y - self.y0_learning
                                        )  # TODO:  investigate / pull out this hardcode (which seems very small)
                                        + sum(
                                            sum(
                                                self.capacity_builds[r, tech, year, step]
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
                        * self.capacity_builds[r, tech, y, step]
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
                        self.CapCostLearning[r, tech, y, step]
                        * self.capacity_builds[r, tech, y, step]
                        for (r, tech, y, step) in self.CapCostLearning
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
                    * self.trade_interregional[r, r1, y, hr]
                    * self.TranCost[r, r1, y]
                    for (r, r1, y, hr) in self.trade_interregional
                ) + sum(
                    self.WeightDay[self.MapHourDay[hr]]
                    * self.WeightYear[y]
                    * self.trade_international[r, R_int, y, step, hr]
                    * self.TranCostInt[r, R_int, step, y]
                    for (r, R_int, y, step, hr) in self.international_trade_index
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
                        self.generation_ramp_up[T_conv, y, r, step, hr] * self.RampUpCost[T_conv]
                        + self.generation_ramp_down[T_conv, y, r, step, hr]
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
                    # TODO:  Review the odd 0.01 cost here for spinning/flex
                    (
                        self.RegReservesCost[tech]
                        if restype == ReserveType.REGULATION
                        else SPINNING_RESERVE_DEFAULT_COST
                    )
                    * self.WeightDay[self.MapHourDay[hr]]
                    * self.WeightYear[y]
                    * self.reserves_procurement[restype, tech, y, r, step, hr]
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
            # TODO:  Clean up the double-conditionals here and make the cost zero where created
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

        # self.sw_trade = elec_config.regional_exchange  # Only needed by rule (commented out) below

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
            return self.Load[r, y, hr] <= sum(
                self.generation_total[tech, y, r, step, hr]
                for (tech, step) in self.GenSetDemandBalance[y, r, hr]
            ) + sum(
                self.storage_outflow[tech, y, r, step, hr]
                - self.storage_inflow[tech, y, r, step, hr]
                for (tech, step) in self.StorageSetDemandBalance[y, r, hr]
            ) + self.unmet_load[r, y, hr] + (
                sum(
                    # TODO:  Sauleh, review this.  Seems transmission loss should apply to what
                    #        region "r" receives not what it sends out...?
                    self.trade_interregional[r, r1, y, hr] * (1 - TRANSMISSION_LOSS_FACTOR)
                    - self.trade_interregional[r1, r, y, hr]
                    for (r1) in self.regional_partners[y, r, hr]
                )
                # note:  don't need to check "region_trade" as the lookup in partners could be empty
                if elec_config.regional_exchange  # and r in self.region_trade
                else 0
            ) + (
                sum(
                    self.trade_international[r, R_int, y, step, hr] * (1 - TRANSMISSION_LOSS_FACTOR)
                    for (R_int, step) in self.international_partners[y, r, hr]
                )
                if elec_config.regional_exchange
                else 0
            )

        # First hour
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
                self.storage_level[T_stor, y, r, step, hr1]
                == self.storage_level[T_stor, y, r, step, hr1 + self.num_hr_day - 1]
                + self.BatteryEfficiency[T_stor] * self.storage_inflow[T_stor, y, r, step, hr1]
                - self.storage_outflow[T_stor, y, r, step, hr1]
            )

        # Not first hour
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
                self.storage_level[T_stor, y, r, step, hr23]
                == self.storage_level[T_stor, y, r, step, hr23 - 1]
                + self.BatteryEfficiency[T_stor] * self.storage_inflow[T_stor, y, r, step, hr23]
                - self.storage_outflow[T_stor, y, r, step, hr23]
            )

        # self.populate_hydro_sets = pyo.BuildAction(rule=em.populate_hydro_sets_rule)

        # quick reverse lookup
        idx = defaultdict(list)
        for hour, season in self.MapHourSeason.items():
            idx[season].append(hour)
        self.hour_season_index = pyo.Set(self.season, initialize=idx)

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
                    self.generation_total[T_hydro, y, r, 1, hr]  # TODO:  Why the hardcode step=1 ?
                    * self.WeightDay[self.MapHourDay[hr]]
                    for hr in self.hour_season_index[season]
                )
                <= self.capacity_total[r, season, T_hydro, 1, y]
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
                self.generation_total[T_disp, y, r, step, hr]
                + (
                    sum(
                        self.reserves_procurement[restype.value, T_disp, y, r, step, hr]
                        for restype in ReserveType
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, self.MapHourSeason[hr], T_disp, step, y]
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
                self.generation_total[T_hydro, y, r, step, hr]
                + sum(
                    self.reserves_procurement[restype.value, T_hydro, y, r, step, hr]
                    for restype in ReserveType
                )
                if elec_config.spinning_reserve_required
                else 0
            ) <= self.capacity_total[
                (r, self.MapHourSeason[hr], T_hydro, step, y)
            ] * self.HydroCapFactor[r, self.MapHourSeason[hr]] * self.WeightHour[hr]

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
                self.generation_total[T_vre, y, r, step, hr]
                + (
                    sum(
                        self.reserves_procurement[restype.value, T_vre, y, r, step, hr]
                        for restype in ReserveType
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, self.MapHourSeason[hr], T_vre, step, y]
                * self.CapFactorVRE[T_vre, y, r, step, hr]
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
                self.storage_inflow[tech, y, r, step, hr]
                <= self.capacity_total[r, self.MapHourSeason[hr], tech, step, y]
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
                self.storage_outflow[tech, y, r, step, hr]
                + (
                    sum(
                        self.reserves_procurement[restype.value, tech, y, r, step, hr]
                        for restype in ReserveType
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, self.MapHourSeason[hr], tech, step, y]
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
                self.storage_level[tech, y, r, step, hr]
                <= self.capacity_total[r, self.MapHourSeason[hr], tech, step, y]
                * self.HourstoBuy[tech]
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
            return self.capacity_total[r, season, tech, step, y] == self.SupplyCurve[
                (r, season, tech, step, y)
            ] + (
                sum(self.capacity_builds[r, tech, year, step] for year in self.year if year <= y)
                if elec_config.capacity_expansion and (tech, step) in self.tech_buildable
                else 0
            ) - (
                sum(
                    self.capacity_retirements[tech, year, r, step]
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
                # TODO:  remove the hard-coded 2 after we trim season out of SupplyCurve
                return self.capacity_retirements[tech, y, r, step] <= (
                    (
                        self.SupplyCurve[r, '2', tech, step, y]
                        if (r, '2', tech, step, y) in self.capacity_total
                        else 0
                    )
                    + (
                        sum(
                            self.capacity_builds[r, tech, year, step]
                            for year in self.year
                            if year < y
                        )
                        if (tech, step) in self.tech_buildable
                        else 0
                    )
                    - sum(
                        self.capacity_retirements[tech, year, r, step]
                        for year in self.year
                        if year < y
                    )
                )

        # if trade operation is on
        if elec_config.regional_exchange:  # and len(self.TranLineLimitInt_index) != 0:
            # self.populate_trade_sets = pyo.BuildAction(rule=em.populate_trade_sets_rule)

            # filter out the "step" from the international trade index
            idx = [
                (region, region_int, year, hour)
                for (region, region_int, year, _, hour) in self.international_trade_index
            ]

            @self.Constraint(idx)  # (self.TranLineLimitInt_index)
            def trade_international_capacity_ub(self, r, R_int, y, hr):
                """International interregional trade upper bound where
                Interregional Trade <= Interregional Transmission Capabilities * Time

                basically:  sum across all steps that use this line and ensure within capacity of line

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
                    # sum across all viable steps for this route
                    sum(
                        self.trade_international[r, R_int, y, step, hr]
                        for step in self.viable_international_steps[r, R_int, y, hr]
                    )
                    <= self.TranLimitCapInt[r, R_int, y, hr] * self.WeightHour[hr]
                )

            # filter the domestic region out of the international trade index and resequence
            # TODO:  Look to standardize the sequencing here
            idx = [
                (region_int, step, year, hour)
                for (_, region_int, year, step, hour) in self.international_trade_index
            ]

            @self.Constraint(idx)
            def trade_international_generation_ub(self, R_int, step, y, hr):
                """International electricity supply upper bound where
                Interregional Trade <= Interregional Supply

                sum across all destination regions to ensure the international generation capacity
                for this international region is not exceeded

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
                        self.trade_international[r, R_int, y, step, hr]
                        for r in self.domestic_destinations[R_int, y, hr]
                    )
                    <= self.TranLimitGenInt[R_int, step, y, hr] * self.WeightHour[hr]
                )

            @self.Constraint(self.trade_interregional.index_set())
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
                    self.trade_interregional[r, r1, y, hr]
                    <= self.TranLimit[r, r1, y, hr] * self.WeightHour[hr]
                )

        # if reserve margin requirements are on
        # TODO:  Ask Sauleh why we reference "expansion" here as it seems like separate concept.
        #        Why not JUST reserve_margin?
        if elec_config.capacity_expansion and elec_config.reserve_margin_required:
            # self.populate_RM_sets = pyo.BuildAction(rule=em.populate_RM_sets_rule)

            @self.Constraint(self.Load.index_set())
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
                return self.Load[r, y, hr] * (1 + self.ReserveMargin[r]) <= self.WeightHour[
                    hr
                ] * sum(
                    (
                        self.CapacityCredit[tech, y, r, step, hr]
                        * (
                            self.storage_avail_cap[tech, y, r, step, hr]
                            if tech in self.tech_stor
                            else self.capacity_total[r, self.MapHourSeason[hr], tech, step, y]
                        )
                    )
                    for (tech, step) in self.capacity_sources[y, r, self.MapHourSeason[hr]]
                )

            @self.Constraint(setA.storage_index)
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
                    self.storage_avail_cap[T_stor, y, r, step, hr]
                    <= self.capacity_total[r, self.MapHourSeason[hr], T_stor, step, y]
                )

            @self.Constraint(setA.storage_index)
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
                    self.storage_avail_cap[T_stor, y, r, step, hr]
                    <= self.storage_level[T_stor, y, r, step, hr]
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
                    self.generation_total[T_conv, y, r, step, hr1]
                    == self.generation_total[T_conv, y, r, step, hr1 + self.num_hr_day - 1]
                    + self.generation_ramp_up[T_conv, y, r, step, hr1]
                    - self.generation_ramp_down[T_conv, y, r, step, hr1]
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
                    self.generation_total[T_conv, y, r, step, hr23]
                    == self.generation_total[T_conv, y, r, step, hr23 - 1]
                    + self.generation_ramp_up[T_conv, y, r, step, hr23]
                    - self.generation_ramp_down[T_conv, y, r, step, hr23]
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
                    self.generation_ramp_up[T_conv, y, r, step, hr]
                    <= self.WeightHour[hr]
                    * self.RampRate[T_conv]
                    * self.capacity_total[r, self.MapHourSeason[hr], T_conv, step, y]
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
                    self.generation_ramp_down[T_conv, y, r, step, hr]
                    <= self.WeightHour[hr]
                    * self.RampRate[T_conv]
                    * self.capacity_total[r, self.MapHourSeason[hr], T_conv, step, y]
                )

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            # self.populate_reserves_sets = pyo.BuildAction(rule=em.populate_reserves_sets_rule)

            @self.Constraint(self.Load.index_set())
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
                        self.reserves_procurement['spinning', tech, y, r, step, hr]
                        for (tech, step) in self.ProcurementSetReserves['spinning', r, y, hr]
                    )
                    >= SPINNING_RESERVE_PROPORTION * self.Load[r, y, hr]
                )

            @self.Constraint(self.Load.index_set())
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
                # TODO:  Extract these magic numbers to constants.py / config ?
                return sum(
                    self.reserves_procurement['regulation', tech, y, r, step, hr]
                    for (tech, step) in self.ProcurementSetReserves['regulation', r, y, hr]
                ) >= REGULATION_RESERVE_PROPORTION * self.Load[
                    (r, y, hr)
                ] + WIND_REGULATION_RESERVE_PROPORTION * sum(
                    self.generation_total[T_wind, y, r, step, hr]
                    for (T_wind, step) in self.WindSetReserves[y, r, hr]
                ) + SOLAR_REGULATION_RESERVE_PROPORTION * self.WeightHour[hr] * sum(
                    self.capacity_total[r, self.MapHourSeason[hr], T_solar, step, y]
                    for (T_solar, step) in self.SolarSetReserves[y, r, hr]
                )

            @self.Constraint(self.Load.index_set())
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
                # TODO:  Verify (Sauleh) the wind reqt.  Code has 10%, docstring has 1% and 10%... picking 10% :)
                return sum(
                    self.reserves_procurement['flex', tech, y, r, step, hr]
                    for (tech, step) in self.ProcurementSetReserves['flex', r, y, hr]
                ) >= WIND_FLEX_RESERVE_PROPORTION * sum(
                    self.generation_total[T_wind, y, r, step, hr]
                    for (T_wind, step) in self.WindSetReserves[y, r, hr]
                ) + SOLAR_FLEX_RESERVE_PROPORTION * self.WeightHour[hr] * sum(
                    self.capacity_total[r, self.MapHourSeason[hr], T_solar, step, y]
                    for (T_solar, step) in self.SolarSetReserves[y, r, hr]
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
                    self.reserves_procurement[restypes, tech, y, r, step, hr]
                    <= self.ResTechUpperBound[restypes, tech]
                    * self.WeightHour[hr]
                    * self.capacity_total[r, self.MapHourSeason[hr], tech, step, y]
                )
