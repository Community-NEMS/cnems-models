"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  sauleh@american.edu
Created on:  11/2/26

Tests for C-HSM's Henry Hub path from C-NGMM's hub price.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.common.models_modes import ModelType
from src.common.update_package import NG_HUB_PRICE_INDEX, NG_HUB_PRICE_VALUE, NGHubPricePackage
from src.models.hsm.update_reader import HSMData, HSMUpdateReader, load_reference_pair
from src.models.natural_gas.constants import HUB_REGION, PRICE_DOLLAR_YEAR

REFERENCE_FILE = Path(PROJECT_ROOT, 'input/hsm/henry_hub_path_aeo2026_1987usd.csv')


@pytest.mark.parametrize(
    'ratios',
    [
        pytest.param(dict.fromkeys(range(2025, 2051, 5), 1.0), id='reference'),
        pytest.param({2030: 1.0, 2040: 1.2}, id='interpolated_and_held_flat'),
    ],
)
def test_hub_price_sets_the_henry_hub_path(ratios: dict[int, float]) -> None:
    """The hub price scales C-HSM's reference path by its ratio to the reference price.

    The ratio is linear between package years and held at the nearest one outside them.
    """
    reference_1987, reference_usd = load_reference_pair(REFERENCE_FILE, PRICE_DOLLAR_YEAR)
    data = HSMData(
        regions=[],
        supply_cost_tiers={},
        henry_hub_1987=dict(reference_1987),
        reference_1987=reference_1987,
        reference_usd=reference_usd,
        reference_dollar_year=PRICE_DOLLAR_YEAR,
    )
    years = sorted(ratios)
    package = NGHubPricePackage(
        elements=pd.DataFrame(
            {NG_HUB_PRICE_VALUE: [ratios[year] * reference_usd[year] for year in years]},
            index=pd.Index(years, name=NG_HUB_PRICE_INDEX[0]),
        ),
        hub_region=HUB_REGION,
        dollar_year=PRICE_DOLLAR_YEAR,
        source=ModelType.NATURAL_GAS,
    )
    HSMUpdateReader().read([package], data)
    ratio_values = [ratios[year] for year in years]
    expected = {
        year: float(np.interp(year, years, ratio_values)) * price
        for year, price in reference_1987.items()
    }
    assert data.henry_hub_1987 == pytest.approx(expected, rel=1e-12)
