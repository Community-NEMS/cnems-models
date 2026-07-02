"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/15/26

A temporary (?) test to lock down the current outputs of a basic no-frills test run

"""

from pathlib import Path

import pytest
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common import config_setup
from src.common.common_config import CommonConfig
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.runner import run_elec_model
from tests.model_diagnostics import (
    breakdown_obj_elements,
    gather_set_data,
    capacity_inspector,
    load_inspector,
    gather_var_data,
    gather_constraint_data,
    gather_param_data,
)

verbose = False

# TODO:  Add combination with expansion + margin required to test combo constraint near line 1500 in model

# Test configurations with expected ORIGINAL outputs:
# Run Type                                  Total Cost         Variables    Constraints      Notes for new
# ----------------------------------------  -----------------  -----------  -----------     ---------------
# Basic No-Frills                           3452103301.9            17886        19440      constr = 19632 VRE_UB fix (+192)
# Exchange Enabled                          2278237043.0            21342        23088      constr = 23280 (+192 constr, from above)
# Expansion (no learning)                   3455793875.5            18060        19566      same... +192
# Ramping Required                          3522284566.9            32862        41904      same... +192
# Reserve Margin (mandatory expansion)      4925573167.9            19212        22446
# Agg Years                                 ??  Broken.  Suspect it is used in preprocessor

configs = [
    ('basic_elec_config.toml', 3452103301.9, 17886, 19632),
    ('exchange_elec_config.toml', 2278237043.0, 21342, 23280),
    ('expansion_no_learning_elec_config.toml', 3455793875.5, 18060, 19758),
    ('ramping_elec_config.toml', 3522284566.9, 32862, 42096),
    ('reserve_with_expansion_no_learning_elec_config.toml', 4925573167.9, 19212, 22638),
    # ('agg_years_elec_config.toml', 3452103301.9, 17886, 19440),
]


@pytest.mark.parametrize(
    'config_file,expected_total_cost,expected_nvariables,expected_nconstraints',
    configs,
    ids=[
        'Basic No-Frills',
        'Exchange Enabled',
        'Expansion (no learning)',
        'Ramping Required',
        'Reserve with Expansion (no learning)',
        # 'Agg Years',
    ],
)
def test_basic_run(config_file, expected_total_cost, expected_nvariables, expected_nconstraints):
    """
    Perform a couple of basic runs (with some features in isolation) and compare results to captured values

    dev notes:
    1.  basic config file turns OFF many features that may need separate verification
    2.  the values captured here for test were generated from run of legacy code and are *assumed*
        good for this test and dataset
    """
    # config_path = Path(PROJECT_ROOT, 'tests/electric/meta_config.toml')
    config_path = Path(PROJECT_ROOT, 'tests/electric', config_file)
    common_config, remainder = CommonConfig.from_toml(config_path)

    # introduce the new ElecConfig
    elec_config = ElecConfig(**remainder.pop('elec_config'))

    elec_model = run_elec_model(common_config, elec_config, solve=True)
    if verbose:
        print('\n~~ Sets ~~')
        gather_set_data(elec_model)
        print('\n~~ Variables ~~')
        gather_var_data(elec_model)
        print('\n~~ Parameters ~~')
        gather_param_data(elec_model)
        print('\n~~ Constraints ~~')
        gather_constraint_data(elec_model)
        print()
        breakdown_obj_elements(elec_model)
        capacity_inspector(elec_model, region='7', year=2025)
        load_inspector(elec_model, region='7')
        if hasattr(elec_model, 'fixed_om_cost'):
            elec_model.fixed_om_cost.pprint()
            # print(f'terms in om cost: {elec_model.fixed_om_cost.linear_vars}')
            print(
                f'sum of capacity: {sum(value(elec_model.capacity_total[i]) for i in elec_model.capacity_total)}'
            )
            print(
                f'sum of expansion: {sum(value(elec_model.capacity_builds[i]) for i in elec_model.capacity_builds)}'
            )
            print(
                f'sum of retirements: {sum(value(elec_model.capacity_retirements[i]) for i in elec_model.capacity_retirements)}'
            )
    # for test development/capture:
    print(value(elec_model.total_cost), elec_model.nvariables(), elec_model.nconstraints())

    assert value(elec_model.total_cost) == pytest.approx(expected_total_cost), (
        f'found {value(elec_model.total_cost)} total cost'
    )
    assert elec_model.nvariables() == expected_nvariables, (
        f'found {elec_model.nvariables()} variables'
    )
    assert elec_model.nconstraints() == expected_nconstraints, (
        f'found {elec_model.nconstraints()} constraints'
    )
