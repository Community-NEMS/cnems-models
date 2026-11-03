"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/9/26

Tests for route_updates:  delivery by receiver and the sender-order guarantee.
"""

import logging
from dataclasses import dataclass

import pytest

from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage, route_updates

ELEC = ModelType.ELECTRICITY
NG = ModelType.NATURAL_GAS
MAGIC = ModelType.MAGIC


@dataclass(frozen=True)
class Mail(UpdatePackage):
    """A minimal package with free receivers, for routing tests."""

    receivers: tuple[ModelType, ...] = ()
    label: str = 'mail'

    @property
    def size(self) -> int:
        """No payload."""
        return 0


def test_empty_mapping_gives_empty_bins_in_circuit_order() -> None:
    """Every circuit member gets a key, in circuit order, and ALL is never a key."""
    routed = route_updates({}, (NG, ELEC))
    assert list(routed) == [NG, ELEC]
    assert all(bin_ == [] for bin_ in routed.values())


@pytest.mark.parametrize(
    'receivers, expected',
    [
        ((ELEC,), {ELEC: 1, NG: 0, MAGIC: 0}),
        ((ModelType.ALL,), {ELEC: 1, NG: 1, MAGIC: 1}),
        ((ModelType.ALL, NG), {ELEC: 1, NG: 1, MAGIC: 1}),  # no duplicate for NG
        ((ELEC, NG), {ELEC: 1, NG: 1, MAGIC: 0}),
    ],
)
def test_delivery_by_receiver(
    receivers: tuple[ModelType, ...], expected: dict[ModelType, int]
) -> None:
    """A package reaches each named receiver once; ALL reaches the whole circuit."""
    routed = route_updates({MAGIC: [Mail(receivers=receivers)]}, (ELEC, MAGIC, NG))
    assert {model: len(bin_) for model, bin_ in routed.items()} == expected


@pytest.mark.parametrize('circuit', [(ELEC, MAGIC, NG), (NG, MAGIC, ELEC), (MAGIC, NG, ELEC)])
def test_senders_walked_in_circuit_order(circuit: tuple[ModelType, ...]) -> None:
    """Each receiver's packages group by sender in circuit order, whatever the mapping order."""
    sent = {
        model: [Mail(receivers=(ModelType.ALL,), source=model) for _ in range(2)]
        for model in (NG, ELEC, MAGIC)
    }  # deliberately not circuit order
    routed = route_updates(sent, circuit)
    expected = [package for sender in circuit for package in sent[sender]]
    for model in circuit:
        assert routed[model] == expected


def test_off_circuit_receiver_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """A receiver outside the circuit gets nothing, and a warning is logged."""
    with caplog.at_level(logging.WARNING):
        routed = route_updates({ELEC: [Mail(receivers=(NG, MAGIC))]}, (ELEC, NG))
    assert len(routed[NG]) == 1
    assert MAGIC not in routed
    assert 'not in the circuit' in caplog.text


def test_off_circuit_sender_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """Packages from a sender outside the circuit are not delivered, and a warning is logged."""
    with caplog.at_level(logging.WARNING):
        routed = route_updates({MAGIC: [Mail(receivers=(ModelType.ALL,))]}, (ELEC, NG))
    assert routed == {ELEC: [], NG: []}
    assert 'Dropping packages from' in caplog.text
