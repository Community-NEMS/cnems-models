"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/14/26
"""

import logging
from collections.abc import Sequence

from pyomo.common.numeric_types import value
from pyomo.opt import SolverFactory, check_optimal_termination

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage
from src.common.utilities import setup_logger
from src.models.natural_gas.data import apply_update_package, load_all
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
            built.  A package type with no registered handler raises.

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
        data = load_all(common_config=common_config, ng_config=model_config)
        for package in update_packages or []:
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

            appsi_gurobi, gurobi_direct, gurobi, highs, appsi_highs

        The ordering is load-bearing and is explained in full in the comments below.  In short:
        the three Gurobi entries lead purely for speed (in-memory, no LP-file round trip), while
        the last two are the correctness-critical pair.  ``appsi_highs`` calls
        ``generate_standard_repn(quadratic=False)`` internally and so raises ``DegreeError`` on
        this model's quadratic objective (still true in pyomo 6.10.1); ``highs`` is the
        newer interface (pyomo >= 6.10) that builds a Hessian and handles a convex QP properly.
        ``highs`` therefore must precede it, and ``solver_name='appsi_highs'`` will not work.

        Gurobi is additionally pinned to the barrier method with duals requested and
        ``BarConvTol`` at 1e-6; HiGHS detects the QP and picks an interior-point method itself.
        The solver actually chosen is logged at INFO.
        """
        solver_name = kwargs.pop('solver_name', None)
        logger.debug('Requested solver: %s', solver_name)
        if solver_name is None:
            # Note ordering:
            #   1. appsi_gurobi: Pyomo's APPSI interface to Gurobi (used by unified.py
            #      already; supports QP and warm starts).
            #   2. gurobi_direct: direct Pyomo→Gurobi interface, fall-through if
            #      APPSI is unavailable.
            # 3. highs: the standalone HiGHS interface, supports convex QP since
            #      HiGHS 1.5.  We use this NOT appsi_highs because Pyomo's APPSI
            #      HiGHS wrapper rejects degree-2 expressions (Pyomo bug, fix not
            #      backported as of v6.10).
            # The original list is all-unavailable
            # in the current `bsky` env (appsi_gurobi/gurobi_direct bindings absent; the ASL
            # 'highs' executable is not installed). Added the classic 'gurobi' interface first
            # (QP-capable and the only working Gurobi binding here) and 'appsi_highs' as a
            # Gurobipy 12.0.1 now installed; prefer in-memory
            # appsi_gurobi for the QP (no LP-file I/O, fast). Old gurobi-first order preserved:
            # candidates = ['gurobi', 'appsi_gurobi', 'gurobi_direct', 'highs', 'appsi_highs']
            # ORDERING MATTERS, do not reorder. The two Gurobi entries lead purely for
            # speed. The critical pair is the last two: 'highs' MUST precede 'appsi_highs'.
            #
            # 'appsi_highs' calls generate_standard_repn(quadratic=False) internally, so it raises
            # DegreeError on any quadratic objective, still true in pyomo 6.10.1. 'highs' is the
            # new-generation interface (pyomo >= 6.10) that builds a Hessian and handles a convex
            # QP properly. With this ordering a Gurobi-free environment lands on the interface
            # that works rather than the one that raises, which is why 'appsi_highs' is kept at
            # the end as a last resort rather than removed outright.
            #
            # Confirm which was chosen from the log line below, or from HiGHS's own output under
            # tee, which reports "1476 Hessian nonzeros" for the full model.
            candidates = ['appsi_gurobi', 'gurobi_direct', 'gurobi', 'highs', 'appsi_highs']
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
        # termination check below and then discarded rather than returned.
        results = opt.solve(self.model)

        if not check_optimal_termination(results):
            logger.error('C-NGMM: non-optimal solve! Results:\n%s', results)
            return ModelType.NATURAL_GAS, IterationStatus.ERROR

        logger.info('C-NGMM: solve complete, status %s', results.solver.termination_condition)

        # ── attach result tables to the model for reporting ──────────────────────
        # TODO:  Extraction below on hold till model running and then maybe refactor to not
        #        "staple on" instance variables and do it cleaner
        # m.results_production = postprocessor._extract_production(m)
        # m.results_flows = postprocessor._extract_flows(m)
        # m.results_prices = postprocessor._extract_prices(m)
        # m.results_storage = postprocessor._extract_storage(m)
        # m.results_balance = postprocessor._extract_balance(m)
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
        """Get the outbound update packages.  C-NGMM produces none yet."""
        return []

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
    logger.info('Solved with status: %s', status)
    obj_value = value(sequencer.model.total_cost)
    logger.info('Objective value: %0.2f', obj_value)
    sequencer.full_postprocess()
