"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/23/26

Any constants used in elec processing

"""

# TODO:  After these are imported/aligned, switch to all caps
TransLoss = 0.02  # Transmission losses %
# 13.84 kwh/kg, for kwh/kg H2 -> 54.3, #conversion kwh/kg to GWh/kg
H2Heatrate = 13.84 / 1000000

UNMET_LOAD_PRICE = 500_000  # TODO:  This seems quite high, but scale is unclear RN.  Re-evaluate

STORAGE_LEVEL_COST = 0.00000001  # TODO:  This seems wayyyy small and has no units.  Re-evaluate
