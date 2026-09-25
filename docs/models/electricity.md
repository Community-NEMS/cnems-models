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
| $SEA$                  | season                                        | Set        | All seasons                                                  |
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
| $\Theta_{cc}$          | cap_cost.index_set()                          | Sparse Set | Set of  capacity costs                                       |
| $\Theta_{cc0}$         | cap_cost_initial.index_set()                  | Sparse Set | Set of initial year's capacity costs                         |
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
| $\theta^{GDB}_{y,r,h}$     | generation_demand_balance | Sparse subset | Set for generation indexed by y,r,h                                                 |
| $\theta^{SDB}_{y,r,h}$     | storage_demand_balance    | Sparse subset | Set for storage indexed by y,r,h                                                    |
| $\theta^{TDB}_{y,r,h}$     | regional_sources          | Sparse subset | Set for trade indexed by y,r,h                                                      |
| $\theta^{TCDB}_{y,r,h}$    | international_partners    | Sparse subset | Set for international trade indexed by y,r,h                                        |
| $\theta^{windor}_{y,r,h}$  | wind_reserves             | Sparse subset | Set for wind generaton for operational reserves indexed by y,r,h                    |
| $\theta^{solor}_{y,r,h}$   | solar_reserves            | Sparse subset | Set for solar capacity for operational reserves indexed by y,r,h                    |
| $\theta^{opres}_{y,r,h}$   | eligible_reserves         | Sparse subset | Set for procurement of operating reserves for operational reserves indexed by y,r,h |
| $\theta^{scrm}_{y,r,seas}$ | capacity_sources          | Sparse subset | Set for supply curve for reserve margin indexed by y,r,seas                         |
| $\theta^{HSH}_{seas}$      | hour_season_index         | Sparse subset | Set for hours indexed by season                                                     |

### Parameters

Note: the existing code shows cost units in MW/MWh instead of GW/GWh; we are aware and just haven't updated the code
yet.

| Parameter                         | Code                     | Domain           | Short Description                                                                            | Units                      |
|:----------------------------------|:-------------------------|:-----------------|:---------------------------------------------------------------------------------------------|:---------------------------|
| $YR0$                             | y0_learning              | $\mathbb{I}$     | First year of model                                                                          | unitless                   |
| $N$                               | num_hr_day               | $\mathbb{I}$     | Number of representative hours in a representative day                                       | unitless                   |
| $LOAD_{r,y,h}$                    | elec_load                | $\mathbb{R}^+_0$ | Electricity demand                                                                           | instantaneous GW           |
| $CAP^{exist}_{r,seas,t,s,y}$      | supply_curve             | $\mathbb{R}^+_0$ | Existing capacity (prescribed or initial)                                                    | GW                         |
| $SPR_{r,seas,t,s,y}$              | supply_price             | $\mathbb{R}^+_0$ | Fuel + variable O&M price                                                                    | \$/GWh                     |
| $ICF_{t,y,r,s,h}$                 | cap_factor_vre           | $\mathbb{R}^+_0$ | Intermittent technology maximum capacity factor                                              | fraction                   |
| $HCF_{t,y,r,s,h}$                 | hydro_cap_factor         | $\mathbb{R}^+_0$ | Hydroelectric technology maximum capacity factor                                             | fraction                   |
| $STORLC$                          | storage_level_cost       | $\mathbb{R}^+_0$ | Cost to hold storage (mimics losses)                                                         | \$/GWh                     |
| $EFF_t$                           | battery_efficiency       | $\mathbb{R}^+_0$ | Roundtrip efficiency of storage                                                              | fraction                   |
| $STOR^{dur}_t$                    | hours_to_buy             | $\mathbb{R}^+_0$ | Storage duration                                                                             | hours                      |
| $UMLPEN$                          | unmet_load_penalty       | $\mathbb{R}^+_0$ | Unmet load penalty                                                                           | \$/GWh                     |
| $WY_y$                            | weight_year              | $\mathbb{I}$     | number of years represented by a representative year (weight)                                | years/representative years |
| $HW_h$                            | weight_hour              | $\mathbb{I}$     | number of hours represented by a representative hours(weight)                                | hours/representative hours |
| $WeightDay_d$                     | weight_day               | $\mathbb{I}$     | number of days representated by a representative day (weight)                                | days/representative day    |
| $MHD_h$                           | map_hour_day             | $\mathbb{I}$     | map representative hour to representative day                                                | unitless                   |
| $WHS_{seas}$                      | weight_season            | $\mathbb{I}$     | number of hours (per year) in a season (weight)                                              | unitless                   |
| $MHS_h$                           | map_hour_season          | $\mathbb{I}$     | map representative hour to season                                                            | unitless                   |
| $FOMC_{r,t,s}$                    | fom_cost                 | $\mathbb{R}^+_0$ | Fixed O&M cost                                                                               | \$/GW-year                 |
| $CC_{t,y,r,s,h}$                  | capacity_credit          | $\mathbb{R}^+_0$ | Capacity credit                                                                              | fraction                   |
| $RM_r$                            | reserve_margin           | $\mathbb{R}^+_0$ | Reserve margin requirement                                                                   | fraction                   |
| $RUC_{t}$                         | ramp_up_cost             | $\mathbb{R}^+_0$ | Ramp up cost                                                                                 | \$/GW                      |
| $RDC_{t}$                         | ramp_down_cost           | $\mathbb{R}^+_0$ | Ramp down cost                                                                               | \$/GW                      |
| $RR_t$                            | ramp_rate                | $\mathbb{R}^+_0$ | Max ramp rate                                                                                | GW                         |
| $TRALINLIM_{r,r1,seas,y}$         | tran_limit               | $\mathbb{R}^+_0$ | Domestic interregional trade line limit                                                      | GW                         |
| $TRALIM^{int}_{r^{int},c,y,h}$    | tran_limit_gen_int       | $\mathbb{R}^+_0$ | International interregional trade limit                                                      | GW                         |
| $TRALINLIM^{int}_{r,r^{int},y,h}$ | tran_limit_cap_int       | $\mathbb{R}^+_0$ | International interregional trade line limit                                                 | GW                         |
| $TRAC_{r,r1,y}$                   | tran_cost                | $\mathbb{R}^+_0$ | Transmission hurdle rate (cost)                                                              | \$/GWh                     |
| $TRACC_{r,r^{int},c,y}$           | tran_cost_int            | $\mathbb{R}^+_0$ | International transmission hurdle rate (cost)                                                | \$/GWh                     |
| $LL$                              | TRANSMISSION_LOSS_FACTOR | $\mathbb{R}^+_0$ | Transmission line losses from 1 region to another                                            | fraction                   |
| $OPRP_t$                          | reg_reserves_cost        | $\mathbb{R}^+_0$ | Cost of operating reserve procurement (TODO: update this in code so it contains all optypes) | \$/GWh                     |
| $RTUB_{o,t}$                      | res_tech_upper_bound     | $\mathbb{R}^+_0$ | Maximum amount of capacity which can be used to procure operating reserves                   | fraction                   |
| $H2HR$                            | h2_heatrate              | $\mathbb{R}^+_0$ | Hydrogen heatrate                                                                            | kg/GWh                     |
| $H2PR_{r,seas,t,s,y}$             | h2_price                 | $\mathbb{R}^+_0$ | Hydrogen fuel price. Mutable parameter.                                                      | \$/kg                      |
| $CAPCL_{r,t,y,s}$                 | cap_cost                 | $\mathbb{R}^+_0$ | Cost of capacity based on technology learning. Mutable parameter.                            | \$/GW                      |
| $CAPC0_{r,t,s}$                   | cap_cost_initial         | $\mathbb{R}^+_0$ | Initial year's capacity cost to build                                                        | \$/GW                      |
| $LR_t$                            | learning_rate            | $\mathbb{R}^+_0$ | Learning rate factor                                                                         | unitless                   |
| $SCL_t$                           | supply_curve_learning    | $\mathbb{R}^+$   | Baseline capacity the learning curve is measured from.  Must be strictly positive, since the curve divides by it and needs the base of the fractional power to stay positive | GW                         |

### Variables

| Variable                      | Code                 | Domain           | Short Description                                                     | Units | Switch notes                                      |
|:------------------------------|:---------------------|:-----------------|:----------------------------------------------------------------------|:------|:--------------------------------------------------|
| $STOR^{in}_{t,y,r,s,h}$       | storage_inflow       | $\mathbb{R}^+_0$ | Storage inflow                                                        | GW    |                                                   |
| $STOR^{out}_{t,y,r,s,h}$      | storage_outflow      | $\mathbb{R}^+_0$ | Storage outflow                                                       | GW    |                                                   |
| $STOR^{level}_{t,y,r,s,h}$    | storage_level        | $\mathbb{R}^+_0$ | Storage level (state-of-charge)                                       | GWh   |                                                   |
| $GEN_{t,y,r,s,h}$             | generation_total     | $\mathbb{R}^+_0$ | Instantaneous generation                                              | GW    |                                                   |
| $UNLOAD_{r,y,h}$              | unmet_load           | $\mathbb{R}^+_0$ | Unmet load                                                            | GW    |                                                   |
| $CAP^{tot}_{r,seas,t,s,y}$    | capacity_total       | $\mathbb{R}^+_0$ | Total capacity                                                        | GW    |                                                   |
| $CAP^{new}_{r,t,y,s}$         | capacity_builds      | $\mathbb{R}^+_0$ | New capacity built                                                    | GW    | Only created if capacity_expansion is True        |
| $CAP^{ret}_{t,y,r,s}$         | capacity_retirements | $\mathbb{R}^+_0$ | Retirement capacity                                                   | GW    | Only created if capacity_expansion is True        |
| $TRA_{r,r1,y,h}$              | trade_interregional  | $\mathbb{R}^+_0$ | Interregional trade from region $r1$ to region $r$                    | GW    | Only created if regional_exchange is True         |
| $TRA^{int}_{r,r^{int},y,c,h}$ | trade_international  | $\mathbb{R}^+_0$ | International interregional trade from region $r^{int}$ to region $r$ | GW    | Only created if regional_exchange is True         |
| $RAMP^{up}_{t,y,r,s,h}$       | generation_ramp_up   | $\mathbb{R}^+_0$ | Ramp up (increase in generation for dispatchable cap)                 | GW    | Only created if ramping_required is True          |
| $RAMP^{down}_{t,y,r,s,h}$     | generation_ramp_down | $\mathbb{R}^+_0$ | Ramp down (decrease in generation for dispatchable cap)               | GW    | Only created if ramping_required is True          |
| $ORP_{o,t,y,r,s,h}$           | reserves_procurement | $\mathbb{R}^+_0$ | Operating reserves procurement amount                                 | GW    | Only created if spinning_reserve_required is True |
| $STOR^{avail}_{t,y,r,s,h}R$   | storage_avail_cap    | $\mathbb{R}^+_0$ | Available storage capacity to meet the reserve margin                 | GW    | Only created if reserve_margin_required is True   |

### Objective Function

Objective is to minimize costs to the electric power system. Costs include dispatch cost (e.g., variable O&M cost),
fixed operation and maintenance (FOM)
cost, capacity expansion cost component (nonlinear and linear options available), interregional trade cost, ramping
cost, operating reserve cost and unmet load cost (note: unmet load cost should equal zero).

Minimize total cost (\$)

$$
\begin{aligned} \min \mathbf{C_{tot}} = &C_{disp}+ C_{unload} \\ &+ C_{exp} + C_{fom} \quad (\text{if } \mathtt{capacity\_expansion})\\ &+ C_{tra} \quad (\text{if } \mathtt{regional\_exchange} )\\ &+ C_{ramp} \quad (\text{if } \mathtt{ramping\_required} )\\ &+ C_{or}\quad (\text{if } \mathtt{spinning\_reserve\_required} )
\end{aligned} \tag{1}
$$

where:

Dispatch cost:

$$
\begin{aligned} C_{disp} = \sum_{h \in H | s=MHS_h}{} WD_h \Big (&\sum_{{t,y,r,s} \in \theta^{GSH}_h}{WY_y \times SPR_{r,seas,t,s,y} \times \mathbf{GEN}_{t,y,r,s,h}}\\ &+\sum_{{t,y,r,s} \in \theta^{SSH}_h}{ (WY_y \times (0.5 \times SPR_{r,seas,t,s,y} \times (\mathbf{STOR^{in}}_{t,y,r,s,h} + \mathbf{STOR^{out}}_{t,y,r,s,h})}\\ &+ (HW_h \times STORLC) \times \mathbf{STOR^{level}}_{t,y,r,s,h}))\\ &+\sum_{{t,y,r,s} \in \theta^{H2SH}_h}{WY_y \times H2PR_{r,seas,t,s,y} \times H2HR \times \mathbf{GEN}_{t,y,r,1,h}}\Big)
\end{aligned} \tag{2}
$$

Unmet load cost:

$$
\begin{aligned} C_{unload} = \sum_{{r,y,h} \in \Theta_{um}}{ WD_h \times WY_y \times UMLPEN \times \mathbf{UNLOAD}_{r,y,h}} \end{aligned} \tag{3}
$$

Capacity expansion cost:

$$
\begin{aligned} C_{exp} = &\sum_{{r,t,y,s} \in \Theta_{cc}} CAPC0_{r,t,s}\\ &\times \left (\frac{ SCL_t
+ \sum_{{r',t',s'} \in \Theta_{cc0} | t' = t}{ \sum_{y' \in Y | y'\lt y}{\mathbf{CAP^{new}}_{r',t,y',s'}}} }{SCL_t} \right) ^{-LR_t} \times \mathbf{CAP^{new}}_{r,t,y,s} \\ &\quad \text{if } \mathtt{expansion\_learning\_type} = \mathtt{nonlinear} \end{aligned} \tag{4a}
$$

The multiplier is driven solely by cumulative builds in strictly prior years, so a build never
discounts its own cost. The cumulative term pools that technology's builds across **all**
regions and steps, written above as $r'$ and $s'$, so experience is national rather than regional.

The curve is computed by `learning_multiplier` in `src/models/electricity/learning.py`. Both
modes use it: the nonlinear objective directly, and the linear iteration through
`cost_learning_func` in the same file, which also holds the linear mode's other helpers.

Solving with `nonlinear` requires a nonlinear solver. `select_solver` requests IPOPT, which is
**not currently a project dependency**, so this mode will not run without installing it.

One difference from the linear path is known and **not** addressed here. The linear formula still
carries a calendar-time drift term $d \times (y - YR0)$ with $d = 0.0001$ GW/year, which the
nonlinear form above omits. It is an absolute quantity divided by a technology-specific $SCL_t$
spanning 0.01 to 264 GW, so its effect varies by roughly four orders of magnitude across
technologies. On the reference dataset it produced the entire measurable output of nonlinear
learning while endogenous learning contributed nothing, which is why the revived nonlinear form
leaves it out.

Note $LR_t$ is consumed **directly as the curve exponent**, while the input file names its column
`rate`. If those values are learning rates meaning fractional reduction per doubling, the exponent
would instead be $-\ln(1-LR_t)/\ln 2$. That ambiguity is unresolved, so no conversion is applied and
no quantitative result from this mode should be treated as calibrated until it is settled.

$$
\begin{aligned} C_{exp} = &\sum_{{r,t,y,s} \in \Theta_{cc}}{ CAPCL_{r,t,y,s} \times \mathbf{CAP^{new}}_{r,t,y,s}} \\ &\quad \text{if } \mathtt{expansion\_learning\_type} \neq \mathtt{nonlinear} \end{aligned} \tag{4b}
$$

Fixed O\&M cost:

$$
\begin{aligned} C_{fom} = \sum_{{r,seas,t,s,y} \in \Theta_{sc} | seas=2}{ WY_y \times FOMC_{r,t,s} \times \mathbf{CAP^{tot}}_{r,seas,t,s,y}} \end{aligned} \tag{5}
$$

Interregional trade cost:

$$
\begin{aligned} C_{tra} = &\sum_{{r,r1,y,h} \in \Theta_{tra}}{ WD_h \times WY_y \times TRAC_{r,r1,y} \times \mathbf{TRA}_{r,r1,y,h}}\\ &+ \sum_{{r,r^{int},y,c,h} \in \Theta_{tracan}}{WD_h \times WY_y \times TRACC_{r,r^{int},c,y} \times \mathbf{TRA^{int}_{r,r^{int},y,c,h}}} \end{aligned} \tag{6}
$$

Ramping cost:

$$
\begin{aligned} C_{ramp} = \sum_{{t,y,r,s,h} \in \Theta_{ramp}}{ WD_h \times WY_y \times (RUC_t \times \mathbf{RAMP^{up}}_{t,y,r,s,h} + RDC_t \times \mathbf{RAMP^{up}}_{t,y,r,s,h})} \end{aligned} \tag{7}
$$

Operating reserve cost:

$$
\begin{aligned} C_{op} = \sum_{{o,t,y,r,s,h} \in \Theta_{orp}}{ WD_h \times WY_y \times ORC_t \times \mathbf{ORP}_{o,t,y,r,s,h} } \end{aligned} \tag{8}
$$

### Constraints

#### Balance Constraints

Balance constraints exist for generation as well as energy storage. For demand, this means that generation must equal to
or exceed demand for electricity.

For energy storage technologies, the balance constraints ensure that the storage level in the current time segment is
equal to the storage level in the previous time-segment plus any storage charge and/or discharge (while also accounting
for round-trip efficiency losses).

Demand balance constraint:

$$
\begin{aligned} LOAD_{r,y,h} \leq &\sum_{{t,s} \in \theta^{GDB}_{y,r,h}}{\mathbf{GEN}_{t,y,r,s,h}}\\ &+ \sum_{{t,s} \in \theta^{SDB}_{y,r,h}}{ (\mathbf{STOR^{out}}_{t,y,r,s,h} - \mathbf{STOR^{in}}_{t,y,r,s,h})}\\ &+ \mathbf{UNLOAD}_{r,y,h}\\ & (+ \sum_{r1 \in \theta^{TDB}_{y,r,h}}{\left (\mathbf{TRA}_{r,r1,y,h} \times (1 - LL) - \mathbf{TRA}_{r1,r,y,h}\right)} \quad \text{if } \mathtt{regional\_exchange})\\ & (+ \sum_{r_{int},c \in \theta^{TCDB}_{y,r,h}}{ (\mathbf{TRA}^{int}_{r,r_{int},y,c,h} \times (1 - LL) - \mathbf{TRA}^{int}_{r_{int},r,y,c,h})} \quad \text{if } \mathtt{regional\_exchange})\\ &\forall {r,y,h} \in \Theta_{load} \end{aligned} \tag{1}
$$

First hour storage balance constraint:

$$
\begin{aligned} \mathbf{STOR^{level}}_{t,y,r,s,h} = &\mathbf{STOR^{level}}_{t,y,r,s,h+N - 1}\\ &+ EFF_t \times \mathbf{STOR^{in}}_{t,y,r,s,h} - \mathbf{STOR^{out}}_{t,y,r,s,h}\\ &\forall {t,y,r,s,h} \in \Theta_{SBFH} \end{aligned} \tag{2}
$$

Storage balance (not first hour) constraint:

$$
\begin{aligned} \mathbf{STOR^{level}}_{t,y,r,s,h} = &\mathbf{STOR^{level}}_{t,y,r,s,h - 1}\\ &+ EFF_t \times \mathbf{STOR^{in}}_{t,y,r,s,h} - \mathbf{STOR^{out}}_{t,y,r,s,h}\\ &\forall {t,y,r,s,h} \in \Theta_{SBH} \end{aligned} \tag{3}
$$

#### Generation Upper Bounds

Generation upper bound constraints limit generation from generating technologies, accounting for reserve requirements,
operating capacity, and capacity factors where:

$$ \begin{aligned} Generation + Reserve Procurement \le Capacity \times Capacity Factor \end{aligned} $$

This is the same constraint for dispatchable, hydroelectric, and intermittent technologies. For intermittent
technologies, the capacity factors are exogenously specified in the input data. In addition, hydroelectric generation
has an additional seasonal constraint, where hydroelectric capacity is seasonally limited based on assumed availability
of water resources seasonally, as specified in the input data. Storage upper bound constraints need to account for the
upper bounds on both the charge and discharge of the technology, as well as the operating level in any given time
segment.

Hydroelectric generation seasonal upper bound:

$$
\begin{aligned} &\sum_{h \in \theta^{HSH}_{seas}}{\mathbf{GEN}_{t,y,r,1,h} \times WeightDay_{MHD_{h}}} \leq \mathbf{CAP^{tot}}_{r,seas,t,1,y} \times HCF_{r,seas} \times WHS_{seas}\\ &\forall {t,y,r,seas} \in \Theta_{hs} \end{aligned} \tag{4}
$$

Dispatchable technology generation upper bound:

$$
\begin{aligned} &\mathbf{GEN}_{t,y,r,s,h} \\ & (+ \sum_{rt \in RT}{\mathbf{OPRP}_{rt,t,y,r,s,h}} \quad \text{if } \mathtt{reserve\_margin\_required})\\ &\leq \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y} \times HW_h\\ &\forall {t,y,r,s,h} \in \Theta_{dt^{max}} \end{aligned} \tag{5}
$$

Hydroelectric technology generation upper bound:

$$
\begin{aligned} &\mathbf{GEN}_{t,y,r,s,h} \\ & (+ \sum_{rt \in RT}{\mathbf{OPRP}_{rt,t,y,r,s,h}} \quad \text{if } \mathtt{reserve\_margin\_required})\\ &\leq \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y} \times HCF_{r,MHS_h} \times HW_h\\ &\forall {t,y,r,s,h} \in \Theta_{ht^{max}} \end{aligned} \tag{6}
$$

Intermittent technology upper bound:

$$
\begin{aligned} \mathbf{GEN}_{t,y,r,s,h} \\ & (+ \sum_{rt \in RT}{\mathbf{OPRP}_{rt,t,y,r,s,h}} \quad \text{if } \mathtt{reserve\_margin\_required})\\ &\leq \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y} \times ICF_{t,y,r,s,h} \times HW_h\\ &\forall {t,y,r,s,h} \in \Theta_{it^{max}} \end{aligned} \tag{7}
$$

Storage technology inflow upper bound:

$$
\begin{aligned} \mathbf{STOR^{in}}_{t,y,r,s,h} + &\leq \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y} \times HW_h\\ &\forall {t,y,r,s,h} \in \Theta_{stor} \end{aligned} \tag{8}
$$

Storage technology outflow upper bound:

$$
\begin{aligned} &\mathbf{STOR^{out}}_{t,y,r,s,h} \\ & (+\sum_{rt \in RT}{\mathbf{OPRP}_{rt,t,y,r,s,h}} \quad \text{if } \mathtt{reserve\_margin\_required})\\ &\leq \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y} \times HW_h\\ &\forall {t,y,r,s,h} \in \Theta_{stor} \end{aligned} \tag{9}
$$

Storage technology level upper bound:

$$
\begin{aligned} &\mathbf{STOR^{level}}_{t,y,r,s,h} \leq \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y} \times STOR^{dur}_t\\ &\forall {t,y,r,s,h} \in \Theta_{stor} \end{aligned} \tag{10}
$$

#### Capacity Expansion

The model can build new generating technologies each year (when the expansion switch is turned on). The expansion
constraint ensures that operating capacity is based on the capacity in the previous year, plus any additions and minus
any retirements. The retirement constraint ensures that retirements never exceed the capacity available on the system.

Total capacity balance:

$$
\begin{aligned} \mathbf{CAP^{tot}}_{r,seas,t,s,y} = &CAP^{exist}_{r,seas,t,s,y} \\ & (+ \sum_{cy \in Y \leq y}{\mathbf{CAP^{new}}_{r,t,cy,s}} \quad \text{if } \mathtt{capacity\_expansion})\\ & (+ \sum_{cy \in Y \leq y}{\mathbf{CAP^{ret}}_{t,cy,r,s}} \quad \text{if } \mathtt{capacity\_expansion})\\ &\forall {r,seas,t,s,y} \in \Theta_{SC} \end{aligned} \tag{11}
$$

Capacity retirement upper bound:

$$
\begin{aligned} \mathbf{CAP^{ret}}_{t,y,r,s} \leq &CAP^{exist}_{r,2,t,s,y} + \sum_{cy \in Y \lt y}{\mathbf{CAP^{new}}_{r,t,cy,s}} - &\sum_{cy \in Y \lt y}{\mathbf{CAP^{ret}}_{t,cy,r,s}} \\ &\forall {t,y,r,s} \in \Theta_{ret} \\ &\quad \text{if } \mathtt{capacity\_expansion} \\ \end{aligned} \tag{12}
$$

#### Trade

Electricity trade constraints ensure that trade within any given time segment cannot exceed the capabilities of the
transmission lines between the regions trading. In addition, there are supply quantity/price constraints for
international trade, where supply from international regions cannot exceed the availability from the region.

International interregional trade line capacity upper bound:

$$
\begin{aligned} \sum_{c}{\mathbf{TRA^{int}}_{r,r^{int},y,c,h}} \leq &TRALINLIM^{int}_{r,r^{int},y,h} * HW_h \\ &\forall {r,r^{int},y,h} \in \Theta_{traLL^{int}} \\ &\quad \text{if } \mathtt{regional\_exchange}\\ \end{aligned} \tag{13}
$$

International interregional trade resource capacity upper bound:

$$
\begin{aligned} \sum_{r}{\mathbf{TRA^{int}}_{r,r^{int},y,c,h}} \leq &TRALIM^{int}_{r^{int},c,y,h} * HW_h \\ &\forall {r,r^{int},y,h} \in \Theta_{traL^{int}} \\ &\quad \text{if } \mathtt{regional\_exchange}\\ \end{aligned} \tag{14}
$$

Domestic interregional trade line capacity upper bound:

$$
\begin{aligned} \mathbf{TRA}_{r,r1,y,h} \leq &TRALINLIM_{r,r1,MHS_h,y} * HW_h \\ &\forall {r,r1,y,h} \in \Theta_{traLL} \\ &\quad \text{if } \mathtt{regional\_exchange}\\ \end{aligned} \tag{15}
$$

#### Reserve Margin

Reserve margin constraints ensure that there is additional quantity of capacity available beyond load requirements in
each time segment. Available capacity that can contribute to the reserve margin is also potentially decremented based on
capacity credit assumptions. Storage technologies have additional reserve margin constraints accounts for both the power
capacity and the energy capacity availability towards contributing to reserve margin requirements.

Reserve margin requirement constraint:

$$
\begin{aligned} LOAD_{r,y,h} \times (1 + RM_r ) \leq &HW_h \times \\ &\sum_{{t,s} \in \theta^{scrm}_{y,r,MHS_h}}{CC_{t,y,r,s,h} \times (\mathbf{STOR^{avail}}_{t,y,r,s,h} + \mathbf{CAP^{tot}_{r,MHS_h,t,s,y}})}\\ &\forall {r,y,h} \in \Theta_{load}\\ &\quad \text{if } \mathtt{reserve\_margin\_required}\\ \end{aligned} \tag{16}
$$

Constraint to ensure available storage capacity to meet RM <= power cap, upper bound:

$$
\begin{aligned} \mathbf{STOR^{avail}}_{t,y,r,s,h} \leq &\mathbf{CAP^{tot}}_{r,MHS_h,t,s,y}\\ &\forall {t,y,r,s,h} \in \Theta_{stor}\\ &\quad \text{if } \mathtt{reserve\_margin\_required}\\ \end{aligned} \tag{17}
$$

Constraint to ensure available storage capacity to meet RM <= existing storage level, upper bound:

$$
\begin{aligned} \mathbf{STOR^{avail}}_{t,y,r,s,h} \leq &\mathbf{STOR^{level}}_{t,y,r,s,h}\\ &\forall {t,y,r,s,h} \in \Theta_{stor}\\ &\quad \text{if } \mathtt{reserve\_margin\_required}\\ \end{aligned} \tag{18}
$$

#### Ramping

Ramping constraints ensure that generating technologies are limited in the rate in which they can increase or decrease
their generation from one time segment to the next. Ramping capabilities are balanced within each day.

First hour ramping balance constraint:

$$
\begin{aligned} \mathbf{GEN}_{t,y,r,s,h} = &\mathbf{GEN}_{t,y,r,s,h+N-1} + \mathbf{RAMP^{up}}_{t,y,r,s,h} - \mathbf{RAMP^{down}}_{t,y,r,s,h}\\ &\forall {t,y,r,s,h} \in \Theta_{ramp1} \\ &\quad \text{if } \mathtt{ramping\_required}\\ \end{aligned} \tag{19}
$$

Ramping balance (not first hour) constraint:

$$
\begin{aligned} \mathbf{GEN}_{t,y,r,s,h} = &\mathbf{GEN}_{t,y,r,s,h-1} + \mathbf{RAMP^{up}}_{t,y,r,s,h} - \mathbf{RAMP^{down}}_{t,y,r,s,h}\\ &\forall {t,y,r,s,h} \in \Theta_{ramp23} \\ &\quad \text{if } \mathtt{ramping\_required}\\ \end{aligned} \tag{20}
$$

Ramp up upper bound:

$$
\begin{aligned} \mathbf{RAMP^{up}}_{t,y,r,s,h} \leq &HW_h \times RR_t \times \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y}\\ &\forall {t,y,r,s,h} \in \Theta_{ramp} \\ &\quad \text{if } \mathtt{ramping\_required}\\ \end{aligned} \tag{21}
$$

Ramp down upper bound:

$$
\begin{aligned} \mathbf{RAMP^{down}}_{t,y,r,s,h} \leq &HW_h \times RR_t \times \mathbf{CAP^{tot}}_{r,MHS_h,t,s,y}\\ &\forall {t,y,r,s,h} \in \Theta_{ramp} \\ &\quad \text{if } \mathtt{ramping\_required}\\ \end{aligned} \tag{22}
$$

#### Operating Reserves

The model allows for three different types of operating reserves to be represented within the model, either spinning
reserves, regulation reserves, or flexibility reserve requirements. These operating reserves reflect the need to
additional capacity to be held in reserve to meet and short-term needs for generation based on un-expected changes in
things like electricity demand or variable renewable generation output.

Spinning reserve requirement constraint. 3\% of load required:

$$
\begin{aligned} 0.03 \times LOAD_{r,y,h} \leq &\sum_{{t,s} \in \theta^{opres}_{1,r,y,h}}{\mathbf{ORP}_{1,t,y,r,s,h}}\\ &\forall {r,y,h} \in \Theta_{load} \\ &\quad \text{if } \mathtt{spinning\_reserve\_required}\\ \end{aligned} \tag{23}
$$

Regulation reserve requirement constraint. 1\% of load + 0.5\% of wind generation + 0.3\% of solar capacity required:

$$
\begin{aligned} &0.01 \times LOAD_{r,y,h}\\ + &0.005 \times \sum_{{t^w,s} \in \theta^{windor}_{y,r,h}}{\mathbf{GEN}_{t^w,y,r,s,h}} \\ + &0.003 \times HW_h \times \sum_{{t^s,s} \in \theta^{solor}_{y,r,h}}{\mathbf{CAP^{tot}}_{r,MHS_h,t^s,s,y}}\\ \leq &\sum_{{t,s} \in \theta^{opres}_{2,r,y,h}}{\mathbf{ORP}_{2,t,y,r,s,h}}\\ &\forall {r,y,h} \in \Theta_{load} \\ &\quad \text{if } \mathtt{spinning\_reserve\_required}\\ \end{aligned} \tag{24}
$$

Flexibility reserve requirement constraint. 10\% of wind generation + 4\% of solar capacity required:

$$
\begin{aligned} &0.1 \times \sum_{{t^w,s} \in \theta^{windor}_{y,r,h}}{\mathbf{GEN}_{t^w,y,r,s,h}} \\ + &0.04 \times HW_h \times \sum_{{t^s,s} \in \theta^{solor}_{y,r,h}}{\mathbf{CAP^{tot}}_{r,MHS_h,t^s,s,y}}\\ \leq &\sum_{{t,s} \in \theta^{opres}_{3,r,y,h}}{\mathbf{ORP}_{3,t,y,r,s,h}}\\ &\forall {r,y,h} \in \Theta_{load} \\ &\quad \text{if } \mathtt{spinning\_reserve\_required}\\ \end{aligned} \tag{25}
$$

Operating reserve procurement upper bound:

$$
\begin{aligned} \mathbf{ORP}_{o,t,y,r,s,h} \leq &RTUB_{o,t} \times HW_h \times \mathbf{CAP^{tot}}_{r,MHS_h,t^s,s,y}\\ &\forall {o,t,y,r,s,h} \in \Theta_{proc} \\ &\quad \text{if } \mathtt{spinning\_reserve\_required}\\ \end{aligned} \tag{26}
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

- `strict_validation = true` — a failed validation writes a notice to `stderr` and terminates the
  run. This is the normal setting.
- `strict_validation = false` — failures are logged and the run continues with the data as read.
  Useful when working with known-incomplete input data, at the risk of a downstream failure or a
  silently mis-specified model.

### Validations performed

- **Seasonal coverage** (`validate_seasonal_coverage`) — for `supply_price` and `hydro_cap_factor`,
  every base index (the index with the season removed) must carry exactly the model's expected set
  of seasons: no gaps and no strays.
- **Hourly coverage** (`validate_hourly_coverage`) — the same check on the hour dimension for the
  transmission tables `ParamData` expands from season to hour (`tran_limit`, `tran_limit_cap_int`,
  `tran_limit_gen_int`), reporting the missing and unexpected hours for each offending base index.
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
`param_utilities.py`). It is applied to:

- `elec_load` — the load is built for every calendar year, so the load for a representative year
  is the mean load over its block of calendar years.
- The year-indexed time-based tables: `supply_curve`, `supply_price`, `cap_cost`, `tran_cost`,
  `tran_cost_int`, `tran_limit`, `tran_limit_cap_int`, and `tran_limit_gen_int`.

The parameter CSVs, however, are read with a year filter of `summary_years`, so the time-based tables
reach the averaging step holding only the representative years. For them the "average" is just
the representative year's own value; only the load is averaged in practice. `cap_factor_vre` and
`hydro_cap_factor` have no year index and are not aggregated. Neither are the parameters loaded
as plain dictionaries, such as `fom_cost` and `cap_cost_initial`.

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

Outside the objective, the linear-learning loop in `sequencer.py` passes `weight_year` to
`calculate_tolerance`, which weights each year's change in capacity growth by $WY_y$ when testing
for convergence.

!!! note
    `y0_learning` ($YR0$) is initialized from `aggregate_start_year` even when aggregation is off.
    The coupling is flagged in `electricity_model.py` for separation.
