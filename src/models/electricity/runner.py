"""This file is a collection of functions that are used to build, run, and solve the electricity model."""

from datetime import datetime
from logging import getLogger

import pyomo.environ as pyo
from pyomo.common.timing import TicTocTimer
from pyomo.opt import SolutionStatus, SolverStatus, TerminationCondition
from pyomo.util.infeasible import log_infeasible_constraints

# Import python modules
from src.common.common_config import CommonConfig
from src.integrator.utilities import select_solver
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.postprocessor import postprocessor, export_variables_to_csv
from src.models.electricity.utilities import check_results

logger = getLogger(__name__)


def build_elec_model(
    model_sets: ModelSets,
    param_data: ParamData,
    elec_config: ElecConfig,
    common_config: CommonConfig,
) -> PowerModel:
    """building pyomo electricity model

    Parameters
    ----------
    all_frames : dict of pd.DataFrame
        input data frames
    setin : Sets
        input settings Sets

    Returns
    -------
    PowerModel
        built (but unsolved) electricity model
    """
    # Building model
    logger.info('Build Pyomo')
    instance = PowerModel(
        model_sets, param_data, elec_config=elec_config, common_config=common_config
    )

    # add electricity price dual
    instance.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)
    # instance.pprint()

    # number of variables
    nvar = pyo.value(instance.nvariables())
    logger.info('Number of variables =' + str(nvar))
    # number of constraints
    ncon = pyo.value(instance.nconstraints())
    logger.info('Number of constraints =' + str(ncon))

    return instance


def solve_elec_model(instance: PowerModel, elec_config: ElecConfig):
    """solve electricity model

    Parameters
    ----------
    instance : PowerModel
        built (but not solved) electricity pyomo model
    """

    # select solver
    opt = select_solver(instance)

    logger.info('Solving Pyomo')

    if (
        elec_config.expansion_learning_type == ExpansionLearningType.LINEAR
    ):  # run iterative learning
        # Set any high tolerance
        tol = 999
        i = 0

        # initialize capacity to set pricing
        init_old_cap(instance)
        instance.new_cap = instance.old_cap
        update_cost(instance)

        while tol > 0.1 and i < 20:
            logger.info('Linear iteration number: ' + str(i))

            i += 1
            # solve model
            opt_success = opt.solve(instance)

            # set new capacities
            set_new_cap(instance)

            # Update tolerance
            tol = sum(
                [
                    abs(instance.old_cap_wt[(tech, y)] - instance.new_cap_wt[(tech, y)])
                    for (tech, y) in instance.cap_set
                ]
            )

            # update learning costs in model
            update_cost(instance)

            # update old capacities
            instance.old_cap = instance.new_cap
            instance.old_cap_wt = instance.new_cap_wt

            logger.info('Tolerance: ' + str(tol))
    else:
        opt_success = opt.solve(instance)

    ### Check results and load model solutions
    # Check results for termination condition and solution status
    # TODO:  re-examine this.  We're getting "failed" reports in log that
    #        appear to be optimially solved
    if check_results(opt_success, SolutionStatus, TerminationCondition):
        name = 'noclass!'
        logger.info(f'[{name}] Solve failed')
        if opt_success is not None:
            logger.info('status=' + str(opt_success.solver.status))
            logger.info('TerminationCondition=' + str(opt_success.solver.termination_condition))

    # If model solved, load model solutions into model, else exit
    try:
        if (opt_success.solver.status == SolverStatus.ok) and (
            opt_success.solver.termination_condition == TerminationCondition.optimal
        ):
            instance.solutions.load_from(opt_success)
        else:
            logger.warning('Solve Failed.')
            exit()
    except:
        logger.warning('Solve Failed.')
        exit()


def run_elec_model(common_config: CommonConfig, elec_config: ElecConfig, solve=True) -> PowerModel:
    """build electricity model (and solve if solve=True) after passing in settings"""

    # Measuring the run time of code
    start_time = datetime.now()
    timer = TicTocTimer(logger=logger)
    timer.tic('start')

    ###############################################################################################
    # Pre-processing

    logger.info('Preprocessing')
    model_sets = ModelSets(common_config, elec_config)
    logger.debug('Model set inputs produced')
    model_params = ParamData(common_config, elec_config, model_sets)
    logger.debug(
        'Model parameter inputs produced with %d dictionaries and %d dataframes',
        len(model_params.param_frames),
        len(model_params.param_dicts),
    )

    # all_frames, setin = prep.preprocessor(prep.ModelSets(common_config, elec_config))

    # logger.debug(
    #     f'Proceeding to build model for years: {settings.years} and regions: {settings.regions}'
    # )
    timer.toc('preprocessor finished')

    ###############################################################################################
    # Build model

    instance = build_elec_model(
        model_sets, model_params, elec_config=elec_config, common_config=common_config
    )
    timer.toc('build model finished')

    # stop here if no solve requested...
    if not solve:
        return instance

    ###############################################################################################
    # Solve model
    solve_elec_model(instance, elec_config=elec_config)

    timer.toc('solve model finished')
    logger.info('Solve complete')

    # save electricity prices for H2 connection
    # component_objects_to_df(instance.)

    # Check
    # Objective Value
    obj_val = pyo.value(instance.total_cost)
    # print('Objective Function Value =',obj_val)

    logger.info('Displaying solution...')
    logger.info(f'instance.total_cost(): {instance.total_cost()}')

    logger.info('Logging infeasible constraints...')
    log_infeasible_constraints(instance, logger=logger)

    logger.info('dispatch cost value =' + str(pyo.value(instance.dispatch_cost)))
    logger.info('unmet load cost value =' + str(pyo.value(instance.unmet_load_cost)))
    if elec_config.capacity_expansion:
        logger.info('cap expansion value =' + str(pyo.value(instance.capacity_expansion_cost)))
        logger.info('fixed om cost value =' + str(pyo.value(instance.fixed_om_cost)))
    if elec_config.spinning_reserve_required:
        logger.info('op res value =' + str(pyo.value(instance.operating_reserves_cost)))
    if elec_config.ramping_required:
        logger.info('ramp cost value =' + str(pyo.value(instance.ramp_cost)))
    if elec_config.regional_exchange:
        logger.info('trade cost value =' + str(pyo.value(instance.trade_cost)))

    logger.info('Obj complete')

    timer.toc('done with checks and extracting vars')

    ###############################################################################################
    # Post-procressing

    export_variables_to_csv(instance, output_dir=common_config.output_path, core_only=True)
    timer.toc('postprocessing done')

    # final steps for measuring the run time of the code
    end_time = datetime.now()
    run_time = end_time - start_time
    timer.toc('finished')
    logger.info(
        '\nStart Time: '
        + datetime.strftime(start_time, '%m/%d/%Y %H:%M')
        + ', Run Time: '
        + str(round(run_time.total_seconds() / 60, 2))
        + ' mins'
    )

    return instance


###################################################################################################
# Support functions


def init_old_cap(instance: PowerModel):
    """initialize capacity for 0th iteration

    Parameters
    ----------
    instance : PowerModel
        unsolved electricity model
    """
    instance.old_cap = {}
    instance.cap_set = []
    instance.old_cap_wt = {}

    for r, tech, y, step in instance.CapCostLearning:
        if (tech, y) not in instance.old_cap:
            instance.cap_set.append((tech, y))
            # each tech will increase cap by 1 GW per year. reasonable starting point.
            # TODO:  come back to this assumption after better understanding of process
            instance.old_cap[(tech, y)] = (y - instance.y0_learning) * 1
            instance.old_cap_wt[(tech, y)] = instance.WeightYear[y] * instance.old_cap[(tech, y)]


def set_new_cap(instance: PowerModel):
    """calculate new capacity after solve iteration

    Parameters
    ----------
    instance : PowerModel
        solved electricity pyomo model
    """
    instance.new_cap = {}
    instance.new_cap_wt = {}
    for r, tech, y, step in instance.CapCostLearning:
        if (tech, y) not in instance.new_cap:
            instance.new_cap[(tech, y)] = 0.0
        instance.new_cap[(tech, y)] = instance.new_cap[(tech, y)] + sum(
            instance.capacity_builds[(r, tech, year, step)].value
            for year in instance.year
            if year < y
        )
        instance.new_cap_wt[(tech, y)] = instance.WeightYear[y] * instance.new_cap[(tech, y)]


def cost_learning_func(instance: PowerModel, tech, y):
    """function for updating learning costs by technology and year

    Parameters
    ----------
    instance : PowerModel
        electricity pyomo model
    tech : int
        technology type
    y : int
        year

    Returns
    -------
    int
        updated capital cost based on learning calculation
    """
    cost = (
        (
            instance.SupplyCurveLearning[tech]
            + 0.0001 * (y - instance.y0_learning)
            + instance.new_cap[tech, y]
        )
        / instance.SupplyCurveLearning[tech]
    ) ** (-1.0 * instance.LearningRate[tech])
    return cost


def update_cost(instance):
    """update capital cost based on new capacity learning

    Parameters
    ----------
    instance : PowerModel
        electricity pyomo model
    """
    new_multiplier = {}
    for tech, y in instance.cap_set:
        new_multiplier[(tech, y)] = cost_learning_func(instance, tech, y)

    new_cost = {}
    # Assign new learning
    for r, tech, y, step in instance.CapCostLearning:
        # updating learning cost
        new_cost[(r, tech, y, step)] = (
            instance.CapCostInitial[(r, tech, step)] * new_multiplier[tech, y]
        )
        instance.CapCostLearning[(r, tech, y, step)].value = new_cost[(r, tech, y, step)]
