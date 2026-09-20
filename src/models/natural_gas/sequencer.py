"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/14/26
"""

import logging
import sys
from collections.abc import Sequence

import pandas as pd
from pyomo.common.numeric_types import value
from pyomo.opt import SolverFactory, check_optimal_termination

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import NG_PRICE_INDEX, NG_PRICE_VALUE, NGPricePackage, UpdatePackage
from src.common.utilities import setup_logger
from src.integrator.region_crosswalk import QuantityKind, crosswalk_values
from src.models.natural_gas.data import apply_update_package, load_all, superseded_sectors
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import NGModel
from src.models.natural_gas.postprocessor import report

logger = logging.getLogger(__name__)


class NGSequencer(IntegratedModelSequencer[NGModel, NGConfig]):
    """Sequencer for Natural Gas models."""

    def __init__(self):
        """Initialize the sequencer."""
        self._model = None
        self._ng_config: NGConfig | None = None
        self._common_config: CommonConfig | None = None
        self._last_status: IterationStatus | None = None

    @property
    def model(self) -> NGModel:
        """The built model.  Raises ``RuntimeError`` if ``build_model()`` has not run yet."""
        if self._model is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return self._model

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
        self,
        common_config: CommonConfig,
        model_config: NGConfig,
        update_packages: Sequence[UpdatePackage] | None = None,
        **kwargs,
    ) -> NGModel:
        """Build the Natural Gas Market Model.

        Parameters
        ----------
        common_config : CommonConfig
            The ``[common]`` settings.  Only ``mode`` and ``summary_years`` are read.
        model_config : NGConfig
            The ``[natural_gas]`` settings.
        update_packages : Sequence[UpdatePackage], optional
            Inbound data updates, applied to the loaded data (via
            :func:`src.models.natural_gas.data.apply_update_package`) before the model is
            built.  A package type with no registered handler raises.  A package that
            supersedes a demand sector (``data.SECTOR_SUPERSEDED_BY``) also gates off that
            sector's growth projection in ``load_all``.

        Returns
        -------
        NGModel
            The built (unsolved) model, also retained on the sequencer.

        Raises
        ------
        NotImplementedError
            If an update package has no registered handler in ``data.py``.
        """
        self._common_config = common_config
        self._ng_config = model_config
        update_packages = list(update_packages or [])
        data = load_all(
            common_config=common_config,
            ng_config=model_config,
            superseded=superseded_sectors(update_packages),
        )
        for package in update_packages:
            logger.info('Applying update package: %s', type(package).__name__)
            apply_update_package(package, data)
        self._model = NGModel(model_data=data, common_config=common_config, ng_config=model_config)
        return self._model

    def update_model(self, **kwargs) -> NGModel:
        """Not implemented; C-NGMM is not yet wired into the iterative integrator."""
        raise NotImplementedError

    def solve_model(self, **kwargs) -> tuple[ModelType, IterationStatus]:
        """Solve the built model, a convex QP, and report how the solve terminated.

        Solves ``self.model``, so ``build_model()`` must have been called first.  The NGMM-aligned
        QP rewrite needs a convex-QP-capable solver, which rules out the ``select_solver()`` default
        in src/integrator/utilities.py that the electricity path uses; this method probes for one
        instead.

        Parameters
        ----------
        **kwargs
            ``solver_name`` : str, optional
                Force this specific Pyomo ``SolverFactory`` name instead of probing.  No fallback
                is attempted, so an unavailable name raises rather than quietly landing on
                something else -- which is the point when a run has to be reproducible, or when
                comparing solvers.  Any other keyword is ignored.

        Returns
        -------
        tuple[ModelType, IterationStatus]
            the model type (for accounting) and the status of solve on this iteration

        Raises
        ------
        RuntimeError
            If no candidate solver is available.

        Notes
        -----
        Left to itself, the method takes the first available of, in order::

            appsi_gurobi, gurobi_direct, gurobi, highs

        The three Gurobi entries lead purely for speed (in-memory, no LP-file round trip);
        ``highs``, the current ``pyomo.contrib.solver`` interface, builds a Hessian and handles
        the convex QP properly, so a Gurobi-free environment still solves.  ``appsi_highs`` is
        not a candidate and ``solver_name='appsi_highs'`` will not work:  it calls
        ``generate_standard_repn(quadratic=False)`` internally and so raises ``DegreeError`` on
        this model's quadratic objective (still true in pyomo 6.10.1).

        Gurobi is additionally pinned to the barrier method with duals requested and
        ``BarConvTol`` at 1e-6; HiGHS detects the QP and picks an interior-point method itself.
        The solver actually chosen is logged at INFO.
        """
        solver_name = kwargs.pop('solver_name', None)
        logger.debug('Requested solver: %s', solver_name)
        if solver_name is None:
            # Ordering:
            #   1-3. the Gurobi bindings, which lead purely for speed -- in-memory
            #        appsi_gurobi first (no LP-file I/O), then gurobi_direct, then the classic
            #        'gurobi' shell interface. All three handle a QP; gurobipy 12.0.1 is
            #        installed here, earlier envs had none of them.
            #   4.   'highs': the current pyomo.contrib.solver interface (also what
            #        select_solver() hands the electricity path), which builds a Hessian and
            #        handles a convex QP properly, so a Gurobi-free env still solves.
            #
            # 'appsi_highs' is deliberately NOT a candidate: it calls
            # generate_standard_repn(quadratic=False) internally and so raises DegreeError on any
            # quadratic objective, still true in pyomo 6.10.1.
            #
            # Confirm which was chosen from the log line below, or from HiGHS's own output under
            # tee, which reports "1476 Hessian nonzeros" for the full model.
            candidates = ['appsi_gurobi', 'gurobi_direct', 'gurobi', 'highs']
        else:
            candidates = [solver_name]

        opt = None
        chosen = None
        for cand in candidates:
            try:
                trial = SolverFactory(cand)
                if trial.available(exception_flag=False):
                    opt = trial
                    chosen = cand
                    logger.info('Selected %s solver', cand)
                    break
            except Exception as exc:  # noqa: BLE001 - probing, any failure means 'try the next'
                logger.debug('C-NGMM: solver %s unavailable (%s)', cand, exc)

        if opt is None:
            raise RuntimeError(f'C-NGMM: none of the candidate solvers are available: {candidates}')

        # Tighten solver options for the convex QP rewrite, Gurobi's barrier method
        # is the standard QP path; HiGHS auto-detects QP and uses an interior-point.
        # Apply the Gurobi QP options for the classic
        # 'gurobi' interface too (the available one here), not just appsi_gurobi.
        # Set QP options via the interface-appropriate API
        # (APPSI uses .gurobi_options; classic uses .options). Barrier is the QP path;
        # duals requested.

        elif chosen in {'gurobi', 'gurobi_direct', 'appsi_gurobi'}:
            opt.options['Method'] = 2  # barrier (default for QP, explicit for safety)
            opt.options['QCPDual'] = 1  # request meaningful duals on the QCP
            opt.options['BarConvTol'] = 1e-6

        logger.info('C-NGMM: solving with %s (QP) …', chosen)
        # No tee= here, so the solver's own output is not shown, and `results` is used for the
        # termination check below and then discarded rather than returned.  load_solutions=False
        # keeps that check reachable:  'highs' raises from inside solve() when asked to load a
        # solution that an infeasible run never produced.  Variable values and duals land on the
        # model at load_from() instead.
        results = opt.solve(self.model, load_solutions=False)

        if not check_optimal_termination(results):
            logger.error('C-NGMM: non-optimal solve! Results:\n%s', results)
            self._last_status = IterationStatus.ERROR
            return ModelType.NATURAL_GAS, IterationStatus.ERROR

        self.model.solutions.load_from(results)
        logger.info('C-NGMM: solve complete, status %s', results.solver.termination_condition)

        # ── attach result tables to the model for reporting ──────────────────────
        # TODO:  Extraction below on hold till model running and then maybe refactor to not
        #        "staple on" instance variables and do it cleaner
        # m.results_production = postprocessor._extract_production(m)
        # m.results_flows = postprocessor._extract_flows(m)
        # m.results_prices = postprocessor._extract_prices(m)
        # m.results_storage = postprocessor._extract_storage(m)
        # m.results_balance = postprocessor._extract_balance(m)
        self._last_status = IterationStatus.USABLE
        return ModelType.NATURAL_GAS, IterationStatus.USABLE

    def full_postprocess(self, **kwargs):
        """Write the result CSVs for the solved model.

        Extracts production, pipeline flows, prices, storage and the regional balance, and
        writes them to ``<output_path>/<scenario_name>/natural_gas/``. ``report`` derives the
        tables itself, so this does not depend on the extraction calls commented out in
        ``solve_model``.
        """
        scenario_dir = (
            self.common_config.output_path / self.common_config.scenario_name / 'natural_gas'
        )
        report(m=self.model, output_dir=scenario_dir)

    def iteration_postprocess(self, **kwargs):
        """Not implemented; C-NGMM is not yet wired into the iterative integrator."""

    def get_outbound_updates(self) -> list[UpdatePackage]:
        """Package the solved gas prices for the electricity model.

        The regional prices from :meth:`NGModel.poll_gas_price` (duals of ``demand_balance``,
        $/MMBtu) are averaged into electricity regions with the population-weighted crosswalk and
        sent as one :class:`NGPricePackage`.

        Returns
        -------
        list[UpdatePackage]
            A single ``NGPricePackage``, or nothing if the last solve was not usable -- a failed
            solve leaves no duals to read.
        """
        if self._last_status is not IterationStatus.USABLE:
            logger.warning(
                'C-NGMM: no usable solve (status %s); sending no updates', self._last_status
            )
            return []
        ng_prices = self.model.poll_gas_price()
        series = pd.Series(
            {(gi.region, gi.year): price for gi, price in ng_prices.items()}, name=NG_PRICE_VALUE
        )
        series.index = series.index.set_names(NG_PRICE_INDEX)
        elec_prices = crosswalk_values(
            series, ModelType.NATURAL_GAS, ModelType.ELECTRICITY, QuantityKind.INTENSIVE
        )
        by_year = elec_prices.groupby(level='year').agg(['min', 'max'])
        for year, row in by_year.iterrows():
            logger.info(
                'C-NGMM: %d gas price to electricity regions:  %0.2f to %0.2f $/MMBtu',
                year,
                row['min'],
                row['max'],
            )
        package = NGPricePackage(elements=elec_prices.to_frame(), source=ModelType.NATURAL_GAS)
        return [package]

    def get_objective_value(self) -> float | None:
        """Get the solved objective value (``total_cost``, in dollars)."""
        return value(self.model.total_cost)


if __name__ == '__main__':
    logger.info('Trial run from sequencer')
    config_path = PROJECT_ROOT / 'run_configs/basic_ng_config.toml'
    common_config, remainder = parse_config_file(config_path)
    setup_logger(common_config)
    ng_config = NGConfig(**remainder.pop('natural_gas'))
    sequencer = NGSequencer()
    sequencer.build_model(common_config, ng_config)
    _, status = sequencer.solve_model()

    if status is IterationStatus.ERROR:
        logger.error('C-NGMM: solve failed with status %s, no results written', status)
        sys.exit(1)

    logger.info('Solved with status: %s', status)
    obj_value = sequencer.get_objective_value()
    logger.info('Objective value: %0.2f', obj_value)
    sequencer.full_postprocess()
