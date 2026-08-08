"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/7/26

Test class to attempt multiprocessing run with electricity and magic models

"""

import logging
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from multiprocessing import Pool

from common.common_config import ModelConfig
from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.integrated_model_sequencer import IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.magic.magic_model import MagicConfig, MagicSequencer

logger = logging.getLogger(__name__)

common_config_path = PROJECT_ROOT / 'run_configs/basic_elec_config.toml'

# the models participating in this run; order here fixes the order packages are routed in
CIRCUIT: tuple[ModelType, ...] = (ModelType.ELECTRICITY, ModelType.MAGIC)


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


def _logger_setup(iter_call: IterationCall):
    """Setup logging for the process."""
    # TODO:  This is fragile.  If any import touches logging setup, this is discarded
    #        As we go forward, need to make this more imperative
    scenario_name = iter_call.common_config.scenario_name
    process_name = iter_call.model_type.value
    logger_name = f'{scenario_name}-{process_name}'
    output_folder = PROJECT_ROOT / 'output' / scenario_name
    logging.basicConfig(
        filename=output_folder / f'{logger_name}.log',
        encoding='utf-8',
        filemode='a',
        format='%(asctime)s | %(name)s | %(levelname)s :: %(message)s',
        datefmt='%d-%b-%y %H:%M:%S',
        level=logging.INFO,
    )


def driver(
    iter_call: IterationCall,
) -> tuple[tuple[ModelType, IterationStatus], list[UpdatePackage]]:
    """Run one model end to end in a pool worker.

    Parameters
    ----------
    iter_call : IterationCall
        The model to run, its configs, and any per-iteration kwargs.

    Returns
    -------
    tuple of (IterationStatus, list of UpdatePackage)
        The solve status and any packages the model wants sent onward.

    Raises
    ------
    NotImplementedError
        If ``iter_call.model`` has no sequencer wired up here.
    TypeError
        If the call's config does not match its model.
    """
    # start the logging for this process
    _logger_setup(iter_call)
    match iter_call.model_type:
        case ModelType.ELECTRICITY:
            status, updates = ElectricitySequencer().full_run(
                iter_call.common_config, iter_call.model_config, **iter_call.kwargs
            )
        case ModelType.MAGIC:
            status, updates = MagicSequencer().full_run(
                iter_call.common_config, iter_call.model_config, **iter_call.kwargs
            )
        case _:
            raise NotImplementedError()
    return status, updates


def main() -> None:
    """Run the electricity and magic models in parallel until converged or iteration-capped."""
    common_config, remainder = parse_config_file(common_config_path)
    elec_cfg = ElecConfig(**remainder.pop('elec_config'))
    magic_cfg = MagicConfig(**remainder.pop('magic_config', {}))

    # set up iterative solve
    iteration = 0
    iter_limit = 4
    tolerance = 100  # cost units in electricity model
    eps = float('inf')
    routed_updates = route_updates([], CIRCUIT)

    # one pool for the whole run; spawning workers per iteration re-imports the world each time
    with Pool(processes=6) as worker_pool:
        while eps > tolerance and iteration < iter_limit:
            # here we go...
            elec_iter = IterationCall(
                ModelType.ELECTRICITY,
                common_config,
                elec_cfg,
                {'update_packages': routed_updates[ModelType.ELECTRICITY]},
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
            iter_calls = [elec_iter, magic_iter]
            results = worker_pool.map(driver, iter_calls)
            # log status of the model's solves
            status_list = [type_status for (type_status, update_packages) in results]
            for model_type, status in status_list:
                logger.info(f'iteration {iteration}, model: {model_type.value}, status: {status}')

            # route each model's outbound packages to their receivers for the next iteration
            outbound = [pkg for _status, packages in results for pkg in packages]
            routed_updates = route_updates(outbound, CIRCUIT)

            # TODO:  compute a real convergence measure; eps is never updated, so this loop
            #        currently always runs the full iter_limit
            logger.info('done with iteration %d, results: %s', iteration, results)
            iteration += 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
