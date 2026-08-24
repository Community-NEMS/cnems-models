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

from matplotlib import pyplot as plt
from pyomo.common.numeric_types import value

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, ModelConfig, parse_config_file
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


@dataclass
class IterationResult:
    """One model's worth of finished work for a single iteration, returned from a pool worker.

    Must stay picklable -- workers are spawned, so every field crosses a process boundary.

    Attributes
    ----------
    model_type : ModelType
        The model that produced this result.
    status : IterationStatus
        The solve status reported by the model's sequencer.
    objective_value : float | None
        The solved objective value, or ``None`` for models that have no objective.
    update_packages : list of UpdatePackage
        The packages this model wants routed onward to its receivers.
    """

    model_type: ModelType
    status: IterationStatus
    objective_value: float | None
    update_packages: list[UpdatePackage]

    def pprint(self, indent: int = 0) -> str:
        """Render a 4-line summary: model, status, objective value, and update package types.

        Returns
        -------
        str
            The packages are named by class only -- their payloads are not summarized.
        """
        obj_value = 'n/a' if self.objective_value is None else f'{self.objective_value:,.2f}'
        package_names = [type(package).__name__ for package in self.update_packages]
        ind = ' ' * indent if indent else ''
        return ind.join(
            (
                '',
                f'IterationResult for model: {self.model_type.value}\n',
                f'  status:               {self.status.name}\n',
                f'  objective value:      {obj_value}\n',
                f'  update packages sent: {package_names}',
            )
        )


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


def _process_logger_setup(iter_call: IterationCall) -> None:
    """Set up logging for a sub-process within an iteration from the IterationCall object."""
    _logger_setup(iter_call.common_config.scenario_name, iter_call.model_type.value)


def _logger_setup(scenario_name: str, process_name: str) -> None:
    """Setup logging for the process."""
    # TODO:  This is fragile.  If any import touches logging setup, this is discarded.
    #        As we go forward, need to make this more imperative
    logger_name = f'{scenario_name}-{process_name}'
    output_folder = PROJECT_ROOT / 'output' / scenario_name
    output_folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=output_folder / f'{logger_name}.log',
        encoding='utf-8',
        filemode='a',
        format='%(asctime)s | %(name)s | %(levelname)s :: %(message)s',
        datefmt='%d-%b-%y %H:%M:%S',
        level=logging.INFO,
    )
    logger.info('Logging started for scenario %s, process: %s', scenario_name, process_name)


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
    # start the logging for this process
    _process_logger_setup(iter_call)
    # IterationCall carries the config as the ModelConfig base, so each arm has to confirm it
    # got the config its sequencer expects -- this is the TypeError the docstring promises.
    match iter_call.model_type:
        case ModelType.ELECTRICITY:
            if not isinstance(iter_call.model_config, ElecConfig):
                raise TypeError(
                    f'ModelType.ELECTRICITY needs an ElecConfig, '
                    f'got {type(iter_call.model_config).__name__}'
                )
            sequencer = ElectricitySequencer()
            (model_type, status), updates = sequencer.full_run(
                iter_call.common_config, iter_call.model_config, **iter_call.kwargs
            )
            obj_value = value(sequencer.model.total_cost)
        case ModelType.MAGIC:
            if not isinstance(iter_call.model_config, MagicConfig):
                raise TypeError(
                    f'ModelType.MAGIC needs a MagicConfig, '
                    f'got {type(iter_call.model_config).__name__}'
                )
            (model_type, status), updates = MagicSequencer().full_run(
                iter_call.common_config, iter_call.model_config, **iter_call.kwargs
            )
            obj_value = None  # MagicModel has no objective
        case _:
            raise NotImplementedError()
    return IterationResult(model_type, status, obj_value, updates)


def main() -> None:
    """Run the electricity and magic models in parallel until converged or iteration-capped."""
    common_config, remainder = parse_config_file(common_config_path)
    elec_cfg = ElecConfig(**remainder.pop('elec_config'))
    magic_cfg = MagicConfig(**remainder.pop('magic_config', {}))

    # start the logger for the outer/control loop
    _logger_setup(scenario_name=common_config.scenario_name, process_name='MAIN')

    # set up iterative solve
    iteration = 0
    iter_limit = 4
    tolerance = 100  # cost units in electricity model
    eps = float('inf')
    routed_updates = route_updates([], CIRCUIT)

    # collect OBJ values for Electricity
    electricity_obj_vals: list[float] = []

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
            results: list[IterationResult] = worker_pool.map(driver, iter_calls)
            # log status of the model's solves
            for result in results:
                logger.info('\n' + result.pprint(indent=2))
                # only ELECTRICITY carries an objective; its None arm is unreachable in practice
                obj_value = result.objective_value
                if result.model_type is ModelType.ELECTRICITY and obj_value is not None:
                    electricity_obj_vals.append(obj_value)
            # route each model's outbound packages to their receivers for the next iteration
            outbound = [pkg for result in results for pkg in result.update_packages]
            routed_updates = route_updates(outbound, CIRCUIT)

            # TODO:  compute a real convergence measure; eps is never updated, so this loop
            #        currently always runs the full iter_limit
            logger.info('Done with iteration %d', iteration)
            iteration += 1

    plt.scatter(list(range(iteration)), electricity_obj_vals)
    plt.show()


# temp note:  This can be run from the project ROOT level by running as a module:
#    pixi run python -m src.integrator.combine
#              -- or just --
#    python -m src.integrator.combine

if __name__ == '__main__':
    main()
