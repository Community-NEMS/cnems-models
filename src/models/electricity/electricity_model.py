"""Electricity Model, a pyomo optimization model of the electric power sector.

The class is organized by sections: settings, sets, parameters, variables, objective function,
constraints, plus additional misc support functions.
"""

from collections import defaultdict
from logging import getLogger

import pyomo.environ as pyo

from src.common.common_config import CommonConfig
from src.common.integrated_model import IntegratedModel
from src.common.validators import region_check
from src.models.electricity.constants import (
    H2_HEATRATE,
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
)
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType, ReserveType
from src.models.electricity.learning import learning_cost
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.validators import (
    reserve_procurement_check,
    reserve_tech_check,
    tech_name_check,
)

# Establish logger
logger = getLogger(__name__)


class PowerModel(pyo.ConcreteModel, IntegratedModel):
    """A PowerModel instance."""

    def __init__(
        self,
        model_sets: ModelSets,
        param_data: ParamData,
        elec_config: ElecConfig,
        common_config: CommonConfig,
        *args,
        **kwargs,
    ):
        pyo.ConcreteModel.__init__(self, *args, **kwargs)

        #  =======================================
        #                   Sets
        #  =======================================

        # temporal sets
        self.hour = pyo.Set(initialize=model_sets.hour)
        self.hour_first = pyo.Set(initialize=model_sets.hour_first, within=self.hour)
        self.hour_most = pyo.Set(initialize=model_sets.hour_most, within=self.hour)
        self.day = pyo.Set(initialize=model_sets.day)
        self.season = pyo.Set(initialize=model_sets.season)
        self.year = pyo.Set(initialize=model_sets.year_map.values())

        # spatial sets
        self.region = pyo.Set(initialize=model_sets.region, validate=region_check)
        self.region_int = pyo.Set(initialize=model_sets.region_international, within=self.region)
        self.region_dom = pyo.Set(initialize=model_sets.region_domestic, within=self.region)
        self.region_analyze = pyo.Set(initialize=model_sets.region_analyze, within=self.region_dom)

        # technology sets
        self.tech = pyo.Set(initialize=model_sets.tech, validate=tech_name_check)
        self.step = pyo.Set(initialize=model_sets.step)  # TODO:  come back to this
        self.tech_conv = pyo.Set(initialize=model_sets.tech_conv, within=self.tech)
        self.tech_re = pyo.Set(initialize=model_sets.tech_re, within=self.tech)
        self.tech_hydro = pyo.Set(initialize=model_sets.tech_hydro, within=self.tech)
        self.tech_stor = pyo.Set(initialize=model_sets.tech_stor, within=self.tech)
        self.tech_vre = pyo.Set(initialize=model_sets.tech_vre, within=self.tech)
        self.tech_wind = pyo.Set(initialize=model_sets.tech_wind, within=self.tech)
        self.tech_solar = pyo.Set(initialize=model_sets.tech_solar, within=self.tech)
        self.tech_h2 = pyo.Set(initialize=model_sets.tech_h2, within=self.tech)
        self.tech_disp = pyo.Set(initialize=model_sets.tech_disp, within=self.tech)
        self.tech_gen = pyo.Set(initialize=model_sets.tech_gen, within=self.tech)
        self.tech_buildable = pyo.Set(
            dimen=2, initialize=model_sets.tech_builds, within=self.tech * self.step
        )
        self.tech_retireable = pyo.Set(
            dimen=2, initialize=model_sets.tech_retires, within=self.tech * self.step
        )

        # CONSTRAINT INDEXING SETS
        self.storage_most_hours_balance_index = pyo.Set(
            initialize=model_sets.storage_most_hours_balance_index,
            within=self.region_analyze * self.tech_stor * self.step * self.year * self.hour_most,
        )
        self.storage_first_hour_balance_index = pyo.Set(
            initialize=model_sets.storage_first_hour_balance_index,
            within=self.region_analyze * self.tech_stor * self.step * self.year * self.hour_first,
        )
        self.ramp_most_hours_balance_index = pyo.Set(
            initialize=model_sets.ramp_most_hours_balance_index
        )
        self.ramp_first_hour_balance_index = pyo.Set(
            initialize=model_sets.ramp_first_hour_balance_index
        )
        self.generation_hydro_ub_index = pyo.Set(initialize=model_sets.generation_hydro_ub_index)
        self.generation_dispatchable_ub_index = pyo.Set(
            initialize=model_sets.generation_dispatchable_ub_index
        )
        self.generation_ramp_index = pyo.Set(initialize=model_sets.generation_ramp_index)
        self.capacity_hydro_ub_index = pyo.Set(initialize=model_sets.capacity_hydro_ub_index)
        self.reserves_procurement_index = pyo.Set(
            initialize=model_sets.reserves_procurement_index, validate=reserve_procurement_check
        )

        self.generation_vre_ub_index = pyo.Set(initialize=model_sets.generation_vre_ub_index)

        # international trade indices
        self.international_trade_index = pyo.Set(
            dimen=5,
            initialize=model_sets.international_trade_index,
            within=self.region_analyze * self.region_int * self.step * self.year * self.hour,
        )

        ################# Indexed sets

        # Derivative reserve indexing sets...
        if elec_config.spinning_reserve_required:
            idx = defaultdict(list)
            wind_idx = defaultdict(set)
            solar_idx = defaultdict(set)
            associated_res_types: dict[tuple, list[ReserveType]] = defaultdict(list)
            for region, res_type, tech, step, year, hour in model_sets.reserves_procurement_index:
                idx[region, res_type, year, hour].append((tech, step))
                associated_res_types[region, tech, step, year, hour].append(res_type)
                if tech in self.tech_wind:
                    wind_idx[region, year, hour].add((tech, step))
                elif tech in self.tech_solar:
                    solar_idx[region, year, hour].add((tech, step))

            self.associated_reserve_types = pyo.Set(
                self.region_analyze,
                self.tech,
                self.step,
                self.year,
                self.hour,
                within=ReserveType,
                initialize=associated_res_types,
            )
            """The valid reserve types for this index combo"""

            # The wind/solar members are accumulated as sets to drop the duplicate (tech, step)
            # seen once per reserve type, so sort them for a deterministic build order.  These stay
            # defaultdicts because the data is sparse but the consumers are not: the reserve
            # requirement constraints index wind_reserves/solar_reserves for every
            # (region, year, hour) in elec_load.index_set().  Pyomo re-invokes the initializer
            # for an index absent from the data, where a plain dict raises KeyError and a
            # defaultdict yields an empty set.
            wind_members = defaultdict(list, {k: sorted(v) for k, v in wind_idx.items()})
            solar_members = defaultdict(list, {k: sorted(v) for k, v in solar_idx.items()})

            # TODO:  Rename these 3 sets...they are all VRE... can we be more clear?
            self.eligible_reserves = pyo.Set(
                self.region_analyze,
                ReserveType,
                self.year,
                self.hour,
                within=self.tech * self.step,
                initialize=idx,
            )
            self.wind_reserves = pyo.Set(
                self.region_analyze,
                self.year,
                self.hour,
                within=self.tech_wind * self.step,
                initialize=wind_members,
            )
            self.solar_reserves = pyo.Set(
                self.region_analyze,
                self.year,
                self.hour,
                within=self.tech_solar * self.step,
                initialize=solar_members,
            )

        # make an indexed set of storage (region, tech, step, year) indexed by hour
        self.storage_hour_index = pyo.Set(
            self.hour,
            initialize=model_sets.storage_hour_index,
            within=self.region_analyze * self.tech_stor * self.step * self.year,
        )

        # Generation-eligible hours
        self.generation_hour_index = pyo.Set(self.hour, initialize=model_sets.generation_hour_index)

        # Generation-eligible hours for H2 technologies
        self.h2_generation_hour_index = pyo.Set(
            self.hour, initialize=model_sets.h2_generation_hour_index
        )

        self.generation_demand_balance = pyo.Set(
            self.region_analyze, self.year, self.hour, initialize=model_sets.generation_demand_index
        )
        self.storage_demand_balance = pyo.Set(
            self.region_analyze, self.year, self.hour, initialize=model_sets.storage_demand_index
        )

        # Capacity sources indexed by region, year
        idx = defaultdict(list)
        for region, tech, step, year in model_sets.capacity_index:
            idx[region, year].append((tech, step))
        self.capacity_sources = pyo.Set(
            self.region_analyze,
            self.year,
            within=self.tech * self.step,
            initialize=idx,
        )

        # if capacity expansion is on
        if elec_config.capacity_expansion:

            def retireable(m, _, tech, step, __):
                """Check if the combination of tech-step is in the eligible set."""
                return (tech, step) in m.tech_retireable

            self.capacity_retirements_index = pyo.Set(
                dimen=4,
                within=self.region_analyze * self.tech * self.step * self.year,
                initialize=model_sets.retirement_index,
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

        #  =======================================
        #                Parameters
        #  =======================================

        # conveniences to get the dataframe/dict pieces from the param data:
        all_frames = param_data.param_frames
        all_dicts = param_data.param_dicts

        # temporal parameters
        self.y0_learning = pyo.Param(
            initialize=common_config.aggregate_start_year
        )  # TODO:  Separate this concept from aggregation
        self.num_hr_day = pyo.Param(initialize=model_sets.num_hr_day)
        # TODO:  Consider making these mappings just dictionaries.  They don't really "fit the mold"
        #        of a *numeric* parameter.  They are just simple LUTs
        self.map_hour_season = pyo.Param(
            self.hour, initialize=all_frames['map_hour_season'], within=pyo.Any
        )
        self.map_hour_day = pyo.Param(
            self.hour, initialize=all_frames['map_hour_day']['day'], within=pyo.Any
        )

        self.weight_year = pyo.Param(self.year, initialize=all_frames['weight_year'])

        self.weight_hour = pyo.Param(self.hour, initialize=all_frames['weight_hour']['weight_hour'])
        self.weight_day = pyo.Param(self.day, initialize=all_frames['weight_day'])
        self.weight_season = pyo.Param(self.season, initialize=all_frames['weight_season'])

        # load and technology parameters
        # dev note:  set a default of 0.0 for all missing values,
        #            so that we can iterate over r, y, hr confidently as they should all be defined
        self.elec_load = pyo.Param(
            self.region_analyze,
            self.year,
            self.hour,
            initialize=all_frames['elec_load'],
            within=pyo.NonNegativeReals,
            default=0.0,
        )

        self.unmet_load_penalty = pyo.Param(initialize=UNMET_LOAD_PRICE)

        # dev note: A missing price value (sparse set) will cause fail w/o a default value here,
        #           which is OK
        self.supply_price = pyo.Param(
            self.region_analyze,
            self.tech,
            self.step,
            self.year,
            self.season,
            initialize=all_frames['supply_price'],
            within=pyo.NonNegativeReals,
        )

        # dev note: We do not supply a built index set here, so we should iterate over the
        #           param keys where needed
        self.supply_curve = pyo.Param(
            self.region_analyze,
            self.tech,
            self.step,
            self.year,
            initialize=all_frames['supply_curve'],
            within=pyo.NonNegativeReals,
        )
        # dev note:  a default of 0.0 is supplied because the indexing set is larger than the
        #            upper bound limit from the data
        self.cap_factor_vre = pyo.Param(
            self.region_analyze,
            self.tech_vre,
            self.step,
            self.year,
            self.hour,
            initialize=all_frames['cap_factor_vre'],
            within=pyo.NonNegativeReals,
            # TODO:  Needed to "make it work" with MIA values.  Decide if that is intended...
            default=0.0,
        )
        self.hydro_cap_factor = pyo.Param(
            self.region_analyze,
            self.season,
            initialize=all_frames['hydro_cap_factor'],
            within=pyo.NonNegativeReals,
        )
        self.battery_efficiency = pyo.Param(
            self.tech_stor, initialize=all_dicts['battery_efficiency'], within=pyo.NonNegativeReals
        )
        self.hours_to_buy = pyo.Param(
            self.tech_stor, initialize=all_dicts['hours_to_buy'], within=pyo.NonNegativeReals
        )
        self.h2_price = pyo.Param(
            self.region_analyze,
            self.tech_h2,
            self.step,
            self.year,
            self.season,
            initialize=all_frames['h2_price'],
            within=pyo.NonNegativeReals,
            mutable=True,
        )

        self.storage_level_cost = pyo.Param(initialize=STORAGE_LEVEL_COST)

        self.h2_heatrate = pyo.Param(initialize=H2_HEATRATE)

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            self.fom_cost = pyo.Param(
                self.region_analyze, self.tech, self.step, initialize=all_dicts['fom_cost']
            )
            self.capacity_credit = pyo.Param(
                self.region_analyze,
                self.tech,
                self.step,
                self.year,
                self.hour,
                initialize=all_frames['capacity_credit'],
            )

            # if capacity expansion and learning are on
            if elec_config.expansion_learning_type is not ExpansionLearningType.DISABLED:
                self.learning_rate = pyo.Param(self.tech, initialize=all_dicts['learning_rate'])
                self.cap_cost_initial = pyo.Param(
                    self.region_analyze,
                    self.tech,
                    self.step,
                    initialize=all_dicts['cap_cost_initial'],
                )
                self.supply_curve_learning = pyo.Param(
                    self.tech, initialize=all_dicts['supply_curve_learning']
                )

            # cap_cost is declared in every mode because capacity_builds is indexed from its
            # keys; only DISABLED and LINEAR consume its values in the objective.
            if elec_config.expansion_learning_type in {
                ExpansionLearningType.DISABLED,
                ExpansionLearningType.LINEAR,
                ExpansionLearningType.NONLINEAR,
            }:
                if elec_config.expansion_learning_type == ExpansionLearningType.DISABLED:
                    mute = False
                else:
                    mute = True
                self.cap_cost = pyo.Param(
                    self.region,
                    self.tech,
                    self.step,
                    self.year,
                    initialize=all_frames['cap_cost'],
                    mutable=mute,
                )

        # if trade operation is on
        if elec_config.regional_exchange:
            self.tran_cost = pyo.Param(
                self.region_analyze,
                self.region_analyze,
                self.year,
                initialize=all_frames['tran_cost'],
            )
            self.tran_limit = pyo.Param(
                self.region_analyze,
                self.region_analyze,
                self.year,
                self.hour,
                initialize=all_frames['tran_limit'],
            )
            """destination, source, year, hour"""

            # An aside to make an indexed set of trading partners
            # dev note:  It might be worthwhile to make this a sparse set and fabricate the index
            #            of this separately to maintain the validation?  That would require a
            #            conditional membership check where this is used.
            partners = defaultdict(list)
            for destination_region, source_region, year, hour in all_frames['tran_limit'].index:
                partners[destination_region, year, hour].append(source_region)
            self.regional_sources = pyo.Set(
                self.region_analyze,
                self.year,
                self.hour,
                initialize=lambda m, r, y, h: partners.get((r, y, h), []),
            )
            self.tran_cost_int = pyo.Param(
                self.region_analyze,
                self.region_int,
                self.step,
                self.year,
                initialize=all_frames['tran_cost_int'],
            )
            self.tran_limit_gen_int = pyo.Param(
                self.region_int,
                self.step,
                self.year,
                self.hour,
                initialize=all_frames['tran_limit_gen_int'],
            )
            self.tran_limit_cap_int = pyo.Param(
                self.region_analyze,
                self.region_int,
                self.year,
                self.hour,
                initialize=all_frames['tran_limit_cap_int'],
            )
            # use the index to create reverse-lookup to make index of intl regions that are
            # connected to a domestic region
            partners = defaultdict(list)
            for region, region_int, step, year, hour in self.international_trade_index:
                partners[region, year, hour].append((region_int, step))
            self.international_partners = pyo.Set(
                self.region_analyze,
                self.year,
                self.hour,
                initialize=lambda m, r, y, h: partners.get((r, y, h), []),
            )

            # index of what steps are available by international connection
            viable_steps = defaultdict(list)
            for region, region_int, step, year, hour in self.international_trade_index:
                viable_steps[region, region_int, year, hour].append(step)
            self.viable_international_steps = pyo.Set(
                self.region_analyze,
                self.region_int,
                self.year,
                self.hour,
                initialize=lambda m, ra, ri, y, h: viable_steps.get((ra, ri, y, h), []),
            )
            domestic_destinations = defaultdict(list)
            for region, region_int, _, year, hour in self.international_trade_index:
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
            self.reserve_margin = pyo.Param(
                self.region_analyze, initialize=all_dicts['reserve_margin']
            )

        # if ramping requirements are on
        if elec_config.ramping_required:
            self.ramp_up_cost = pyo.Param(self.tech_conv, initialize=all_dicts['ramp_up_cost'])
            self.ramp_down_cost = pyo.Param(self.tech_conv, initialize=all_dicts['ramp_down_cost'])
            self.ramp_rate = pyo.Param(self.tech_conv, initialize=all_dicts['ramp_rate'])

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.reg_reserves_cost = pyo.Param(self.tech, initialize=all_dicts['reg_reserves_cost'])
            # note:  The data is cast to cover all combinations of ReserveType and Tech
            #        with 0's as appropriate
            self.res_tech_upper_bound = pyo.Param(
                ReserveType,
                self.tech,
                initialize=all_dicts['res_tech_upper_bound'],
                validate=reserve_tech_check,
            )

        # Cross-talk from H2 model  # preserved as basis for expansion/ideas...?
        # TODO:  Extract these?  ...not used
        self.fixed_elec_request = pyo.Param(
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

        #  =======================================
        #                 Variables
        #  =======================================

        # Generation, capacity, and technology variables
        self.generation_total = pyo.Var(model_sets.generation_index, within=pyo.NonNegativeReals)
        """region, tech, step, year, hour"""
        self.unmet_load = pyo.Var(
            self.region_analyze, self.year, self.hour, within=pyo.NonNegativeReals
        )
        self.capacity_total = pyo.Var(model_sets.capacity_index, within=pyo.NonNegativeReals)
        """region, tech, step, year"""
        self.storage_inflow = pyo.Var(model_sets.storage_index, within=pyo.NonNegativeReals)
        self.storage_outflow = pyo.Var(model_sets.storage_index, within=pyo.NonNegativeReals)
        self.storage_level = pyo.Var(model_sets.storage_index, within=pyo.NonNegativeReals)

        # if capacity expansion is on
        if elec_config.capacity_expansion:
            # TODO:  Review this creation of var index from parameter keys.
            #        Done in a few spots, seems like best plan.
            self.capacity_builds = pyo.Var(list(self.cap_cost.keys()), within=pyo.NonNegativeReals)
            self.capacity_retirements = pyo.Var(
                self.capacity_retirements_index, within=pyo.NonNegativeReals
            )

        # if trade operation is on
        if elec_config.regional_exchange:
            # Interregional trade is limited by the tran_limit Param, so we can index with it
            self.trade_interregional = pyo.Var(
                list(self.tran_limit.keys()),
                within=pyo.NonNegativeReals,
            )
            self.trade_international = pyo.Var(
                self.international_trade_index,
                within=pyo.NonNegativeReals,
            )

        # if reserve margin constraints are on
        if elec_config.reserve_margin_required:
            self.storage_avail_cap = pyo.Var(model_sets.storage_index, within=pyo.NonNegativeReals)

        # if ramping requirements are on
        if elec_config.ramping_required:
            self.generation_ramp_up = pyo.Var(
                self.generation_ramp_index, within=pyo.NonNegativeReals
            )
            self.generation_ramp_down = pyo.Var(
                self.generation_ramp_index, within=pyo.NonNegativeReals
            )

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            self.reserves_procurement = pyo.Var(
                self.reserves_procurement_index, within=pyo.NonNegativeReals
            )

        #  =======================================
        #                 Objective
        #  =======================================

        def dispatch_cost(self):
            """Dispatch cost (e.g., variable O&M cost) component for the objective function.

            Returns
            -------
            int
                Dispatch cost
            """
            return sum(
                self.weight_day[self.map_hour_day[hr]]
                * (
                    sum(
                        self.weight_year[y]
                        * self.supply_price[r, tech, step, y, season]
                        * self.generation_total[r, tech, step, y, hr]
                        for (r, tech, step, y) in self.generation_hour_index[hr]
                    )
                    + sum(
                        self.weight_year[y]
                        * (
                            0.5
                            * self.supply_price[r, tech, step, y, season]
                            * (
                                self.storage_inflow[r, tech, step, y, hr]
                                + self.storage_outflow[r, tech, step, y, hr]
                            )
                            + (self.weight_hour[hr] * self.storage_level_cost)
                            * self.storage_level[r, tech, step, y, hr]
                        )
                        for (r, tech, step, y) in self.storage_hour_index[hr]
                    )
                    # dimensional analysis for cost:
                    # $/kg * kg/Gwh * Gwh = $
                    # so we need 1/heatrate for kg/Gwh
                    + sum(
                        self.weight_year[y]
                        * self.h2_price[r, tech, step, y, season]
                        / self.h2_heatrate
                        * self.generation_total[r, tech, 1, y, hr]  # TODO:  Why the hardcode "1"?
                        for (r, tech, step, y) in self.h2_generation_hour_index[hr]
                    )
                )
                for hr in self.hour
                if (season := self.map_hour_season[hr])
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
                self.weight_day[self.map_hour_day[hr]]
                * self.weight_year[y]
                * self.unmet_load[r, y, hr]
                * self.unmet_load_penalty
                for (r, y, hr) in self.elec_load
                if r in self.region_analyze
            )

        self.unmet_load_cost = pyo.Expression(expr=unmet_load_cost)

        # if capacity expansion is on
        if elec_config.capacity_expansion:

            def fixed_om_cost(self):
                """Fixed operation and maintenance (FOM) cost component for the objective function.

                Returns
                -------
                int
                    FOM cost component
                """
                return sum(
                    self.weight_year[y]
                    * self.fom_cost[r, tech, step]
                    * self.capacity_total[r, tech, step, y]
                    for (r, tech, step, y) in self.capacity_total
                )

            self.fixed_om_cost = pyo.Expression(expr=fixed_om_cost)

            # nonlinear expansion costs
            if elec_config.expansion_learning_type == ExpansionLearningType.NONLINEAR:

                def capacity_expansion_cost(self):
                    """Capacity expansion cost component for the objective function.

                    Applies when the learning switch is set to the nonlinear option.  The curve
                    itself lives in ``learning.py`` and is shared with the linear path, so the two
                    modes cannot drift apart.

                    Returns
                    -------
                    pyomo expression
                        Capacity expansion cost component (nonlinear learning)
                    """
                    return sum(
                        learning_cost(
                            build_quantity=self.capacity_builds[r, tech, step, y],
                            # Experience is this technology's builds across every region and step,
                            # in years strictly before y, so a build never discounts its own cost.
                            cumulative_quantity=sum(
                                self.capacity_builds[region, tech, other_step, year]
                                for (region, other_tech, other_step) in self.cap_cost_initial
                                if other_tech == tech
                                for year in self.year
                                if year < y
                            ),
                            baseline_quantity=self.supply_curve_learning[tech],
                            initial_cost=self.cap_cost_initial[r, tech, step],
                            learning_rate=self.learning_rate[tech],
                        )
                        for (r, tech, step, y) in self.capacity_builds
                    )

                self.capacity_expansion_cost = pyo.Expression(expr=capacity_expansion_cost)

            # linear expansion costs
            else:

                def capacity_expansion_cost(self):
                    """Capacity expansion cost component for the objective function.

                    Applies when the learning switch is set to the linear option.

                    Returns
                    -------
                    int
                        Capacity expansion cost component (linear learning)
                    """
                    return sum(
                        self.cap_cost[r, tech, step, y] * self.capacity_builds[r, tech, step, y]
                        for (r, tech, step, y) in self.cap_cost
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
                    self.weight_day[self.map_hour_day[hr]]
                    * self.weight_year[y]
                    * self.trade_interregional[r, r1, y, hr]
                    * self.tran_cost[r, r1, y]
                    for (r, r1, y, hr) in self.trade_interregional
                ) + sum(
                    self.weight_day[self.map_hour_day[hr]]
                    * self.weight_year[y]
                    * self.trade_international[r, r_int, step, y, hr]
                    * self.tran_cost_int[r, r_int, step, y]
                    for (r, r_int, step, y, hr) in self.international_trade_index
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
                    self.weight_day[self.map_hour_day[hr]]
                    * self.weight_year[y]
                    * (
                        self.generation_ramp_up[r, t_conv, step, y, hr] * self.ramp_up_cost[t_conv]
                        + self.generation_ramp_down[r, t_conv, step, y, hr]
                        * self.ramp_down_cost[t_conv]
                    )
                    for (r, t_conv, step, y, hr) in self.generation_ramp_index
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
                        self.reg_reserves_cost[tech]
                        if restype == ReserveType.REGULATION
                        else SPINNING_RESERVE_DEFAULT_COST
                    )
                    * self.weight_day[self.map_hour_day[hr]]
                    * self.weight_year[y]
                    * self.reserves_procurement[r, restype, tech, step, y, hr]
                    for (r, restype, tech, step, y, hr) in self.reserves_procurement_index
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

        #  =======================================
        #               Constraints
        #  =======================================

        # self.regional_exchange = elec_config.regional_exchange  # Only needed by rule below

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
            return self.elec_load[r, y, hr] <= sum(
                self.generation_total[r, tech, step, y, hr]
                for (tech, step) in self.generation_demand_balance[r, y, hr]
            ) + sum(
                self.storage_outflow[r, tech, step, y, hr]
                - self.storage_inflow[r, tech, step, y, hr]
                for (tech, step) in self.storage_demand_balance[r, y, hr]
            ) + self.unmet_load[r, y, hr] + (
                sum(
                    self.trade_interregional[r, r1, y, hr] * (1 - TRANSMISSION_LOSS_FACTOR)
                    - self.trade_interregional[r1, r, y, hr]
                    for r1 in self.regional_sources[r, y, hr]
                )
                # note:  don't need to check "region_trade" as the lookup in partners could be empty
                if elec_config.regional_exchange  # and r in self.region_trade
                else 0
            ) + (
                sum(
                    self.trade_international[r, r_int, step, y, hr] * (1 - TRANSMISSION_LOSS_FACTOR)
                    for (r_int, step) in self.international_partners[r, y, hr]
                )
                if elec_config.regional_exchange
                else 0
            )

        # First hour
        @self.Constraint(self.storage_first_hour_balance_index)
        def storage_first_hour_balance(self, r, t_stor, step, y, hr_first):
            """Storage balance constraint for the first hour time-segment in each day-type.

            Storage level == Storage level (in final hour time-segment in current day-type)
                            + Storage inflow * Battery efficiency
                            - Storage outflow.

            Parameters
            ----------
            t_stor : pyomo.core.base.set.OrderedScalarSet
                storage technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr_first : pyomo.core.base.set.OrderedScalarSet
                set containing first hour time-segment in each day-type

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage balance constraint for the first hour time-segment in each day-type
            """
            return (
                self.storage_level[r, t_stor, step, y, hr_first]
                == self.storage_level[r, t_stor, step, y, hr_first + self.num_hr_day - 1]
                + self.battery_efficiency[t_stor]
                * self.storage_inflow[r, t_stor, step, y, hr_first]
                - self.storage_outflow[r, t_stor, step, y, hr_first]
            )

        # Not first hour
        @self.Constraint(self.storage_most_hours_balance_index)
        def storage_most_hours_balance(self, r, t_stor, step, y, hr_most):
            """Storage balance constraint for every time-segment after the first in a day-type.

            Storage level == Storage level (in previous hour time-segment)
                            + Storage inflow * Battery efficiency
                            - Storage outflow.

            Parameters
            ----------
            t_stor : pyomo.core.base.set.OrderedScalarSet
                storage technology set
            y : pyomo.core.base.set.OrderedScalarSet
                year set
            r : pyomo.core.base.set.OrderedScalarSet
                region set
            step : pyomo.core.base.set.OrderedScalarSet
                supply curve price/quantity step set
            hr_most : pyomo.core.base.set.OrderedScalarSet
                set containing time-segment except first hour in each day-type

            Returns
            -------
            pyomo.core.base.constraint.IndexedConstraint
                Storage balance constraint for the time-segment in each day-type other than
            the first hour time-segment
            """
            return (
                self.storage_level[r, t_stor, step, y, hr_most]
                == self.storage_level[r, t_stor, step, y, hr_most - 1]
                + self.battery_efficiency[t_stor] * self.storage_inflow[r, t_stor, step, y, hr_most]
                - self.storage_outflow[r, t_stor, step, y, hr_most]
            )

        # self.populate_hydro_sets = pyo.BuildAction(rule=em.populate_hydro_sets_rule)

        # quick reverse lookup
        idx = defaultdict(list)
        for hour, season in self.map_hour_season.items():
            idx[season].append(hour)
        self.hour_season_index = pyo.Set(self.season, initialize=idx)

        @self.Constraint(self.capacity_hydro_ub_index)
        def capacity_hydro_ub(self, r, t_hydro, y, season):
            """Hydroelectric generation seasonal upper bound.

            Hydo generation <= Hydo capacity * Hydro capacity factor.

            Parameters
            ----------
            t_hydro : pyomo.core.base.set.OrderedScalarSet
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
                    self.generation_total[r, t_hydro, 1, y, hr]  # TODO:  Why the hardcode step=1 ?
                    * self.weight_day[self.map_hour_day[hr]]
                    for hr in self.hour_season_index[season]
                )
                <= self.capacity_total[r, t_hydro, 1, y]
                * self.hydro_cap_factor[r, season]
                * self.weight_season[season]
            )

        @self.Constraint(self.generation_dispatchable_ub_index)
        def generation_dispatchable_ub(self, r, t_disp, step, y, hr):
            """Dispatchable generation upper bound.

            Dispatchable generation + reserve procurement <= capacity * capacity factor.

            Parameters
            ----------
            t_disp : pyomo.core.base.set.OrderedScalarSet
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
                self.generation_total[r, t_disp, step, y, hr]
                + (
                    sum(
                        self.reserves_procurement[r, restype, t_disp, step, y, hr]
                        for restype in self.associated_reserve_types[r, t_disp, step, y, hr]
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, t_disp, step, y] * self.weight_hour[hr]
            )

        @self.Constraint(self.generation_hydro_ub_index)
        def generation_hydro_ub(self, r, t_hydro, step, y, hr):
            """Hydroelectric generation upper bound.

            Hydroelectric generation + reserve procurement <= capacity * capacity factor.

            Parameters
            ----------
            t_hydro : pyomo.core.base.set.OrderedScalarSet
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
                self.generation_total[r, t_hydro, step, y, hr]
                + (
                    sum(
                        self.reserves_procurement[r, restype, t_hydro, step, y, hr]
                        for restype in self.associated_reserve_types[r, t_hydro, step, y, hr]
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, t_hydro, step, y]
                * self.hydro_cap_factor[r, self.map_hour_season[hr]]
                * self.weight_hour[hr]
            )

        @self.Constraint(self.generation_vre_ub_index)
        def generation_vre_ub(self, r, t_vre, step, y, hr):
            """Intermittent generation upper bound.

            Intermittent generation + reserve procurement <= capacity * capacity factor.

            Parameters
            ----------
            t_vre : pyomo.core.base.set.OrderedScalarSet
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
                self.generation_total[r, t_vre, step, y, hr]
                + (
                    # TODO:  Review this.  Why is it gated on spinning reserve when others aren't?
                    sum(
                        self.reserves_procurement[r, restype, t_vre, step, y, hr]
                        for restype in self.associated_reserve_types[r, t_vre, step, y, hr]
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, t_vre, step, y]
                * self.cap_factor_vre[r, t_vre, step, y, hr]
                * self.weight_hour[hr]
            )

        # TODO:  internalize this set from the inputs ?   maybe?
        @self.Constraint(model_sets.storage_index)
        def storage_inflow_ub(self, r, tech, step, y, hr):
            """Storage inflow upper bound.

            Storage inflow <= Storage Capacity.

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
                self.storage_inflow[r, tech, step, y, hr]
                <= self.capacity_total[r, tech, step, y] * self.weight_hour[hr]
            )

        # TODO:  internalize this set from the inputs ?   maybe?

        # TODO check if it's only able to build in regions with existing capacity?
        @self.Constraint(model_sets.storage_index)
        def storage_outflow_ub(self, r, tech, step, y, hr):
            """Storage outflow upper bound.

            Storage outflow <= Storage Capacity.

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
                self.storage_outflow[r, tech, step, y, hr]
                + (
                    sum(
                        self.reserves_procurement[r, restype, tech, step, y, hr]
                        for restype in self.associated_reserve_types[r, tech, step, y, hr]
                    )
                    if elec_config.spinning_reserve_required
                    else 0
                )
                <= self.capacity_total[r, tech, step, y] * self.weight_hour[hr]
            )

        # TODO:  internalize this set from the inputs ?   maybe?
        @self.Constraint(model_sets.storage_index)
        def storage_level_ub(self, r, tech, step, y, hr):
            """Storage level upper bound.

            Storage level <= Storage power capacity * storage energy capacity.

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
                self.storage_level[r, tech, step, y, hr]
                <= self.capacity_total[r, tech, step, y] * self.hours_to_buy[tech]
            )

        # TODO:  internalize this set from the inputs ?   maybe?
        @self.Constraint(model_sets.capacity_index)
        def capacity_balance(self, r, tech, step, y):
            """Capacity Equality constraint.

            Capacity = Operating Capacity
                      + New Builds Capacity
                      - Retired Capacity.

            Parameters
            ----------
            r : pyomo.core.base.set.OrderedScalarSet
                region set
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
            return self.capacity_total[r, tech, step, y] == self.supply_curve[
                (r, tech, step, y)
            ] + (
                sum(self.capacity_builds[r, tech, step, year] for year in self.year if year <= y)
                if elec_config.capacity_expansion and (tech, step) in self.tech_buildable
                else 0
            ) - (
                sum(
                    self.capacity_retirements[r, tech, step, year]
                    for year in self.year
                    if year <= y
                )
                if elec_config.capacity_expansion
                and (r, tech, step, y) in self.capacity_retirements_index
                else 0
            )

        # if capacity expansion is on
        if elec_config.capacity_expansion:

            @self.Constraint(self.capacity_retirements_index)
            def capacity_retirements_ub(self, r, tech, step, y):
                """Retirement upper bound.

                Capacity Retired <= Operating Capacity
                                   + New Builds Capacity
                                   - Retired Capacity.

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
                return self.capacity_retirements[r, tech, step, y] <= (
                    (
                        self.supply_curve[r, tech, step, y]
                        if (r, tech, step, y) in self.capacity_total
                        else 0
                    )
                    + (
                        sum(
                            self.capacity_builds[r, tech, step, year]
                            for year in self.year
                            if year < y
                        )
                        if (tech, step) in self.tech_buildable
                        else 0
                    )
                    - sum(
                        self.capacity_retirements[r, tech, step, year]
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
                for (region, region_int, _, year, hour) in self.international_trade_index
            ]

            @self.Constraint(idx)  # (self.TranLineLimitInt_index)
            def trade_international_capacity_ub(self, r, r_int, y, hr):
                """International interregional trade upper bound.

                Interregional Trade <= Interregional Transmission Capabilities * Time.

                basically:  sum across all steps that use this line and ensure within
                capacity of line

                Parameters
                ----------
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                r_int : pyomo.core.base.set.OrderedScalarSet
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
                        self.trade_international[r, r_int, step, y, hr]
                        for step in self.viable_international_steps[r, r_int, y, hr]
                    )
                    <= self.tran_limit_cap_int[r, r_int, y, hr] * self.weight_hour[hr]
                )

            # filter the domestic region out of the international trade index and resequence

            idx = [
                (region_int, step, year, hour)
                for (_, region_int, step, year, hour) in self.international_trade_index
            ]

            @self.Constraint(idx)
            def trade_international_generation_ub(self, r_int, step, y, hr):
                """International electricity supply upper bound.

                Interregional Trade <= Interregional Supply.

                sum across all destination regions to ensure the international generation capacity
                for this international region is not exceeded

                Parameters
                ----------
                r_int : pyomo.core.base.set.OrderedScalarSet
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
                        self.trade_international[r, r_int, step, y, hr]
                        for r in self.domestic_destinations[r_int, y, hr]
                    )
                    <= self.tran_limit_gen_int[r_int, step, y, hr] * self.weight_hour[hr]
                )

            @self.Constraint(self.trade_interregional.index_set())
            def trade_domestic_ub(self, r, r1, y, hr):
                """Interregional trade upper bound.

                Interregional Trade <= Interregional Transmission Capabilities * Time.

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
                    <= self.tran_limit[r, r1, y, hr] * self.weight_hour[hr]
                )

        # if reserve margin requirements and expansion are on

        if elec_config.capacity_expansion and elec_config.reserve_margin_required:
            # self.populate_RM_sets = pyo.BuildAction(rule=em.populate_RM_sets_rule)

            @self.Constraint(self.elec_load.index_set())
            def reserve_margin_lb(self, r, y, hr):
                """Reserve margin requirement.

                Load * Reserve Margin <= Capacity * Capacity Credit * Time.

                # must meet reserve margin requirement
                # apply to every hour, a fraction above the final year's load
                # reserve margin requirement <= sum(Max capacity in that hour)

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
                return self.elec_load[r, y, hr] * (1 + self.reserve_margin[r]) <= self.weight_hour[
                    hr
                ] * sum(
                    (
                        self.capacity_credit[r, tech, step, y, hr]
                        * (
                            self.storage_avail_cap[r, tech, step, y, hr]
                            if tech in self.tech_stor
                            else self.capacity_total[r, tech, step, y]
                        )
                    )
                    for (tech, step) in self.capacity_sources[r, y]
                )

            @self.Constraint(model_sets.storage_index)
            def reserve_margin_storage_avail_cap_ub(self, r, t_stor, step, y, hr):
                """Available storage power capacity for meeting reserve margin.

                # ensure available capacity to meet RM for storage < power capacity

                Parameters
                ----------
                t_stor : pyomo.core.base.set.OrderedScalarSet
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
                    self.storage_avail_cap[r, t_stor, step, y, hr]
                    <= self.capacity_total[r, t_stor, step, y]
                )

            @self.Constraint(model_sets.storage_index)
            def reserve_margin_storage_avail_level_ub(self, r, t_stor, step, y, hr):
                """Available storage energy capacity for meeting reserve margin.

                # ensure available capacity to meet RM for storage < existing SOC

                Parameters
                ----------
                t_stor : pyomo.core.base.set.OrderedScalarSet
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
                    self.storage_avail_cap[r, t_stor, step, y, hr]
                    <= self.storage_level[r, t_stor, step, y, hr]
                )

        # if ramping requirements are on
        if elec_config.ramping_required:

            @self.Constraint(self.ramp_first_hour_balance_index)
            def ramp_first_hour_balance(self, r, t_conv, step, y, hr_first):
                """Ramp constraint for the first hour time-segment in each day-type.

                Generation == Generation (in final hour time-segment in current day-type)
                            + Ramp Up
                            - Ramp Down.

                Parameters
                ----------
                t_conv : pyomo.core.base.set.OrderedScalarSet
                    conventional technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr_first : pyomo.core.base.set.OrderedScalarSet
                    set containing first hour time-segment in each day-type

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Ramp constraint for the first hour
                """
                return (
                    self.generation_total[r, t_conv, step, y, hr_first]
                    == self.generation_total[r, t_conv, step, y, hr_first + self.num_hr_day - 1]
                    + self.generation_ramp_up[r, t_conv, step, y, hr_first]
                    - self.generation_ramp_down[r, t_conv, step, y, hr_first]
                )

            @self.Constraint(self.ramp_most_hours_balance_index)
            def ramp_most_hours_balance(self, r, t_conv, step, y, hr_most):
                """Ramp constraint for every time-segment after the first in a day-type.

                Generation == Generation (in previous hour time-segment)
                            + Ramp Up
                            - Ramp Down.

                Parameters
                ----------
                t_conv : pyomo.core.base.set.OrderedScalarSet
                    conventional technology set
                y : pyomo.core.base.set.OrderedScalarSet
                    year set
                r : pyomo.core.base.set.OrderedScalarSet
                    region set
                step : pyomo.core.base.set.OrderedScalarSet
                    supply curve price/quantity step set
                hr_most : pyomo.core.base.set.OrderedScalarSet
                    set containing time-segment except first hour in each day-type

                Returns
                -------
                pyomo.core.base.constraint.IndexedConstraint
                    Ramp constraint for the first hour
                """
                return (
                    self.generation_total[r, t_conv, step, y, hr_most]
                    == self.generation_total[r, t_conv, step, y, hr_most - 1]
                    + self.generation_ramp_up[r, t_conv, step, y, hr_most]
                    - self.generation_ramp_down[r, t_conv, step, y, hr_most]
                )

            @self.Constraint(self.generation_ramp_index)
            def ramp_up_ub(self, r, t_conv, step, y, hr):
                """Ramp rate up upper constraint.

                Ramp Up <= Capaciry * Ramp Rate * Time.

                Parameters
                ----------
                t_conv : pyomo.core.base.set.OrderedScalarSet
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
                    self.generation_ramp_up[r, t_conv, step, y, hr]
                    <= self.weight_hour[hr]
                    * self.ramp_rate[t_conv]
                    * self.capacity_total[r, t_conv, step, y]
                )

            @self.Constraint(self.generation_ramp_index)
            def ramp_down_ub(self, r, t_conv, step, y, hr):
                """Ramp rate down upper constraint.

                Ramp Up <= Capaciry * Ramp Rate * Time.

                Parameters
                ----------
                t_conv : pyomo.core.base.set.OrderedScalarSet
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
                    self.generation_ramp_down[r, t_conv, step, y, hr]
                    <= self.weight_hour[hr]
                    * self.ramp_rate[t_conv]
                    * self.capacity_total[r, t_conv, step, y]
                )

        # if operating reserve requirements are on
        if elec_config.spinning_reserve_required:
            # self.populate_reserves_sets = pyo.BuildAction(rule=em.populate_reserves_sets_rule)

            @self.Constraint(self.elec_load.index_set())
            def reserve_requirement_spin_lb(self, r, y, hr):
                """Spinning reserve requirements (3% of load).

                Spinning reserve procurement >= 0.03 * Load.

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
                        self.reserves_procurement[r, ReserveType.SPINNING, tech, step, y, hr]
                        for (tech, step) in self.eligible_reserves[r, ReserveType.SPINNING, y, hr]
                    )
                    >= SPINNING_RESERVE_PROPORTION * self.elec_load[r, y, hr]
                )

            @self.Constraint(self.elec_load.index_set())
            def reserve_requirement_reg_lb(self, r, y, hr):
                """Regulation Reserve Req (1% of load + 0.5% of wind gen + 0.3% of solar cap).

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
                    self.reserves_procurement[r, ReserveType.REGULATION, tech, step, y, hr]
                    for (tech, step) in self.eligible_reserves[r, ReserveType.REGULATION, y, hr]
                ) >= REGULATION_RESERVE_PROPORTION * self.elec_load[
                    (r, y, hr)
                ] + WIND_REGULATION_RESERVE_PROPORTION * sum(
                    self.generation_total[r, t_wind, step, y, hr]
                    for (t_wind, step) in self.wind_reserves[r, y, hr]
                ) + SOLAR_REGULATION_RESERVE_PROPORTION * self.weight_hour[hr] * sum(
                    self.capacity_total[r, t_solar, step, y]
                    for (t_solar, step) in self.solar_reserves[r, y, hr]
                )

            @self.Constraint(self.elec_load.index_set())
            def reserve_requirement_flex_lb(self, r, y, hr):
                """Flexible Reserve Requirement (10% of wind gen + 4% of solar cap).

                Reserves Requirement >= 0.10 * Wind Gen
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
                    self.reserves_procurement[r, ReserveType.FLEX, tech, step, y, hr]
                    for (tech, step) in self.eligible_reserves[r, ReserveType.FLEX, y, hr]
                ) >= WIND_FLEX_RESERVE_PROPORTION * sum(
                    self.generation_total[r, t_wind, step, y, hr]
                    for (t_wind, step) in self.wind_reserves[r, y, hr]
                ) + SOLAR_FLEX_RESERVE_PROPORTION * self.weight_hour[hr] * sum(
                    self.capacity_total[r, t_solar, step, y]
                    for (t_solar, step) in self.solar_reserves[r, y, hr]
                )

            # TODO:  Review this.  It operates on the x-product of tech x restype, yet many
            #        techs are not "reserve-able" so we could make the variable
            #        `reserve_procurement` more sparse
            #        and/or use defaults better.
            @self.Constraint(self.reserves_procurement_index)
            def reserve_procurement_ub(self, r, restype, tech, step, y, hr):
                """Reserve Requirement Procurement Upper Bound.

                Reserve Procurement <= Capacity
                                    * Tech Reserve Contribution Share
                                    * Time.

                Parameters
                ----------
                restype : pyomo.core.base.set.OrderedScalarSet
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
                    self.reserves_procurement[r, restype, tech, step, y, hr]
                    <= self.res_tech_upper_bound[restype, tech]
                    * self.weight_hour[hr]
                    * self.capacity_total[r, tech, step, y]
                )
