"""This file is the main postprocessor for the electricity model.

It writes out all relevant model outputs (e.g., variables, parameters, constraints). It contains:
 - A function that converts pyomo component objects to dataframes
 - A function that writes the dataframes to output directories
 - A function to make the electricity output sub-directories
 - The postprocessor function, which loops through the model component objects and applies the
 functions to convert and write out the data to dfs to the electricity output sub-directories

"""

###################################################################################################
# Setup

import os
from logging import getLogger
from pathlib import Path

import pyomo.environ as pyo

from src.models.electricity.utilities import create_obj_df

# Establish logger
logger = getLogger(__name__)

###################################################################################################
# Review of Variables, Sets, Parameters, Constraints


def report_obj_df(mod_object, instance, dir_out, sub_dir):
    """Creates a df of the component object within the pyomo model, separates the key data into
    different columns and then names the columns if the names are included in the cols_dict.
    Writes the df out to the output directory.

    Parameters
    ----------
    obj : pyomo component object
        e.g., pyo.Var, pyo.Set, pyo.Param, pyo.Constraint
    instance : pyomo model
        electricity concrete model
    dir_out : str
        output electricity directory
    sub_dir : str
        output electricity sub-directory
    """
    # get name of object
    if '.' in mod_object.name:
        name = mod_object.name.split('.')[1]
    else:
        name = mod_object.name

    # list of names to not report
    # TODO:  Consider if these objs needs reporting, and if so adjust...
    if name not in ['var_elec_request', 'FixedElecRequest']:
        # get data associated with object
        df = create_obj_df(mod_object)
        if not df.empty:
            # get column names associated with object if available
            if name in instance.cols_dict:
                df.columns = ['Key'] + instance.cols_dict[name]
            elif len(df.columns) == 2:
                df.columns = ['Key', name]
            else:
                pass
                # logger.debug('Electricity Model:' + name + ' missing from cols_dict')
            df.to_csv(Path(dir_out / sub_dir / f'{name}.csv'), index=False)
        else:
            logger.info('Electricity Model:' + name + ' is empty.')


def make_elec_output_dir(output_dir):
    """generates an output subdirectory to write electricity model results. It includes subdirs for
    vars, params, constraints.

    Returns
    -------
    string
        the name of the output directory
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        os.makedirs(Path(output_dir / 'variables'))
        os.makedirs(Path(output_dir / 'parameters'))
        os.makedirs(Path(output_dir / 'constraints'))
        os.makedirs(Path(output_dir / 'sets'))
        os.makedirs(Path(output_dir / 'prices'))
        os.makedirs(Path(output_dir / 'obj'))


###################################################################################################
# Main Project Execution
def postprocessor(instance):
    """master postprocessor function that writes out the final dataframes from to the electricity
    model. Creates the output directories and writes out dataframes for variables, parameters, and
    constraints. Gets the correct columns names for each dataframe using the cols_dict.

    Parameters
    ----------
    instance : pyomo model
        electricity concrete model

    Returns
    -------
    string
        output directory name
    """
    output_dir = Path(instance.OUTPUT_ROOT / 'electricity')
    make_elec_output_dir(output_dir)

    for variable in instance.component_objects(pyo.Var, active=True):
        report_obj_df(variable, instance, output_dir, 'variables')

    for set in instance.component_objects(pyo.Set, active=True):
        report_obj_df(set, instance, output_dir, 'sets')

    for parameter in instance.component_objects(pyo.Param, active=True):
        report_obj_df(parameter, instance, output_dir, 'parameters')

    for constraint in instance.component_objects(pyo.Constraint, active=True):
        report_obj_df(constraint, instance, output_dir, 'constraints')
