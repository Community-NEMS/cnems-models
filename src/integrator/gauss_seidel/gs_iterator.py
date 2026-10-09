"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Gauss-Seidel iteration between the electricity and natural gas models.

Both models are built once and held in the control process.  Each iteration solves electricity on
the latest gas prices, then gas on that iteration's electricity burn, applying each model's
inbound packages to its built instance with ``update_model`` rather than rebuilding it.  Package
routing (``route_updates``) and the stopping test are shared with the Jacobi iterator, and the
rest of the bookkeeping is shared with it through ``src/integrator/bookeeping_utilities.py``.
"""

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd
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
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NG_PRICE_VALUE,
    NGElectricalDemandPackage,
    NGPricePackage,
    UpdatePackage,
    route_updates,
)
from src.integrator.bookeeping_utilities import (
    accept_packages,
    final_status,
    log_progress,
    show_iteration,
)
from src.integrator.convergence import ConvergenceTracker
from src.integrator.gauss_seidel.gs_config import DEFAULT_GS_CONFIG_PATH, GaussSeidelConfig
from src.integrator.iteration_monitor import IterationMonitor
from src.integrator.jacobi.jacobi_iterator import _CONFIG_SECTIONS
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.natural_gas.sequencer import NGSequencer

logger = logging.getLogger(__name__)

# the order the models solve in each iteration:  gas sees the burn electricity just produced
GS_ORDER: tuple[ModelType, ...] = (ModelType.ELECTRICITY, ModelType.NATURAL_GAS)


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
        ``burn_bcf``, the electricity burn summed over gas regions, and ``mean_price``, the gas
        price averaged over electricity regions without weights, each keyed by year.  A package
        type that was not sent is absent.
    """
    summary: dict[str, dict[int, float]] = {}
    for package in packages:
        if isinstance(package, NGElectricalDemandPackage):
            burn = package.elements[NG_ELEC_DEMAND_VALUE].groupby(level='year').sum()
            summary['burn_bcf'] = dict(
                zip(map(int, burn.index.to_list()), burn.to_list(), strict=True)
            )
        elif isinstance(package, NGPricePackage):
            price = package.elements[NG_PRICE_VALUE].groupby(level='year').mean()
            summary['mean_price'] = dict(
                zip(map(int, price.index.to_list()), price.to_list(), strict=True)
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
    if 'burn_bcf' in summary:
        burn = summary['burn_bcf']
        parts.append('burn ' + ', '.join(f'{v:,.1f} Bcf ({y})' for y, v in burn.items()))
    if 'mean_price' in summary:
        price = summary['mean_price']
        parts.append(
            'mean gas price ' + ', '.join(f'{v:.3f} $/MMBtu ({y})' for y, v in price.items())
        )
    return 'Exchanged:  ' + ('; '.join(parts) if parts else 'nothing')


def add_topup(
    packages: Sequence[UpdatePackage], topup: dict[tuple[str, int], float]
) -> list[UpdatePackage]:
    """Add the preflight's top-up to the burn in each gas demand package.

    Parameters
    ----------
    packages : Sequence[UpdatePackage]
        Packages bound for the gas model.
    topup : dict of (region, year) to float
        Bcf to add per gas cell, from ``PreflightReport.topup_by_cell``.

    Returns
    -------
    list[UpdatePackage]
        The packages, with each ``NGElectricalDemandPackage`` replaced by one whose covered cells
        carry the top-up.  Unchanged when ``topup`` is empty.
    """
    if not topup:
        return list(packages)
    out: list[UpdatePackage] = []
    for package in packages:
        if isinstance(package, NGElectricalDemandPackage):
            frame = package.elements.copy()
            extra = pd.Series(topup).rename_axis(NG_ELEC_DEMAND_INDEX)
            frame[NG_ELEC_DEMAND_VALUE] = frame[NG_ELEC_DEMAND_VALUE].add(
                extra.reindex(frame.index), fill_value=0.0
            )
            package = NGElectricalDemandPackage(elements=frame, source=package.source)
        out.append(package)
    return out


class GaussSeidelIterator(IterativeSequencer[GaussSeidelConfig]):
    """Gauss-Seidel iteration over the electricity and natural gas models.

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
        """Build the electricity and natural gas configs.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its ``models_to_run`` must select exactly electricity and
            natural gas (``['all']`` does).
        remainder : dict
            The config file's other sections:  ``elec_config`` and ``natural_gas``.

        Returns
        -------
        dict of ModelType to ModelConfig
            One config per model.

        Raises
        ------
        ValueError
            If the selected models are not exactly electricity and natural gas, or a section is
            missing.
        """
        selected = set(resolve_models_to_run(common_config.models_to_run))
        if selected != set(GS_ORDER):
            raise ValueError(
                f'The Gauss-Seidel iterator runs exactly {[m.value for m in GS_ORDER]}; '
                f'models_to_run selects {sorted(m.value for m in selected)}'
            )
        configs: dict[ModelType, ModelConfig] = {}
        for model in GS_ORDER:
            section, config_cls = _CONFIG_SECTIONS[model]
            if section not in remainder:
                raise ValueError(f'{model.value!r} needs a [{section}] section in the config')
            configs[model] = config_cls(**remainder[section])
        return configs

    def run(
        self, common_config: CommonConfig, remainder: dict[str, Any], **kwargs
    ) -> tuple[RunStatus, dict[int, list[IterationResult]]]:
        """Build both models once, then iterate until converged or iteration-capped.

        Each iteration solves electricity on the gas packages last accepted, then gas on the
        electricity packages just accepted, updating the held models in place.  A model whose
        status is outside ``ALLOW_OUTBOUND_UPDATES`` keeps its last accepted packages, and the
        run stops as the Jacobi iterator's does (:class:`ConvergenceTracker`).  Before the first
        solve, ``build_preflight`` checks coverage, and the gas cells the electricity model
        supplies are declared external.  At the end, result files are written for each model
        whose last solve did not fail.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its scenario output folder is claimed here.
        remainder : dict
            The config file's other sections:  ``elec_config`` and ``natural_gas``.
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

        sequencers: dict[ModelType, IntegratedModelSequencer[Any, Any, Any]] = {
            ModelType.ELECTRICITY: ElectricitySequencer(),
            ModelType.NATURAL_GAS: NGSequencer(),
        }
        logs = {m: common_config.output_folder / f'{m.value}.log' for m in GS_ORDER}
        build_seconds: dict[ModelType, float] = {}
        for model in GS_ORDER:
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

        # elec_model = sequencers[ModelType.ELECTRICITY].model
        # ng_model = sequencers[ModelType.NATURAL_GAS].model
        # report = build_preflight(
        #     elec_model, ng_model, allow_partial_coverage=self.config.allow_partial_coverage
        # )
        # for cell in report.ownership_cells:
        #     ng_model.declare_external(*cell)
        # topup = dict(report.topup_by_cell)

        tracker = ConvergenceTracker(self.config.epsilon, self.config.convergence_iterations)
        monitor = IterationMonitor(GS_ORDER, delta_mode=self.config.monitor_delta_mode)
        console = Console()
        accepted: dict[ModelType, list[UpdatePackage]] = {}
        all_results: dict[int, list[IterationResult]] = {}
        iteration = 1
        converged = False
        while not converged and iteration <= self.config.iteration_limit:
            results: list[IterationResult] = []
            for model in GS_ORDER:
                inbound = route_updates(accepted, GS_ORDER)[model]
                # if model is ModelType.NATURAL_GAS:
                #     inbound = add_topup(inbound, topup)
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
            converged = tracker.update(results)
            log_progress(
                tracker, iteration, self.config.epsilon, self.config.iteration_limit, logger
            )
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
