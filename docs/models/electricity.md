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
| $Y$                    | year                                          | Sparse set | All selected model years                                     |
| $SEAS$                 | season                                        | Set        | All seasons                                                  |
| $D$                    | day                                           | Set        | All representative days                                      |
| $R$                    | region                                        | Set        | All selected model domestic regions                          |
| $R^{int}$              | region_int                                    | Set        | All selected model international regions                     |
| $\Theta_{load}$        | elec_load.index_set()                         | Sparse set | All load sparse set                                          |
| $\Theta_{gen}$         | generation_index                              | Sparse set | All non-storage generation sparse set                        |
| $\Theta_{H2gen}$       | h2_generation_index                           | Sparse set | All hydrogen generation sparse set                           |
| $\Theta_{stor}$        | storage_index                                 | Sparse set | All storage set                                              |
| $\Theta_{um}$          | unmet_load.index_set()                        | Sparse Set | All unmet load set                                           |
| $\Theta_{SC}$          | capacity_index                                | Sparse Set | Existing capacity set                                        |
| $\Theta_{dt^{max}}$    | generation_dispatchable_ub_index              | Sparse Set | Dispatchable technology generation upper bound set           |
| $\Theta_{it^{max}}$    | generation_vre_ub_index                       | Sparse Set | Intermittent technology generation upper bound set           |
| $\Theta_{ht^{max}}$    | generation_hydro_ub_index                     | Sparse Set | Hydroelectric generation upper bound set                     |
| $\Theta_{hs}$          | capacity_hydro_ub_index                       | Sparse Set | Hydroelectric generation seasonal upper bound set            |
| $\Theta_{ret}$         | capacity_retirements_index                    | Sparse Set | Retirable capacity set                                       |
| $\Theta_{new}$         | capacity_builds.index_set()                   | Sparse Set | Buildable capacity set                                       |
| $\Theta_{cc}$          | capital_cost.index_set()                      | Sparse Set | Set of capacity costs                                        |
| $\Theta_{cc0}$         | capital_cost_initial.index_set()              | Sparse Set | Set of initial year's capacity costs                         |
| $\Theta_{SBFH}$        | storage_first_hour_balance_index              | Sparse set | First hour storage balance set                               |
| $\Theta_{SBH}$         | storage_most_hours_balance_index              | Sparse set | (non-first hour) storage balance set                         |
| $\Theta_{proc}$        | reserves_procurement_index                    | Sparse set | Set for procurement of operating reserves                    |
| $\Theta_{ramp}$        | generation_ramp_index                         | Sparse set | Set for ramping                                              |
| $\Theta_{ramp1}$       | ramp_first_hour_balance_index                 | Sparse set | Set for ramping in first hour of each representative day     |
| $\Theta_{ramp23}$      | ramp_most_hours_balance_index                 | Sparse set | Set for ramping in non-first hour of each representative day |
| $\Theta_{tra}$         | trade_interregional.index_set()               | Sparse set | Domestic interregional trade set                             |
| $\Theta_{tracan}$      | international_trade_index                     | Sparse set | International interregional trade set                        |
| $\Theta_{traL^{int}}$  | trade_international_generation_ub.index_set() | Sparse set | International interregional trade limit set                  |
| $\Theta_{traLL}$       | tran_limit.keys()                             | Sparse set | Domestic interregional trade limit set                       |
| $\Theta_{traLL^{int}}$ | trade_international_capacity_ub.index_set()   | Sparse set | International interregional trade line limit set             |

### Re-Indexed Sets

These sets are re-indexed for specific constraints. They are all sub-sets accessed by certain indices to return the
remaining indices.

| Set                        | Code                      | Data Type     | Short Description                                                                   |
|:---------------------------|:--------------------------|:--------------|:------------------------------------------------------------------------------------|
| $\theta^{H2H}_h$           | h2_generation_hour_index  | Sparse subset | Set for H2 generation indexed by hour                                               |
| $\theta^{GSH}_h$           | generation_hour_index     | Sparse subset | Set for generation indexed by hour                                                  |
| $\theta^{SSH}_h$           | storage_hour_index        | Sparse subset | Set for storage indexed by hour                                                     |
| $\theta^{GDB}_{r,y,h}$     | generation_demand_balance | Sparse subset | Set for generation indexed by r,y,h                                                 |
| $\theta^{SDB}_{r,y,h}$     | storage_demand_balance    | Sparse subset | Set for storage indexed by r,y,h                                                    |
| $\theta^{TDB}_{r,y,h}$     | regional_sources          | Sparse subset | Set for trade indexed by r,y,h                                                      |
| $\theta^{TCDB}_{r,y,h}$    | international_partners    | Sparse subset | Set for international trade indexed by r,y,h                                        |
| $\theta^{windor}_{r,y,h}$  | wind_reserves             | Sparse subset | Set for wind generation for operational reserves indexed by r,y,h                   |
| $\theta^{solor}_{r,y,h}$   | solar_reserves            | Sparse subset | Set for solar capacity for operational reserves indexed by r,y,h                    |
| $\theta^{opres}_{r,o,y,h}$ | eligible_reserves         | Sparse subset | Set for procurement of operating reserves for operational reserves indexed by r,o,y,h |
| $\theta^{scrm}_{r,y}$      | capacity_sources          | Sparse subset | Set for supply curve for reserve margin indexed by r,y                              |
| $\theta^{HSH}_{seas}$      | hour_season_index         | Sparse subset | Set for hours indexed by season                                                     |

### Parameters

Note: the model works in MW, MWh and USD throughout; input files carry their units in the value column name
(e.g. `generation_cost_usd_per_mwh`) and are read without scaling. Quantities indexed by representative hour $h$
(load, generation, storage flows, trade, ramping, reserve procurement) are energy over the time segment that hour
stands for, i.e. power times $HW_h$, so they are in MWh; capacities and line limits are in MW.

| Parameter                         | Code                     | Domain           | Short Description                                                                            | Units                      |
|:----------------------------------|:-------------------------|:-----------------|:---------------------------------------------------------------------------------------------|:---------------------------|
| $N$                               | num_hr_day               | $\mathbb{I}$     | Number of representative hours in a representative day                                       | unitless                   |
| $LOAD_{r,y,h}$                    | elec_load                | $\mathbb{R}^+_0$ | Electricity demand                                                                           | MWh                        |
| $CAP^{exist}_{r,t,s,y}$           | available_capacity       | $\mathbb{R}^+_0$ | Existing capacity (prescribed or initial)                                                    | MW                         |
| $SPR_{r,t,s,y,seas}$              | generation_cost          | $\mathbb{R}^+_0$ | Fuel + variable O&M price                                                                    | USD/MWh                    |
| $ICF_{r,t,s,y,h}$                 | capacity_factor_vre      | $\mathbb{R}^+_0$ | Intermittent technology maximum capacity factor                                              | fraction                   |
| $HCF_{r,seas}$                    | hydro_capacity_factor    | $\mathbb{R}^+_0$ | Hydroelectric technology maximum capacity factor                                             | fraction                   |
| $STORLC$                          | storage_level_cost       | $\mathbb{R}^+_0$ | Cost to hold storage (mimics losses)                                                         | USD/MWh                    |
| $EFF_t$                           | storage_efficiency       | $\mathbb{R}^+_0$ | Roundtrip efficiency of storage                                                              | fraction                   |
| $STOR^{dur}_t$                    | storage_duration         | $\mathbb{R}^+_0$ | Storage duration                                                                             | hours                      |
| $UMLPEN$                          | unmet_load_penalty       | $\mathbb{R}^+_0$ | Unmet load penalty                                                                           | USD/MWh                    |
| $WY_y$                            | weight_year              | $\mathbb{I}$     | number of years represented by a representative year (weight)                                | years/representative years |
| $HW_h$                            | weight_hour              | $\mathbb{I}$     | number of hours represented by a representative hours(weight)                                | hours/representative hours |
| $WeightDay_d$                     | weight_day               | $\mathbb{I}$     | number of days represented by a representative day (weight)                                 | days/representative day    |
| $MHD_h$                           | map_hour_day             | $\mathbb{I}$     | map representative hour to representative day                                                | unitless                   |
| $WHS_{seas}$                      | weight_season            | $\mathbb{I}$     | number of hours (per year) in a season (weight)                                              | unitless                   |
| $MHS_h$                           | map_hour_season          | $\mathbb{I}$     | map representative hour to season                                                            | unitless                   |
| $FOMC_{r,t,s}$                    | fom_cost                 | $\mathbb{R}^+_0$ | Fixed O&M cost                                                                               | USD/MW-year                |
| $CC_{r,t,s,y,h}$                  | capacity_credit          | $\mathbb{R}^+_0$ | Capacity credit                                                                              | fraction                   |
| $RM_r$                            | planning_reserve_margin  | $\mathbb{R}^+_0$ | Reserve margin requirement                                                                   | fraction                   |
| $RUC_{t}$                         | ramp_up_cost             | $\mathbb{R}^+_0$ | Ramp up cost                                                                                 | USD/MWh                    |
| $RDC_{t}$                         | ramp_down_cost           | $\mathbb{R}^+_0$ | Ramp down cost                                                                               | USD/MWh                    |
| $RR_t$                            | ramp_rate                | $\mathbb{R}^+_0$ | Max ramp rate                                                                                | fraction of capacity per hour |
| $TRALINLIM_{r,r1,y,h}$            | tran_limit               | $\mathbb{R}^+_0$ | Domestic interregional trade line limit                                                      | MW                         |
| $TRALIM^{int}_{r^{int},c,y,h}$    | supply_limit_intl        | $\mathbb{R}^+_0$ | International interregional trade limit                                                      | MW                         |
| $TRALINLIM^{int}_{r,r^{int},y,h}$ | tran_limit_cap_intl      | $\mathbb{R}^+_0$ | International interregional trade line limit                                                 | MW                         |
| $TRAC_{r,r1,y}$                   | tran_cost                | $\mathbb{R}^+_0$ | Transmission hurdle rate (cost)                                                              | USD/MWh                    |
| $TRACC_{r,r^{int},c,y}$           | tran_cost_intl           | $\mathbb{R}^+_0$ | International transmission hurdle rate (cost)                                                | USD/MWh                    |
| $LL$                              | TRANSMISSION_LOSS_FACTOR | $\mathbb{R}^+_0$ | Transmission line losses from 1 region to another                                            | fraction                   |
| $ORC_{o,t}$                       | reserve_cost             | $\mathbb{R}^+_0$ | Cost of operating reserve procurement, by reserve type and technology                        | USD/MWh                    |
| $RTUB_{o,t}$                      | reserve_tech_limit       | $\mathbb{R}^+_0$ | Maximum amount of capacity which can be used to procure operating reserves                   | fraction                   |
| $CAPCL_{r,t,s,y}$                 | capital_cost             | $\mathbb{R}^+_0$ | Cost to build capacity, from `capital_cost`; the linear mode reprices it before each solve   | USD/MW                     |
| $CAPC0_{r,t,s}$                   | capital_cost_initial     | $\mathbb{R}^+_0$ | Initial cost to build capacity, scaled by the learning multiplier in the learning modes      | USD/MW                     |
| $LR_t$                            | learning_rate            | $\mathbb{R}^+_0$ | Learning curve exponent                                                                      | unitless                   |
| $SCL_t$                           | supply_curve_learning    | $\mathbb{R}^+$   | Baseline capacity the learning curve is measured from.  Must be strictly positive, since the curve divides by it and needs the base of the fractional power to stay positive | MW                         |

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
| $TRA^{int}_{r,r^{int},c,y,h}$ | trade_international  | $\mathbb{R}^+_0$ | International interregional trade from region $r^{int}$ to region $r$ | MWh   | Only created if regional_exchange is True         |
| $RAMP^{up}_{r,t,s,y,h}$       | generation_ramp_up   | $\mathbb{R}^+_0$ | Ramp up (increase in generation for dispatchable cap)                 | MWh   | Only created if ramping_required is True          |
| $RAMP^{down}_{r,t,s,y,h}$     | generation_ramp_down | $\mathbb{R}^+_0$ | Ramp down (decrease in generation for dispatchable cap)               | MWh   | Only created if ramping_required is True          |
| $ORP_{r,o,t,s,y,h}$           | reserves_procurement | $\mathbb{R}^+_0$ | Operating reserves procurement amount                                 | MWh   | Only created if spinning_reserve_required is True |
| $STOR^{avail}_{r,t,s,y,h}$    | storage_avail_cap    | $\mathbb{R}^+_0$ | Available storage capacity to meet the reserve margin                 | MW    | Only created if reserve_margin_required is True   |

### Objective Function

Objective is to minimize costs to the electric power system. Costs include dispatch cost (e.g., variable O&M cost),
fixed operation and maintenance (FOM)
cost, capacity expansion cost component (nonlinear and linear options available), interregional trade cost, ramping
cost, operating reserve cost and unmet load cost (note: unmet load cost should equal zero).

Minimize total cost (USD)

$$
\begin{aligned}
\min \mathbf{C_{tot}} = &C_{disp}+ C_{unload} \\
&+ C_{exp} + C_{fom} \quad (\text{if capacity expansion is on}) \\
&+ C_{tra} \quad (\text{if regional exchange is on} ) \\
&+ C_{ramp} \quad (\text{if ramping is on} ) \\
&+ C_{op}\quad (\text{if operating reserves are on} ) \qquad (1)
\end{aligned}
$$

where:

Dispatch cost:

$$
\begin{aligned}
C_{disp} = \sum_{h \in H | s=MHS_h}{} WD_h \Big (&\sum_{{r,t,s,y} \in \theta^{GSH}_h}{WY_y \times SPR_{r,t,s,y,seas} \times \mathbf{GEN}_{r,t,s,y,h}} \\
&+\sum_{{r,t,s,y} \in \theta^{SSH}_h}{ (WY_y \times (0.5 \times SPR_{r,t,s,y,seas} \times (\mathbf{STOR^{in}}_{r,t,s,y,h} + \mathbf{STOR^{out}}_{r,t,s,y,h})} \\
&+ (HW_h \times STORLC) \times \mathbf{STOR^{level}}_{r,t,s,y,h})) \\
&+\sum_{{r,t,s,y} \in \theta^{H2SH}_h}{WY_y \times H2PR_{r,t,s,y,seas} \times H2HR \times \mathbf{GEN}_{r,t,1,y,h}}\Big) \qquad (2)
\end{aligned}
$$

Unmet load cost:

$$
\begin{aligned}
C_{unload} = \sum_{{r,y,h} \in \Theta_{um}}{ WD_h \times WY_y \times UMLPEN \times \mathbf{UNLOAD}_{r,y,h}} \qquad (3)
\end{aligned}
$$

Capacity expansion cost, (4a) when `expansion_learning_type` is `nonlinear` and (4b) when it is `linear` or
`disabled`:

$$
\begin{aligned}
C_{exp} = &\sum_{{r,t,s,y} \in \Theta_{cc}} CAPC0_{r,t,s} \\
&\times \left (\frac{ SCL_t + \sum_{{r',t',s'} \in \Theta_{cc0} | t' = t}{ \sum_{y' \in Y | y'\lt y}{\mathbf{CAP^{new}}_{r',t,s',y'}}} }{SCL_t} \right) ^{-LR_t} \times \mathbf{CAP^{new}}_{r,t,s,y} \\
&\quad \text{if expansion learning type} = \mathtt{nonlinear} \qquad (\text{4a})
\end{aligned}
$$

The multiplier is driven solely by cumulative builds in strictly prior years, so a build never
discounts its own cost. The cumulative term pools that technology's builds across **all**
regions and steps, written above as $r'$ and $s'$, so experience is national rather than regional.

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
NEMS (AEO2026 EMM Assumptions, Table 5, page 11). Which technology gets which rate comes from the
source data, which predates AEO2026; geothermal, for one, is 8 percent there and 10 percent here.

$$
\begin{aligned}
C_{exp} = &\sum_{{r,t,s,y} \in \Theta_{cc}}{ CAPCL_{r,t,s,y} \times \mathbf{CAP^{new}}_{r,t,s,y}} \\
&\quad \text{if expansion learning type} \neq \mathtt{nonlinear} \qquad (\text{4b})
\end{aligned}
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
different builds. On the linear learning test case, the nonlinear mode builds ahead of load in
the first two years, while the linear mode builds what the disabled mode does.

Fixed O\&M cost:

$$
\begin{aligned}
C_{fom} = \sum_{{r,t,s,y} \in \Theta_{SC}}{ WY_y \times FOMC_{r,t,s} \times \mathbf{CAP^{tot}}_{r,t,s,y}} \qquad (5)
\end{aligned}
$$

Interregional trade cost:

$$
\begin{aligned}
C_{tra} = &\sum_{{r,r1,y,h} \in \Theta_{tra}}{ WD_h \times WY_y \times TRAC_{r,r1,y} \times \mathbf{TRA}_{r,r1,y,h}} \\
&+ \sum_{{r,r^{int},c,y,h} \in \Theta_{tracan}}{WD_h \times WY_y \times TRACC_{r,r^{int},c,y} \times \mathbf{TRA^{int}_{r,r^{int},c,y,h}}} \qquad (6)
\end{aligned}
$$

Ramping cost:

$$
\begin{aligned}
C_{ramp} = \sum_{{r,t,s,y,h} \in \Theta_{ramp}}{ WD_h \times WY_y \times (RUC_t \times \mathbf{RAMP^{up}}_{r,t,s,y,h} + RDC_t \times \mathbf{RAMP^{down}}_{r,t,s,y,h})} \qquad (7)
\end{aligned}
$$

Operating reserve cost:

$$
\begin{aligned}
C_{op} = \sum_{{r,o,t,s,y,h} \in \Theta_{proc}}{ WD_h \times WY_y \times ORC_{o,t} \times \mathbf{ORP}_{r,o,t,s,y,h} } \qquad (8)
\end{aligned}
$$

### Constraints

#### Balance Constraints

Balance constraints exist for generation as well as energy storage. For demand, this means that generation must equal
or exceed demand for electricity.

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
& (+ \sum_{r_{int},c \in \theta^{TCDB}_{r,y,h}}{ (\mathbf{TRA}^{int}_{r,r_{int},c,y,h} \times (1 - LL) - \mathbf{TRA}^{int}_{r_{int},r,c,y,h})} \quad \text{if regional exchange is on}) \\
&\forall {r,y,h} \in \Theta_{load} \qquad (9)
\end{aligned}
$$

First hour storage balance constraint:

$$
\begin{aligned}
\mathbf{STOR^{level}}_{r,t,s,y,h} = &\mathbf{STOR^{level}}_{r,t,s,y,h+N - 1} \\
&+ EFF_t \times \mathbf{STOR^{in}}_{r,t,s,y,h} - \mathbf{STOR^{out}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{SBFH} \qquad (10)
\end{aligned}
$$

Storage balance (not first hour) constraint:

$$
\begin{aligned}
\mathbf{STOR^{level}}_{r,t,s,y,h} = &\mathbf{STOR^{level}}_{r,t,s,y,h - 1} \\
&+ EFF_t \times \mathbf{STOR^{in}}_{r,t,s,y,h} - \mathbf{STOR^{out}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{SBH} \qquad (11)
\end{aligned}
$$

#### Generation Upper Bounds

Generation upper bound constraints limit generation from generating technologies, accounting for reserve requirements,
operating capacity, and capacity factors where:

$$ \text{Generation} + \text{Reserve procurement} \le \text{Capacity} \times \text{Capacity factor} $$

This is the same constraint for dispatchable, hydroelectric, and intermittent technologies. For intermittent
technologies, the capacity factors are exogenously specified in the input data. In addition, hydroelectric generation
has an additional seasonal constraint, where hydroelectric capacity is seasonally limited based on assumed availability
of water resources seasonally, as specified in the input data. Storage upper bound constraints need to account for the
upper bounds on both the charge and discharge of the technology, as well as the operating level in any given time
segment.

Hydroelectric generation seasonal upper bound:

$$
\begin{aligned}
&\sum_{h \in \theta^{HSH}_{seas}}{\mathbf{GEN}_{r,t,1,y,h} \times WeightDay_{MHD_{h}}} \leq \mathbf{CAP^{tot}}_{r,t,1,y} \times HCF_{r,seas} \times WHS_{seas} \\
&\forall {r,t,y,seas} \in \Theta_{hs} \qquad (12)
\end{aligned}
$$

Dispatchable technology generation upper bound:

$$
\begin{aligned}
&\mathbf{GEN}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT}{\mathbf{OPRP}_{r,rt,t,s,y,h}} \quad \text{if the reserve margin is on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{dt^{max}} \qquad (13)
\end{aligned}
$$

Hydroelectric technology generation upper bound:

$$
\begin{aligned}
&\mathbf{GEN}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT}{\mathbf{OPRP}_{r,rt,t,s,y,h}} \quad \text{if the reserve margin is on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HCF_{r,MHS_h} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{ht^{max}} \qquad (14)
\end{aligned}
$$

Intermittent technology upper bound:

$$
\begin{aligned}
&\mathbf{GEN}_{r,t,s,y,h} \\
& (+ \sum_{rt \in RT}{\mathbf{OPRP}_{r,rt,t,s,y,h}} \quad \text{if the reserve margin is on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times ICF_{r,t,s,y,h} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{it^{max}} \qquad (15)
\end{aligned}
$$

Storage technology inflow upper bound:

$$
\begin{aligned}
\mathbf{STOR^{in}}_{r,t,s,y,h} &\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \qquad (16)
\end{aligned}
$$

Storage technology outflow upper bound:

$$
\begin{aligned}
&\mathbf{STOR^{out}}_{r,t,s,y,h} \\
& (+\sum_{rt \in RT}{\mathbf{OPRP}_{r,rt,t,s,y,h}} \quad \text{if the reserve margin is on}) \\
&\leq \mathbf{CAP^{tot}}_{r,t,s,y} \times HW_h \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \qquad (17)
\end{aligned}
$$

Storage technology level upper bound:

$$
\begin{aligned}
&\mathbf{STOR^{level}}_{r,t,s,y,h} \leq \mathbf{CAP^{tot}}_{r,t,s,y} \times STOR^{dur}_t \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \qquad (18)
\end{aligned}
$$

#### Capacity Expansion

The model can build new generating technologies each year (when the expansion switch is turned on). The expansion
constraint ensures that operating capacity is based on the capacity in the previous year, plus any additions and minus
any retirements. The retirement constraint ensures that retirements never exceed the capacity available on the system.

Total capacity balance:

$$
\begin{aligned}
\mathbf{CAP^{tot}}_{r,t,s,y} = &CAP^{exist}_{r,t,s,y} \\
& (+ \sum_{cy \in Y \leq y}{\mathbf{CAP^{new}}_{r,t,s,cy}} \quad \text{if capacity expansion is on}) \\
& (- \sum_{cy \in Y \leq y}{\mathbf{CAP^{ret}}_{r,t,s,cy}} \quad \text{if capacity expansion is on}) \\
&\forall {r,t,s,y} \in \Theta_{SC} \qquad (19)
\end{aligned}
$$

Capacity retirement upper bound:

$$
\begin{aligned}
\mathbf{CAP^{ret}}_{r,t,s,y} \leq &CAP^{exist}_{r,t,s,y} + \sum_{cy \in Y \lt y}{\mathbf{CAP^{new}}_{r,t,s,cy}} - \sum_{cy \in Y \lt y}{\mathbf{CAP^{ret}}_{r,t,s,cy}} \\
&\forall {r,t,s,y} \in \Theta_{ret} \\
&\quad \text{if capacity expansion is on} \qquad (20)
\end{aligned}
$$

#### Trade

Electricity trade constraints ensure that trade within any given time segment cannot exceed the capabilities of the
transmission lines between the regions trading. In addition, there are supply quantity/price constraints for
international trade, where supply from international regions cannot exceed the availability from the region.

International interregional trade line capacity upper bound:

$$
\begin{aligned}
\sum_{c}{\mathbf{TRA^{int}}_{r,r^{int},c,y,h}} \leq &TRALINLIM^{int}_{r,r^{int},y,h} * HW_h \\
&\forall {r,r^{int},y,h} \in \Theta_{traLL^{int}} \\
&\quad \text{if regional exchange is on} \qquad (21)
\end{aligned}
$$

International interregional trade resource capacity upper bound:

$$
\begin{aligned}
\sum_{r}{\mathbf{TRA^{int}}_{r,r^{int},c,y,h}} \leq &TRALIM^{int}_{r^{int},c,y,h} * HW_h \\
&\forall {r,r^{int},y,h} \in \Theta_{traL^{int}} \\
&\quad \text{if regional exchange is on} \qquad (22)
\end{aligned}
$$

Domestic interregional trade line capacity upper bound:

$$
\begin{aligned}
\mathbf{TRA}_{r,r1,y,h} \leq &TRALINLIM_{r,r1,y,h} * HW_h \\
&\forall {r,r1,y,h} \in \Theta_{traLL} \\
&\quad \text{if regional exchange is on} \qquad (23)
\end{aligned}
$$

#### Reserve Margin

Reserve margin constraints ensure that there is additional quantity of capacity available beyond load requirements in
each time segment. Available capacity that can contribute to the reserve margin is also potentially decremented based on
capacity credit assumptions. Storage technologies have additional reserve margin constraints that account for both the power
capacity and the energy capacity availability towards contributing to reserve margin requirements.

Reserve margin requirement constraint:

$$
\begin{aligned}
LOAD_{r,y,h} \times (1 + RM_r ) \leq &HW_h \times \\
&\sum_{{t,s} \in \theta^{scrm}_{r,y}}{CC_{r,t,s,y,h} \times (\mathbf{STOR^{avail}}_{r,t,s,y,h} + \mathbf{CAP^{tot}_{r,t,s,y}})} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if the reserve margin is on} \qquad (24)
\end{aligned}
$$

Constraint to ensure available storage capacity to meet RM <= power cap, upper bound:

$$
\begin{aligned}
\mathbf{STOR^{avail}}_{r,t,s,y,h} \leq &\mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \\
&\quad \text{if the reserve margin is on} \qquad (25)
\end{aligned}
$$

Constraint to ensure available storage capacity to meet RM <= existing storage level, upper bound:

$$
\begin{aligned}
\mathbf{STOR^{avail}}_{r,t,s,y,h} \leq &\mathbf{STOR^{level}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{stor} \\
&\quad \text{if the reserve margin is on} \qquad (26)
\end{aligned}
$$

#### Ramping

Ramping constraints ensure that generating technologies are limited in the rate in which they can increase or decrease
their generation from one time segment to the next. Ramping capabilities are balanced within each day.

First hour ramping balance constraint:

$$
\begin{aligned}
\mathbf{GEN}_{r,t,s,y,h} = &\mathbf{GEN}_{r,t,s,y,h+N-1} + \mathbf{RAMP^{up}}_{r,t,s,y,h} - \mathbf{RAMP^{down}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp1} \\
&\quad \text{if ramping is on} \qquad (27)
\end{aligned}
$$

Ramping balance (not first hour) constraint:

$$
\begin{aligned}
\mathbf{GEN}_{r,t,s,y,h} = &\mathbf{GEN}_{r,t,s,y,h-1} + \mathbf{RAMP^{up}}_{r,t,s,y,h} - \mathbf{RAMP^{down}}_{r,t,s,y,h} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp23} \\
&\quad \text{if ramping is on} \qquad (28)
\end{aligned}
$$

Ramp up upper bound:

$$
\begin{aligned}
\mathbf{RAMP^{up}}_{r,t,s,y,h} \leq &HW_h \times RR_t \times \mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp} \\
&\quad \text{if ramping is on} \qquad (29)
\end{aligned}
$$

Ramp down upper bound:

$$
\begin{aligned}
\mathbf{RAMP^{down}}_{r,t,s,y,h} \leq &HW_h \times RR_t \times \mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,t,s,y,h} \in \Theta_{ramp} \\
&\quad \text{if ramping is on} \qquad (30)
\end{aligned}
$$

#### Operating Reserves

The model allows for three different types of operating reserves to be represented within the model, either spinning
reserves, regulation reserves, or flexibility reserve requirements. These operating reserves reflect the need for
additional capacity to be held in reserve to meet short-term needs for generation from unexpected changes in
things like electricity demand or variable renewable generation output.

Spinning reserve requirement constraint. 3\% of load required:

$$
\begin{aligned}
0.03 \times LOAD_{r,y,h} \leq &\sum_{{t,s} \in \theta^{opres}_{r,1,y,h}}{\mathbf{ORP}_{r,1,t,s,y,h}} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if operating reserves are on} \qquad (31)
\end{aligned}
$$

Regulation reserve requirement constraint. 1\% of load + 0.5\% of wind generation + 0.3\% of solar capacity required:

$$
\begin{aligned}
&0.01 \times LOAD_{r,y,h} \\
{+} &0.005 \times \sum_{{t^w,s} \in \theta^{windor}_{r,y,h}}{\mathbf{GEN}_{r,t^w,s,y,h}} \\
{+} &0.003 \times HW_h \times \sum_{{t^s,s} \in \theta^{solor}_{r,y,h}}{\mathbf{CAP^{tot}}_{r,t^s,s,y}} \\
\leq &\sum_{{t,s} \in \theta^{opres}_{r,2,y,h}}{\mathbf{ORP}_{r,2,t,s,y,h}} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if operating reserves are on} \qquad (32)
\end{aligned}
$$

Flexibility reserve requirement constraint. 10\% of wind generation + 4\% of solar capacity required:

$$
\begin{aligned}
&0.1 \times \sum_{{t^w,s} \in \theta^{windor}_{r,y,h}}{\mathbf{GEN}_{r,t^w,s,y,h}} \\
{+} &0.04 \times HW_h \times \sum_{{t^s,s} \in \theta^{solor}_{r,y,h}}{\mathbf{CAP^{tot}}_{r,t^s,s,y}} \\
\leq &\sum_{{t,s} \in \theta^{opres}_{r,3,y,h}}{\mathbf{ORP}_{r,3,t,s,y,h}} \\
&\forall {r,y,h} \in \Theta_{load} \\
&\quad \text{if operating reserves are on} \qquad (33)
\end{aligned}
$$

Operating reserve procurement upper bound:

$$
\begin{aligned}
\mathbf{ORP}_{r,o,t,s,y,h} \leq &RTUB_{o,t} \times HW_h \times \mathbf{CAP^{tot}}_{r,t,s,y} \\
&\forall {r,o,t,s,y,h} \in \Theta_{proc} \\
&\quad \text{if operating reserves are on} \qquad (34)
\end{aligned}
$$

## Data Validation

The raw input data is checked before the pyomo model is built. `ElectricitySequencer.build_model`
constructs the `ModelSets` and `ParamData` as usual and then calls
`validate_all(model_sets, param_data)` from `src/models/electricity/data_validation.py`. Validation
therefore sees the ingested parameter tables — after region/year filtering and the season-to-hour
expansions performed by `ParamData`, but before the data is handed to the model — so coverage gaps
and structural problems in the input CSVs are reported against the data the model will actually use,
rather than surfacing later as an obscure `KeyError` or an unexpectedly infeasible solve.

Each validation is independent and every one runs on each pass, so a single build reports all of the
problems found rather than stopping at the first. Findings are written to the log: recoverable
issues as warnings, showstoppers as errors.

### Strict validation

The `strict_validation` switch in the `[common]` section of the run configuration
(`CommonConfig.strict_validation`, default `true`) controls what happens when validation fails:

- `strict_validation = true` — a failed validation raises `DataValidationError`
  (`src/common/exceptions.py`) after all the checks have run, so the build stops and the details
  are in the log. This is the normal setting.
- `strict_validation = false` — failures are logged and the run continues with the data as read.
  Useful when working with known-incomplete input data, at the risk of a downstream failure or a
  silently mis-specified model.

### Validations performed

- **Seasonal coverage** (`validate_seasonal_coverage`) — for `generation_cost` and
  `hydro_capacity_factor`, every base index (the index with the season removed) must carry exactly
  the model's expected set of seasons: no gaps and no strays.
- **Hourly coverage** (`validate_hourly_coverage`) — the same check on the hour dimension for the
  transmission tables `ParamData` expands from season to hour (`tran_limit`, `tran_limit_cap_intl`,
  `supply_limit_intl`), reporting the missing and unexpected hours for each offending base index.
- **Supply curve vs. supply price coverage** (`validate_supply_price_coverage`) — the supply curve
  and the supply price tables must cover each other. A price with no matching supply entry is a
  warning; a supply entry with no price is fatal.
- **Domestic transmission network** (`validate_domestic_network`) — neither `tran_limit` nor
  `tran_cost` may contain a self-loop (a region trading with itself), and the two must cover the
  same `(destination, source, year)` links. Self-loops and a cost without a matching limit are
  warnings; a limit without a matching cost is fatal.

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
| `aggregate_start_year` | `int`, optional | The first calendar year covered by aggregation.                                         |

`CommonConfig` requires `aggregate_start_year` whenever `aggregate_years = true`, and rejects an
`aggregate_start_year` later than the earliest summary year.

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
  years.
- The year-indexed time-based tables: `available_capacity`, `generation_cost`, `capital_cost`,
  `tran_cost`, `tran_cost_intl`, `tran_limit`, `tran_limit_cap_intl`, and `supply_limit_intl`.

**Missing data stops the run.** `avg_by_group` requires complete data within every block it
averages. It logs an error and raises a `ValueError`, naming the table, in either of two cases:

- the table lacks a year (or, for the hourly aggregation in the same step, an hour) that the map
  expects, anywhere in the table;
- some rows of the table (for example one region/technology/step combination) lack a year that
  other rows carry. The error gives how many such groups there are and lists the first few.

A combination with no rows at all in a block is ordinary sparsity and passes, and an empty table
is not checked. The check applies with aggregation off as well. There the map is one-to-one, so
it only catches a summary year missing from a table.

This is deliberately a showstopper rather than a warning. An average over an incomplete block
cannot tell what the missing years mean. A missing row might be a retired plant (really zero), a
unit not yet online, or a gap in the source data, and each calls for a different value. Averaging
over just the years present picks one answer silently. For example, a plant with capacity in 2023
and 2024 but no 2025 row would show full capacity for the whole 2023–2025 block. That value then
enters the sparse index sets and the objective with no sign of where it came from. Stopping the run
makes the data owner decide: fill the missing years explicitly (zeros where appropriate), or
choose `summary_years` / `aggregate_start_year` so that the blocks match the data.

`capacity_factor_vre` and `hydro_capacity_factor` have no year index and are not aggregated.
Neither are the parameters loaded as plain dictionaries, such as `fom_cost` and
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
