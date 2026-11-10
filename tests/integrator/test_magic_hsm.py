"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/9/26

Tests for Magic HSM, the test model for the coupling with C-NGMM.  They are kept apart from the
iterator tests so that they can be removed with the model.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from pyomo.environ import value

from definitions import PROJECT_ROOT
from src.common.common_config import parse_config_file
from src.common.iterative_sequencer import RunStatus
from src.common.log_setup import _retire_previous_setup
from src.common.update_package import (
    HSM_CAPACITY_VALUE,
    NG_HUB_PRICE_VALUE,
    HSMSupplyCapacityPackage,
    NGHubPricePackage,
)
from src.integrator.gauss_seidel.gs_iterator import GaussSeidelIterator
from src.models.magic_hsm.magic_hsm_model import MagicHSMConfig
from src.models.natural_gas.ng_config import NGConfig
from src.models.natural_gas.sequencer import NGSequencer


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Undo the run's root logging setup, so later tests in this worker do not log through it."""
    root = logging.getLogger()
    level = root.level
    yield
    _retire_previous_setup()
    root.setLevel(level)


@pytest.mark.usefixtures('restore_logging')
def test_magic_hsm_capacity_follows_the_hub_price(tmp_path: Path) -> None:
    """Magic HSM and gas settle, and the last capacity is Q0 x (hub price / reference) ^ e.

    Q0 is C-NGMM's at build, over its regions and years, and the hub price is the one C-NGMM sent
    the iteration before.
    """
    common, remainder = parse_config_file(PROJECT_ROOT / 'run_configs' / 'gs_magic_hsm_ng.toml')
    common = common.model_copy(update={'output_path': tmp_path, 'summary_years': [2025, 2030]})
    hsm = MagicHSMConfig(**remainder['magic_hsm'])
    q0 = NGSequencer().build_model(common, NGConfig(**remainder['natural_gas'])).q0

    status, results = GaussSeidelIterator().run(common, remainder)

    assert status is RunStatus.CONVERGED
    last = max(results)
    (capacity,) = [
        p.elements[HSM_CAPACITY_VALUE]
        for r in results[last]
        for p in r.update_packages
        if isinstance(p, HSMSupplyCapacityPackage)
    ]
    (hub,) = [
        p.elements[NG_HUB_PRICE_VALUE]
        for r in results[last - 1]
        for p in r.update_packages
        if isinstance(p, NGHubPricePackage)
    ]
    expected = {
        (str(region), int(year)): value(q0[region, year])
        * (hub[year] / hsm.reference_price) ** hsm.price_elasticity
        for region, year in q0
    }
    sent = {(str(region), int(year)): float(bcf) for (region, year), bcf in capacity.items()}
    assert sent == pytest.approx(expected, rel=1e-12)
