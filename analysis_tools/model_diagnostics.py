"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/29/26
"""

from itertools import groupby

from pyomo.common.numeric_types import value
from pyomo.core import Constraint, Param, Var
from pyomo.core.base.set import Set
from pyomo.repn import generate_standard_repn

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
    """Break out the elements of the objective function."""
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


def breakdown_obj_terms(em: PowerModel) -> None:
    """Print the objective function (``em.total_cost``) as (variable, index, coefficient) triplets.

    Uses Pyomo's standard representation generator to decompose the linear objective
    expression into its individual variable terms, printing one row per term sorted by
    descending coefficient magnitude (ties broken by variable name, then index). Terms that
    share an identical coefficient value are collapsed: only the first is printed, followed by
    a summary line noting how many others were folded into it. A final row is printed for the
    constant term, if nonzero.

    Parameters
    ----------
    em : PowerModel
        A solved PowerModel instance.
    """
    repn = generate_standard_repn(em.total_cost.expr)
    terms = [(var, value(coef)) for var, coef in zip(repn.linear_vars, repn.linear_coefs)]
    terms.sort(
        key=lambda term: (-abs(term[1]), term[0].parent_component().name, str(term[0].index()))
    )

    print(f'{"Variable":<30}{"Index":<50}{"Coefficient":>15}')
    for coef_value, group in groupby(terms, key=lambda term: term[1]):
        group = list(group)
        var, _ = group[0]
        print(f'{var.parent_component().name:<30}{str(var.index()):<50}{coef_value:>19.4e}')
        if len(group) > 1:
            print(f'  ... and {len(group) - 1} others with same coefficient')

    if repn.constant:
        print(f'{"constant":<30}{"":<50}{value(repn.constant):>15.4f}')


def capacity_inspector(em: PowerModel, region: str, year: int):
    """breakdown the capacity variable for all techs in a particular region-year combo.

    Reads from this variable:
    self.capacity_total[r, tech, step, y]
    """
    for tech in em.tech:  # range(1,16):
        for step in em.step:  # [1,2,3]:
            try:
                print(
                    f'Capacity for {tech}-{step} in {region} {year}: {value(em.capacity_total[region, tech, step, year])}'
                )  # [region, tech, step, year])}')
            except:
                print(f'Capacity for {tech}-{step} in {region} {year}: N/A')
            for hour in range(1, 11):
                try:
                    print(
                        f'   Generation: {value(em.generation_total[region, tech, step, year, hour])}'
                    )
                except:
                    print('   Generation: N/A')
        print()


def load_inspector(em: PowerModel, region: str):
    """Inpsect the load parameter.

    Reads from this parameter:
    self.Load[(r, y, hr)]
    """
    for year in [2025, 2030]:
        for hour in range(10):
            try:
                print(f'Load for {year} hour {hour}: {value(em.Load[region, year, hour])}')
            except:
                print(f'Load for {year} hour {hour}: N/A')
