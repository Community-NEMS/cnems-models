"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Gauss-Seidel iteration over the electricity and natural gas models, and the C-HSM sample model.

The selected models are built once and held in the control process.  Each iteration runs C-HSM and
electricity on the latest gas prices, then gas on that iteration's capacity and burn, applying each
model's inbound packages with ``update_model``.  C-HSM rebuilds from its held data; the others
update their built instances.  Package routing (``route_updates``), the stopping test and the rest
of the bookkeeping (``src/integrator/bookeeping_utilities.py``) are shared with the Jacobi iterator.
"""

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from time import perf_counter
from typing import Any

from rich.console import Console

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model_sequencer import (
    IntegratedModelSequencer,
    IterationResult,
    IterationStatus,
)
from src.common.iterative_sequencer import IterativeSequencer, RunStatus
from src.common.log_setup import _scenario_log, setup_control_loop_logging
from src.common.models_modes import ModelType, resolve_models_to_run
from src.common.update_package import (
    HSM_CAPACITY_VALUE,
    NG_ELEC_DEMAND_VALUE,
    NG_HUB_PRICE_VALUE,
    NG_PRICE_VALUE,
    ExchangeSpec,
    HSMSupplyCapacityPackage,
    NGElectricalDemandPackage,
    NGHubPricePackage,
    NGPricePackage,
    UpdatePackage,
    route_updates,
)
from src.integrator.bookeeping_utilities import (
    accept_packages,
    final_status,
    log_exchange_progress,
    log_progress,
    show_iteration,
)
from src.integrator.config_sections import CONFIG_SECTIONS, check_model_configs
from src.integrator.convergence import ConvergenceTracker, ExchangeTracker
from src.integrator.gauss_seidel.gs_config import DEFAULT_GS_CONFIG_PATH, GaussSeidelConfig
from src.integrator.iteration_monitor import IterationMonitor
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.hsm.sequencer import HSMSequencer
from src.models.natural_gas.sequencer import NGSequencer

logger = logging.getLogger(__name__)

# the solve order in each iteration, over the models a run selects:  gas sees that iteration's
# capacity and burn
GS_ORDER: tuple[ModelType, ...] = (ModelType.HSM, ModelType.ELECTRICITY, ModelType.NATURAL_GAS)
# the model sets the iterator runs
GS_RUNS: tuple[frozenset[ModelType], ...] = (
    frozenset({ModelType.ELECTRICITY, ModelType.NATURAL_GAS}),
    frozenset({ModelType.HSM, ModelType.NATURAL_GAS}),
    frozenset({ModelType.HSM, ModelType.ELECTRICITY, ModelType.NATURAL_GAS}),
)
# each model's sequencer, built from the run's exchanges
SEQUENCERS: dict[ModelType, Callable[[ExchangeSpec], IntegratedModelSequencer[Any, Any, Any]]] = {
    ModelType.HSM: HSMSequencer,
    ModelType.ELECTRICITY: ElectricitySequencer,
    ModelType.NATURAL_GAS: NGSequencer,
}


def exchange_summary(packages: Iterable[UpdatePackage]) -> dict[str, dict[int, float]]:
    """Total burn and mean gas price by year, from the packages the models exchanged.

    The iteration monitor shows how many entries each package carries; this gives the values.

    Parameters
    ----------
    packages : iterable of UpdatePackage
        Packages sent in one iteration.

    Returns
    -------
    dict
        ``burn_tbtu``, the electricity burn summed over gas regions in TBtu; ``mean_price``, the
        gas price averaged over electricity regions without weights; ``capacity_bcf``, C-HSM's
        capacity summed over gas regions; and ``hub_price``, the Henry Hub region's gas price;
        each keyed by year.  A package type that was not sent is absent.
    """
    summary: dict[str, dict[int, float]] = {}
    for package in packages:
        if isinstance(package, NGElectricalDemandPackage):
            burn = package.elements[NG_ELEC_DEMAND_VALUE].groupby(level='year').sum() / 1e6
            summary['burn_tbtu'] = dict(
                zip(map(int, burn.index.to_list()), burn.to_list(), strict=True)
            )
        elif isinstance(package, NGPricePackage):
            price = package.elements[NG_PRICE_VALUE].groupby(level='year').mean()
            summary['mean_price'] = dict(
                zip(map(int, price.index.to_list()), price.to_list(), strict=True)
            )
        elif isinstance(package, HSMSupplyCapacityPackage):
            capacity = package.elements[HSM_CAPACITY_VALUE].groupby(level='year').sum()
            summary['capacity_bcf'] = dict(
                zip(map(int, capacity.index.to_list()), capacity.to_list(), strict=True)
            )
        elif isinstance(package, NGHubPricePackage):
            hub = package.elements[NG_HUB_PRICE_VALUE]
            summary['hub_price'] = dict(
                zip(map(int, hub.index.to_list()), hub.to_list(), strict=True)
            )
    return summary


def format_exchange(summary: dict[str, dict[int, float]]) -> str:
    """Render :func:`exchange_summary` as one line for the console and the log.

    Parameters
    ----------
    summary : dict
        From :func:`exchange_summary`.

    Returns
    -------
    str
        The burn and mean gas price by year, or a note that nothing was exchanged.
    """
    parts = []
    if 'burn_tbtu' in summary:
        burn = summary['burn_tbtu']
        parts.append('burn ' + ', '.join(f'{v:,.1f} TBtu ({y})' for y, v in burn.items()))
    if 'mean_price' in summary:
        price = summary['mean_price']
        parts.append(
            'mean gas price ' + ', '.join(f'{v:.3f} USD/MMBtu ({y})' for y, v in price.items())
        )
    if 'capacity_bcf' in summary:
        capacity = summary['capacity_bcf']
        parts.append(
            'C-HSM capacity ' + ', '.join(f'{v:,.0f} Bcf ({y})' for y, v in capacity.items())
        )
    if 'hub_price' in summary:
        hub = summary['hub_price']
        parts.append('hub price ' + ', '.join(f'{v:.3f} USD/MMBtu ({y})' for y, v in hub.items()))
    return 'Exchanged:  ' + ('; '.join(parts) if parts else 'nothing')


class GaussSeidelIterator(IterativeSequencer[GaussSeidelConfig]):
    """Gauss-Seidel iteration over one of the model sets in ``GS_RUNS``.

    Settings come from ``gs_config.toml`` beside this module.  The stopping rule is the Jacobi
    iterator's, so the two can be compared at the same tolerance.
    """

    @property
    def label(self) -> str:
        """Display name."""
        return 'Gauss-Seidel'

    @property
    def _config_class(self) -> type[GaussSeidelConfig]:
        """Validates ``gs_config.toml``."""
        return GaussSeidelConfig

    @property
    def _default_config_path(self) -> Path:
        """``gs_config.toml`` beside this module."""
        return DEFAULT_GS_CONFIG_PATH

    def _process_configs(
        self, common_config: CommonConfig, remainder: dict[str, Any]
    ) -> dict[ModelType, ModelConfig]:
        """Build the config of each selected model, in ``GS_ORDER``.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its ``models_to_run`` must select one of ``GS_RUNS``
            (``['all']`` selects electricity and natural gas; ``['all', 'hsm']`` adds C-HSM).
        remainder : dict
            The config file's other sections:  one per selected model (``hsm``,
            ``elec_config``, ``natural_gas``).

        Returns
        -------
        dict of ModelType to ModelConfig
            One config per selected model, in ``GS_ORDER``.

        Raises
        ------
        ValueError
            If the selected models are not one of ``GS_RUNS``, a section is missing, or the
            configs cannot run together (``check_model_configs``).
        """
        selected = frozenset(resolve_models_to_run(common_config.models_to_run))
        if selected not in GS_RUNS:
            raise ValueError(
                'The Gauss-Seidel iterator runs one of '
                f'{[sorted(m.value for m in run) for run in GS_RUNS]}; '
                f'models_to_run selects {sorted(m.value for m in selected)}'
            )
        configs: dict[ModelType, ModelConfig] = {}
        for model in GS_ORDER:
            if model not in selected:
                continue
            section, config_cls = CONFIG_SECTIONS[model]
            if section not in remainder:
                raise ValueError(f'{model.value!r} needs a [{section}] section in the config')
            configs[model] = config_cls(**remainder[section])
        check_model_configs(configs)
        return configs

    def run(
        self, common_config: CommonConfig, remainder: dict[str, Any], **kwargs
    ) -> tuple[RunStatus, dict[int, list[IterationResult]]]:
        """Build the selected models once, then iterate until converged or iteration-capped.

        Each iteration runs the models in ``GS_ORDER``, each on the packages last accepted from
        the others, so a later model sees what an earlier one sent in the same iteration.  A
        model whose status is outside ``ALLOW_OUTBOUND_UPDATES`` keeps its last accepted
        packages, and the run stops as the Jacobi iterator's does (:class:`ConvergenceTracker`
        and :class:`ExchangeTracker`).  The exchanges and region crosswalks are built once,
        before the models (see :meth:`ExchangeSpec.build`); the writers and readers check the
        crosswalked data against each model's regions.  At the end, result files are written
        for each model whose last solve did not fail.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its scenario output folder is claimed here.
        remainder : dict
            The config file's other sections:  one per selected model.
        **kwargs
            Unused; accepted to match :meth:`IterativeSequencer.run`.

        Returns
        -------
        tuple of (RunStatus, dict of int to list of IterationResult)
            ``CONVERGED`` or ``ITERATION_LIMIT``, and each iteration's results in solve order.
            Each result's ``timings`` holds ``update``, ``solve`` and ``collect`` seconds, plus
            ``build`` in iteration 1.
        """
        common_config.make_scenario_dir()
        setup_control_loop_logging(common_config.output_folder / 'MAIN.log')
        logger.info('Starting %s run for scenario "%s"', self.label, common_config.scenario_name)
        configs = self._process_configs(common_config, remainder)

        order = tuple(configs)
        exchange = self._build_exchange(order)
        sequencers: dict[ModelType, IntegratedModelSequencer[Any, Any, Any]] = {
            model: SEQUENCERS[model](exchange) for model in order
        }
        logs = {m: common_config.output_folder / f'{m.value}.log' for m in order}
        build_seconds: dict[ModelType, float] = {}
        for model in order:
            start = perf_counter()
            self._in_model_log(
                model,
                logs[model],
                'build',
                sequencers[model].build_model,
                common_config,
                configs[model],
            )
            build_seconds[model] = perf_counter() - start

        tracker = ConvergenceTracker(self.config.epsilon, self.config.convergence_iterations)
        exchange_tracker = ExchangeTracker(
            self.config.exchange_epsilon,
            self.config.convergence_iterations,
            set(self.config.exchange_stop_packages) & {t.__name__ for t in exchange.expected},
        )
        monitor = IterationMonitor(order, delta_mode=self.config.monitor_delta_mode)
        console = Console()
        accepted: dict[ModelType, list[UpdatePackage]] = {}
        all_results: dict[int, list[IterationResult]] = {}
        iteration = 1
        converged = False
        while not converged and iteration <= self.config.iteration_limit:
            results: list[IterationResult] = []
            for model in order:
                # TODO:  check the packages produced against exchange.expected, once it is decided
                #        how a missing or unexpected exchange feeds routing and the run status
                inbound = route_updates(accepted, order)[model]
                start = perf_counter()
                self._in_model_log(
                    model, logs[model], 'update', sequencers[model].update_model, inbound
                )
                update_seconds = perf_counter() - start
                result = self._in_model_log(
                    model, logs[model], 'solve', sequencers[model].solve_iteration
                )
                result.timings['update'] = update_seconds
                if iteration == 1:
                    result.timings['build'] = build_seconds[model]
                results.append(result)
                # logged and accepted at once, so the next model in this iteration sees it, and a
                # later failure in the iteration still leaves this result in the log
                logger.info('\n%s', result.pprint(indent=2))
                accept_packages(result, accepted, iteration, logger)
            all_results[iteration] = results
            show_iteration(iteration, results, monitor, console, logger, log_results=False)
            # the monitor counts entries; this line gives the values behind them
            exchanged = format_exchange(
                exchange_summary(pkg for result in results for pkg in result.update_packages)
            )
            logger.info(exchanged)
            console.print(f'      {exchanged}', highlight=False)
            # update both trackers every iteration; the run stops when both pass
            objectives_settled = tracker.update(results)
            exchanges_settled = exchange_tracker.update(results)
            converged = objectives_settled and exchanges_settled
            log_progress(
                tracker, iteration, self.config.epsilon, self.config.iteration_limit, logger
            )
            log_exchange_progress(exchange_tracker, iteration, logger)
            iteration += 1

        status = final_status(
            converged,
            iteration - 1,
            self.config.iteration_limit,
            all_results.get(iteration - 1, []),
            logger,
        )

        # the models are still held, so their final solutions can be written
        for result in all_results.get(iteration - 1, []):
            if result.status is IterationStatus.ERROR:
                logger.error('%s: last solve failed, no results written', result.model_type.value)
                continue
            self._in_model_log(
                result.model_type,
                logs[result.model_type],
                'postprocess',
                sequencers[result.model_type].full_postprocess,
            )
        return status, all_results

    @staticmethod
    def _in_model_log(model: ModelType, log_file: Path, step: str, call: Any, *args: Any) -> Any:
        """Run one model step with its records in the model's own log, as a Jacobi worker does.

        An exception is logged to the model's log and to ``MAIN.log``, then re-raised, so a
        failed run leaves its traceback in both, as in the Jacobi iterator.

        Parameters
        ----------
        model : ModelType
            The model the step belongs to.
        log_file : Path
            The model's log, e.g. ``electricity.log``.
        step : str
            The step's name, for the log.
        call : callable
            The step.
        *args
            Passed to ``call``.

        Returns
        -------
        Any
            What ``call`` returns.
        """
        try:
            with _scenario_log(log_file):
                try:
                    return call(*args)
                except Exception:
                    logger.exception('%s %s raised; the run will stop', model.value, step)
                    raise
        except Exception:
            logger.exception('%s %s raised; stopping the run', model.value, step)
            raise
