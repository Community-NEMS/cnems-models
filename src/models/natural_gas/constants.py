"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Any constants used in natural gas processing

"""

# a solved unserved demand [Bcf] above this in any region-year puts the solve in PENALTY status and
# is listed in a warning; anything smaller is treated as solver decimal dust.  Solver residuals run
# under 1e-12 Bcf with HiGHS (an interior-point solve without crossover can leave more), and real
# shortfalls seen so far are 1.4 Bcf and up
UNSERVED_PENALTY_TOL = 1e-4

# $/MMBtu penalty on unserved demand (see the unserved-demand backstop in ng_model.py). Set ~100x
# any plausible gas price so the backstop is never economic and only relieves a genuine shortfall:
# demand above what a region can deliver, or a subset whose supplying neighbors were dropped.
UNSERVED_DEMAND_PENALTY = 1000.0


# Coupling with the Magic HSM test model.  HUB_REGION's price stands in for Henry Hub, which is in
# West South Central.  PRICE_DOLLAR_YEAR is the dollar year of C-NGMM's prices:  input/natural_gas
# does not declare one, and its AEO2026 sources report 2025 dollars.
HUB_REGION = 'west_south_central'
PRICE_DOLLAR_YEAR = 2025
