# Hydrocarbon Supply

C-HSM projects US natural gas production capacity by census division, cost tier and gas class,
Canadian natural gas production, and gas and crude oil from existing US wells, for every year from
2023 to 2050, in response to the Henry Hub and Brent prices. It is adapted from the Hydrocarbon
Supply Module of EIA's National Energy Modeling System (NEMS) and takes its supply regions and
cost tiers from C-NGMM.

C-HSM is not an optimization model. Each year is computed from that year's prices and, for Canada
and the optional well-level engine, from the wells drilled in earlier years, so the years are run
in order. This page sets out every equation the model evaluates. Code references are to
`src/models/hsm/`. The origin of each number is in `src/models/hsm/PROVENANCE.md`, and what the
model can and cannot be used for is in `src/models/hsm/EVALUATION.md`.

| Part | Code | Results | Section |
|---|---|---|---|
| US gas capacity (reduced form) | `us_gas.py` | `hsm_us_gas_capacity.csv` | 2 to 4 |
| Canada | `canada.py` | `hsm_canada_na_prod.csv`, `hsm_canada_ad_prod.csv`, `hsm_canada_realized_na_prod.csv` | 5 |
| Existing US wells | `us_onshore.py`, `OnshoreLegacy` | `hsm_us_legacy_gas.csv`, `hsm_us_crude.csv` | 6 |
| Well-level engine (optional, off by default) | `us_onshore.py`, `OnshoreEngine` | replaces section 2 when on | 7 |

## Notation

A name of several letters, such as $\mathit{Cap}$ or $\mathit{share}$, is one symbol. A prime on an
index ($j'$) marks a second index over the same set. In the tables of calculated quantities, "Eq."
is the equation that defines the quantity; for parameters it is where the parameter is first used
or derived.

### Sets and indices

| Symbol | Description | Members |
|---|---|---|
| $r$ | census division, the C-NGMM supply region | 9 |
| $j$ | cost tier | $\mathrm{Low}$ (`low_cost`), $\mathrm{Med}$ (`medium_cost`), $\mathrm{High}$ (`high_cost`) |
| $\mathit{type}$ | gas type | US: conventional (conv), tight, shale, coalbed methane (cbm); Canada: non-associated, tight, shale, coalbed methane |
| $a$ | gas associated class | $\mathrm{NA}$ (non-associated), $\mathrm{AD}$ (associated-dissolved) |
| $y$ | calendar year | 2023 to 2050; the Canada results also cover 2005 to 2022 |
| $\mathit{cr}$ | Canadian region | 1 = east, 2 = west |
| $\ell$ | Canadian province | |
| $i$ | project in a NEMS onshore deck | |
| $n$ | year of a production profile, 1 = its first year | 1 to 40 in the NEMS decks, 1 to 46 in Canada |
| $v$ | vintage, the year a well is drilled | |

### Prices and constants

| Symbol | Code or file | Description | Value or unit |
|---|---|---|---|
| $H_y$ | `rest_henry_hub` | Henry Hub price given to C-HSM | 1987 \$/MMBtu |
| $B_y$ | `brent_prices`, or `brent_reference_path.csv` if none | Brent price given to C-HSM; `rest_brent_price` holds $m\, B_y$ | 1987 \$/bbl |
| $H^{\mathrm{ref}}_y$, $B^{\mathrm{ref}}_y$ | `hh_reference_path.csv`, `brent_reference_path.csv` | AEO2026 reference paths | 1987 \$/MMBtu, 1987 \$/bbl |
| $H^{\mathrm{nom}}_y$, $B^{\mathrm{gal}}_y$ | AEO2026 Tables 59 and 57 | Henry Hub and Brent spot prices the reference paths are built from | nominal \$/MMBtu, nominal \$/gallon |
| $m$ | `brent_multiplier` | Brent scenario multiplier | 1.0 |
| $D_y$ | `_GDP_DEFLATOR` (`module.py`) | GDP deflator, $D_{1987} = 1$ | $D_{2023} = 2.085$, $D_{2016} = 1.771$ |
| $\beta_r$ | `REGIONAL_BASIS` (`module.py`) | wellhead price basis to Henry Hub | 2023 \$/MMBtu, table below |
| $\bar p$ | `BASE_WELLHEAD_PRICE_PER_MMBTU` | base gas price | 2.50 (2023 \$/MMBtu) |
| $\bar o$ | `BASE_OIL_PRICE_PER_BBL` | base oil price | 65 (2023 \$/bbl) |
| $y_0$ | `history_year` in `setup.csv` | start of the technology and depletion clocks | 2023 |
| $y_a$ | `CAL_ANCHOR_YEAR` | year the calibration is normalised to | 2025 |

### US supply: parameters

| Symbol | Code or file | Description | Value with the shipped inputs |
|---|---|---|---|
| $\mathit{Cap}^{\mathrm{NG}}_{r,j}$ | `input/natural_gas/ng_supply_cost_tiers.csv` | capacity of cost tier $j$ in C-NGMM | BCF/yr |
| $\mathit{share}_{j,\mathit{type}}$ | `_load_cost_tier_shares` | share of a gas type in cost tier $j$ | $\mathrm{Low}$: conventional 0.4633, cbm 0.5367 (eq. 14); $\mathrm{Med}$: tight 0.40, shale 0.60; $\mathrm{High}$: conventional 0.20, shale 0.80 |
| $\varepsilon_{\mathit{type}}$ | `SUPPLY_ELASTICITY` | price elasticity of capacity | conventional 0.30, tight 0.55, shale 0.65, cbm 0.25 |
| $\tau_{\mathit{type}}$ | `_load_tech_trend` | technology trend, per year | conventional 0.0025, tight 0.010, shale 0.020, cbm 0.0025 (eq. 15) |
| $\delta_r$ | `_load_depletion_rate` | depletion rate of the low-cost tier, per year | eq. 16, table below |
| $\sigma_r$ | `us_ad_gas_share.csv` | share of capacity that is AD gas | eq. 17, table below |
| $\eta^{\mathrm{up}}_r$, $\eta^{\mathrm{dn}}_r$ | `us_ad_elasticity.csv` | Brent elasticity of AD gas above and below the reference | eq. 18, table below |
| $k_r$, $g_r$, $c_r$ | `us_gas_calibration.csv` | calibration | section 4, table below |
| $\phi$ | `share_ratio` in `on_constraint_params.csv` | oil-associated share of medium-cost capacity, used only without the NA/AD split | 0.15 (eq. 13) |
| $\varepsilon^{\mathrm{oil}}_r$ | `OIL_ASSOCIATED_GAS_ELASTICITY` | oil price elasticity of that capacity, used only without the NA/AD split | eq. 13 |

Regional values with the inputs shipped with C-HSM:

| Region | $\beta_r$ | $\delta_r$ | $\sigma_r$ | $\eta^{\mathrm{up}}_r$ | $\eta^{\mathrm{dn}}_r$ | $k_r$ | $g_r$ | $c_r$ |
|---|---|---|---|---|---|---|---|---|
| new_england | +0.80 | 0.0089 | 0.0000 | 0.0000 | 0.0000 | 0.779409 | −0.021845 | 0.00064412 |
| middle_atlantic | +0.20 | 0.0089 | 0.0003 | 0.5456 | 0.7276 | 0.835358 | 0.021147 | −0.00001372 |
| east_north_central | +0.10 | 0.0089 | 0.0643 | 0.5418 | 0.7262 | 0.846579 | 0.020752 | 0.00000754 |
| west_north_central | 0.00 | 0.0200 | 0.8529 | 0.5430 | 0.7266 | 0.787641 | −0.022056 | 0.00142422 |
| south_atlantic | −0.10 | 0.0098 | 0.0025 | 0.1659 | 0.5947 | 0.871153 | 0.020231 | 0.00005405 |
| east_south_central | −0.20 | 0.0143 | 0.0618 | 0.2639 | 0.6290 | 0.924466 | −0.029963 | 0.00087986 |
| west_south_central | −0.50 | 0.0160 | 0.2467 | 0.4872 | 0.7071 | 0.949826 | −0.024350 | 0.00102842 |
| mountain | −0.30 | 0.0051 | 0.3196 | 0.5214 | 0.7191 | 0.888692 | −0.048562 | 0.00181968 |
| pacific | +0.15 | 0.0050 | 0.3565 | 0.1225 | 0.5795 | 0.819018 | −0.098554 | 0.00245773 |

### US supply: calculated quantities

| Symbol | Description | Units | Eq. |
|---|---|---|---|
| $p_{r,y}$ | wellhead gas price | 2023 \$/MMBtu | 1 |
| $\tilde o_y$ | Brent after the scenario multiplier | 2023 \$/bbl | 2 |
| $o_y$ | oil price the US supply uses | 2023 \$/bbl | 2 |
| $p^{\mathrm{ref}}_{r,y}$, $o^{\mathrm{ref}}_y$ | wellhead gas price and oil price at the reference paths | 2023 \$/MMBtu, 2023 \$/bbl | 3 |
| $\overline{\mathit{Cap}}_{r,j,\mathit{type}}$ | base capacity of a gas type in cost tier $j$ | BCF/yr | 4 |
| $\mathit{Cap}_{r,j}(p, y)$ | capacity of cost tier $j$ at a gas price $p$, before calibration | BCF/yr | 5 |
| $\Lambda_{r,j,y}$ | depletion factor | | 6 |
| $\eta_{r,y}$ | Brent elasticity of AD gas in effect | | 7 |
| $f_{r,y}$ | Brent factor on AD gas | | 7 |
| $Q^{\mathrm{NA}}_{r,j,y}$, $Q^{\mathrm{AD}}_{r,\mathrm{Med},y}$ | NA and AD capacity, what C-HSM reports | BCF/yr | 8, 9 |
| $x$ | years from the anchor year, $y - y_a$ | years | 10 |
| $K_{r,y}$ | calibration factor | | 10 |
| $\omega_{r,j,\mathit{type},y}$ | share of a gas type in the capacity of cost tier $j$ | | 12 |
| $Q_{r,j,y}$ | capacity without the NA/AD split | BCF/yr | 13 |

### Derived inputs and calibration

| Symbol | Description | Units | Eq. |
|---|---|---|---|
| $\mathit{opex}_{\mathit{type}}$ | mean production plus transport operating cost of conventional gas or coalbed methane wells in `on_region_avg_cost.csv` | \$/bbl | 14 |
| $\mathit{wt}$, $\mathit{wt}_i$ | a NEMS `well_type_number`, and that of project $i$: 1 conventional oil, 2 tight oil, 3 conventional gas, 4 tight gas, 5 shale gas, 6 coalbed methane | | 15, 17 |
| $\mathit{eur}_{\mathit{wt}}$ | `tier_1_eur_tech` of well type $\mathit{wt}$ in `on_tech_levers.csv` | per year | 15 |
| $S_r$, $s$ | onshore districts of division $r$ in `mapping.csv` whose NEMS region has a dry-hole rate, and one such district | | 16 |
| $h_s$ | dry-hole rate of district $s$'s NEMS region, development conventional gas (`on_dryhole_rate.csv`) | | 16 |
| $h_r$ | mean dry-hole rate of division $r$ | | 16 |
| $\mathcal I^{\mathrm{leg}}_r$, $\mathcal I^{\mathrm{leg}}_{r,a}$ | projects of the producing and CO2-EOR decks in region $r$, and those of class $a$ | | 17, 31 |
| $\mathit{GP}_{i,n}$, $\mathit{OP}_{i,n}$ | gas, and crude oil with lease condensate, of project $i$ in profile year $n$ | MMcf/yr, thousand bbl/yr | 17, 31 |
| $\lambda_r$ | tight oil share of the AD gas of region $r$ | | 18 |
| $Y_a$ | C-NGMM's model years, 2025 to 2050 in steps of 5 | | 19 |
| $\mu_{r,y}$ | `q0_mult` in `input/natural_gas/ng_supply_anchors.csv` | | 19 |
| $Q^0_{r,y}$ | C-NGMM's supply anchor, the calibration target | BCF/yr | 19 |
| $\hat Q_{r,y}$ | capacity at the reference prices before calibration | BCF/yr | 20 |

### Canada

| Symbol | Description | Units | Eq. |
|---|---|---|---|
| $H^b_y$, $B^b_y$ | benchmark Henry Hub and Brent prices (`can_benchmark_prices.csv`) | 1987 \$/MMBtu, 1987 \$/bbl | 23, 25 |
| $\mathit{Base}^{\mathrm{AD}}_{\ell,y}$ | baseline AD production of province $\ell$ (`can_natgas_baseline_prod.csv`) | BCF/yr | 23 |
| $e^{\mathrm{high}}_{\ell,y}$, $e^{\mathrm{low}}_{\ell,y}$ | Brent elasticity of AD gas above and below the benchmark (`can_elasticity_ad_gas.csv`) | | 23 |
| $e^{\mathrm{AD}}_{\ell,y}$ | the one of the two in effect | | 23 |
| $A_{\ell,y}$ | AD production of province $\ell$ | BCF/yr | 23 |
| $\mathit{AD}_{\mathit{cr},y}$ | AD production of Canadian region $\mathit{cr}$ | BCF/yr | 24 |
| $W_{\ell,\mathit{type},y}$ | new wells | wells | 25 |
| $e^{W}_{\ell,\mathit{type},y}$ | Brent exponent of new wells, `oilprc_high` or `oilprc_low` (`can_natgas_poly_eqs.csv`) | | 25 |
| $\Pi_{\ell,\mathit{type}}$; $b^{(0)}, \dots, b^{(3)}$ | well polynomial, and its coefficients `b0` to `b3` (`can_natgas_poly_eqs.csv`) | | 26 |
| $P^{\mathrm{pipe}}$ | Canada pipeline price term | 1987 \$/MMBtu | 26 |
| $\mathit{IP}_{\ell,\mathit{type}}$ | initial production rate of one well (`ipr`) | BCF/day | 27 |
| $b_{\ell,\mathit{type}}$, $\tilde b_{\ell,\mathit{type}}$ | hyperbolic exponent (`b`), and the value used in the decline exponent | | 27 |
| $d_{\ell,\mathit{type}}$ | decline rate (`dr`) | per year | 27 |
| $\kappa_n$ | first-year factor: 0.5 in year 1, 1 after | | 27 |
| $q_{\ell,\mathit{type},n}$ | production of one well in year $n$ of its life | BCF/yr | 27 |
| $\mathit{Base}^{\mathrm{NA}}_{\ell,\mathit{type},y}$ | baseline NA production (`can_natgas_baseline_prod.csv`) | BCF/yr | 28 |
| $\tau^c_{\ell,\mathit{type}}$ | technology rate of new wells (`tech` in `can_natgas_dc_vars.csv`) | per year | 28 |
| $X_y$ | production not available for export (`can_natgas_no_export_prod.csv`) | BCF/day | 28 |
| $\mathit{NA}_{\mathit{cr},y}$ | NA production of Canadian region $\mathit{cr}$ | BCF/yr | 28 |
| $\mathit{NA}^{\mathrm{ref}}_{\mathit{cr},y}$ | the same at the reference prices (`can_na_reference.csv`) | BCF/yr | 29 |
| $\mathit{Imp}_y$, $\mathit{Exp}_y$ | US pipeline imports from and exports to Canada, AEO2026 Table 61 | Tcf | 29 |
| $\chi_y$ | share of extra Canadian production that reaches the US (`can_us_export_share.csv`) | | 29 |
| $\Delta_y$ | Canadian supply a coupled run would send to C-NGMM | BCF/yr | 30 |

### Existing wells and the engine

| Symbol | Description | Units | Eq. |
|---|---|---|---|
| $G^{\mathrm{leg}}_{r,a,y}$, $O^{\mathrm{leg}}_{r,a,y}$ | gas and crude oil from existing wells | BCF/yr, thousand bbl/yr | 31 |
| $N_i$, $N^{\mathrm{cum}}_i$ | well limit (`totpat`) and wells drilled so far (`past_wells`) of a continuous project | wells | 32 |
| $\rho^d$ | discount rate, 0.10 | per year | 32 |
| $\psi_n$, $\mathcal A$ | discount factor of profile year $n$, and the sum of the 40 factors | | 32 |
| $\nu$ | share of revenue left after royalty (0.1875) and severance (0.06) | | 33 |
| $\mathcal G_i$, $\mathcal O_i$ | discounted gas and oil of one well, net of royalty and severance | MMBtu, bbl | 33 |
| $\mathit{capex}_i$, $\mathit{fac}_i$ | drilling capex and facility capex of one well | 2023 \$ | 34 |
| $\mathit{ovh}_i$, $\mathit{opx}_i$ | overhead a year, and operating cost per barrel of oil equivalent | 2023 \$ | 34 |
| $\mathcal F_i$ | discounted cost of one well | 2023 \$ | 34 |
| $P^g_{i,y}$, $P^o_{i,y}$ | gas and oil prices the engine uses | 2023 \$/MMBtu, 2023 \$/bbl | 35 |
| $\mathit{NPV}_{i,y}$ | net present value of one well | 2023 \$ | 35 |
| $D^{\mathrm{cap}}$ | the engine's own deflator for drilling cost, 1987 to 2023 dollars (`capex_deflator_1987_to_2023`, 2.17) | ratio | 36 |
| $\zeta_0$, $\zeta_{\mathit{basin}(i)}$, $\zeta_L$, $\zeta_V$ | drilling cost regression (`on_drill_cost_eqs.csv`): intercept, the term of basin $\mathit{basin}(i)$ (or of the state when no basin matches), and the coefficients of $L_i$ and $V_i$ | | 36 |
| $\mathit{basin}(i)$ | basin of project $i$, matched from its play name | | 36 |
| $r(i)$ | census division of project $i$ (its district mapped with `mapping.csv`) | | 35 |
| $L_i$, $V_i$ | lateral length and vertical depth of a well | ft | 36 |
| $\bar W^{\mathrm{oil}}_y$, $\bar W^{\mathrm{gas}}_y$ | national yearly caps on oil-directed and gas-directed wells | wells | 37 |
| $\gamma$ | growth rate of the caps, 0 as shipped | per year | 37 |
| $w_{i,y}$ | wells project $i$ drills in year $y$ | wells | 38 |
| $\Omega_{i,y}$ | wells from the NEMS drilling rule | wells | 39 |
| $R_i$, $\xi_i$, $\alpha_i$ | `max_annual_wells`, `max_annual_%_dev`, `max_drill_rate_frac` | | 39 |
| $\pi^{\mathrm{pre}}$, $T$ | share of wells drilled before the drilling rate declines (0.70), ramp-up years (5) | | 39 |
| $w^{\mathrm{last}}_i$ | wells project $i$ drilled last year | wells | 39 |
| $u_0$, $u$, $u_d$, $\Sigma$, $\Omega$, $\Omega^0$, $\bar\Omega_i$ | working values of the drilling rule, section 7.3 | | 39 |
| $\bar P^g$, $\bar P^o_i$ | the engine's base gas price (`base_gas_price_2023`, 2.50, the value of $\bar p$), and the base oil price of project $i$ (`base_oil_prc_by_play.csv` by play name, 50 when none) | 2023 \$/MMBtu, 2023 \$/bbl | 39 |
| $\theta^o$, $\theta^g$, $z$, $\Phi_{i,y}$ | oil and gas price ratios, working value, and the price adjustment | | 39 |
| $\mathcal I_{r,a}$ | continuous projects of region $r$ and class $a$ | | 40 |
| $\tau^E_i$ | technology rate of new wells | per year | 40 |
| $G^E_{r,a,y}$, $O^E_{r,a,y}$ | gas and crude oil from the engine, existing and new wells | BCF/yr, thousand bbl/yr | 40, 41 |
| $\mathit{GP}'_{i,n}$ | gas profile after the decline overrides ($\mathit{OP}'_{i,n}$ likewise) | MMcf/yr | 42 |
| $d^{\mathrm{first}}_i$, $d^{\mathrm{later}}_i$ | first-year and later decline rates of the overrides | | 42 |
| $K^E_{r,y}$ | calibration factor of the engine | | 43 |

## 1. Prices

C-HSM takes Henry Hub and Brent in 1987 dollars, the NEMS convention. For the US supply it turns
both into real 2023 dollars with the 2023 deflator in every year (`module.py`, `run_year`); the
Canada submodule uses them in 1987 dollars (section 5). Using each year's own deflator would feed
inflation into the model as if it were a price signal.

The wellhead gas price of region $r$ is Henry Hub plus a fixed regional basis:

$$
p_{r,y} = \max\big(D_{2023}\, H_y + \beta_r,\; 0.01\big) \tag{1}
$$

The oil price is Brent, scaled by the scenario multiplier, and replaced by the base oil price when
it is below 20 \$/bbl in 2023 dollars:

$$
\tilde o_y = D_{2023}\, m\, B_y, \qquad
o_y = \begin{cases} \tilde o_y & \text{if } \tilde o_y \ge 20 \\ \bar o & \text{otherwise} \end{cases} \tag{2}
$$

The reference prices, used by the NA/AD split (section 2.3), are built the same way from the
reference paths, without the multiplier:

$$
p^{\mathrm{ref}}_{r,y} = \max\big(D_{2023}\, H^{\mathrm{ref}}_y + \beta_r,\; 0.01\big), \qquad
o^{\mathrm{ref}}_y = \begin{cases} D_{2023}\, B^{\mathrm{ref}}_y & \text{if } D_{2023}\, B^{\mathrm{ref}}_y \ge 20 \\ o_y & \text{otherwise} \end{cases} \tag{3}
$$

A year missing from a reference path would also take the actual price, which makes the split
neutral for that year; the shipped paths cover every year. The code also floors both oil prices at
1 \$/bbl, which never binds.

The reference paths are AEO2026 results converted to 1987 dollars with the same deflator table:
$H^{\mathrm{ref}}_y = H^{\mathrm{nom}}_y / D_y$, from the Henry Hub spot price in Table 59, and
$B^{\mathrm{ref}}_y = 42\, B^{\mathrm{gal}}_y / D_y$, from the Brent spot price in \$/gallon in Table 57, both stored
to four decimals. So $D_{2023}\, H^{\mathrm{ref}}_y = H^{\mathrm{nom}}_y\, D_{2023} / D_y$ is the nominal AEO2026
price restated in 2023 dollars. When C-HSM is given no Brent path, as in the standard run,
$B_y = B^{\mathrm{ref}}_y$. The Henry Hub path in the standard run config
(`henry_hub_path_aeo2026_1987usd.csv`) is the same series before rounding.

## 2. US gas supply capacity

This is the reduced form in `us_gas.py`, which C-HSM runs by default. Capacity is in BCF/yr.

### 2.1 Base capacity

Each C-NGMM cost tier is split across gas types by fixed shares:

$$
\overline{\mathit{Cap}}_{r,j,\mathit{type}} = \mathit{share}_{j,\mathit{type}}\; \mathit{Cap}^{\mathrm{NG}}_{r,j} \tag{4}
$$

The low-cost tier holds conventional gas and coalbed methane, the medium-cost tier tight and shale
gas, and the high-cost tier deep conventional and frontier shale or offshore gas. Only the tier
capacities are read; the tier costs in the same file are not used.

### 2.2 Price-driven capacity

Capacity of cost tier $j$ at gas price $p$ in year $y$, before calibration:

$$
\mathit{Cap}_{r,j}(p, y) = \Lambda_{r,j,y} \sum_{\mathit{type}} \overline{\mathit{Cap}}_{r,j,\mathit{type}} \left(\frac{p}{\bar p}\right)^{\varepsilon_{\mathit{type}}} (1 + \tau_{\mathit{type}})^{\,y - y_0} \tag{5}
$$

with depletion on the low-cost tier only:

$$
\Lambda_{r,j,y} = \begin{cases}
\max\big(1 - \delta_r \max(y - y_0,\, 0),\; 0.5\big) & j = \mathrm{Low} \\
1 & j \in \{\mathrm{Med}, \mathrm{High}\}
\end{cases} \tag{6}
$$

Depletion is linear in the years since 2023 and stops at half the base. With the shipped rates the
floor is reached only in West North Central ($\delta_r = 0.020$), from 2048. Capacity in a year
depends only on that year's price: the reduced form has no memory of earlier years.

### 2.3 Non-associated and associated-dissolved gas

A share $\sigma_r$ of capacity is AD gas, which follows the oil price instead of the gas price. The
Brent response is

$$
f_{r,y} = \left(\frac{o_y}{o^{\mathrm{ref}}_y}\right)^{\eta_{r,y}}, \qquad
\eta_{r,y} = \begin{cases} \eta^{\mathrm{up}}_r & \text{if } o_y > o^{\mathrm{ref}}_y \\ \eta^{\mathrm{dn}}_r & \text{otherwise} \end{cases} \tag{7}
$$

and the capacity C-HSM reports is

$$
Q^{\mathrm{NA}}_{r,j,y} = (1 - \sigma_r)\, \mathit{Cap}_{r,j}(p_{r,y}, y)\, K_{r,y}, \qquad j \in \{\mathrm{Low}, \mathrm{Med}, \mathrm{High}\} \tag{8}
$$

$$
Q^{\mathrm{AD}}_{r,\mathrm{Med},y} = \sigma_r \sum_{j} \mathit{Cap}_{r,j}(p^{\mathrm{ref}}_{r,y}, y)\, f_{r,y}\, K_{r,y} \tag{9}
$$

where $K_{r,y}$ is the calibration factor of section 2.4. AD gas is evaluated at the reference gas
price, so it does not respond to the gas price, and it is reported under `medium_cost`, where NEMS
also books the associated gas of tight oil. The code clamps $\sigma_r$ to $[0, 1]$ and floors
both quantities at zero; with positive factors the floors never bind.

### 2.4 Calibration factor

$$
K_{r,y} = k_r\, (1 + g_r)^{x} \exp\big(c_r x^2\big), \qquad x = y - y_a \tag{10}
$$

$k_r$ takes up level differences, such as the gas type shares and the base price; $g_r$ and $c_r$
take up the trend and the rise and fall of AEO2026 production that the reduced form cannot produce
on its own. Section 4 gives the fit.

### 2.5 Properties

At the reference prices ($p_{r,y} = p^{\mathrm{ref}}_{r,y}$, $o_y = o^{\mathrm{ref}}_y$, so $f_{r,y} = 1$) NA and AD
gas add up to the calibrated total without the split, which is what the calibration was fitted to:

$$
\sum_j Q^{\mathrm{NA}}_{r,j,y} + Q^{\mathrm{AD}}_{r,\mathrm{Med},y} = K_{r,y} \sum_j \mathit{Cap}_{r,j}(p^{\mathrm{ref}}_{r,y}, y) \tag{11}
$$

Away from the price floor, the elasticity of NA capacity to the wellhead price is the
capacity-weighted mean of the gas type elasticities:

$$
\frac{\partial \ln Q^{\mathrm{NA}}_{r,j,y}}{\partial \ln p_{r,y}} = \sum_{\mathit{type}} \omega_{r,j,\mathit{type},y}\, \varepsilon_{\mathit{type}}, \qquad
\omega_{r,j,\mathit{type},y} = \frac{\overline{\mathit{Cap}}_{r,j,\mathit{type}}\, (p_{r,y}/\bar p)^{\varepsilon_{\mathit{type}}} (1 + \tau_{\mathit{type}})^{y - y_0}}
{\sum_{\mathit{type}'} \overline{\mathit{Cap}}_{r,j,\mathit{type}'}\, (p_{r,y}/\bar p)^{\varepsilon_{\mathit{type}'}} (1 + \tau_{\mathit{type}'})^{y - y_0}} \tag{12}
$$

It lies between 0.25 and 0.30 for the low-cost tier, 0.55 and 0.65 for the medium-cost tier, and
0.30 and 0.65 for the high-cost tier. The elasticity to Henry Hub is this times
$\partial \ln p_{r,y} / \partial \ln H_y = D_{2023} H_y / p_{r,y}$, which is below 1 where the
basis is positive and above 1 where it is negative. NA gas does not respond to Brent. AD gas does
not respond to the gas price, and while $\tilde o_y \ge 20$ its Brent elasticity is
$\partial \ln Q^{\mathrm{AD}}_{r,\mathrm{Med},y} / \partial \ln o_y = \eta_{r,y}$, with a kink at the
reference price.

### 2.6 Without the NA/AD split

The split is on only when both `us_ad_gas_share.csv` and `us_ad_elasticity.csv` load; both are
shipped. Without them C-HSM falls back to an older form with no gas class, which adds
oil-associated gas to the medium-cost tier:

$$
Q_{r,j,y} = K_{r,y} \Big( \mathit{Cap}_{r,j}(p_{r,y}, y) + [\,j = \mathrm{Med}\,]\, [\,\lvert \varepsilon^{\mathrm{oil}}_r \rvert > 10^{-9}\,]\; \phi\, \mathit{Cap}^{\mathrm{NG}}_{r,\mathrm{Med}} \left(\frac{o_y}{\bar o}\right)^{\varepsilon^{\mathrm{oil}}_r} \Big) \tag{13}
$$

Here $[\cdot]$ is 1 when the condition holds and 0 otherwise, so the term is added only to the
medium-cost tier and only where $\varepsilon^{\mathrm{oil}}_r$ is not zero (the code tests
$\lvert \varepsilon^{\mathrm{oil}}_r \rvert > 10^{-9}$). $\phi = 0.15$ is `share_ratio` in
NEMS `on_constraint_params.csv`, and $\varepsilon^{\mathrm{oil}}_r$ is `OIL_ASSOCIATED_GAS_ELASTICITY`
(West South Central 0.35, Mountain 0.20, West North Central 0.15, South Atlantic, East South
Central and Pacific 0.05, the rest 0). The shipped calibration was fitted to eq. 11, so with this
form capacity at the reference prices is higher than eq. 11 by the added term.

## 3. Parameters derived from NEMS inputs

When it starts, C-HSM computes three parameters from NEMS onshore input files in
`input/hsm/onshore/`, unchanged from NEMS (`us_gas.py`), reads a fourth, $\phi$ in eq. 13,
directly, and reads two files in `input/hsm/` that were built from the NEMS decks.

**Low-cost tier shares** (`on_region_avg_cost.csv`). With $\mathit{opex}_{\mathit{type}}$ the mean
over the file's rows for a well type of `production_opex_brl` + `transport_opex_brl`,

$$
\mathit{share}_{\mathrm{Low},\mathrm{conv}} = \frac{1/\mathit{opex}_{\mathrm{conv}}}{1/\mathit{opex}_{\mathrm{conv}} + 1/\mathit{opex}_{\mathrm{cbm}}}, \qquad
\mathit{share}_{\mathrm{Low},\mathrm{cbm}} = \frac{1/\mathit{opex}_{\mathrm{cbm}}}{1/\mathit{opex}_{\mathrm{conv}} + 1/\mathit{opex}_{\mathrm{cbm}}} \tag{14}
$$

rounded to four decimals, so the cheaper type gets the larger share. The medium and high-cost
shares are set by hand; NEMS onshore has no separate tight gas cost entry.

**Technology trend** (`on_tech_levers.csv`). With $\mathit{wt}(\mathit{type})$ = 3, 4, 5 and 6 for
conventional, tight, shale and coalbed methane,

$$
\tau_{\mathit{type}} = \mathit{eur}_{\mathit{wt}(\mathit{type})} \tag{15}
$$

an annual improvement in recovery per well, used here as a trend in capacity.

**Depletion rate** (`on_dryhole_rate.csv`, `mapping.csv`). With $h_s$ the NEMS dry-hole rate of
district $s$ (`drill_category` 3, development conventional, and `resource_type` gas, of the
district's NEMS region) and $S_r$ the onshore districts of census division $r$ that have one,

$$
h_r = \frac{1}{|S_r|} \sum_{s \in S_r} h_s, \qquad
\delta_r = 0.005 + 0.015\, \frac{h_r - \min_{r'} h_{r'}}{\max_{r'} h_{r'} - \min_{r'} h_{r'}} \tag{16}
$$

rounded to four decimals: a higher dry-hole rate means faster depletion. The ranking comes from
NEMS; the range, 0.005 to 0.020 a year, is set for C-HSM.

**AD share** (`us_ad_gas_share.csv`, built from the producing and CO2-EOR decks). With
$\mathcal I^{\mathrm{leg}}_r$ the projects of region $r$ in those decks and $\mathit{GP}_{i,1}$ their gas in the first
projection year,

$$
\sigma_r = \frac{\sum_{i \in \mathcal I^{\mathrm{leg}}_r,\, \mathit{wt}_i < 3} \mathit{GP}_{i,1}}{\sum_{i \in \mathcal I^{\mathrm{leg}}_r} \mathit{GP}_{i,1}} \tag{17}
$$

Well types 1 and 2 are conventional oil and tight oil, so this is the rule NEMS uses for its AD gas
output. The shares are on a gross basis and rounded to four decimals; $\sigma_r = 0$ where the
denominator is 0 (New England has no projects).

**AD elasticity** (`us_ad_elasticity.csv`). Each region mixes a tight oil and a conventional oil
elasticity by $\lambda_r$, the tight oil share of its AD gas (the first-year gas of well type 2
over that of well types 1 and 2, from the same decks):

$$
\eta^{\mathrm{up}}_r = 0.5456\, \lambda_r + 0.1225\, (1 - \lambda_r), \qquad
\eta^{\mathrm{dn}}_r = 0.7276\, \lambda_r + 0.5795\, (1 - \lambda_r) \tag{18}
$$

rounded to four decimals, and 0 for New England, which has no AD gas. The four national values are
recorded in the file's header as the 2030 to 2050 mean response of crude production to Brent in
the AEO2025 High and Low Oil Price cases; AEO2026 has no oil price cases. The script that computed
them is not part of C-HSM.

## 4. Calibration

The calibration fits US capacity at the reference prices to C-NGMM's supply anchors. The targets
are C-NGMM's anchor quantities in its six model years:

$$
Q^0_{r,y} = \mu_{r,y} \sum_j \mathit{Cap}^{\mathrm{NG}}_{r,j}, \qquad y \in Y_a = \{2025, 2030, \dots, 2050\} \tag{19}
$$

$Q^0_{r,y}$ is the parameter `q0` that C-NGMM builds from the same two files. The capacity at the
reference prices before calibration is

$$
\hat Q_{r,y} = \sum_j \mathit{Cap}_{r,j}(p^{\mathrm{ref}}_{r,y}, y) \tag{20}
$$

which is the total NA plus AD capacity at the reference prices (eq. 11 with $K_{r,y} = 1$). For each
region, the fit is ordinary least squares on the log of the ratio, with a quadratic trend:

$$
(k_r, g_r, c_r) = \arg\min_{k > 0,\; g > -1,\; c} \sum_{y \in Y_a} \left( \ln \frac{Q^0_{r,y}}{\hat Q_{r,y}} - \ln k - x \ln(1 + g) - c\, x^2 \right)^2, \qquad x = y - y_a \tag{21}
$$

$$
\ln K_{r,y} = \ln k_r + x \ln(1 + g_r) + c_r\, x^2 \tag{22}
$$

The fit is linear in $\ln k_r$, $\ln(1 + g_r)$ and $c_r$, so it has a closed-form solution. The
file stores $k_r$ and $g_r$ to six decimals and $c_r$ to eight. Refitting from the shipped inputs
gives back the shipped values to that precision.

Six targets and three parameters leave a residual. Calibrated capacity at the reference prices
against the target, $K_{r,y} \hat Q_{r,y} / Q^0_{r,y} - 1$, in percent:

| Region | 2025 | 2030 | 2035 | 2040 | 2045 | 2050 |
|---|---|---|---|---|---|---|
| new_england | −0.4 | +0.6 | 0.0 | +0.4 | −1.1 | +0.6 |
| middle_atlantic | −0.8 | +2.3 | −1.9 | +0.3 | +0.1 | 0.0 |
| east_north_central | −0.8 | +2.3 | −1.9 | +0.3 | +0.1 | 0.0 |
| west_north_central | −9.8 | +18.7 | +2.5 | −6.3 | −10.5 | +8.7 |
| south_atlantic | −0.8 | +2.3 | −1.9 | +0.4 | +0.1 | 0.0 |
| east_south_central | +3.3 | −2.7 | −7.2 | +6.6 | +4.5 | −3.7 |
| west_south_central | +1.2 | −1.3 | −2.5 | +3.9 | −0.6 | −0.5 |
| mountain | −1.4 | −0.4 | +5.7 | −0.3 | −7.2 | +4.1 |
| pacific | −3.1 | +4.7 | +2.1 | −2.1 | −4.4 | +3.2 |

The factor applies in every year, so 2023, 2024 and the years between the anchor years follow the
fitted quadratic. At the reference prices US capacity is therefore C-NGMM's anchors plus this
residual: the level comes from AEO2026, through the anchors, and what C-HSM adds is the response to
prices around it. The calibration must be refitted whenever C-NGMM's cost tiers or anchors change.

The well-level engine has its own calibration, `us_gas_calibration_engine.csv`, of the same form,
fitted in the same way to the engine's capacity at the reference Henry Hub and Brent paths. The
shipped values were fitted with an earlier set of engine settings (`ENGINE_PLAN.md`).

## 5. Canada

The Canada submodule is the NEMS AEO2025 one, adapted (`canada.py`). It projects production by
province from the Canada Energy Regulator's baseline, with new wells in western Canada that respond
to Brent. Quantities are in BCF/yr and reported for 2005 to 2050 by Canadian region: west
(Alberta, British Columbia, Saskatchewan) and east. All the prices in this section are in 1987
dollars and are not converted to 2023 dollars. The submodule's inputs are in
`input/hsm/canada/`; the two files for a coupled run (section 5.5) are in `input/hsm/`.

### 5.1 Associated-dissolved gas

AD production of province $\ell$ is its baseline scaled by Brent against a benchmark:

$$
A_{\ell,y} = \mathit{Base}^{\mathrm{AD}}_{\ell,y} \left(\frac{m\, B_y}{B^b_y}\right)^{e^{\mathrm{AD}}_{\ell,y}}, \qquad
e^{\mathrm{AD}}_{\ell,y} = \begin{cases} e^{\mathrm{high}}_{\ell,y} & \text{if } m\, B_y > B^b_y \\ e^{\mathrm{low}}_{\ell,y} & \text{otherwise} \end{cases} \tag{23}
$$

$\mathit{Base}^{\mathrm{AD}}_{\ell,y}$ is the `Solution` gas of Alberta, British Columbia and
Saskatchewan, and all the production of Nova Scotia, New Brunswick, Quebec, Ontario, Yukon and the
Northwest Territories. $e^{\mathrm{high}}$ and $e^{\mathrm{low}}$ are given for the three western provinces from
2016; elsewhere $e^{\mathrm{AD}}_{\ell,y} = 0$. The Brent here is the model's path, $m B_y$, with no
\$20 check. Years before 2023 keep their baseline. The adjustment is made once, when 2023 runs, for
all years.

$$
\mathit{AD}_{2,y} = \sum_{\ell \in \text{west}} A_{\ell,y}, \qquad
\mathit{AD}_{1,y} = \sum_{\ell \in \{\text{NS, NB, QC, ON, YT, NT}\}} A_{\ell,y} \tag{24}
$$

Newfoundland and Labrador is in neither.

### 5.2 New wells

New wells are drilled for nine province and gas type pairs $(\ell, \mathit{type})$: Alberta
non-associated, tight, shale and coalbed methane; British Columbia tight, shale and non-associated;
Saskatchewan tight and non-associated. For $y \ge 2024$:

$$
W_{\ell,\mathit{type},y} = \operatorname{round}\Big( \max\Big( \Pi_{\ell,\mathit{type}}\big(H^b_y\big)\; P^{\mathrm{pipe}} \Big[ H^b_y \Big(\frac{m\, B_y}{B^b_y}\Big)^{e^{W}_{\ell,\mathit{type},y}} \Big]^{0.75},\; 0 \Big) \Big) \tag{25}
$$

$$
\Pi_{\ell,\mathit{type}}\big(H^b_y\big) = b^{(3)}_{\ell,\mathit{type}} \big(H^b_y\big)^3 + b^{(2)}_{\ell,\mathit{type}} \big(H^b_y\big)^2 + b^{(1)}_{\ell,\mathit{type}} H^b_y + b^{(0)}_{\ell,\mathit{type}}, \qquad
P^{\mathrm{pipe}} = 4.0 + \frac{0.96}{D_{2016}} \approx 4.542 \tag{26}
$$

$e^{W}_{\ell,\mathit{type},y}$ is `oilprc_high` when $m B_y > B^b_y$ and `oilprc_low` otherwise.
4.0 is the Canada pipeline price, fixed in C-HSM where NEMS computes it. Rounding is to the nearest
integer, halves to even. The wells of 2023 are the historical counts in `can_wells.csv`, used as
they are.

$H^b_y$ and $P^{\mathrm{pipe}}$ are fixed inputs, so new wells do not depend on the Henry Hub price C-HSM
is given, and before rounding and the zero floor their Brent elasticity is
$0.75\, e^{W}_{\ell,\mathit{type},y}$. (The AEO2026 release of NEMS drops the second $H^b_y$ and
$P^{\mathrm{pipe}}$ from eq. 25.)

### 5.3 Production of one well

Each new well follows a hyperbolic (Arps) decline:

$$
q_{\ell,\mathit{type},n} = \kappa_n\; 365\; \mathit{IP}_{\ell,\mathit{type}}\, \big(1 + b_{\ell,\mathit{type}}\, d_{\ell,\mathit{type}}\, n\big)^{-1/\tilde b_{\ell,\mathit{type}}},
\qquad n = 1, \dots, 46 \tag{27}
$$

with $\tilde b = b$ if $b > 0$ and $b + 0.01$ otherwise, and $\kappa_1 = 0.5$: a well produces half a
year in the year it is drilled. For $b = 0$ the base is 1 and the rate is constant; in the shipped
file only pairs with $\mathit{IP} = 0$ have $b = 0$.

### 5.4 Non-associated gas

$$
\mathit{NA}_{2,y} = \max\Big( \sum_{(\ell, \mathit{type})} \Big[ \mathit{Base}^{\mathrm{NA}}_{\ell,\mathit{type},y} + \sum_{v = 2023}^{y} W_{\ell,\mathit{type},v}\; q_{\ell,\mathit{type},\,y - v + 1}\, (1 + \tau^c_{\ell,\mathit{type}})^{v - 2005} \Big] - 365\, X_y,\; 0 \Big), \qquad \mathit{NA}_{1,y} = 0 \tag{28}
$$

The sum runs over the nine pairs; $\tau^c$ is 0.005 or 0.01 a year, counted from 2005, and $X_y$ is
read in row order for 2005 to 2050. The sum over vintages is empty before 2023. Eastern Canada has
no non-associated baseline, so $\mathit{NA}_{1,y} = 0$. `hsm_canada_realized_na_prod.csv` is a copy of
$\mathit{NA}_{\mathit{cr},y}$.

### 5.5 For a coupled run

Two more inputs, in `input/hsm/`, are for a coupled run with C-NGMM; a standalone run loads them
but does not use them. `can_us_export_share.csv` holds the share of extra Canadian production
that reaches the US. It was built as

$$
\chi_y = \min\Big( \max\Big( \frac{1000\, (\mathit{Imp}_y - \mathit{Exp}_y)}{\mathit{NA}^{\mathrm{ref}}_{2,y}},\; 0 \Big),\; 1 \Big) \tag{29}
$$

with a missing export taken as 0 and a year Table 61 does not have given the table's first year;
$\mathit{NA}^{\mathrm{ref}}_{\mathit{cr},y}$ is eq. 28 at the reference prices, stored in `can_na_reference.csv`. No
code in C-HSM computes or sends anything to C-NGMM yet; a coupling runner would send

$$
\Delta_y = \chi_y \big( \mathit{NA}_{2,y} - \mathit{NA}^{\mathrm{ref}}_{2,y} \big) \tag{30}
$$

so that Canada adds nothing at the reference prices: net imports from Canada are already in
C-NGMM's own data. Since Canadian production does not respond to Henry Hub, $\Delta_y$ moves only
with Brent.

## 6. Existing US wells

Gas and crude oil from wells already producing come from the NEMS producing oil, producing gas and
CO2-EOR project decks (`OnshoreLegacy`). Each project $i$ has a production profile $\mathit{GP}_{i,n}$ and
$\mathit{OP}_{i,n}$ for profile years $n = 1, \dots, 40$, and profile year 1 is taken as calendar 2024. With
$\mathcal I^{\mathrm{leg}}_{r,a}$ the projects of region $r$ (districts mapped to census divisions with
`mapping.csv`) and class $a$ (AD if $\mathit{wt}_i < 3$, NA otherwise):

$$
G^{\mathrm{leg}}_{r,a,y} = \operatorname{round}_3 \Big( \frac{1}{1000} \sum_{i \in \mathcal I^{\mathrm{leg}}_{r,a}} \mathit{GP}_{i,\, y - 2023} \Big), \qquad
O^{\mathrm{leg}}_{r,a,y} = \operatorname{round}_1 \Big( \sum_{i \in \mathcal I^{\mathrm{leg}}_{r,a}} \mathit{OP}_{i,\, y - 2023} \Big) \tag{31}
$$

in BCF and thousand barrels, for 2024 to 2050, where $\operatorname{round}_3$ and
$\operatorname{round}_1$ round to three decimals and one. There is no new drilling in these numbers, and they do not feed the capacity of section 2.

## 7. The optional well-level engine

With `[hsm] onshore_engine_file` set, the engine in `us_onshore.py` replaces the reduced form of
section 2 from 2024 on, in every region with projects in the NEMS decks (all but New England). It
is off by default and not yet ready to be the default: `ENGINE_PLAN.md` has its status, three known
bugs and the plan to finish it. The equations below are what the code evaluates; notes say where
the shipped inputs or a known bug change what applies. The settings are in
`input/hsm/us_onshore_engine.csv`.

### 7.1 Cash-flow test

The engine works on the 3,181 projects of the continuous deck, `on_projects_continuous.csv`
(process codes 12 tight oil, 13 shale gas and 15 coalbed methane). Project $i$ has a per-well
profile $\mathit{GP}_{i,n}$, $\mathit{OP}_{i,n}$, $n = 1, \dots, 40$, a well limit $N_i$ and wells drilled so far
$N^{\mathrm{cum}}_i$. With discount rate $\rho^d = 0.10$ and revenue share after royalty and severance
$\nu = 1 - 0.1875 - 0.06$:

$$
\psi_n = (1 + \rho^d)^{-(n-1)}, \qquad \mathcal A = \sum_{n=1}^{40} \psi_n \tag{32}
$$

$$
\mathcal G_i = 1037\, \nu \sum_{n} \psi_n\, \mathit{GP}_{i,n}, \qquad
\mathcal O_i = 1000\, \nu \sum_{n} \psi_n\, \mathit{OP}_{i,n} \tag{33}
$$

$$
\mathcal F_i = \mathit{capex}_i + \mathit{fac}_i + \mathit{ovh}_i\, \mathcal A + \mathit{opx}_i \Big( 1000 \sum_n \psi_n\, \mathit{OP}_{i,n} + \frac{1000}{5.6} \sum_n \psi_n\, \mathit{GP}_{i,n} \Big) \tag{34}
$$

$$
\mathit{NPV}_{i,y} = \mathcal G_i\, P^g_{i,y} + \mathcal O_i\, P^o_{i,y} - \mathcal F_i \tag{35}
$$

$\mathcal G_i$ is discounted gas in MMBtu (1,037 MMBtu per MMcf) and $\mathcal O_i$ discounted oil in
barrels, both net of royalty and severance; the costs are not. The operating cost is per barrel of
oil equivalent (5.6 Mcf per barrel). $\mathit{fac}_i$, $\mathit{ovh}_i$ and $\mathit{opx}_i$ are
the means by well type in `on_basin_avg_cost.csv` (fill-ins $3 \times 10^5$, $5 \times 10^5$ and
3.0). Drilling capex is the NEMS log-linear regression in `on_drill_cost_eqs.csv` (the oil
equation for tight oil, the gas equation otherwise), scaled from 1987 to 2023 dollars by
$D^{\mathrm{cap}} = 2.17$:

$$
\mathit{capex}_i = D^{\mathrm{cap}} \exp\big( \zeta_0 + \zeta_{\mathit{basin}(i)} + \zeta_L L_i + \zeta_V V_i \big) \tag{36}
$$

with the basin term found by matching the project's play name to a basin, the state term used
instead when there is no basin match, and the code using the drilling depth for $V_i$. **As
shipped, only $\zeta_0$ applies.** The continuous deck has state codes, lateral lengths and
drilling depths, but the engine drops those columns when it reads the deck, and the deck's
`play_name` column is empty for every project, so no basin matches either (known bug 1). Every
project of a resource type therefore has the same drilling capex.

The prices are the reduced form's, without the gas price floor: $P^g_{i,y} = D_{2023} H_y +
\beta_{r(i)}$, and $P^o_{i,y} = \tilde o_y$ when $\tilde o_y \ge 20$, otherwise the project's base
oil price $\bar P^o_i$ (`base_oil_prc_by_play.csv` by play name, 50 \$/bbl when there is none; as
shipped, 50 for every project).

### 7.2 Which projects drill

Each year from 2024, projects are taken in order of $\mathit{NPV}_{i,y}$, highest first. A project
drills if $\mathit{NPV}_{i,y} > 0$, $N_i > 0$, $N^{\mathrm{cum}}_i < N_i$ and its well cap has room;
otherwise its wells last year are set to 0. There is a national yearly cap for oil-directed wells
(tight oil) and one for gas-directed wells:

$$
\bar W^{\mathrm{oil}}_y = 11000\, (1 + \gamma)^{y - 2024}, \qquad \bar W^{\mathrm{gas}}_y = 9000\, (1 + \gamma)^{y - 2024} \tag{37}
$$

with $\gamma = 0$ as shipped. A project that drills drills

$$
w_{i,y} = \min\big( \Omega_{i,y},\; N_i - N^{\mathrm{cum}}_i,\; \text{what is left of its cap} \big) \tag{38}
$$

wells, where $\Omega_{i,y}$ is the NEMS drilling rule of section 7.3, and $N^{\mathrm{cum}}_i$ and the cap
are updated before the next project.

### 7.3 NEMS drilling rule

`on_next_wells` and `_base_well_count`, rewritten from NEMS `drilling_equations.py`. For project
$i$: $R_i$ = `max_annual_wells` and $\xi_i$ = `max_annual_%_dev` (`on_drill_eq_constraints.csv`),
$\alpha_i$ = `max_drill_rate_frac` (`on_process_codes.csv`), share of wells drilled before the
drilling rate declines $\pi^{\mathrm{pre}} = 0.70$, ramp-up years $T = 5$, and $w^{\mathrm{last}}_i$ its wells last
year. $\lfloor\cdot\rfloor$ rounds down.

1. Start year: $u_0 = T$ if $w^{\mathrm{last}}_i > 0.8 R_i$, otherwise
   $u_0 = \lceil T\, w^{\mathrm{last}}_i / \max(1, R_i) \rceil + 1$.
2. Base count $\Omega^0$ and year $u$, by replaying the project's drilling from year $u_0$ until
   the wells replayed exceed $N^{\mathrm{cum}}_i$. If $N_i \le 0$ or $N_i \le N^{\mathrm{cum}}_i$, then
   $\Omega^0 = 0$ and $u = u_0$. Otherwise start with $u = u_0$, $\Sigma = 0$, $u_d = 0$, and
   repeat while $\Sigma \le N^{\mathrm{cum}}_i$:
    - if $u < T$: $\Omega = \max(w^{\mathrm{last}}_i, \lfloor R_i\, u / T \rfloor)$. If
      $\Sigma / N_i > \pi^{\mathrm{pre}}$, set $\Omega = \lfloor \Omega\, (1 - \alpha_i)^{u - u_d} \rfloor$
      and leave the loop at once, skipping the last step below; otherwise set $u_d = u$;
    - if $u \ge T$: $\Omega = \lfloor R_i \rfloor$. If $\Sigma / N_i > \pi^{\mathrm{pre}}$, set
      $\Omega = \lfloor \Omega\, (1 - \alpha_i)^{u - u_d} \rfloor$; otherwise set $u_d = u$;
    - last step: $\Omega = \max(\Omega, 5)$, $\Sigma = \Sigma + \Omega$, $u = u + 1$.

    $\Omega^0$ is the last $\Omega$, and $u$ the year reached.
3. If $\Omega^0 > 2 w^{\mathrm{last}}_i$ and $w^{\mathrm{last}}_i > 0$, $\Omega^0 = \max(2 w^{\mathrm{last}}_i, 10)$.
4. Development limit: $\bar\Omega_i = \max\big(\xi_i (N_i - N^{\mathrm{cum}}_i),\, 0\big)$, times $u / T$ if
   $u < T$.
5. With the price adjustment $\Phi_{i,y}$ below,

$$
\Omega_{i,y} = \Big\lfloor \max\Big( \min\big( \max(\Omega^0, 0)\, \Phi_{i,y},\; \bar\Omega_i \big),\; 0.8\, w^{\mathrm{last}}_i \Big) \Big\rfloor \tag{39}
$$

The price adjustment (`calculate_price_adjustment`) compares prices with base prices,
$\theta^o = P^o_{i,y} / \bar P^o_i$ and $\theta^g = P^g_{i,y} / \bar P^g$ with $\bar P^g = 2.50$ (`base_gas_price_2023`),
and uses the project's first-year production $\mathit{OP}_{i,1}$ and $\mathit{GP}_{i,1}$:

- Oil wells ($\mathit{wt}_i$ = 1 or 2): if $\mathit{OP}_{i,1} = 0$, $z = \sqrt{\theta^o}$. Otherwise
  $z = \theta^o$, and for tight oil ($\mathit{wt}_i = 2$) with $\mathit{OP}_{i,1} \ge 1$: if $z < 1$,
  $z = \min\big(z^{1/\sqrt{\mathit{OP}_{i,1}/100}},\, 0.98\big)$; if $z > 1$,
  $z = \max\big(z^{\sqrt{\mathit{OP}_{i,1}/100}},\, 1.02\big)$. Then, if the gas-oil ratio
  $5600\, \mathit{GP}_{i,1} / \mathit{OP}_{i,1}$ exceeds 6000, $z = z^{\sqrt{\min(\theta^g,\, 1.5)}}$.
- Gas wells: if $\mathit{GP}_{i,1} = 0$, $z = \sqrt{\theta^g}$. Otherwise $z = \theta^g$, and for tight or
  shale gas ($\mathit{wt}_i$ = 4 or 5) with $\mathit{GP}_{i,1} \ge 10$: if $z < 1$,
  $z = \min\big(z^{1/\sqrt{\mathit{GP}_{i,1}/1000}},\, 0.98\big)$; if $z > 1$,
  $z = \max\big(z^{\sqrt{\mathit{GP}_{i,1}/1000}},\, 1.02\big)$.
- $\Phi_{i,y} = \min(z, 2)$, multiplied by 1.25 when it is below 1 and `low_price_flag` is 1 (0 as
  shipped).

The clamps for tight oil and for tight or shale gas make larger producers respond more to price
rises and less to falls, and make any change at least 2%, before the gas-oil ratio step and the
low-price flag.

### 7.4 Production

Gas and crude oil from the engine are the existing wells plus every vintage of new wells, with
later vintages slightly more productive:

$$
G^E_{r,a,y} = G^{\mathrm{leg}}_{r,a,y} + \frac{1}{1000} \sum_{i \in \mathcal I_{r,a}} \sum_{v = 2024}^{y} w_{i,v}\; \mathit{GP}_{i,\, y - v + 1}\, (1 + \tau^E_i)^{v - 2023} \tag{40}
$$

$$
O^E_{r,a,y} = O^{\mathrm{leg}}_{r,a,y} + \sum_{i \in \mathcal I_{r,a}} \sum_{v = 2024}^{y} w_{i,v}\; \mathit{OP}_{i,\, y - v + 1}\, (1 + \tau^E_i)^{v - 2023} \tag{41}
$$

in BCF and thousand barrels. $\mathcal I_{r,a}$ is the continuous projects of region $r$ and class
$a$ (AD for tight oil). $\tau^E_i$ is 0.01 a year for tight oil and shale gas and 0.0025 for
coalbed methane; the engine is meant to read these from `on_tech_levers.csv` but does not (known
bug 2), so shale gets 0.01, not the file's 0.02.

In the engine, $G^{\mathrm{leg}}$ and $O^{\mathrm{leg}}$ (eq. 31) are computed after NEMS's decline overrides when
`apply_decline_overrides` is 1, as shipped. They apply to existing projects with process code 7 or
below that are oil wells ($\mathit{wt}_i$ = 1 or 2) with $\mathit{OP}_{i,1} > 0$, using oil decline rates,
or shale gas wells ($\mathit{wt}_i = 5$) with $\mathit{GP}_{i,1} > 0$, using gas decline rates. Each of the
project's two profiles whose first-year value is positive is rebuilt from that value:

$$
\mathit{GP}'_{i,1} = \mathit{GP}_{i,1}, \qquad \mathit{GP}'_{i,n} = \mathit{GP}_{i,1}\, (1 - d^{\mathrm{first}}_i)\, (1 - d^{\mathrm{later}}_i)^{n - 2}, \quad n \ge 2 \tag{42}
$$

and the same for $\mathit{OP}$. The first-year decline $d^{\mathrm{first}}_i$ and the later decline $d^{\mathrm{later}}_i$ are
looked up by play, then by region and oil or gas type, then by region; a project for which no
positive first-year decline is found keeps its profiles. So `hsm_us_legacy_gas.csv` differs in an
engine run, and `hsm_us_crude.csv` reports $O^E$, new wells included.

### 7.5 Capacity from the engine

For $y \ge 2024$, in regions with projects in the decks, NA gas is spread over the cost tiers in
proportion to their base capacities (eq. 4), AD gas goes to `medium_cost`, and the engine's
calibration $K^E_{r,y}$ (eq. 10 with the values in `us_gas_calibration_engine.csv`) is applied to
each:

$$
Q^{\mathrm{NA}}_{r,j,y} = G^E_{r,\mathrm{NA},y}\, \frac{\sum_{\mathit{type}} \overline{\mathit{Cap}}_{r,j,\mathit{type}}}{\sum_{j',\, \mathit{type}} \overline{\mathit{Cap}}_{r,j',\mathit{type}}}\, K^E_{r,y}, \qquad
Q^{\mathrm{AD}}_{r,\mathrm{Med},y} = G^E_{r,\mathrm{AD},y}\, K^E_{r,y} \tag{43}
$$

New England, which has no projects in the decks, in every year, and every region in 2023 use the
reduced form without the split, $Q^{\mathrm{NA}}_{r,j,y} = \mathit{Cap}_{r,j}(p_{r,y}, y)\,
K^E_{r,y}$, and have no AD row. Using $K^E$ for 2023 is known bug 3: it makes 2023 about three
times too high.

## 8. Results

| File | Content | Equations |
|---|---|---|
| `hsm_us_gas_capacity.csv` | $Q^{\mathrm{NA}}_{r,j,y}$ (`gas_type` `na`) and $Q^{\mathrm{AD}}_{r,\mathrm{Med},y}$ (`ad`), BCF/yr | 8, 9; with the engine 43, and for 2023 and New England the unsplit form of section 7.5 |
| `hsm_canada_na_prod.csv` | $\mathit{NA}_{\mathit{cr},y}$, BCF/yr | 28 |
| `hsm_canada_ad_prod.csv` | $\mathit{AD}_{\mathit{cr},y}$, BCF/yr | 24 |
| `hsm_canada_realized_na_prod.csv` | copy of $\mathit{NA}_{\mathit{cr},y}$ | 28 |
| `hsm_us_legacy_gas.csv` | $G^{\mathrm{leg}}_{r,a,y}$, BCF/yr | 31; with the engine, after eq. 42 |
| `hsm_us_crude.csv` | $O^{\mathrm{leg}}_{r,a,y}$, or $O^E_{r,a,y}$ with the engine, thousand barrels/yr | 31, or 41 with the engine |
