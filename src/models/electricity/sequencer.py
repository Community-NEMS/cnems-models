"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/16/26

Sequencer for the electricity model.

``ElectricitySequencer`` implements the ``IntegratedModelSequencer`` interface (build / update /
solve / postprocess) so the electricity model can be driven either standalone or inside an
integrated run. It replaces the procedural helpers previously in ``runner.py``; the module-level
``run_elec_model`` / ``solve_elec_model`` functions are thin wrappers preserved so existing callers
only need to repoint their import at this module.
"""

from datetime import datetime
from logging import getLogger

import pyomo.environ as pyo
from pydantic import BaseModel
from pyomo.common.timing import TicTocTimer
from pyomo.opt import check_optimal_termination
from pyomo.util.infeasible import log_infeasible_constraints

from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.integrator.utilities import select_solver
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.postprocessor import export_variables_to_csv

logger = getLogger(__name__)

# convergence controls for the linear-learning outer iteration
_LEARNING_TOLERANCE = 0.1
_LEARNING_MAX_ITER = 20


class ElectricitySequencer(IntegratedModelSequencer):
    """Build/solve orchestration for the electricity :class:`PowerModel`.

    Ports the functionality formerly held by ``runner.py`` into the
    :class:`~src.common.integrated_model_sequencer.IntegratedModelSequencer` interface. The built
    model plus the configs it was built from are retained on the instance so the solve/postprocess
    steps can run without re-threading arguments.
    """

    def __init__(
        self,
        model: PowerModel | None = None,
        common_config: CommonConfig | None = None,
        model_config: ElecConfig | None = None,
    ) -> None:
        """
        Parameters
        ----------
        model : PowerModel | None
            An already-built model to adopt (used by the ``solve_elec_model`` compatibility path).
        common_config : CommonConfig | None
            Common config the model was built from; needed for postprocessing paths.
        model_config : ElecConfig | None
            Electricity config the model was built from; needed for solve/postprocess branching.
        """
        self._model = model
        self._common_config = common_config
        self._model_config = model_config
        self._opt = None

    @property
    def model(self) -> PowerModel:
        """The built electricity model.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model` (or construction with an explicit model).
        """
        if self._model is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return self._model

    def build_model(
        self, common_config: CommonConfig, model_config: BaseModel, **kwargs
    ) -> PowerModel:
        """Preprocess inputs and build (but do not solve) the electricity model.

        Ports ``runner.build_elec_model`` plus the preprocessing (``ModelSets`` / ``ParamData``)
        that ``runner.run_elec_model`` performed inline.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : BaseModel
            Electricity configuration (:class:`ElecConfig`).

        Returns
        -------
        PowerModel
            The built, unsolved model (also retained as :attr:`model`).
        """
        logger.info('Preprocessing')
        model_sets = ModelSets(common_config, model_config)
        logger.debug('Model set inputs produced')
        model_params = ParamData(common_config, model_config, model_sets)
        logger.debug(
            'Model parameter inputs produced with %d dictionaries and %d dataframes',
            len(model_params.param_frames),
            len(model_params.param_dicts),
        )

        logger.info('Build Pyomo')
        instance = PowerModel(
            model_sets, model_params, elec_config=model_config, common_config=common_config
        )
        # add electricity price dual
        instance.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)

        logger.info('Number of variables =' + str(pyo.value(instance.nvariables())))
        logger.info('Number of constraints =' + str(pyo.value(instance.nconstraints())))

        self._model = instance
        self._common_config = common_config
        self._model_config = model_config
        return instance

    def update_model(self, **kwargs) -> PowerModel:
        """Refresh the learning-curve capital costs held in the model.

        Ports ``runner.update_cost`` — used both to seed costs before the first learning solve and
        to update them between iterations.

        Returns
        -------
        PowerModel
            The updated model.
        """
        update_cost(self.model)
        return self.model

    def solve_model(self, **kwargs) -> IterationStatus:
        """Solve the electricity model, iterating externally for linear learning.

        Ports ``runner.solve_elec_model``. For ``ExpansionLearningType.LINEAR`` this runs the
        outer build→solve→update loop until capacity converges (or the iteration cap is hit),
        driving each iteration through :meth:`iteration_postprocess` and :meth:`update_model`.

        Returns
        -------
        IterationStatus
            ``BEST`` on optimal termination, ``ERROR`` otherwise.
        """
        instance = self.model
        self._opt = select_solver(instance)

        logger.info('Solving Pyomo')

        if self._model_config.expansion_learning_type == ExpansionLearningType.LINEAR:
            # run iterative (external) learning
            tol = float('inf')
            i = 0

            # initialize capacity to set pricing
            init_old_cap(instance)
            instance.new_cap = instance.old_cap
            self.update_model()
            results = None
            while tol > _LEARNING_TOLERANCE and i < _LEARNING_MAX_ITER:
                logger.info('Linear iteration number: ' + str(i))
                i += 1

                # solve model
                results = self._opt.solve(instance)

                # set new capacities and measure convergence
                tol = self.iteration_postprocess()

                # update learning costs in model
                self.update_model()

                # roll capacities forward
                instance.old_cap = instance.new_cap
                instance.old_cap_wt = instance.new_cap_wt

                logger.info('Tolerance in linear learning iterations: ' + str(tol))
        else:
            results = self._opt.solve(instance)

        # Check results
        if not check_optimal_termination(results):
            logger.error('Solve Failed.  Inspect solver log for more info.')
            logger.info(
                'Termination condition: '
                + str(results.solver.termination_condition)
                + ', status: '
                + str(results.solver.status)
            )
            return IterationStatus.ERROR

        logger.info('Solve Successful')
        return IterationStatus.BEST

    def iteration_postprocess(self, **kwargs) -> float:
        """Per-iteration capacity update and convergence measure for linear learning.

        Ports the ``set_new_cap`` + tolerance computation from ``runner.solve_elec_model``'s loop.

        Returns
        -------
        float
            The weighted capacity change between the previous and new iteration.
        """
        instance = self.model
        set_new_cap(instance)
        return sum(
            abs(instance.old_cap_wt[(tech, y)] - instance.new_cap_wt[(tech, y)])
            for (tech, y) in instance.cap_set
        )

    def full_postprocess(self, **kwargs) -> None:
        """Log solution diagnostics and export the model variables to CSV.

        Ports the reporting / export tail of ``runner.run_elec_model``.
        """
        instance = self.model
        elec_config = self._model_config
        common_config = self._common_config

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

        scenario_dir = common_config.output_path / common_config.scenario_name / 'electricity'
        export_variables_to_csv(instance, output_dir=scenario_dir / 'variables', core_only=True)


###################################################################################################
# Module-level compatibility wrappers + learning-curve support functions.
#
# These preserve the call signatures previously exported by runner.py so callers only need to
# repoint their import at this module. run_elec_model / solve_elec_model delegate to
# ElectricitySequencer; the init_old_cap / set_new_cap / cost_learning_func / update_cost helpers
# operate directly on a model instance (as before) and are shared by the sequencer methods.


def run_elec_model(common_config: CommonConfig, elec_config: ElecConfig, solve=True) -> PowerModel:
    """Build the electricity model (and solve + postprocess if ``solve``), returning the model."""
    start_time = datetime.now()
    timer = TicTocTimer(logger=logger)
    timer.tic('start')

    sequencer = ElectricitySequencer()
    instance = sequencer.build_model(common_config, elec_config)
    timer.toc('build model finished')

    # stop here if no solve requested...
    if not solve:
        return instance

    status = sequencer.solve_model()
    timer.toc('solve model finished')
    logger.info('Solve complete')

    if status is IterationStatus.ERROR:
        return instance

    sequencer.full_postprocess()

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


def solve_elec_model(instance: PowerModel, elec_config: ElecConfig) -> IterationStatus:
    """Solve an already-built electricity model (compat wrapper around the sequencer)."""
    sequencer = ElectricitySequencer(model=instance, model_config=elec_config)
    return sequencer.solve_model()


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

    for r, tech, step, y in instance.CapCostLearning:
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
    for r, tech, step, y in instance.CapCostLearning:
        if (tech, y) not in instance.new_cap:
            instance.new_cap[(tech, y)] = 0.0
        instance.new_cap[(tech, y)] = instance.new_cap[(tech, y)] + sum(
            instance.capacity_builds[(r, tech, step, year)].value
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
    for r, tech, step, y in instance.CapCostLearning:
        # updating learning cost
        new_cost[(r, tech, step, y)] = (
            instance.CapCostInitial[(r, tech, step)] * new_multiplier[tech, y]
        )
        instance.CapCostLearning[(r, tech, step, y)].value = new_cost[(r, tech, step, y)]
