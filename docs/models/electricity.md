# Electricity

The electricity model is a least-cost optimization that meets demand from a portfolio of
generation technologies, covering dispatch and capacity expansion. Reserve
margins, operating reserves, ramping, and interregional trade are switched on individually
through the model configuration.

## Model Overview

### Sets

| Set                    | Code                                          | Data Type  | Short Description                                            |
|:-----------------------|:----------------------------------------------|:-----------|:-------------------------------------------------------------|
| $H$                    | hour                                          | Set        | All representative hours                                     |
| $Y$                    | year                                          | Set        | The summary years, the years the model solves; with year aggregation each stands for a block of calendar years |
| $SEAS$                 | season                                        | Set        | All seasons                                                  |
| $D$                    | day                                           | Set        | All representative days                                      |
| $R$                    | region_analyze                                | Set        | Domestic regions in the run: those in region_filter, or all of them |
| $R^{int}$              | region_int                                    | Set        | All international regions                                    |
| $T$                    | tech                                          | Set        | All technologies                                             |
| $S$                    | step                                          | Set        | Technology supply steps (see Supply Steps below)             |
| $C$                    | step of international_trade_index             | Set        | International supply steps, separate from $S$                |
| $RT$                   | ReserveType                                   | Set        | Operating reserve types: spinning, regulation and flex       |
| $T^{gen}$              | tech_gen                                      | Subset     | Generating technologies, all but storage                     |
| $T^{stor}$             | tech_stor                                     | Subset     | Storage technologies                                         |
| $T^{disp}$             | tech_disp                                     | Subset     | Dispatchable technologies, both hydro technologies included  |
| $T^{vre}$              | tech_vre                                      | Subset     | Intermittent technologies (wind and solar)                   |
| $T^{w}$                | tech_wind                                     | Subset     | Wind technologies, written $t^w$ in the formulas             |
| $T^{s}$                | tech_solar                                    | Subset     | Solar technologies, written $t^s$ in the formulas            |
| $T^{hreg}$             | tech_hydro_regular                            | Subset     | Regular hydro                                                |
| $T^{hseas}$            | tech_hydro_seasonal                           | Subset     | Seasonal hydro                                               |
| $T^{conv}$             | tech_conv                                     | Subset     | Conventional technologies, the ones with ramping constraints |
| $\Theta_{load}$        | elec_load.index_set()                         | Sparse set | All load sparse set                                          |
| $\Theta_{gen}$         | generation_index                              | Sparse set | All non-storage generation sparse set                        |
| $\Theta_{stor}$        | storage_index                                 | Sparse set | All storage set                                              |
| $\Theta_{um}$          | unmet_load.index_set()                        | Sparse Set | All unmet load set                                           |
| $\Theta_{SC}$          | capacity_index                                | Sparse Set | Total capacity set: existing capacity, plus buildable capacity when capacity_expansion is on |
| $\Theta_{dt^{max}}$    | generation_dispatchable_ub_index              | Sparse Set | Dispatchable technology generation upper bound set           |
| $\Theta_{it^{max}}$    | generation_vre_ub_index                       | Sparse Set | Intermittent technology generation upper bound set           |
| $\Theta_{ht^{max}}$    | generation_hydro_ub_index                     | Sparse Set | Hydroelectric generation upper bound set                     |
| $\Theta_{hs}$          | seasonal_hydro_index                       | Sparse Set | Hydroelectric generation seasonal upper bound set            |
| $\Theta_{ret}$         | capacity_retirements_index                    | Sparse Set | Retirable capacity set                                       |
| $\Theta_{new}$         | capacity_build_index                   | Sparse Set | Buildable capacity set                                       |
| $\Theta_{cc}$          | capital_cost.index_set()                      | Sparse Set | Set of capacity costs                                        |
| $\Theta_{cc0}$         | capital_cost_initial.keys()              | Sparse Set | Set of initial year's capacity costs                         |
| $\Theta_{SBFH}$        | storage_first_hour_balance_index              | Sparse set | First hour storage balance set                               |
| $\Theta_{SBH}$         | storage_most_hours_balance_index              | Sparse set | (non-first hour) storage balance set                         |
| $\Theta_{proc}$        | reserves_procurement_index                    | Sparse set | Operating reserve procurement set: (region, reserve type, technology, step, year, hour) with $RTUB_{rt,t} > 0$ |
| $\Theta_{ramp}$        | generation_ramp_index                         | Sparse set | Set for ramping                                              |
| $\Theta_{ramp1}$       | ramp_first_hour_balance_index                 | Sparse set | Set for ramping in first hour of each representative day     |
| $\Theta_{ramp23}$      | ramp_most_hours_balance_index                 | Sparse set | Set for ramping in non-first hour of each representative day |
| $\Theta_{tra}$         | trade_interregional.index_set()               | Sparse set | Domestic interregional trade set                             |
| $\Theta_{tracan}$      | international_trade_index                     | Sparse set | International trade set: (region, international region, supply step, year, hour) |
| $\Theta_{traL^{int}}$  | trade_international_generation_ub.index_set() | Sparse set | International supply limit set: (international region, supply step, year, hour) |
| $\Theta_{traLL}$       | tran_limit.keys()                             | Sparse set | Domestic interregional trade limit set                       |
| $\Theta_{traLL^{int}}$ | trade_international_capacity_ub.index_set()   | Sparse set | International interregional trade line limit set             |

### Supply Steps

A supply step $s \in S$ is a tranche of a technology's capacity in a region, with its own existing
capacity, generation cost, fixed O&M cost and, where it can be built, capital cost. Each step has its
own variables, bounded by its own capacity, and the load, reserve margin and operating reserve
requirements add up every step that can contribute to them. The steps of each technology, $S_t$, are
declared in the `steps` column of `tech.csv`, and `tech_build.csv` and `tech_retire.csv` say which
steps can be built and retired.

In the shipped data, coal steam, oil steam, gas turbine and gas combined cycle have three steps of
equal capacity in each region, each with its own generation cost. New coal steam, gas turbine and
combined cycle capacity can be built only on step 3, and oil steam cannot be built. Every other
technology has one step. Steps are not vintages, since capacity built in any year joins its step.
They also no longer mark sub-types: seasonal and regular hydro, and utility and end-use solar, are
separate technologies.

International supply steps $c \in C$ are separate. They belong to an international region, and each
has its own supply limit $TRALIM^{int}$ and cost $TRACC$.

### Re-Indexed Sets

These sets are re-indexed for specific constraints. They are all sub-sets accessed by certain indices to return the
remaining indices.

| Set                        | Code                      | Data Type     | Short Description                                                                   |
|:---------------------------|:--------------------------|:--------------|:------------------------------------------------------------------------------------|
| $S_t$                      | steps_for_tech            | Sparse subset | Set for supply steps indexed by technology                                          |
| $\theta^{GSH}_h$           | generation_hour_index     | Sparse subset | Set for generation indexed by hour                                                  |
| $\theta^{SSH}_h$           | storage_hour_index        | Sparse subset | Set for storage indexed by hour                                                     |
| $\theta^{GDB}_{r,y,h}$     | generation_demand_balance | Sparse subset | Set for generation indexed by r,y,h                                                 |
| $\theta^{SDB}_{r,y,h}$     | storage_demand_balance    | Sparse subset | Set for storage indexed by r,y,h                                                    |
| $\theta^{TDB}_{r,y,h}$     | regional_sources          | Sparse subset | Set for trade indexed by r,y,h                                                      |
| $\theta^{TCDB}_{r,y,h}$    | international_partners    | Sparse subset | Set for (international region, international supply step) pairs indexed by r,y,h    |
| $\theta^{windor}_{r,y,h}$  | wind_generation           | Sparse subset | Wind (technology, step) pairs whose generation adds to the regulation and flex requirements, indexed by r,y,h |
| $\theta^{solor}_{r,y,h}$   | solar_generation          | Sparse subset | Solar (technology, step) pairs whose capacity adds to the regulation and flex requirements, indexed by r,y,h |
| $\theta^{opres}_{r,rt,y,h}$ | eligible_reserves         | Sparse subset | (technology, step) pairs that can provide reserve type $rt$, indexed by r,rt,y,h    |
| $\theta^{rmh}_{r,y}$       | capacity_sources_hourly_factor   | Sparse subset | Intermittent (technology, step) pairs in $\Theta_{SC}$, counted in the reserve margin at their hourly factor, indexed by r,y |
| $\theta^{rms}_{r,y}$       | capacity_sources_seasonal_factor | Sparse subset | Hydro (technology, step) pairs in $\Theta_{SC}$, counted in the reserve margin at their seasonal factor, indexed by r,y |
| $\theta^{rmc}_{r,y}$       | capacity_sources_constant_factor | Sparse subset | All other (technology, step) pairs in $\Theta_{SC}$, storage included, counted in the reserve margin at a factor of 1, indexed by r,y |
| $\theta^{HSH}_{seas}$      | hour_season_index         | Sparse subset | Set for hours indexed by season                                                     |

### Parameters

Note: the model works in MW, MWh and USD throughout; input files carry their units in the value column name
(e.g. `generation_cost_usd_per_mwh`) and are read without unit conversion. Quantities indexed by representative
hour $h$ (load, generation, storage flows, trade and ramping) are energy over the time segment that hour stands
for, i.e. power times $HW_h$, so they are in MWh. Reserve procurement is in MWh in the same sense, capacity held
in reserve times $HW_h$, not energy delivered. Capacities and line limits are in MW.

| Parameter                         | Code                     | Domain           | Short Description                                                                            | Units                      |
|:----------------------------------|:-------------------------|:-----------------|:---------------------------------------------------------------------------------------------|:---------------------------|
| $N$                               | num_hr_day               | $\mathbb{I}$     | Number of representative hours in a representative day                                       | unitless                   |
| $LOAD_{r,y,h}$                    | elec_load                | $\mathbb{R}^+_0$ | Electricity demand                                                                           | MWh                        |
| $CAP^{exist}_{r,t,s,y}$           | available_capacity       | $\mathbb{R}^+_0$ | Existing capacity (prescribed or initial)                                                    | MW                         |
| $SPR_{r,t,s,y,seas}$              | generation_cost          | $\mathbb{R}^+_0$ | Fuel + variable O&M price                                                                    | USD/MWh                    |
| $NGADJ_{r,t,s,y,seas}$            | ng_fuel_adj              | $\mathbb{R}$     | Gas fuel-cost adjustment, set by the natural gas coupling; zero in a standalone run          | USD/MWh                    |
| $CF^{hr}_{r,t,s,h}$               | capacity_factor_hourly   | $\mathbb{R}^+_0$ | Hourly capacity factor of the intermittent technologies; the same in every year, and 0 where no profile is given | fraction                   |
| $CF^{seas}_{r,t,s,seas}$          | capacity_factor_seasonal | $\mathbb{R}^+_0$ | Seasonal capacity factor of the hydro technologies                                           | fraction                   |
| $STORLC$                          | storage_level_cost       | $\mathbb{R}^+_0$ | Cost of holding stored energy, per MWh held per hour; a constant (1e-11), so negligible. Storage losses come from $EFF_t$ | USD/MWh per hour           |
| $EFF_t$                           | storage_efficiency       | $\mathbb{R}^+_0$ | Roundtrip efficiency of storage                                                              | fraction                   |
| $STOR^{dur}_t$                    | storage_duration         | $\mathbb{R}^+_0$ | Storage duration                                                                             | hours                      |
| $UMLPEN$                          | unmet_load_penalty       | $\mathbb{R}^+_0$ | Unmet load penalty, a constant (50,000)                                                      | USD/MWh                    |
| $WY_y$                            | weight_year              | $\mathbb{I}$     | number of years represented by a representative year (weight)                                | years/representative years |
| $HW_h$                            | weight_hour              | $\mathbb{I}$     | Number of hours represented by a representative hour (weight)                                | hours/representative hour  |
| $WD_d$                            | weight_day               | $\mathbb{I}$     | Number of days represented by a representative day (weight); $WD_h$ is short for $WD_{MHD_h}$, the weight of the day hour $h$ belongs to | days/representative day    |
| $MHD_h$                           | map_hour_day             | $\mathbb{I}$     | map representative hour to representative day                                                | unitless                   |
| $WHS_{seas}$                      | weight_season            | $\mathbb{I}$     | Number of hours (per year) in a season (weight)                                              | hours                      |
| $MHS_h$                           | map_hour_season          | $SEAS$           | Map representative hour to season                                                            | unitless                   |
| $FOMC_{r,t,s}$                    | fom_cost                 | $\mathbb{R}^+_0$ | Fixed O&M cost                                                                               | USD/MW-year                |
| $RM_r$                            | planning_reserve_margin  | $\mathbb{R}^+_0$ | Reserve margin requirement                                                                   | fraction                   |
| $RUC_{t}$                         | ramp_up_cost             | $\mathbb{R}^+_0$ | Ramp up cost                                                                                 | USD/MWh                    |
| $RDC_{t}$                         | ramp_down_cost           | $\mathbb{R}^+_0$ | Ramp down cost                                                                               | USD/MWh                    |
| $RR_t$                            | ramp_rate                | $\mathbb{R}^+_0$ | Maximum change in output from one representative hour to the next, as a fraction of capacity. The input column says per hour, but the bound applies per representative hour, whatever its length | fraction of capacity       |
| $TRALINLIM_{r,r1,y,h}$            | tran_limit               | $\mathbb{R}^+_0$ | Domestic interregional trade line limit, from region $r1$ to region $r$; seasonal in the input file, expanded to hours | MW                         |
| $TRALIM^{int}_{r^{int},c,y,h}$    | supply_limit_intl        | $\mathbb{R}^+_0$ | Supply limit of an international region at an international supply step; seasonal in the input file, expanded to hours | MW                         |
| $TRALINLIM^{int}_{r,r^{int},y,h}$ | tran_limit_cap_intl      | $\mathbb{R}^+_0$ | International trade line limit; seasonal in the input file, expanded to hours                | MW                         |
| $TRAC_{r,r1,y}$                   | tran_cost                | $\mathbb{R}^+_0$ | Transmission hurdle rate (cost)                                                              | USD/MWh                    |
| $TRACC_{r,r^{int},c,y}$           | tran_cost_intl           | $\mathbb{R}^+_0$ | Cost of imports from an international region at an international supply step                 | USD/MWh                    |
| $LL$                              | TRANSMISSION_LOSS_FACTOR | $\mathbb{R}^+_0$ | Transmission loss factor, applied to imports; a constant (0.02)                              | fraction                   |
| $ORC_{rt,t}$                      | reserve_cost             | $\mathbb{R}^+_0$ | Cost of operating reserve procurement, by reserve type and technology                        | USD/MWh                    |
| $RTUB_{rt,t}$                     | reserve_tech_limit       | $\mathbb{R}^+_0$ | Maximum share of capacity that can provide reserve type $rt$                                 | fraction                   |
| $CAPCL_{r,t,s,y}$                 | capital_cost             | $\mathbb{R}^+_0$ | Cost to build capacity, from `capital_cost`; the linear mode reprices it before each solve   | USD/MW                     |
| $CAPC0_{r,t,s}$                   | capital_cost_initial     | $\mathbb{R}^+_0$ | Initial cost to build capacity, scaled by the learning multiplier in the learning modes      | USD/MW                     |
| $LR_t$                            | learning_rate            | $\mathbb{R}^+_0$ | Learning curve exponent                                                                      | unitless                   |
| $SCL_t$                           | supply_curve_learning    | $\mathbb{R}^+$   | Baseline capacity the learning curve is measured from. Must be strictly positive, since the curve divides by it and needs the base of the fractional power to stay positive | MW                         |

### Variables

| Variable                      | Code                 | Domain           | Short Description                                                     | Units | Switch notes                                      |
|:------------------------------|:---------------------|:-----------------|:----------------------------------------------------------------------|:------|:--------------------------------------------------|
| $STOR^{in}_{r,t,s,y,h}$       | storage_inflow       | $\mathbb{R}^+_0$ | Storage inflow                                                        | MWh   |                                                   |
| $STOR^{out}_{r,t,s,y,h}$      | storage_outflow      | $\mathbb{R}^+_0$ | Storage outflow                                                       | MWh   |                                                   |
| $STOR^{level}_{r,t,s,y,h}$    | storage_level        | $\mathbb{R}^+_0$ | Storage level (state-of-charge)                                       | MWh   |                                                   |
| $GEN_{r,t,s,y,h}$             | generation_total     | $\mathbb{R}^+_0$ | Generation in the time segment                                        | MWh   |                                                   |
| $UNLOAD_{r,y,h}$              | unmet_load           | $\mathbb{R}^+_0$ | Unmet load                                                            | MWh   |                                                   |
| $CAP^{tot}_{r,t,s,y}$         | capacity_total       | $\mathbb{R}^+_0$ | Total capacity                                                        | MW    |                                                   |
| $CAP^{new}_{r,t,s,y}$         | capacity_builds      | $\mathbb{R}^+_0$ | New capacity built                                                    | MW    | Only created if capacity_expansion is True        |
| $CAP^{ret}_{r,t,s,y}$         | capacity_retirements | $\mathbb{R}^+_0$ | Retirement capacity                                                   | MW    | Only created if capacity_expansion is True        |
| $TRA_{r,r1,y,h}$              | trade_interregional  | $\mathbb{R}^+_0$ | Interregional trade from region $r1$ to region $r$                    | MWh   | Only created if regional_exchange is True         |
| $TRA^{int}_{r,r^{int},c,y,h}$ | trade_international  | $\mathbb{R}^+_0$ | Imports from international region $r^{int}$ to region $r$ at international supply step $c$ | MWh   | Only created if regional_exchange is True         |
| $RAMP^{up}_{r,t,s,y,h}$       | generation_ramp_up   | $\mathbb{R}^+_0$ | Ramp up (increase in generation of a conventional technology)         | MWh   | Only created if ramping_required is True          |
| $RAMP^{down}_{r,t,s,y,h}$     | generation_ramp_down | $\mathbb{R}^+_0$ | Ramp down (decrease in generation of a conventional technology)       | MWh   | Only created if ramping_required is True          |
| $ORP_{r,rt,t,s,y,h}$          | reserves_procurement | $\mathbb{R}^+_0$ | Operating reserve held, by reserve type (capacity held times $HW_h$)  | MWh   | Only created if spinning_reserve_required is True |
| $STOR^{avail}_{r,t,s,y,h}$    | storage_avail_cap    | $\mathbb{R}^+_0$ | Available storage capacity to meet the reserve margin                 | MW    | Only created if reserve_margin_required is True, which requires capacity_expansion |

### Objective Function

The objective is to minimize costs to the electric power system: dispatch cost (fuel and variable O&M), fixed
operation and maintenance (FOM) cost, capacity expansion cost (priced without learning, or with linear or nonlinear
learning), interregional trade cost, ramping cost, operating reserve cost and unmet load cost. The expansion cost
applies only when `capacity_expansion` is true, the trade cost when `regional_exchange` is, the ramping cost when
`ramping_required` is, and the operating reserve cost when `spinning_reserve_required` is. Unmet load is
penalized at $UMLPEN$, so the model uses it only where serving load is infeasible or would cost more. A solve with
more than 0.1 MWh of unmet load in any (region, year, hour) is reported with PENALTY status.

Minimize total cost (USD)

$$
\begin{aligned}
\min \mathbf{C_{tot}} = &C_{disp} + C_{unload} + C_{fom} \\
&+ C_{exp} \quad (\text{if capacity expansion is on}) \\
&+ C_{tra} \quad (\text{if regional exchange is on} ) \\
&+ C_{ramp} \quad (\text{if ramping is on} ) \\
&+ C_{op}\quad (\text{if operating reserves are on} )
\end{aligned} \tag{1}
$$

where:

Dispatch cost:

$$
\begin{aligned}
C_{disp} = \sum_{y \in Y} WY_y \sum_{h \in H} WD_h \Big( &\sum_{(r,t,s) | (r,t,s,y) \in \theta^{GSH}_h}{(SPR_{r,t,s,y,MHS_h} + NGADJ_{r,t,s,y,MHS_h}) \times \mathbf{GEN}_{r,t,s,y,h}} \\
&+ \sum_{(r,t,s) | (r,t,s,y) \in \theta^{SSH}_h}{\big( 0.5 \times SPR_{r,t,s,y,MHS_h} \times (\mathbf{STOR^{in}}_{r,t,s,y,h} + \mathbf{STOR^{out}}_{r,t,s,y,h})} \\
&+ HW_h \times STORLC \times \mathbf{STOR^{level}}_{r,t,s,y,h} \big) \Big)
\end{aligned} \tag{2}
$$

$NGADJ$ is zero in a standalone run. In an integrated run with the natural gas model, the
generation cost of the gas-linked technologies (the gas turbine in the shipped constants) follows
the gas price $p$ received for its region and year: half of $SPR$ is taken to be fuel bought at
4 USD/MMBtu, so the cost becomes $SPR \times (1 + 0.5 (p - 4)/4)$. Gauss-Seidel writes the change
into $NGADJ$ on the built model, and Jacobi rescales $SPR$ before each build; both give the same
cost.

Storage pays half its price $SPR$ on each MWh charged and half on each MWh discharged. With the efficiency
$EFF_t$ applied on charging, that is $0.5 \times SPR \times (1 + 1/EFF_t)$ per MWh discharged, about 1.09 to
1.10 times $SPR$ for the shipped efficiencies of 0.85 and 0.83. The shipped storage price is 0.0001 USD/MWh, so
this is a small charge on throughput; the split is the model's assumption, and its rationale is not recorded.
$STORLC$ is charged on the stored level for the $HW_h$ hours each time segment stands for. Hydrogen turbines are
generators, so their generation is priced by $SPR$ like any other.

Unmet load cost:

$$
\begin{aligned}
C_{unload} = \sum_{{r,y,h} \in \Theta_{um}}{ WD_h \times WY_y \times UMLPEN \times \mathbf{UNLOAD}_{r,y,h}}
\end{aligned} \tag{3}
$$

Capacity expansion cost, (4a) when `expansion_learning_type` is `nonlinear` and (4b) when it is `linear` or
`disabled`. It applies only when `capacity_expansion` is true; otherwise $C_{exp} = 0$.

$$
\begin{aligned}
C_{exp} = &\sum_{{r,t,s,y} \in \Theta_{new}} CAPC0_{r,t,s} \\
&\times \left (\frac{ SCL_t + \sum_{{r',t',s'} \in \Theta_{cc0} | t' = t}{ \sum_{y' \in Y | y'\lt y}{\mathbf{CAP^{new}}_{r',t,s',y'}}} }{SCL_t} \right) ^{-LR_t} \times \mathbf{CAP^{new}}_{r,t,s,y} \\
&\quad \text{if expansion learning type} = \mathtt{nonlinear}
\end{aligned} \tag{4a}
$$

The multiplier is driven solely by cumulative builds in strictly prior years, so a build never
discounts its own cost. The cumulative term pools that technology's builds across all modeled
regions and steps, written above as $r'$ and $s'$, so experience is shared rather than regional.
When `region_filter` limits the run, only the selected regions count.

The curve is computed by `learning_multiplier` in `src/models/electricity/learning.py`. Both
modes use it with the same experience, cumulative builds in strictly prior years: the nonlinear
objective directly, and the linear iteration through `cost_learning_func` in the same file, which
also holds the linear mode's other helpers. The linear mode's first solve comes before any
builds exist, so it is priced with zero experience, at $CAPC0$.

Solving with `nonlinear` requires a nonlinear solver. `select_solver` requests IPOPT, which is
**not currently a project dependency**, so this mode will not run without installing it.

Note $LR_t$ is the **curve exponent**, the `learning_exponent` column of `learning_rate.csv`. Each
doubling of $SCL_t$ plus prior builds cuts cost by $1 - 2^{-LR_t}$, so a learning rate $LR$ per
doubling converts as $LR_t = -\ln(1-LR)/\ln 2$. The file holds the exponents for rates of 1, 10
and 20 percent, rounded to 0.0145, 0.152 and 0.322. Those are the component learning rates in
NEMS (AEO2026 EMM Assumptions, Table 5, page 10). Which technology gets which rate comes from the
source data, which predates AEO2026; geothermal, for one, is 8 percent there and 10 percent here.

$$
\begin{aligned}
C_{exp} = &\sum_{{r,t,s,y} \in \Theta_{new}}{ CAPCL_{r,t,s,y} \times \mathbf{CAP^{new}}_{r,t,s,y}} \\
&\quad \text{if expansion learning type} \neq \mathtt{nonlinear}
\end{aligned} \tag{4b}
$$

With learning disabled, builds are priced from `capital_cost`, which falls by 2 percent of its 2023
value each year, to 46 percent of it by 2050. That decline stands in for learning. The learning
modes price builds from $CAPC0$ times the multiplier instead and do not also apply the decline:
the linear mode overwrites $CAPCL$ before each solve, and the nonlinear mode uses $CAPC0$ in
(4a).

The two learning modes are different formulations, not two ways of solving one problem. The
linear mode takes each solve's costs as given and iterates until they match its builds, so it
never builds early to lower later costs. The nonlinear mode puts the curve in the objective, so
it can; the problem is nonconvex, and IPOPT returns a local solution. The two can choose
different builds. On the linear learning test case they do not: load forces every build, so the
disabled, linear and nonlinear modes build the same capacity and differ only in price.

Fixed O\&M cost:

$$
\begin{aligned}
C_{fom} = \sum_{{r,t,s,y} \in \Theta_{SC}}{ WY_y \times FOMC_{r,t,s} \times \mathbf{CAP^{tot}}_{r,t,s,y}}
\end{aligned} \tag{5}
$$

Interregional trade cost:

$$
\begin{aligned}
C_{tra} = &\sum_{{r,r1,y,h} \in \Theta_{tra}}{ WD_h \times WY_y \times TRAC_{r,r1,y} \times \mathbf{TRA}_{r,r1,y,h}} \\
&+ \sum_{{r,r^{int},c,y,h} \in \Theta_{tracan}}{WD_h \times WY_y \times TRACC_{r,r^{int},c,y} \times \mathbf{TRA^{int}}_{r,r^{int},c,y,h}}
\end{aligned} \tag{6}
$$

Ramping cost:

$$
\begin{aligned}
C_{ramp} = \sum_{{r,t,s,y,h} \in \Theta_{ramp}}{ WD_h \times WY_y \times (RUC_t \times \mathbf{RAMP^{up}}_{r,t,s,y,h} + RDC_t \times \mathbf{RAMP^{down}}_{r,t,s,y,h})}
\end{aligned} \tag{7}
$$

Operating reserve cost:

$$
\begin{aligned}
C_{op} = \sum_{{r,rt,t,s,y,h} \in \Theta_{proc}}{ WD_h \times WY_y \times ORC_{rt,t} \times \mathbf{ORP}_{r,rt,t,s,y,h} }
\end{aligned} \tag{8}
$$

### Constraints

#### Balance Constraints

Balance constraints exist for generation as well as energy storage. In each region and time segment, generation, plus
storage discharge net of charging, plus unmet load, plus net imports when `regional_exchange` is true, must equal or
exceed load (9). Imports arrive less the transmission loss $LL$. International regions only supply domestic regions,
so the balance has no export term for them.

For energy storage technologies, the balance constraints ensure that the storage level in the current time segment is
equal to the storage level in the previous time-segment plus any storage charge and/or discharge (while also accounting
for round-trip efficiency losses).

Demand balance constraint:

$$
\begin{aligned}
LOAD_{r,y,h} \leq &\sum_{{t,s} \in \theta^{GDB}_{r,y,h}}{\mathbf{GEN}_{r,t,s,y,h}} \\
&+ \sum_{{t,s} \in \theta^{SDB}_{r,y,h}}{ (\mathbf{STOR^{out}}_{r,t,s,y,h} - \mathbf{STOR^{in}}_{r,t,s,y,h})} \\
&+ \mathbf{UNLOAD}_{r,y,h} \\
& (+ \sum_{r1 \in \theta^{TDB}_{r,y,h}}{\left (\mathbf{TRA}_{r,r1,y,h} \times (1 - LL) - \mathbf{TRA}_{r1,r,y,h}\right)} \quad \text{if regional exchange is on}) \\
& (+ \sum_{(r^{int},c) \in \theta^{TCDB}_{r,y,h}}{\mathbf{TRA^{int}}_{r,r^{int},c,y,h} \times (1 - LL)} \quad \text{if regional exchange is on}) \\
&\forall {r,y,h} \in \Theta_{load}
\end{aligned} \tag{9}
$$

First hour storage balance constraint:

$$
\begin{aligned}
\mathbf{STOR^{level}}_{r,t,s,y,h} = &\mathbf{STOR^{level}}_{r,t,s,y,h+N - 1} \\
&+ EFF_t \times \mathbf{STOR^{in}}_{r,t,s,y,h} - \mathbf{STOR^{out}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{SBFH}
\end{aligned} \tag{10}
$$

Storage balance (not first hour) constraint:

$$
\begin{aligned}
\mathbf{STOR^{level}}_{r,t,s,y,h} = &\mathbf{STOR^{level}}_{r,t,s,y,h - 1} \\
&+ EFF_t \times \mathbf{STOR^{in}}_{r,t,s,y,h} - \mathbf{STOR^{out}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{SBH}
\end{aligned} \tag{11}
$$

#### Generation Upper Bounds

Generation upper bound constraints limit generation, plus any operating reserves held, to what the capacity can deliver
over the time segment:

$$ \text{Generation} + \text{Reserves} \le \text{Capacity} \times \text{Availability} \times HW_h $$

Dispatchable technologies (13) have no availability factor: they can run at full capacity in every time segment, with no
outage or maintenance derate. Regular hydro (14) uses the seasonal hydro capacity factor, and intermittent technologies
(15) use their hourly capacity factors, both from the input data. Seasonal hydro instead has a seasonal energy budget
(12), set by the same seasonal factor. Both hydro technologies are also in the dispatchable set, so (13) applies to them
as well. Reserves enter these bounds only when `spinning_reserve_required` is true, and only for the reserve types a
technology can supply ($RTUB_{rt,t} > 0$). Storage holds reserves on the discharge side only (17). Charging (16) and
discharging plus reserves (17) are each limited by power capacity, and the stored energy (18) by power capacity times
duration.

Hydroelectric generation seasonal upper bound:

$$
\begin{aligned}
&\sum_{s | (r,t,s,y) \in \Theta_{SC}} \sum_{h \in \theta^{HSH}_{seas}}{\mathbf{GEN}_{r,t,s,y,h} \times WD_h} \leq \sum_{s | (r,t,s,y) \in \Theta_{SC}}{\mathbf{CAP^{tot}}_{r,t,s,y} \times CF^{seas}_{r,t,s,seas}} \times WHS_{seas} \\
&\forall {r,t,y,seas} \in \Theta_{hs}
\end{aligned} \tag{12}
$$

Dispatchable technology generation upper bound:

$$
\begin{aligned}
&\mathbf{GEN}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT | RTUB_{rt,t} > 0}{\mathbf{ORP}_{r,rt,t,s,y,h}} \quad \text{if operating reserves are on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{dt^{max}}
\end{aligned} \tag{13}
$$

Hydroelectric technology generation upper bound:

$$
\begin{aligned}
&\mathbf{GEN}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT | RTUB_{rt,t} > 0}{\mathbf{ORP}_{r,rt,t,s,y,h}} \quad \text{if operating reserves are on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times CF^{seas}_{r,t,s,MHS_h} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{ht^{max}}
\end{aligned} \tag{14}
$$

Intermittent technology upper bound:

$$
\begin{aligned}
&\mathbf{GEN}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT | RTUB_{rt,t} > 0}{\mathbf{ORP}_{r,rt,t,s,y,h}} \quad \text{if operating reserves are on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times CF^{hr}_{r,t,s,h} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{it^{max}}
\end{aligned} \tag{15}
$$

Storage technology inflow upper bound:

$$
\begin{aligned}
\mathbf{STOR^{in}}_{r,t,s,y,h} &\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{stor}
\end{aligned} \tag{16}
$$

Storage technology outflow upper bound:

$$
\begin{aligned}
&\mathbf{STOR^{out}}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT | RTUB_{rt,t} > 0}{\mathbf{ORP}_{r,rt,t,s,y,h}} \quad \text{if operating reserves are on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{stor}
\end{aligned} \tag{17}
$$

Storage technology level upper bound:

$$
\begin{aligned}
&\mathbf{STOR^{level}}_{r,t,s,y,h} \leq \mathbf{CAP^{tot}}_{r,t,s,y} \times STOR^{dur}_t \\
&\forall {r,t,s,y,h} \in \Theta_{stor}
\end{aligned} \tag{18}
$$

#### Capacity Expansion

With `capacity_expansion` true, the model can build and retire capacity in each solved year. Total capacity in year $y$
(19) is the existing capacity $CAP^{exist}$ given for year $y$, plus all builds in solved years up to and including $y$,
minus all retirements in solved years up to and including $y$. A change placed in year $y$ therefore counts for the whole
of year $y$; with year aggregation it also counts for every calendar year that $y$ stands for, and $CAP^{exist}$ is then
the average over those years. Builds are possible only for the technology and step pairs marked buildable in
`tech_build.csv`, and retirements only for those marked retirable in `tech_retire.csv`. The retirement bound (20) limits
retirements in year $y$ to the existing capacity of year $y$, plus builds in earlier solved years, minus retirements in
earlier solved years. With `capacity_expansion` false, (19) fixes total capacity at $CAP^{exist}$. The input data does
not record whether $CAP^{exist}$ describes the start or the end of a year.

Total capacity balance:

$$
\begin{aligned}
\mathbf{CAP^{tot}}_{r,t,s,y} = &CAP^{exist}_{r,t,s,y} \\
& (+ \sum_{y' \in Y | y' \leq y}{\mathbf{CAP^{new}}_{r,t,s,y'}} \quad \text{if capacity expansion is on and } (t,s) \text{ is buildable}) \\
& (- \sum_{y' \in Y | y' \leq y}{\mathbf{CAP^{ret}}_{r,t,s,y'}} \quad \text{if capacity expansion is on and } (t,s) \text{ is retirable}) \\
&\forall {r,t,s,y} \in \Theta_{SC}
\end{aligned} \tag{19}
$$

Capacity retirement upper bound:

$$
\begin{aligned}
\mathbf{CAP^{ret}}_{r,t,s,y} \leq &CAP^{exist}_{r,t,s,y} \\
& (+ \sum_{y' \in Y | y' \lt y}{\mathbf{CAP^{new}}_{r,t,s,y'}} \quad \text{if } (t,s) \text{ is buildable}) \\
&- \sum_{y' \in Y | y' \lt y}{\mathbf{CAP^{ret}}_{r,t,s,y'}} \\
&\forall {r,t,s,y} \in \Theta_{ret} \\
&\quad \text{if capacity expansion is on}
\end{aligned} \tag{20}
$$

#### Trade

Electricity trade constraints ensure that trade within any given time segment cannot exceed the capabilities of the
transmission lines between the regions trading. In addition, imports from an international region cannot exceed what
the region offers at each supply step. The line and supply limits are read by season and apply to every representative
hour of the season.

International interregional trade line capacity upper bound:

$$
\begin{aligned}
\sum_{c | (r,r^{int},c,y,h) \in \Theta_{tracan}}{\mathbf{TRA^{int}}_{r,r^{int},c,y,h}} \leq &TRALINLIM^{int}_{r,r^{int},y,h} \times HW_h \\
&\forall {r,r^{int},y,h} \in \Theta_{traLL^{int}} \\
&\quad \text{if regional exchange is on}
\end{aligned} \tag{21}
$$

International interregional trade resource capacity upper bound:

$$
\begin{aligned}
\sum_{r | (r,r^{int},c,y,h) \in \Theta_{tracan}}{\mathbf{TRA^{int}}_{r,r^{int},c,y,h}} \leq &TRALIM^{int}_{r^{int},c,y,h} \times HW_h \\
&\forall {r^{int},c,y,h} \in \Theta_{traL^{int}} \\
&\quad \text{if regional exchange is on}
\end{aligned} \tag{22}
$$

Domestic interregional trade line capacity upper bound:

$$
\begin{aligned}
\mathbf{TRA}_{r,r1,y,h} \leq &TRALINLIM_{r,r1,y,h} \times HW_h \\
&\forall {r,r1,y,h} \in \Theta_{traLL} \\
&\quad \text{if regional exchange is on}
\end{aligned} \tag{23}
$$

#### Reserve Margin

Reserve margin constraints ensure that, in each time segment, the capacity available exceeds load by the region's
planning reserve margin $RM_r$. They exist only when `reserve_margin_required` is true, which requires
`capacity_expansion`. Each (technology, step) in $\Theta_{SC}$ counts in exactly one of three groups: intermittent
technologies at their hourly capacity factor $CF^{hr}$ ($\theta^{rmh}$), hydro at its seasonal capacity factor
$CF^{seas}$ for the season of hour $h$ ($\theta^{rms}$), and all other technologies, storage included, at a factor of 1
($\theta^{rmc}$). Storage counts through $STOR^{avail}$ rather than its capacity. (25) caps $STOR^{avail}$ at the power capacity, and (26) at the
stored energy. (26) compares MW with MWh, as if the stored energy had to sustain $STOR^{avail}$ for one hour only,
although (24) counts it over the $HW_h$ hours of the time segment.

Reserve margin requirement constraint:

$$
\begin{aligned}
LOAD_{r,y,h} \times (1 + RM_r ) \leq HW_h \times \Big( &\sum_{(t,s) \in \theta^{rmh}_{r,y}}{CF^{hr}_{r,t,s,h} \times \mathbf{CAP^{tot}}_{r,t,s,y}} \\
&+ \sum_{(t,s) \in \theta^{rms}_{r,y}}{CF^{seas}_{r,t,s,MHS_h} \times \mathbf{CAP^{tot}}_{r,t,s,y}} \\
&+ \sum_{(t,s) \in \theta^{rmc}_{r,y} | t \notin T^{stor}}{\mathbf{CAP^{tot}}_{r,t,s,y}} \\
&+ \sum_{(t,s) \in \theta^{rmc}_{r,y} | t \in T^{stor}}{\mathbf{STOR^{avail}}_{r,t,s,y,h}} \Big) \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if the reserve margin is on}
\end{aligned} \tag{24}
$$

Constraint to ensure available storage capacity to meet RM <= power cap, upper bound:

$$
\begin{aligned}
\mathbf{STOR^{avail}}_{r,t,s,y,h} \leq &\mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \\
&\quad \text{if the reserve margin is on}
\end{aligned} \tag{25}
$$

Constraint to ensure available storage capacity to meet RM <= existing storage level, upper bound:

$$
\begin{aligned}
\mathbf{STOR^{avail}}_{r,t,s,y,h} \leq &\mathbf{STOR^{level}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \\
&\quad \text{if the reserve margin is on}
\end{aligned} \tag{26}
$$

#### Ramping

Ramping constraints limit how much generation can change from one representative hour to the next. Each representative
day is a cycle, so its first hour ramps from its last (27). The constraints exist only when `ramping_required` is true,
and they apply to the conventional technologies ($T^{conv}$, `is_conventional` in `tech.csv`) at every supply step. With
the shipped data (`ramp_rate.csv`, `ramp_up_cost.csv`, `ramp_down_cost.csv`):

| Technology         | Code | $RR_t$ | Ramp up cost (USD/MWh) | Ramp down cost (USD/MWh) |
|:-------------------|:-----|:------:|:----------------------:|:------------------------:|
| Coal Steam         | 1    | 0.25   | 0.01                   | 0.01                     |
| Oil Steam          | 2    | 0.40   | 0.01                   | 0.01                     |
| Gas Turbine        | 3    | 0.40   | 0.01                   | 0.01                     |
| Gas Combined Cycle | 4    | 0.45   | 0.01                   | 0.01                     |
| Nuclear            | 6    | 0.25   | 0.01                   | 0.01                     |

Other technologies have no ramping limit, including hydro, storage, the other renewables and the hydrogen turbine.

First hour ramping balance constraint:

$$
\begin{aligned}
\mathbf{GEN}_{r,t,s,y,h} = &\mathbf{GEN}_{r,t,s,y,h+N-1} + \mathbf{RAMP^{up}}_{r,t,s,y,h} - \mathbf{RAMP^{down}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp1} \\
&\quad \text{if ramping is on}
\end{aligned} \tag{27}
$$

Ramping balance (not first hour) constraint:

$$
\begin{aligned}
\mathbf{GEN}_{r,t,s,y,h} = &\mathbf{GEN}_{r,t,s,y,h-1} + \mathbf{RAMP^{up}}_{r,t,s,y,h} - \mathbf{RAMP^{down}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp23} \\
&\quad \text{if ramping is on}
\end{aligned} \tag{28}
$$

Ramp up upper bound:

$$
\begin{aligned}
\mathbf{RAMP^{up}}_{r,t,s,y,h} \leq &HW_h \times RR_t \times \mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp} \\
&\quad \text{if ramping is on}
\end{aligned} \tag{29}
$$

Ramp down upper bound:

$$
\begin{aligned}
\mathbf{RAMP^{down}}_{r,t,s,y,h} \leq &HW_h \times RR_t \times \mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp} \\
&\quad \text{if ramping is on}
\end{aligned} \tag{30}
$$

#### Operating Reserves

Operating reserves are capacity held back in each time segment to meet short-term needs from unexpected changes in
demand or in variable renewable output. When `spinning_reserve_required` is true, the model requires all three reserve
products together: spinning, regulation and flexibility (31 to 33).

The requirement coefficients match the operating reserve requirements of the ReEDS model: spinning reserves of 3 percent
of load; regulation reserves of 1 percent of load plus 0.5 percent of wind generation plus 0.3 percent of solar
capacity; and flexibility reserves of 10 percent of wind generation plus 4 percent of solar capacity (Sergi and Cole,
[Operating Reserves in ReEDS](https://docs.nlr.gov/docs/fy22osti/81706.pdf), NREL 2021, slide 3; Cole et al.,
[Operating Reserves in Long-term Planning Models](https://docs.nlr.gov/docs/fy18osti/71148.pdf), NREL 2018, slide 10).
ReEDS applies the solar terms during the day only, while this model applies them in every representative hour, night
included. The solar terms are multiplied by $HW_h$ so that, like the load and wind terms, they are energy over the time
segment.

Which technologies can supply each reserve type, and how much, is set by $RTUB_{rt,t}$ in `reserve_tech_limit.csv`: the
fraction of a technology's capacity, times $HW_h$, that it may hold as that reserve (34). A technology with a limit of 0
gets no reserve variable of that type. Reserves and generation share the same capacity, in (13), (14), (15) and (17).
With the shipped data, every supply step of these technologies is eligible:

| Technology         | Code        | Spinning | Regulation | Flex |
|:-------------------|:------------|:--------:|:----------:|:----:|
| Coal Steam         | 1           | 0.40     | 0.20       | 1.00 |
| Oil Steam          | 2           | 0.40     | 0.20       | 1.00 |
| Gas Turbine        | 3           | 0.80     | 0.40       | 1.00 |
| Gas Combined Cycle | 4           | 0.50     | 0.25       | 1.00 |
| Biomass            | 7           | 0.40     | 0.20       | 1.00 |
| Geothermal         | 8           | 0.40     | 0.20       | 1.00 |
| Municipal Waste    | 9           | 0.40     | 0.20       | 1.00 |
| Seasonal Hydro     | 10_seasonal | 1.00     | 1.00       | 1.00 |
| Regular Hydro      | 10_regular  | 1.00     | 1.00       | 1.00 |
| Pumped Hydro       | 11          | 1.00     | 1.00       | 1.00 |
| Battery Storage    | 12          | 1.00     | 1.00       | 1.00 |

The hydrogen turbine (5), nuclear (6), offshore and onshore wind (13, 14) and utility and end-use solar (15_utility,
15_end_use) have a limit of 0 for all three types and supply no reserves.

These limits match the ReEDS rule, which multiplies a technology's ramp rate, in percent of capacity per minute, by each
product's response time (10 minutes for spinning, 5 for regulation, 60 for flexibility), capped at 100 percent. The ramp
rates are those in Cole et al. (2018), slide 10: 8 for gas turbines, 5 for combined cycle, 4 for coal, oil and gas
steam, geothermal and biopower, and 100 for hydro. Storage qualifies on that slide but has no listed ramp rate, and
municipal waste is not listed; here storage has the limits of hydro, and municipal waste those of biomass. Sergi and
Cole (2021), slide 5, use 2 percent per minute for coal, which would give 0.20 for spinning and 0.10 for regulation.

Spinning reserve requirement constraint. 3% of load required:

$$
\begin{aligned}
0.03 \times LOAD_{r,y,h} \leq &\sum_{{t,s} \in \theta^{opres}_{r,\mathrm{spinning},y,h}}{\mathbf{ORP}_{r,\mathrm{spinning},t,s,y,h}} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if operating reserves are on}
\end{aligned} \tag{31}
$$

Regulation reserve requirement constraint. 1% of load + 0.5% of wind generation + 0.3% of solar capacity required:

$$
\begin{aligned}
&0.01 \times LOAD_{r,y,h} \\
{+} &0.005 \times \sum_{{t^w,s} \in \theta^{windor}_{r,y,h}}{\mathbf{GEN}_{r,t^w,s,y,h}} \\
{+} &0.003 \times HW_h \times \sum_{{t^s,s} \in \theta^{solor}_{r,y,h}}{\mathbf{CAP^{tot}}_{r,t^s,s,y}} \\
\leq &\sum_{{t,s} \in \theta^{opres}_{r,\mathrm{regulation},y,h}}{\mathbf{ORP}_{r,\mathrm{regulation},t,s,y,h}} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if operating reserves are on}
\end{aligned} \tag{32}
$$

Flexibility reserve requirement constraint. 10% of wind generation + 4% of solar capacity required:

$$
\begin{aligned}
&0.1 \times \sum_{{t^w,s} \in \theta^{windor}_{r,y,h}}{\mathbf{GEN}_{r,t^w,s,y,h}} \\
{+} &0.04 \times HW_h \times \sum_{{t^s,s} \in \theta^{solor}_{r,y,h}}{\mathbf{CAP^{tot}}_{r,t^s,s,y}} \\
\leq &\sum_{{t,s} \in \theta^{opres}_{r,\mathrm{flex},y,h}}{\mathbf{ORP}_{r,\mathrm{flex},t,s,y,h}} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if operating reserves are on}
\end{aligned} \tag{33}
$$

$\theta_{r,y,h}^{windor}$ and $\theta_{r,y,h}^{solor}$ hold every wind and solar (technology, step) in $\Theta_{SC}$,
whether or not it can supply reserves. Wind and solar have a reserve limit of 0 for every type in the shipped data, so
they add to the regulation and flexibility requirements without helping to meet them.

Operating reserve procurement upper bound:

$$
\begin{aligned}
\mathbf{ORP}_{r,rt,t,s,y,h} \leq &RTUB_{rt,t} \times HW_h \times \mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,rt,t,s,y,h} \in \Theta_{proc} \\
&\quad \text{if operating reserves are on}
\end{aligned} \tag{34}
$$

Storage reserves use discharge headroom (17) but are not limited by the state of charge.

## Data Validation

The raw input data is checked before the pyomo model is built. `ElectricitySequencer.build_model`
constructs the `ModelSets` and `ParamData`, applies any inbound update packages, and then calls
`validate_all(model_sets, param_data, strict=...)` from `src/models/electricity/data_validation.py`,
with `strict` taken from `strict_validation`. Validation therefore sees the parameter tables as the
model will use them: after region and year filtering, year and hour averaging, and the season-to-hour
expansion of the transmission tables, but before the data is handed to the model. Coverage gaps and
structural problems in the input CSVs are reported against that data, rather than surfacing later as
an obscure `KeyError` or an unexpectedly infeasible solve.

Each validation is independent and every one runs on each pass, so a single build reports all of the
problems found rather than stopping at the first. Findings are written to the log, the less serious
as warnings and the rest as errors. Any finding, warning or error, marks the validation as failed.

### Strict validation

The `strict_validation` switch in the `[common]` section of the run configuration
(`CommonConfig.strict_validation`, default `true`) controls what happens when validation fails:

- `strict_validation = true` — a failed validation raises `DataValidationError`
  (`src/common/exceptions.py`) after all the checks have run, so the build stops and the details
  are in the log. Warning-level findings stop the build too. This is the normal setting.
- `strict_validation = false` — failures are logged and the run continues with the data as read.
  Useful when working with known-incomplete input data, at the risk of a downstream failure or a
  silently mis-specified model.

### Validations performed

- **Seasonal coverage** (`validate_seasonal_coverage`) — for `generation_cost` and
  `capacity_factor_seasonal`, every base index (the index with the season removed) must carry exactly
  the model's expected set of seasons: no gaps and no strays.
- **Hourly coverage** (`validate_hourly_coverage`) — the same check on the hour dimension for the
  transmission tables `ParamData` expands from season to hour (`tran_limit`, `tran_limit_cap_intl`,
  `supply_limit_intl`), reporting the missing and unexpected hours for each offending base index.
- **Capacity index vs. generation cost coverage** (`validate_generation_cost_coverage`) — every
  location in the capacity index (existing `available_capacity` plus anything buildable) must have a
  `generation_cost`; a location with no cost is fatal. A cost with no matching capacity index entry
  is allowed and ignored (logged as a warning).
- **Domestic transmission network** (`validate_domestic_network`) — neither `tran_limit` nor
  `tran_cost` may contain a self-loop (a region trading with itself), and the two must cover the
  same `(destination, source, year)` links. Self-loops and a cost without a matching limit are
  logged as warnings, and a limit without a matching cost as an error. A self-loop stops the coverage
  comparison, so coverage gaps show up only once the loops are removed.
- **Build cost coverage** (`validate_build_cost_coverage`) — when `capacity_expansion` is on,
  `capital_cost` is screened against every `region_analyze` × `tech_buildable` × year. This check
  is advisory: a gap is logged at INFO, naming the (region, tech, step) and the missing years, and
  does not fail validation, since the build set is indexed from the `capital_cost` keys and a gap
  simply leaves that key unbuildable. With learning enabled, `capital_cost_initial` must cover every
  `region_analyze` × `tech_buildable`, and a missing entry is an error naming the key. Extra
  entries in either table are allowed and ignored.

  To make a technology buildable in a region and year:

  - Flag its (tech, step) pair `True` in the `is_buildable` column of `tech_build.csv`.
  - Give it a `capital_cost` entry for that region and year. Any (region, tech, step, year) with no
    `capital_cost` entry is implicitly dropped from the build set, so a buildable pair can be left
    out of a region or year by leaving out its cost.
  - If `expansion_learning_type` is `linear` or `nonlinear`, also give it a `capital_cost_initial`
    entry in every analyzed region. Unlike `capital_cost`, gaps here are errors.
  - Give it a `generation_cost` entry for every year and season. A buildable location joins the
    capacity index even with no `available_capacity`, so it is dispatched and must be priced; a
    missing entry fails validation (and would otherwise fail the model build).
  - Give it a `fom_cost` entry for that region. Fixed O&M is charged on every capacity index entry
    and has no default.
  - For a VRE tech, give it `capacity_factor_hourly` entries for that region. These default to
    0.0, so a missing profile is not an error: the location can be built but generates nothing.

The hourly-coverage and transmission-network checks are only run when `regional_exchange` is
enabled, since the transmission tables are otherwise unused.

## Model Features

### Year Aggregation

The model solves only the years listed in `summary_years`. Year aggregation lets each of those
solved years stand in for a *block* of calendar years, so that a run with a handful of solved years
still accounts for costs over the full horizon. It is controlled by three fields in the `[common]`
section of the run configuration (`CommonConfig`):

| Field                  | Type          | Role                                                                                    |
|:-----------------------|:--------------|:----------------------------------------------------------------------------------------|
| `summary_years`        | `list[int]`   | The representative (solved) years. These are the only members of the model's year set. |
| `aggregate_years`      | `bool`        | Turns aggregation on. When `false`, each summary year represents only itself.          |
| `aggregate_start_year` | `int`         | The first calendar year covered by aggregation. The key must be present even when `aggregate_years = false`, where it is ignored. |

When `aggregate_years = true`, `CommonConfig` rejects an `aggregate_start_year` later than the earliest summary
year.

#### Year mapping and weights

`ModelSets` builds two dictionaries from these settings:

- `year_map` maps every calendar year from `aggregate_start_year` through the last summary year onto
  a representative year: each year maps to the **first summary year at or after it**, so a summary
  year represents itself and the years since the previous summary year.
- `year_agg_weights` counts the calendar years mapped onto each representative year. It becomes the
  `weight_year` parameter ($WY_y$).

For example, with `aggregate_start_year = 2023` and `summary_years = [2025, 2030, 2035]`:

| Representative year | Calendar years aggregated | Weight ($WY_y$) |
|:--------------------|:--------------------------|:---------------:|
| 2025                | 2023 – 2025               |        3        |
| 2030                | 2026 – 2030               |        5        |
| 2035                | 2031 – 2035               |        5        |

Years before `aggregate_start_year` and after the last summary year are not represented at all.
With `aggregate_years = false`, `year_map` is the identity on `summary_years` and every weight is 1.

#### What is aggregated

Aggregation touches the model data in two ways.

**Input averaging.** `ParamData.aggregate_time` uses `year_map` to average any year-indexed table
over the calendar years mapped to each representative year (`avg_by_group` in
`param_utilities.py`). So that every year in a block is available, `ParamData` reads the parameter
CSVs with a year filter of all the years in `year_map`: `aggregate_start_year` through the last
summary year when aggregating, and `summary_years` alone otherwise. Averaging is applied to:

- `elec_load` — the load for a representative year is the mean load over its block of calendar
  years. It is also averaged over the hours each representative hour stands for, and then multiplied
  by $HW_h$.
- The year-indexed time-based tables: `available_capacity`, `generation_cost`, `capital_cost`,
  `tran_cost`, `tran_cost_intl`, `tran_limit`, `tran_limit_cap_intl`, and `supply_limit_intl`.

**Missing data stops the run.** `avg_by_group` requires complete data within every block it
averages. It runs two checks, and on a failure it logs an error and raises a `ValueError` naming
the table:

- the table lacks a value the map expects, anywhere in the table;
- some rows of the table (for example one region, technology and step) lack a value within a block
  that other rows carry. The error gives how many such groups there are and lists the first few.

The year checks run on every year-indexed table listed above, against `year_map`. With aggregation
off that map is one-to-one, so they only catch a summary year missing from a table. The hour checks
run on the tables with an hour column, `elec_load` and `capacity_factor_hourly`, against the map from
the hours of the year to the representative hours, whether or not years are aggregated. A
combination with no rows at all in a block is ordinary sparsity and passes, and an empty table is
not checked.

This is deliberately a showstopper rather than a warning. An average over an incomplete block
cannot tell what the missing years mean. A missing row might be a retired plant (really zero), a
unit not yet online, or a gap in the source data, and each calls for a different value. Averaging
over just the years present picks one answer silently. For example, a plant with capacity in 2023
and 2024 but no 2025 row would show full capacity for the whole 2023–2025 block. That value then
enters the sparse index sets and the objective with no sign of where it came from. Stopping the run
makes the data owner decide: fill the missing years explicitly (zeros where appropriate), or
choose `summary_years` / `aggregate_start_year` so that the blocks match the data.

`capacity_factor_hourly` has no year index, and the model uses the same factors in every summary
year. Like `elec_load`, it is still averaged over the hours each representative hour stands for,
whether or not years are aggregated. `capacity_factor_seasonal`, indexed by region and season only, is
used as read, and so are the parameters loaded as plain dictionaries, such as `fom_cost` and
`capital_cost_initial`.

**Cost weighting.** $WY_y$ multiplies the recurring, per-year costs in the objective so that each
representative year is charged for every calendar year it stands in for:

- dispatch cost ($C_{disp}$, eq. 2)
- unmet load cost ($C_{unload}$, eq. 3)
- fixed O&M cost ($C_{fom}$, eq. 5)
- trade cost ($C_{tra}$, eq. 6)
- ramping cost ($C_{ramp}$, eq. 7)
- operating reserve cost ($C_{op}$, eq. 8)

The capacity expansion cost ($C_{exp}$, eq. 4a/4b) is **not** weighted: a build is a one-time
cost, incurred once whichever year it is placed in. The constraints do not use $WY_y$; each
representative year is operated as a single typical year.
