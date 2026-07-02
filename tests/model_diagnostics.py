"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/29/26
"""

from pyomo.common.numeric_types import value
from pyomo.core import Constraint, Param, Var
from pyomo.core.base.set import Set

from src.models.electricity.electricity_model import PowerModel


def gather_set_data(em: PowerModel):
    """Output the size of every Pyomo Set object in the PowerModel."""
    for set_obj in em.component_objects(Set):
        print(set_obj.name, len(set_obj))


def gather_var_data(em: PowerModel):
    """Output the size of every Pyomo Var object in the PowerModel."""
    for var_obj in em.component_objects(Var):
        print(var_obj.name, len(var_obj))


def gather_param_data(em: PowerModel):
    """Output the size of all params in the PowerModel."""
    for p_obj in em.component_objects(Param):
        print(p_obj.name, len(p_obj))


def gather_constraint_data(em: PowerModel):
    """Output the size of all constraints in the PowerModel."""
    for c_obj in em.component_objects(Constraint):
        print(c_obj.name, len(c_obj))


def breakdown_obj_elements(em: PowerModel):
    """Break out the elements of the objective function"""
    print(f'Dispatch cost: {value(em.dispatch_cost):.2f}')
    print(f'Unmet load cost: {value(em.unmet_load_cost):.2f}')
    try:
        print(f'Fixed O&M cost: {value(em.fixed_om_cost):.2f}')
    except AttributeError:
        print('Fixed O&M cost: N/A')
    try:
        print(f'Capacity expansion cost: {value(em.capacity_expansion_cost):.2f}')
    except AttributeError:
        print('Capacity expansion cost: N/A')
    try:
        print(f'Trade cost: {value(em.trade_cost):.2f}')
    except AttributeError:
        print('Trade cost: N/A')
    try:
        print(f'Ramp cost: {value(em.ramp_cost):.2f}')
    except AttributeError:
        print('Ramp cost: N/A')
    try:
        print(f'Operating reserves cost: {value(em.operating_reserves_cost):.2f}')
    except AttributeError:
        print('Operating reserves cost: N/A')
    print(f'Total cost: {value(em.total_cost):.2f}')


def capacity_inspector(em: PowerModel, region: str, year: int):
    """breakdown the capacity variable for all techs in a particular region-year combo from
    this variable:
    self.capacity_total[(r, season, tech, step, y)]
    """
    for tech in em.tech:  # range(1,16):
        for step in em.step:  # [1,2,3]:
            try:
                print(
                    f'Capacity for {tech}-{step} in {region} {year}: {value(em.capacity_total[(region, "1", tech, step, year)])}'
                )  # [(region, 1, tech, step, year)])}')
            except:
                print(f'Capacity for {tech}-{step} in {region} {year}: N/A')
            for hour in range(1, 11):
                try:
                    print(
                        f'   Generation: {value(em.generation_total[tech, year, region, step, hour])}'
                    )
                except:
                    print(f'   Generation: N/A')
        print()


def load_inspector(em: PowerModel, region: str):
    """Inpsect the load parameter:
    self.Load[(r, y, hr)]
    """
    for year in [2025, 2030]:
        for hour in range(10):
            try:
                print(f'Load for {year} hour {hour}: {value(em.Load[region, year, hour])}')
            except:
                print(f'Load for {year} hour {hour}: N/A')
