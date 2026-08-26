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

from matplotlib import pyplot as plt

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, ModelConfig, parse_config_file
from src.common.integrated_model_sequencer import IterationResult
from src.common.log_setup import _scenario_log, log_path, setup_control_loop_logging
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage
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

common_config_path = PROJECT_ROOT / 'run_configs/basic_elec_config.toml'

# the models participating in this run; order here fixes the order packages are routed in
CIRCUIT: tuple[ModelType, ...] = (ModelType.ELECTRICITY, ModelType.NATURAL_GAS, ModelType.MAGIC)


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
    with _scenario_log(iter_call.common_config.scenario_name, iter_call.model_type.value):
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


def main() -> None:
    """Run the electricity, natural gas, and magic models in parallel until iteration-capped."""
    common_config, remainder = parse_config_file(common_config_path)
    elec_cfg = ElecConfig(**remainder.pop('elec_config'))
    ng_cfg = NGConfig(**remainder.pop('natural_gas'))
    magic_cfg = MagicConfig(**remainder.pop('magic_config', {}))

    # the control process logs to its own file and the console; the per-model scenario logs
    # belong to the workers, not here
    setup_control_loop_logging(log_path(common_config.scenario_name, 'MAIN'))
    logger.info('Starting run for scenario "%s"', common_config.scenario_name)

    # set up iterative solve
    iteration = 1
    iter_limit = 15
    tolerance = 100  # cost units in electricity model
    eps = float('inf')
    routed_updates = route_updates([], CIRCUIT)

    # collect OBJ values per objective-bearing model, in iteration order
    obj_vals: dict[ModelType, list[float]] = {
        ModelType.ELECTRICITY: [],
        ModelType.NATURAL_GAS: [],
    }

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
                # MAGIC carries no objective; the None arms for the other two are
                # unreachable in practice
                obj_value = result.objective_value
                if result.model_type in obj_vals and obj_value is not None:
                    obj_vals[result.model_type].append(obj_value)
            # route each model's outbound packages to their receivers for the next iteration
            outbound = [pkg for result in results for pkg in result.update_packages]
            routed_updates = route_updates(outbound, CIRCUIT)

            # TODO:  compute a real convergence measure; eps is never updated, so this loop
            #        currently always runs the full iter_limit
            logger.info('Done with iteration %d', iteration)
            print(f'Done with iteration {iteration}/{iter_limit}')
            iteration += 1

    # the two objectives are orders of magnitude apart (and NG's is negative), so each model
    # gets its own y-axis; colors keyed by axis so the legend stays readable
    _fig, elec_ax = plt.subplots()
    ng_ax = elec_ax.twinx()
    elec_vals = obj_vals[ModelType.ELECTRICITY]
    ng_vals = obj_vals[ModelType.NATURAL_GAS]
    elec_scatter = elec_ax.scatter(
        list(range(1, len(elec_vals) + 1)), elec_vals, color='tab:blue', label='electricity'
    )
    ng_scatter = ng_ax.scatter(
        list(range(1, len(ng_vals) + 1)), ng_vals, color='tab:orange', label='natural gas'
    )
    elec_ax.set_xlabel('iteration')
    elec_ax.set_ylabel('electricity objective', color='tab:blue')
    ng_ax.set_ylabel('natural gas objective', color='tab:orange')
    elec_ax.legend(handles=[elec_scatter, ng_scatter], loc='lower right')
    plt.show()


# temp note:  This can be run from the project ROOT level by running as a module:
#    pixi run python -m src.integrator.combine
#              -- or just --
#    python -m src.integrator.combine

if __name__ == '__main__':
    main()
