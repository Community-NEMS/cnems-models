"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

Jacobi iteration over the models selected in the run config, run in parallel

"""

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from rich.console import Console

from src.common.common_config import CommonConfig, ModelConfig
from src.common.integrated_model_sequencer import (
    ALLOW_OUTBOUND_UPDATES,
    ALLOW_TERMINATION,
    IterationResult,
)
from src.common.iterative_sequencer import IterativeSequencer
from src.common.log_setup import _scenario_log, setup_control_loop_logging
from src.common.models_modes import ModelType, resolve_models_to_run
from src.common.update_package import UpdatePackage
from src.integrator.iteration_monitor import IterationMonitor
from src.integrator.jacobi.convergence import ConvergenceTracker
from src.integrator.jacobi.jacobi_config import DEFAULT_JACOBI_CONFIG_PATH, JacobiConfig
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.magic.magic_model import MagicConfig, MagicSequencer
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.sequencer import NGSequencer

logger = logging.getLogger(__name__)

# the order the models of a run are circuited in, which fixes the order packages are routed (and
# so applied) in and keeps results deterministic:  every model, alphabetical by enum key.  A run
# circuits only its selected models, in this order; JacobiIterator.run can override it
DEFAULT_CIRCUIT: tuple[ModelType, ...] = tuple(
    sorted((model for model in ModelType if model is not ModelType.ALL), key=lambda m: m.name)
)

# each model's config section in the run config, and the config class it builds.  MAGIC may run
# without a section; the others need theirs
_CONFIG_SECTIONS: dict[ModelType, tuple[str, type[ModelConfig]]] = {
    ModelType.ELECTRICITY: ('elec_config', ElecConfig),
    ModelType.NATURAL_GAS: ('natural_gas', NGConfig),
    ModelType.MAGIC: ('magic_config', MagicConfig),
}
_OPTIONAL_SECTIONS: frozenset[ModelType] = frozenset({ModelType.MAGIC})


@dataclass
class IterationCall:
    """One model's worth of work for a single iteration, sent to a pool worker.

    Must stay picklable -- workers are spawned, so every field crosses a process boundary.

    Attributes
    ----------
    model_type : ModelType
        Selects which sequencer the worker builds.
    common_config : CommonConfig
        Common run configuration.
    model_config : ModelConfig
        The model-specific config, matching ``model``.
    kwargs : dict
        Extra arguments forwarded to the sequencer's ``full_run``.
    """

    model_type: ModelType
    common_config: CommonConfig
    model_config: ModelConfig
    kwargs: dict


def route_updates(
    packages: Iterable[UpdatePackage], circuit: Collection[ModelType]
) -> dict[ModelType, list[UpdatePackage]]:
    """Bin update packages by the models that should receive them.

    A package may name any number of receivers; :attr:`ModelType.ALL` means every model in the
    circuit.  Receivers outside the circuit are dropped with a warning -- nothing is running to
    consume them.

    Parameters
    ----------
    packages : iterable of UpdatePackage
        The packages emitted by this iteration's models.
    circuit : collection of ModelType
        The models participating in the run.

    Returns
    -------
    dict of ModelType to list of UpdatePackage
        One entry per circuit member, in circuit order; empty lists for models with no mail.
        :attr:`ModelType.ALL` is never a key -- it is an indicator, not a destination.
    """
    routed: dict[ModelType, list[UpdatePackage]] = {model: [] for model in circuit}
    for package in packages:
        receivers = set(package.receivers)
        to_all = ModelType.ALL in receivers
        # iterate the circuit (not the receivers) to drop off-circuit destinations and to
        # dedupe a package that names both ALL and a specific model
        for model in circuit:
            if to_all or model in receivers:
                routed[model].append(package)
        unreachable = receivers - {ModelType.ALL} - set(circuit)
        if unreachable:
            logger.warning(
                'Dropping %s addressed to %s; not in the circuit %s',
                type(package).__name__,
                sorted(m.value for m in unreachable),
                [m.value for m in circuit],
            )
    return routed


def driver(iter_call: IterationCall) -> IterationResult:
    """Run one model end to end in a pool worker.

    Parameters
    ----------
    iter_call : IterationCall
        The model to run, its configs, and any per-iteration kwargs.

    Returns
    -------
    IterationResult
        The model's solve status, objective value, and any packages it wants sent onward.

    Raises
    ------
    NotImplementedError
        If ``iter_call.model`` has no sequencer wired up here.
    TypeError
        If the call's config does not match its model.
    """
    # the scenario log is attached only for this task, so a reused worker never inherits it
    log_file = iter_call.common_config.output_folder / f'{iter_call.model_type.value}.log'
    with _scenario_log(log_file):
        # IterationCall carries the config as the ModelConfig base, so each arm has to confirm
        # it got the config its sequencer expects -- the TypeError the docstring promises.
        match iter_call.model_type:
            case ModelType.ELECTRICITY:
                if not isinstance(iter_call.model_config, ElecConfig):
                    raise TypeError(
                        f'ModelType.ELECTRICITY needs an ElecConfig, '
                        f'got {type(iter_call.model_config).__name__}'
                    )
                return ElectricitySequencer().full_run(
                    iter_call.common_config, iter_call.model_config, **iter_call.kwargs
                )
            case ModelType.NATURAL_GAS:
                if not isinstance(iter_call.model_config, NGConfig):
                    raise TypeError(
                        f'ModelType.NATURAL_GAS needs an NGConfig, '
                        f'got {type(iter_call.model_config).__name__}'
                    )
                return NGSequencer().full_run(
                    iter_call.common_config, iter_call.model_config, **iter_call.kwargs
                )
            case ModelType.MAGIC:
                if not isinstance(iter_call.model_config, MagicConfig):
                    raise TypeError(
                        f'ModelType.MAGIC needs a MagicConfig, '
                        f'got {type(iter_call.model_config).__name__}'
                    )
                return MagicSequencer().full_run(
                    iter_call.common_config, iter_call.model_config, **iter_call.kwargs
                )
            case _:
                raise NotImplementedError()


class JacobiIterator(IterativeSequencer[JacobiConfig]):
    """Jacobi iteration over the models selected by ``models_to_run``.

    Every model solves each iteration, in parallel, on the packages the others sent at the end of
    the previous iteration.  Settings come from ``jacobi_config.toml`` beside this module.
    """

    @property
    def label(self) -> str:
        """Display name."""
        return 'Jacobi'

    @property
    def _config_class(self) -> type[JacobiConfig]:
        """Validates ``jacobi_config.toml``."""
        return JacobiConfig

    @property
    def _default_config_path(self) -> Path:
        """``jacobi_config.toml`` beside this module."""
        return DEFAULT_JACOBI_CONFIG_PATH

    def _process_configs(
        self, common_config: CommonConfig, remainder: dict[str, Any]
    ) -> dict[ModelType, ModelConfig]:
        """Build the config of each model selected by ``common_config.models_to_run``.

        ``ModelType.ALL`` expands as in :func:`resolve_models_to_run`.  Warns when a region filter
        on the electricity or natural gas model leaves the other, also selected, partly uncovered.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its ``models_to_run`` selects the models.
        remainder : dict
            The config file's other sections:  ``elec_config``, ``natural_gas``, and optionally
            ``magic_config``.

        Returns
        -------
        dict of ModelType to ModelConfig
            One config per selected model, in ``models_to_run`` order.

        Raises
        ------
        ValueError
            If a selected model has no config section (other than MAGIC's optional one) or no
            Jacobi support.
        """
        configs: dict[ModelType, ModelConfig] = {}
        for model in resolve_models_to_run(common_config.models_to_run):
            if model not in _CONFIG_SECTIONS:
                raise ValueError(f'The Jacobi iterator cannot run {model.value!r}')
            section, config_cls = _CONFIG_SECTIONS[model]
            if section not in remainder and model not in _OPTIONAL_SECTIONS:
                raise ValueError(
                    f'{model.value!r} is in models_to_run but the config has no [{section}] section'
                )
            configs[model] = config_cls(**remainder.get(section, {}))

        elec_cfg = configs.get(ModelType.ELECTRICITY)
        ng_cfg = configs.get(ModelType.NATURAL_GAS)
        if isinstance(elec_cfg, ElecConfig) and elec_cfg.region_filter and ng_cfg is not None:
            logger.warning(
                'The electricity model is filtered to regions %s.  Packages it sends cover only '
                'the gas regions those touch, and the natural gas model holds the rest at '
                'base-year values, so excluding regions may lead to odd results in the '
                'integrated run',
                elec_cfg.region_filter,
            )
        if isinstance(ng_cfg, NGConfig) and ng_cfg.region_filter and elec_cfg is not None:
            logger.warning(
                'The natural gas model is filtered to regions %s.  Prices it sends cover only '
                'the electricity regions those touch (averaged over partial coverage), and '
                'demand it receives for other regions is ignored, so excluding regions may lead '
                'to odd results in the integrated run',
                ng_cfg.region_filter,
            )
        return configs

    def run(
        self,
        common_config: CommonConfig,
        remainder: dict[str, Any],
        circuit: Sequence[ModelType] = DEFAULT_CIRCUIT,
        **kwargs,
    ) -> dict[int, list[IterationResult]]:
        """Run the selected models in parallel until converged or iteration-capped.

        The run stops once every model with an objective has changed by less than
        ``config.epsilon`` (relative to its previous objective) for
        ``config.convergence_iterations`` consecutive iterations, or at ``config.iteration_limit``;
        see :class:`ConvergenceTracker`.  A model in a status outside ``ALLOW_TERMINATION``
        (e.g. ``PENALTY``) keeps the run going.  The worker pool size and monitor delta mode
        also come from :attr:`config`.

        Parameters
        ----------
        common_config : CommonConfig
            Common run configuration; its scenario output folder is claimed here, and its
            ``models_to_run`` selects the models.
        remainder : dict
            The config file's other sections:  ``elec_config``, ``natural_gas``, and optionally
            ``magic_config``.
        circuit : sequence of ModelType, default DEFAULT_CIRCUIT
            The order to circuit the selected models in; packages are routed in this order.  It
            must name every selected model, and may name others.
        **kwargs
            Unused; accepted to match :meth:`IterativeSequencer.run`.

        Returns
        -------
        dict of int to list of IterationResult
            Each iteration's model results, keyed by iteration number.

        Raises
        ------
        ValueError
            If ``circuit`` omits a selected model, or from :meth:`_process_configs`.
        """
        # claim a fresh scenario output dir, as a standalone run does; the workers get the claimed
        # folder with their pickled copy of the config.  The control process logs to MAIN.log, and
        # each worker to a per-model log beside it
        common_config.make_scenario_dir()
        setup_control_loop_logging(common_config.output_folder / 'MAIN.log')
        logger.info('Starting run for scenario "%s"', common_config.scenario_name)

        configs = self._process_configs(common_config, remainder)
        unplaced = set(configs) - set(circuit)
        if unplaced:
            raise ValueError(
                f'circuit {[m.value for m in circuit]} omits selected model(s) '
                f'{sorted(m.value for m in unplaced)}'
            )
        # the run's circuit:  the selected models, in circuit order
        run_circuit = tuple(model for model in circuit if model in configs)
        logger.info('Circuit: %s', [model.value for model in run_circuit])

        # set up iterative solve
        iteration = 1
        iteration_limit = self.config.iteration_limit
        tracker = ConvergenceTracker(self.config.epsilon, self.config.convergence_iterations)
        converged = False
        routed_updates = route_updates([], run_circuit)
        # the last accepted outbound packages per sender, resent while that sender's solves fail
        accepted: dict[ModelType, list[UpdatePackage]] = {}

        # every result from the run, keyed by iteration, for the objective plot
        all_results: dict[int, list[IterationResult]] = {}
        # the text monitor of objective deltas and package traffic, one block per iteration
        monitor = IterationMonitor(run_circuit, delta_mode=self.config.monitor_delta_mode)
        console = Console()

        # one pool for the whole run; spawning workers per iteration re-imports the world each time
        with Pool(processes=self.config.worker_processes) as worker_pool:
            while not converged and iteration <= iteration_limit:
                iter_calls: list[IterationCall] = []
                for model in run_circuit:
                    call_kwargs: dict[str, Any] = {'update_packages': routed_updates[model]}
                    if model is ModelType.MAGIC:
                        # MAGIC fabricates its outputs from the iteration number
                        call_kwargs['sequence_number'] = iteration
                    iter_calls.append(
                        IterationCall(model, common_config, configs[model], call_kwargs)
                    )
                results: list[IterationResult] = worker_pool.map(driver, iter_calls)
                # log status of the model's solves
                for result in results:
                    logger.info('\n%s', result.pprint(indent=2))
                all_results[iteration] = results
                # show this iteration's objective deltas and package traffic
                block = monitor.record(iteration, results)
                logger.info('\n%s', block.plain)
                console.print(block, highlight=False)
                # refresh each sender's accepted packages from this iteration; a sender whose solve
                # status isn't acceptable keeps its last accepted packages, so its receivers see
                # its last good solution rather than falling back to raw input data
                for result in results:
                    if result.status in ALLOW_OUTBOUND_UPDATES:
                        accepted[result.model_type] = list(result.update_packages)
                    else:
                        held = accepted.get(result.model_type)
                        logger.warning(
                            'Iteration %d: rejected %d update package(s) from %s (status %s); %s',
                            iteration,
                            len(result.update_packages),
                            result.model_type.value,
                            result.status.name,
                            f'resending its last accepted {len(held)} package(s)'
                            if held is not None
                            else 'it has no accepted packages to resend',
                        )
                # route in circuit order, which fixes the order receivers apply packages in
                outbound = [pkg for model in run_circuit for pkg in accepted.get(model, [])]
                routed_updates = route_updates(outbound, run_circuit)

                converged = tracker.update(results)
                logger.info(
                    'Iteration %d relative objective changes (epsilon %g): %s',
                    iteration,
                    self.config.epsilon,
                    {
                        model.value: 'n/a' if change is None else f'{change:.3g}'
                        for model, change in tracker.changes.items()
                    },
                )
                logger.info('Done with iteration %d/%d', iteration, iteration_limit)
                iteration += 1

        if converged:
            logger.info('Converged after %d iteration(s)', iteration - 1)
        else:
            final = all_results.get(iteration - 1, [])
            holding = [
                f'{r.model_type.value} ({r.status.name})'
                for r in final
                if r.status not in ALLOW_TERMINATION
            ]
            logger.warning(
                'Did not converge within %d iterations%s',
                iteration_limit,
                f'; ended with {", ".join(holding)}' if holding else '',
            )

        return all_results
