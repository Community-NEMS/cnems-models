"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

Test class to attempt multiprocessing run with electricity, natural gas, and magic models

"""

import logging
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path

from rich.console import Console

from src.common.common_config import CommonConfig, ModelConfig, parse_config_file
from src.common.integrated_model_sequencer import ALLOW_OUTBOUND_UPDATES, IterationResult
from src.common.log_setup import _scenario_log, setup_control_loop_logging
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage
from src.integrator.iteration_monitor import DeltaMode, IterationMonitor
from src.integrator.iteration_plot import plot_objectives
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.magic.magic_model import MagicConfig, MagicSequencer
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.sequencer import NGSequencer

# `python -m` names this module __main__ (__mp_main__ in a spawned worker); __spec__.name is
# the dotted import name under every entry point, keeping these records in the captured tree
logger = logging.getLogger(__name__)

# The logger trees whose records belong in a scenario log.  `src` covers every project module.
# `pyomo` covers solver output -- the bulk of the electricity log -- because every solver
# interface logs under it (`pyomo.contrib.appsi.solvers.{highs,gurobi,...}`), and pyomo pipes the
# solver's own native output through those loggers; highspy and gurobipy register none of their
# own.  A solver driven outside pyomo would need its logger tree added here.
#
# Attaching to these trees rather than to the root logger keeps the run from hijacking a host
# application's logging, at the cost of having to name what to capture.

# the models participating in this run; order here fixes the order packages are routed in
CIRCUIT: tuple[ModelType, ...] = (ModelType.ELECTRICITY, ModelType.NATURAL_GAS, ModelType.MAGIC)

# TEMP:  plot the objectives by iteration when main() finishes (plt.show() blocks until closed)
PLOT_OBJECTIVES = True


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


def main(config_path: Path, iter_limit: int = 15) -> None:
    """Run the electricity, natural gas, and magic models in parallel until iteration-capped.

    Parameters
    ----------
    config_path : Path
        The run config file, with ``[common]``, ``[elec_config]``, ``[natural_gas]`` and
        (optionally) ``[magic_config]`` sections.
    iter_limit : int, default 15
        Number of iterations to run; convergence is not yet measured, so this is the run length.
    """
    common_config, remainder = parse_config_file(config_path)
    elec_cfg = ElecConfig(**remainder.pop('elec_config'))
    ng_cfg = NGConfig(**remainder.pop('natural_gas'))
    magic_cfg = MagicConfig(**remainder.pop('magic_config', {}))

    # claim a fresh scenario output dir, as a standalone run does; the workers get the claimed
    # folder with their pickled copy of the config.  The control process logs to MAIN.log, and
    # each worker to a per-model log beside it
    common_config.make_scenario_dir()
    setup_control_loop_logging(common_config.output_folder / 'MAIN.log')
    logger.info('Starting run for scenario "%s"', common_config.scenario_name)
    if elec_cfg.region_filter:
        logger.warning(
            'The electricity model is filtered to regions %s.  Packages it sends cover only the '
            'gas regions those touch, and the natural gas model holds the rest at base-year '
            'values, so excluding regions may lead to odd results in the integrated run',
            elec_cfg.region_filter,
        )
    if ng_cfg.region_filter:
        logger.warning(
            'The natural gas model is filtered to regions %s.  Prices it sends cover only the '
            'electricity regions those touch (averaged over partial coverage), and demand it '
            'receives for other regions is ignored, so excluding regions may lead to odd results '
            'in the integrated run',
            ng_cfg.region_filter,
        )

    # set up iterative solve
    iteration = 1
    tolerance = 100  # cost units in electricity model
    eps = float('inf')
    routed_updates = route_updates([], CIRCUIT)
    # the last accepted outbound packages per sender, resent while that sender's solves fail
    accepted: dict[ModelType, list[UpdatePackage]] = {}

    # every result from the run, keyed by iteration, for the objective plot
    all_results: dict[int, list[IterationResult]] = {}
    # the text monitor of objective deltas and package traffic, one block per iteration
    monitor = IterationMonitor(CIRCUIT, delta_mode=DeltaMode.ABSOLUTE)
    console = Console()

    # one pool for the whole run; spawning workers per iteration re-imports the world each time
    with Pool(processes=6) as worker_pool:
        while eps > tolerance and iteration <= iter_limit:
            # here we go...
            elec_iter = IterationCall(
                ModelType.ELECTRICITY,
                common_config,
                elec_cfg,
                {'update_packages': routed_updates[ModelType.ELECTRICITY]},
            )
            ng_iter = IterationCall(
                ModelType.NATURAL_GAS,
                common_config,
                ng_cfg,
                {'update_packages': routed_updates[ModelType.NATURAL_GAS]},
            )
            magic_iter = IterationCall(
                ModelType.MAGIC,
                common_config,
                magic_cfg,
                {
                    'sequence_number': iteration,
                    'update_packages': routed_updates[ModelType.MAGIC],
                },
            )
            iter_calls = [elec_iter, ng_iter, magic_iter]
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
            outbound = [pkg for model in CIRCUIT for pkg in accepted.get(model, [])]
            routed_updates = route_updates(outbound, CIRCUIT)

            # TODO:  compute a real convergence measure; eps is never updated, so this loop
            #        currently always runs the full iter_limit
            logger.info('Done with iteration %d/%d', iteration, iter_limit)
            iteration += 1

    if PLOT_OBJECTIVES:
        plot_objectives(all_results)
