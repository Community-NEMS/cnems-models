"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/9/26

Routing of update packages from the models that sent them to the models that receive them.
"""

import logging
from collections.abc import Mapping, Sequence

from src.common.models_modes import ModelType
from src.common.update_package.update_package import UpdatePackage

logger = logging.getLogger(__name__)


def route_updates(
    packages_by_sender: Mapping[ModelType, Sequence[UpdatePackage]], circuit: Sequence[ModelType]
) -> dict[ModelType, list[UpdatePackage]]:
    """Bin update packages by the models that should receive them, in a fixed order.

    Each receiver's list is ordered by sender, senders in ``circuit`` order, then by each
    sender's own package order.  Receivers apply packages in list order, so this keeps results
    deterministic regardless of the order the mapping was filled in.

    A package may name any number of receivers; :attr:`ModelType.ALL` means every model in the
    circuit.  Receivers outside the circuit, and senders outside it, are dropped with a warning --
    nothing is running to consume or to have produced them.

    Parameters
    ----------
    packages_by_sender : mapping of ModelType to sequence of UpdatePackage
        The packages to deliver, keyed by the model that sent them.  Senders with no entry send
        nothing.
    circuit : sequence of ModelType
        The models participating in the run, in the order their packages are delivered.

    Returns
    -------
    dict of ModelType to list of UpdatePackage
        One entry per circuit member, in circuit order; empty lists for models with no mail.
        :attr:`ModelType.ALL` is never a key -- it is an indicator, not a destination.
    """
    off_circuit = set(packages_by_sender) - set(circuit)
    if off_circuit:
        logger.warning(
            'Dropping packages from %s; not in the circuit %s',
            sorted(m.value for m in off_circuit),
            [m.value for m in circuit],
        )
    routed: dict[ModelType, list[UpdatePackage]] = {model: [] for model in circuit}
    for sender in circuit:
        for package in packages_by_sender.get(sender, ()):
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
