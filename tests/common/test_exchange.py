"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/12/26

Tests for the run's exchange selection (``ExchangeSpec``), the writer's choice of packages, and the
reader's received-key check.
"""

import logging
import pickle

import pandas as pd
import pytest

from src.common.models_modes import ModelType
from src.common.update_package import (
    PACKAGE_TYPES,
    ElectricityPriceScaler,
    ExchangeSpec,
    NGDemandScaler,
    NGElectricalDemandPackage,
    NGPricePackage,
    check_region_year_keys,
)
from src.models.electricity.update_writer import ElecUpdateWriter
from src.models.magic.magic_model import MagicModel
from src.models.magic.update_writer import MagicUpdateWriter

ELEC = ModelType.ELECTRICITY
NG = ModelType.NATURAL_GAS
MAGIC = ModelType.MAGIC
STANDARD = list(PACKAGE_TYPES)
LOGGER_NAME = 'src.common.update_package.exchange'


@pytest.mark.parametrize(
    'models, outbound, excluded',
    [
        pytest.param(
            {ELEC, NG, MAGIC},
            {
                ELEC: {NGElectricalDemandPackage},
                NG: {NGPricePackage},
                MAGIC: {ElectricityPriceScaler, NGDemandScaler},
            },
            [],
            id='all_models',
        ),
        pytest.param(
            {ELEC, NG},
            {ELEC: {NGElectricalDemandPackage}, NG: {NGPricePackage}},
            ['ElectricityPriceScaler', 'NGDemandScaler'],
            id='no_magic',
        ),
        pytest.param({ELEC}, {}, STANDARD, id='electricity_only'),
        pytest.param(
            {ELEC, MAGIC},
            {MAGIC: {ElectricityPriceScaler}},
            ['NGDemandScaler', 'NGElectricalDemandPackage', 'NGPricePackage'],
            id='elec_and_magic',
        ),
    ],
)
def test_build_keeps_exchanges_the_models_carry(
    models: set[ModelType],
    outbound: dict[ModelType, set[type]],
    excluded: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A package runs only if its sender and receivers do; the rest are named in one warning."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        spec = ExchangeSpec.build(STANDARD, models)

    assert {m: set(t) for m, t in spec.outbound.items()} == outbound
    assert spec.expected == {t for types in outbound.values() for t in types}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    if excluded:
        assert len(warnings) == 1
        assert all(name in warnings[0] for name in excluded)
    else:
        assert not warnings


def test_build_rejects_unknown_package() -> None:
    """A name that is no package type is a config error, not an exclusion."""
    with pytest.raises(ValueError, match='Unknown update package'):
        ExchangeSpec.build(['NGPricePackage', 'NotAPackage'], {ELEC, NG})


def test_build_loads_crosswalks_and_pickles() -> None:
    """The spec carries the crosswalks between the selected models, and crosses to a worker."""
    spec = ExchangeSpec.build(STANDARD, {ELEC, NG})
    clone = pickle.loads(pickle.dumps(spec))
    assert clone.outbound == spec.outbound
    assert set(clone.crosswalks.crosswalks) == {frozenset({ELEC, NG})}


def test_empty_spec_writes_nothing() -> None:
    """A standalone run's writer builds no packages, without reading the model."""
    writer = ElecUpdateWriter(ExchangeSpec.empty().outbound_for(ELEC))
    assert writer.write(None) == []  # pyrefly: ignore[bad-argument-type]


def test_writer_rejects_unsupported_package() -> None:
    """A writer asked for a package it has no builder for fails at construction."""
    with pytest.raises(ValueError, match='cannot write'):
        ElecUpdateWriter({NGPricePackage})


def test_writer_builds_only_its_outbound() -> None:
    """A writer builds the requested subset of what it supports."""
    model = MagicModel.__new__(MagicModel)  # skip __init__, which sleeps to mimic a solve
    model.sequence_number = 1
    packages = MagicUpdateWriter({NGDemandScaler}).write(model)
    assert [type(p) for p in packages] == [NGDemandScaler]


def keys(entries: list[tuple]) -> pd.MultiIndex:
    """A ``(region, year)`` index."""
    return pd.MultiIndex.from_tuples(entries, names=['region', 'year'])


def test_received_keys_exact_match_passes() -> None:
    """Ids are compared as str region and int year, whatever types the recipient holds."""
    check_region_year_keys(keys([('7', 2025), ('7', 2030)]), [7], {2025, 2030}, name='test')


@pytest.mark.parametrize(
    'received, match',
    [
        pytest.param([('7', 2025)], r"1 missing \[\('7', 2030\)\]", id='underage'),
        pytest.param(
            [('7', 2025), ('7', 2030), ('8', 2025)], r"1 not held \[\('8', 2025\)\]", id='overage'
        ),
    ],
)
def test_received_keys_mismatch_raises(received: list[tuple], match: str) -> None:
    """Missing or unheld (region, year) entries are both errors."""
    with pytest.raises(ValueError, match=match):
        check_region_year_keys(keys(received), ['7'], [2025, 2030], name='test')
