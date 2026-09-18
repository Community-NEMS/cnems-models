"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/23/26

Any constants used in elec processing

"""

TRANSMISSION_LOSS_FACTOR = 0.02  # Transmission losses %

# 13.84 kwh/kg, for kwh/kg H2 -> 54.3, #conversion kwh/kg to GWh/kg
H2_HEATRATE = 13.84 / 1000000  # currently NOT used

UNMET_LOAD_PRICE = 500_000  # TODO:  This seems quite high, but scale is unclear RN.  Re-evaluate

STORAGE_LEVEL_COST = 0.00000001  # TODO:  This seems wayyyy small and has no units.  Re-evaluate

#########  Reserve Policy Data ###########

# used in operating reserve cost in OBJ:
SPINNING_RESERVE_DEFAULT_COST = 0.01  # for spinning/flex  # TODO:  is this too "small"?

# reserve constants
SPINNING_RESERVE_PROPORTION = 0.03

REGULATION_RESERVE_PROPORTION = 0.01
WIND_REGULATION_RESERVE_PROPORTION = 0.005
SOLAR_REGULATION_RESERVE_PROPORTION = 0.003

WIND_FLEX_RESERVE_PROPORTION = 0.1
SOLAR_FLEX_RESERVE_PROPORTION = 0.04

#########  Natural Gas Price Linkage ###########

# $/MMBtu, the gas price the SupplyPrice.csv data is assumed to embed
INITIAL_NG_PRICE = 4.0  # TODO:  Confirm against the source of the supply price data
# share of a gas-linked tech's supply_price that moves with the natural gas price
PRICE_COST_PROPORTION = 0.5  # TODO:  Confirm against source of tech operating costs

# TODO:  May need to further refine cost proportion to be indexed by tech if multiple techs
#        And they have (meaningful) different proportions
# techs whose supply_price is linked to the natural gas price; see input/electricity/tech_data.csv
NG_PRICE_LINKED_TECHS: tuple[str, ...] = ('3',)  # Gas Turbine
