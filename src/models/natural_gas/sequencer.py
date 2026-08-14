"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/14/26
"""

import logging

from pyomo.common.numeric_types import value
from pyomo.opt import SolverFactory, check_optimal_termination

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IntegratedModelSequencer, IterationStatus
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.ng_model import NGModel

logger = logging.getLogger(__name__)


class NGSequencer(IntegratedModelSequencer):
    """Sequencer for Natural Gas models."""

    def __init__(self):
        """Initialize the sequencer."""
        self._model = None
        self._ng_config: NGConfig | None = None
        self._common_config: CommonConfig | None = None

    @property
    def model(self) -> NGModel:
        if self._model is None:
            raise RuntimeError('Model has not been built yet; call build_model() first.')
        return self._model

    def build_model(self, common_config: CommonConfig, model_config: NGConfig, **kwargs) -> NGModel:
        """Build the Natural Gas Market Model.

        Parameters
        ----------
        common_config : CommonConfig
            The ``[common]`` settings.  Only ``mode`` and ``summary_years`` are read.
        model_config : NGConfig
            The ``[natural_gas]`` settings.

        Returns
        -------
        NGModel
            The built (unsolved) model, also retained on the sequencer.
        """
        self._common_config = common_config
        self._ng_config = model_config
        self._model = NGModel(common_config, model_config)
        return self._model

    def update_model(self, **kwargs) -> NGModel:
        pass

    def solve_model(self, **kwargs) -> IterationStatus:
        """Solve the Natural Gas Market Model.

        Switched to a Gurobi-first / HiGHS-fallback
        policy because the NGMM-aligned QP rewrite needs a convex-QP-capable solver,
        and Gurobi is already the standalone-via-meta default (see select_solver()
        in src/integrator/utilities.py).  HiGHS 1.5+ also handles convex QPs.

        Parameters
        ----------
        m : NGModel
            The instantiated (not yet solved) model.
        solver_name : str | None
            If supplied, force this specific Pyomo SolverFactory name.  If None
            (the default), tries 'appsi_gurobi' first and falls back to
            'appsi_highs' if Gurobi is unavailable.
        """
        solver_name = kwargs.pop('solver_name', None)
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
                    break
            except Exception as exc:
                logger.debug('C-NGMM: solver %s unavailable (%s)', cand, exc)

        if opt is None:
            raise RuntimeError(f'C-NGMM: none of the candidate solvers are available: {candidates}')

        # Tighten solver options for the convex QP rewrite, Gurobi's barrier method
        # is the standard QP path; HiGHS auto-detects QP and uses an interior-point.
        # Apply the Gurobi QP options for the classic
        # 'gurobi' interface too (the available one here), not just appsi_gurobi.
        # Set QP options via the interface-appropriate API
        # (APPSI uses .gurobi_options; classic uses .options). Barrier is the QP path; duals requested.
        if chosen == 'appsi_gurobi':
            opt.gurobi_options['Method'] = 2
            opt.gurobi_options['QCPDual'] = 1
            opt.gurobi_options['BarConvTol'] = 1e-6
        elif chosen in ('gurobi', 'gurobi_direct'):
            opt.options['Method'] = 2  # barrier (default for QP, explicit for safety)
            opt.options['QCPDual'] = 1  # request meaningful duals on the QCP
            opt.options['BarConvTol'] = 1e-6

        logger.info('C-NGMM: solving with %s (QP) …', chosen)
        # No tee= here, so the solver's own output is not shown, and `results` is used for the
        # termination check below and then discarded rather than returned.
        results = opt.solve(self.model)

        if not check_optimal_termination(results):
            logger.error('C-NGMM: non-optimal solve! Results:\n%s', results)
            return IterationStatus.ERROR
            # raise RuntimeError('NGModel solve did not reach an optimal solution.')

        logger.info('C-NGMM: solve complete, status %s', results.solver.termination_condition)

        # ── attach result tables to the model for reporting ──────────────────────
        # TODO:  Extraction below on hold till model running and then maybe refactor to not
        #        "staple on" instance variables and do it cleaner
        # m.results_production = _extract_production(m)
        # m.results_flows = _extract_flows(m)
        # m.results_prices = _extract_prices(m)
        # m.results_storage = _extract_storage(m)
        # m.results_balance = _extract_balance(m)
        return IterationStatus.USABLE

    def full_postprocess(self, **kwargs):
        pass

    def iteration_postprocess(self, **kwargs):
        pass


if __name__ == '__main__':
    logger.info('Trial run from sequencer')
    config_path = PROJECT_ROOT / 'run_configs/basic_ng_config.toml'
    common_config, remainder = parse_config_file(config_path)
    ng_config = NGConfig(**remainder.pop('natural_gas'))
    sequencer = NGSequencer()
    sequencer.build_model(common_config, ng_config)
    sequencer.solve_model()
    obj_value = value(sequencer.model.total_cost)
    logger.info('Objective value: %0.2f', obj_value)
    print(f'Objective value: {obj_value}')
