"""
Created as part of the C-NEMS Project.

Sequencer for the electricity model.

``ElectricitySequencer`` implements the ``IntegratedModelSequencer`` interface (build / update /
solve / postprocess) so the electricity model can be driven either standalone or inside an
integrated run. It replaces the procedural helpers previously in ``runner.py``; the module-level
``run_elec_model`` / ``solve_elec_model`` functions are thin wrappers preserved so existing callers
only need to repoint their import at this module.
"""

from collections import defaultdict
from datetime import datetime
from logging import getLogger

import pandas as pd
import pyomo.environ as pyo
from pyomo.common.numeric_types import value
from pyomo.common.timing import TicTocTimer
from pyomo.opt import check_optimal_termination
from pyomo.util.infeasible import log_infeasible_constraints

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NGElectricalDemandPackage,
    UpdatePackage,
)
from src.integrator.region_crosswalk import QuantityKind, crosswalk_values
from src.integrator.utilities import select_solver
from src.models.electricity.constants import NG_HEAT_RATE_MMBTU_PER_MWH
from src.models.electricity.elec_config import ElecConfig, ExpansionLearningType
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.postprocessor import export_variables_to_csv
from src.models.natural_gas.data import load_qp_scalars

logger = getLogger(__name__)

# where the natural gas model's scalars live; the gas burn sent back is denominated in Bcf, so
# the MMBtu-per-Bcf heat content is read from the gas model's own input rather than duplicated
NG_INPUT_DIR = PROJECT_ROOT / 'input' / 'natural_gas'
# the heat content itself, filled by ng_mmbtu_per_bcf() on first use and then reused; None until
# then, so a run that never sends a demand package never reads the gas model's inputs
_MMBTU_PER_BCF: float | None = None

# convergence controls for the linear-learning outer iteration
_LEARNING_TOLERANCE = 0.1
_LEARNING_MAX_ITER = 20


class ElectricitySequencer(IntegratedModelSequencer[PowerModel, ElecConfig]):
    """Build/solve orchestration for the electricity :class:`PowerModel`.

    Ports the functionality formerly held by ``runner.py`` into the
    :class:`~src.common.integrated_model_sequencer.IntegratedModelSequencer` interface, specialized
    to the ``(PowerModel, ElecConfig)`` pair. The built model plus the configs it was built from are
    retained on the instance so the solve/postprocess steps can run without re-threading arguments.
    """

    def __init__(self):
        """Initialize the sequencer."""
        self._model = None
        self._elec_config: ElecConfig | None = None
        self._common_config: CommonConfig | None = None
        self._opt = None
        self._last_status: IterationStatus | None = None

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

    @model.setter
    def model(self, value: PowerModel):
        """Set the model instance.  Caution:  Alignment with config settings not checked."""
        self._model = value

    @property
    def elec_config(self) -> ElecConfig:
        """The electricity config the model was built from.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model`.
        """
        if self._elec_config is None:
            raise RuntimeError('Config is not available; call build_model() first.')
        return self._elec_config

    @property
    def common_config(self) -> CommonConfig:
        """The common config the model was built from.

        Raises
        ------
        RuntimeError
            If accessed before :meth:`build_model`.
        """
        if self._common_config is None:
            raise RuntimeError('Config is not available; call build_model() first.')
        return self._common_config

    def build_model(
        self, common_config: CommonConfig, model_config: ElecConfig, update_packages=None, **kwargs
    ) -> PowerModel:
        """Preprocess inputs and build (but do not solve) the electricity model.

        Ports ``runner.build_elec_model`` plus the preprocessing (``ModelSets`` / ``ParamData``)
        that ``runner.run_elec_model`` performed inline.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration.
        model_config : ElecConfig
            Electricity configuration
        update_packages : list[UpdatePackage], optional
            Update Packages to pass to update read-in data.

        Returns
        -------
        PowerModel
            The built, unsolved model (also retained as :attr:`model`).

        """
        logger.info('Preprocessing')
        self._elec_config = model_config
        self._common_config = common_config
        model_sets = ModelSets(common_config, model_config)
        logger.debug('Model set inputs produced')
        model_params = ParamData(common_config, model_config, model_sets)
        logger.debug(
            'Model parameter inputs produced with %d dictionaries and %d dataframes',
            len(model_params.param_frames),
            len(model_params.param_dicts),
        )
        if update_packages:
            logger.info('Received %s update_packages', len(update_packages))
            for pkg in update_packages:
                model_params.apply_update_package(pkg)
        else:
            logger.info('Received no update_packages')

        logger.info('Building model')
        instance = PowerModel(
            model_sets, model_params, elec_config=model_config, common_config=common_config
        )
        # add electricity price dual
        instance.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)

        logger.info('Number of variables = %d', pyo.value(instance.nvariables()))
        logger.info('Number of constraints = %d', pyo.value(instance.nconstraints()))

        self._model = instance
        return instance

    def update_model(self, **kwargs) -> PowerModel:
        """TBD update process for the electricity model.

        Raises
        ------
        NotImplementedError
            Always; the electricity model has no update step yet.
        """
        raise NotImplementedError('update_model is not implemented for the electricity model.')

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
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
        if instance is None:
            raise RuntimeError('Solve called on model that has not been built.')
        self._opt = select_solver(
            instance,
            nonlinear=self.elec_config.expansion_learning_type is ExpansionLearningType.NONLINEAR,
        )

        logger.info('Solving model')

        if self.elec_config.expansion_learning_type == ExpansionLearningType.LINEAR:
            # run iterative (external) learning
            eps = float('inf')
            i = 0

            # initialize capacity to set pricing
            cap_growth = init_old_cap(instance)

            results = None

            while eps > _LEARNING_TOLERANCE and i < _LEARNING_MAX_ITER:
                # TODO:  Verify this sequence is correct.  We update costs only BEFORE solve s.t.
                #        the solved result holds these costs when tol < limit
                # update learning costs in model
                update_expansion_cost(instance, new_cap=cap_growth)

                # solve model
                results = self._opt.solve(instance)

                # set new capacities and measure convergence
                new_cap_growth = calculate_cap_growth(instance)
                eps = calculate_tolerance(
                    cap_growth=cap_growth,
                    new_cap_growth=new_cap_growth,
                    year_weights=instance.weight_year.extract_values(),
                )

                # update the cap growth for potential next iteration
                cap_growth = new_cap_growth

                logger.info('Tolerance in linear learning iteration %d: %0.4f', i, eps)
                i += 1

            if results is None:  # pragma: no cover - the loop always runs at least once
                raise RuntimeError('Linear learning loop exited without solving the model.')
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
            self._last_status = IterationStatus.ERROR
            return ModelType.ELECTRICITY, IterationStatus.ERROR

        logger.info('Solve Successful')
        self._last_status = IterationStatus.BEST
        return ModelType.ELECTRICITY, IterationStatus.BEST

    def get_outbound_updates(self) -> list[UpdatePackage]:
        """Package the solved gas burn for the natural gas model.

        The annual gas demand of the gas-fired techs (:func:`gas_demand_by_region`, Bcf/yr by
        electricity region) is allocated onto natural gas regions with the population-weighted
        crosswalk and sent as one :class:`NGElectricalDemandPackage`.

        Returns
        -------
        list[UpdatePackage]
            A single ``NGElectricalDemandPackage``, or nothing if the last solve was not usable
            -- a failed solve leaves no generation values to read.
        """
        if self._last_status is not IterationStatus.BEST:
            logger.warning('No usable solve (status %s); sending no updates', self._last_status)
            return []
        elec_demand = gas_demand_by_region(self.model)
        ng_demand = crosswalk_values(
            elec_demand, ModelType.ELECTRICITY, ModelType.NATURAL_GAS, QuantityKind.EXTENSIVE
        )
        for year, total in ng_demand.groupby(level='year').sum().items():
            logger.info(
                '%d gas burn sent to natural gas regions:  %0.1f Bcf over %d region(s)',
                year,
                total,
                ng_demand.xs(year, level='year').shape[0],
            )
        package = NGElectricalDemandPackage(
            elements=ng_demand.to_frame(), source=ModelType.ELECTRICITY
        )
        return [package]

    def get_objective_value(self) -> float | None:
        """Get the solved total cost -- the electricity model's objective."""
        return pyo.value(self.model.total_cost)

    def iteration_postprocess(self, **kwargs):
        """No-op; the electricity model has nothing to do between iterations."""

    def full_postprocess(self, **kwargs) -> None:
        """Log solution diagnostics and export the model variables to CSV.

        Ports the reporting / export tail of ``runner.run_elec_model``.
        """
        instance = self.model

        logger.info('Displaying solution...')
        logger.info(f'instance.total_cost(): {pyo.value(instance.total_cost)}')

        logger.info('Logging infeasible constraints...')
        log_infeasible_constraints(instance, logger=logger)

        logger.info('dispatch cost value = %.2f', pyo.value(instance.dispatch_cost))
        logger.info('unmet load cost value = %.2f', pyo.value(instance.unmet_load_cost))
        logger.info('fixed om cost value = %.2f', pyo.value(instance.fixed_om_cost))
        if self.elec_config.capacity_expansion:
            logger.info('cap expansion value = %.2f', pyo.value(instance.capacity_expansion_cost))
        if self.elec_config.spinning_reserve_required:
            logger.info('op res value = %.2f', pyo.value(instance.operating_reserves_cost))
        if self.elec_config.ramping_required:
            logger.info('ramp cost value = %.2f', pyo.value(instance.ramp_cost))
        if self.elec_config.regional_exchange:
            logger.info('trade cost value = %.2f', pyo.value(instance.trade_cost))
        logger.info('Obj complete')

        scenario_dir = (
            self.common_config.output_path / self.common_config.scenario_name / 'electricity'
        )
        export_variables_to_csv(instance, output_dir=scenario_dir / 'variables', core_only=True)


def calculate_tolerance(
    cap_growth: dict[tuple, float],
    new_cap_growth: dict[tuple, float],
    year_weights: dict[int, int],
    **kwargs,
) -> float:
    """Check the summation of growth between the old and new capacities.

    Returns
    -------
    float
        The OBJ value (total cost).
    """
    if not set(cap_growth.keys()) == set(new_cap_growth.keys()):
        raise ValueError('cap_growth and new_cap_growth must have the same keys')

    return sum(
        abs(cap_growth[tech, y] - new_cap_growth[tech, y]) * year_weights[y]
        for (tech, y) in cap_growth
    )


def run_elec_model(
    common_config: CommonConfig, elec_config: ElecConfig, solve: bool = True, **kwargs
) -> PowerModel:
    """Build the electricity model (and solve + postprocess if ``solve``), returning the model."""
    start_time = datetime.now().astimezone()
    timer = TicTocTimer(logger=logger)
    timer.tic('start')

    sequencer = ElectricitySequencer()
    instance = sequencer.build_model(common_config, elec_config, **kwargs)
    timer.toc('build model finished')

    # stop here if no solve requested...
    if not solve:
        return instance

    _, status = sequencer.solve_model()
    timer.toc('solve model finished')
    logger.info('Solve complete')

    if status is IterationStatus.ERROR:
        return instance

    sequencer.full_postprocess()

    end_time = datetime.now().astimezone()
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


def ng_mmbtu_per_bcf() -> float:
    """The gas model's MMBtu-per-Bcf heat content, read once from its ``ng_scalars.csv``.

    The first call reads the file under ``NG_INPUT_DIR`` and stores the value in the module-level
    ``_MMBTU_PER_BCF``; later calls return that without touching the file again.

    Returns
    -------
    float
        ``mmbtu_per_bcf`` as the natural gas model itself reads it.
    """
    global _MMBTU_PER_BCF  # a lazily filled module cache, by design
    if _MMBTU_PER_BCF is None:
        _MMBTU_PER_BCF = load_qp_scalars(NG_INPUT_DIR)['mmbtu_per_bcf']
        logger.info('Read mmbtu_per_bcf = %0.4g from %s', _MMBTU_PER_BCF, NG_INPUT_DIR)
    return _MMBTU_PER_BCF


def gas_demand_by_region(instance: PowerModel) -> pd.Series:
    """Annual natural gas burned by the gas-fired techs of a solved model, by region and year.

    Sums the solved ``generation_total`` of every tech in ``NG_HEAT_RATE_MMBTU_PER_MWH`` over
    the representative hours, weighting each hour by the days its representative day stands for
    (the same ``weight_day`` the objective applies), and converts with the tech's heat rate::

        Bcf = GWh x 1000 MWh/GWh x MMBtu/MWh / mmbtu_per_bcf

    with ``mmbtu_per_bcf`` taken from the gas model's ``ng_scalars.csv`` via
    :func:`ng_mmbtu_per_bcf`.

    Parameters
    ----------
    instance : PowerModel
        A solved model; ``generation_total`` must hold values.

    Returns
    -------
    pd.Series
        Demand in Bcf/yr named ``NG_ELEC_DEMAND_VALUE``, indexed by ``NG_ELEC_DEMAND_INDEX``
        (electricity region id, model year), sorted.  A region with no gas-fired generation
        appears with 0.0 as long as it holds a gas-fired tech.
    """
    mmbtu: dict[tuple[str, int], float] = defaultdict(float)
    # pyrefly sees the pyomo components declared on PowerModel as None; they are Var/Param
    # instances on a built model, and value() of a solved Var is a float
    for region, tech, step, year, hour in instance.generation_total:  # pyrefly: ignore[not-iterable]
        heat_rate = NG_HEAT_RATE_MMBTU_PER_MWH.get(tech)
        if heat_rate is None:
            continue
        gen_gwh = value(instance.generation_total[region, tech, step, year, hour])
        days = value(instance.weight_day[instance.map_hour_day[hour]])
        # pyrefly: ignore[unsupported-operation]
        # TODO:  review this x1000 multiplier after we get the generation units squared away!
        mmbtu[(region, int(year))] += gen_gwh * days * 1000.0 * heat_rate
    mmbtu_per_bcf = ng_mmbtu_per_bcf()
    series = pd.Series({k: v / mmbtu_per_bcf for k, v in mmbtu.items()}, name=NG_ELEC_DEMAND_VALUE)
    if series.empty:
        logger.warning(
            'No generation rows for gas-fired techs %s; gas demand is empty',
            list(NG_HEAT_RATE_MMBTU_PER_MWH),
        )
        series.index = pd.MultiIndex.from_tuples([], names=NG_ELEC_DEMAND_INDEX)
        return series
    series.index = series.index.set_names(NG_ELEC_DEMAND_INDEX)
    logger.debug('Polled gas demand for %d (region, year) pairs', len(series))
    return series.sort_index()


def init_old_cap(instance: PowerModel) -> dict[tuple, float]:
    """Initialize capacity growth for 0th iteration.

    Parameters
    ----------
    instance : PowerModel
        unsolved electricity model
    """
    initial_growth = {}
    # instance.cap_set = []
    # instance.old_cap_wt = {}

    # pyrefly: ignore[not-iterable]  - pyomo's IndexedComponent.__iter__ is untyped
    for _r, tech, _step, y in instance.cap_cost:
        if (tech, y) not in initial_growth:
            # each tech will increase cap by 1 GW per year. reasonable starting point.
            # TODO:  come back to this assumption after better understanding of process
            initial_growth[tech, y] = (y - instance.y0_learning) * 1
            # instance.old_cap_wt[(tech, y)] = instance.weight_year[y] * instance.old_cap[(tech, y)]
    return initial_growth


def set_new_cap(instance: PowerModel):
    """Currently no-op.

    This legacy approach added instance variables to the model.  See the design pattern in
    the control loop above.  All that should be needed is to calculate the growth where needed
    """
    raise NotImplementedError('see docstring')


def calculate_cap_growth(instance: PowerModel) -> dict[tuple, float]:
    """Calculate the current capacity of all buildable tech by year."""
    result = defaultdict(float)
    # pyrefly: ignore[not-iterable]  - pyomo's IndexedComponent.__iter__ is untyped
    for r, tech, step, y in instance.cap_cost:
        # pyrefly: ignore[no-matching-overload]  - pyomo's value() is typed as returning None too
        result[(tech, y)] += sum(
            value(instance.capacity_builds[r, tech, step, year])
            for year in instance.year
            if year < y
        )
    return result


def cost_learning_func(instance: PowerModel, tech, y, new_cap: float) -> float:
    """Function for updating learning costs by technology and year.

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
        (instance.supply_curve_learning[tech] + 0.0001 * (y - instance.y0_learning) + new_cap)
        / instance.supply_curve_learning[tech]
        # pyrefly: ignore[unsupported-operation]  - pyomo ParamData arithmetic is untyped
    ) ** (-1.0 * instance.learning_rate[tech])
    return cost


def update_expansion_cost(instance, new_cap: dict[tuple, float]):
    """Update capital cost based on new capacity learning."""
    new_multiplier = {}
    for key in new_cap:
        tech, y = key
        new_multiplier[tech, y] = cost_learning_func(instance, tech, y, new_cap[tech, y])

    # Assign new cost
    for r, tech, step, y in instance.cap_cost:
        new_cost = instance.cap_cost_initial[r, tech, step] * new_multiplier[tech, y]
        old_value = value(instance.cap_cost[r, tech, step, y])
        instance.cap_cost[r, tech, step, y] = new_cost
        logger.debug(
            'Reduced cap_cost[%s, %s, %s, %s] from %0.2f to %0.2f',
            r,
            tech,
            step,
            y,
            old_value,
            new_cost,
        )
