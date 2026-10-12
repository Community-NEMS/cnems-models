"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/11/26

Tests for the natural gas price update applied to supply_price.

"""

import pandas as pd
import pytest

from src.common.common_config import CommonConfig
from src.common.update_package import NG_PRICE_INDEX, NG_PRICE_VALUE, NGPricePackage
from src.models.electricity.constants import (
    INITIAL_NG_PRICE,
    NG_PRICE_LINKED_TECHS,
    PRICE_COST_PROPORTION,
)
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.update_reader import ElecUpdateReader


@pytest.fixture
def param_data(config_set: tuple[CommonConfig, ElecConfig]) -> ParamData:
    """Loaded (un-updated) parameter data for the basic test config."""
    common_config, elec_config = config_set
    return ParamData(common_config, elec_config, ModelSets(common_config, elec_config))


def make_price_package(keys: list[tuple[str, int]], price: float) -> NGPricePackage:
    """An ``NGPricePackage`` carrying one price for every ``(region, year)`` in ``keys``."""
    index = pd.MultiIndex.from_tuples(keys, names=NG_PRICE_INDEX)
    return NGPricePackage(elements=pd.DataFrame({NG_PRICE_VALUE: price}, index=index))


def held_region_years(prices: pd.DataFrame) -> list[tuple[str, int]]:
    """The distinct ``(region, year)`` pairs a ``supply_price`` frame holds."""
    keys = zip(
        prices.index.get_level_values('region'),
        prices.index.get_level_values('year'),
        strict=True,
    )
    return sorted(set(keys))


@pytest.mark.parametrize(
    'price_ratio, expected_factor',
    [
        pytest.param(1.5, 1 + PRICE_COST_PROPORTION * 0.5, id='price_up'),
        pytest.param(1.0, 1.0, id='price_unchanged'),
        pytest.param(0.5, 1 - PRICE_COST_PROPORTION * 0.5, id='price_down'),
    ],
)
def test_ng_price_package_scales_linked_techs(
    param_data: ParamData, price_ratio: float, expected_factor: float
) -> None:
    """Gas-linked tech rows scale with the relative gas price move; other techs are untouched."""
    before = param_data.param_frames['supply_price'].copy()
    package = make_price_package(held_region_years(before), INITIAL_NG_PRICE * price_ratio)

    ElecUpdateReader().apply_package(package, param_data)

    after = param_data.param_frames['supply_price']
    linked = after.index.get_level_values('tech').isin(NG_PRICE_LINKED_TECHS)
    assert linked.any(), 'test data holds no gas-linked tech rows'
    pd.testing.assert_frame_equal(after[~linked], before[~linked])
    pd.testing.assert_frame_equal(after[linked], before[linked] * expected_factor)


@pytest.mark.parametrize('gap', ['underage', 'overage'])
def test_ng_price_package_must_cover_held_entries(param_data: ParamData, gap: str) -> None:
    """A package not covering exactly the held (region, year) entries is rejected untouched."""
    before = param_data.param_frames['supply_price'].copy()
    keys = held_region_years(before)
    keys = keys[1:] if gap == 'underage' else [*keys, ('not_a_region', keys[0][1])]
    package = make_price_package(keys, INITIAL_NG_PRICE * 2)

    with pytest.raises(ValueError, match='must cover exactly'):
        ElecUpdateReader().apply_package(package, param_data)

    pd.testing.assert_frame_equal(param_data.param_frames['supply_price'], before)


@pytest.mark.parametrize(
    'frame',
    [
        pytest.param(
            pd.DataFrame(
                {NG_PRICE_VALUE: [-1.0]},
                index=pd.MultiIndex.from_tuples([('7', 2025)], names=NG_PRICE_INDEX),
            ),
            id='negative_price',
        ),
        pytest.param(
            pd.DataFrame(
                {'cost': [3.0]},
                index=pd.MultiIndex.from_tuples([('7', 2025)], names=NG_PRICE_INDEX),
            ),
            id='wrong_value_column',
        ),
        pytest.param(
            pd.DataFrame(
                {NG_PRICE_VALUE: [3.0]},
                index=pd.MultiIndex.from_tuples([('7', 2025)], names=['reg', 'yr']),
            ),
            id='wrong_index_names',
        ),
        pytest.param(
            pd.DataFrame(
                {NG_PRICE_VALUE: [3.0, 4.0]},
                index=pd.MultiIndex.from_tuples([('7', 2025), ('7', 2025)], names=NG_PRICE_INDEX),
            ),
            id='duplicate_rows',
        ),
    ],
)
def test_ng_price_package_rejects_bad_frames(frame: pd.DataFrame) -> None:
    """A frame the recipient could not apply is rejected at construction."""
    with pytest.raises(ValueError):
        NGPricePackage(elements=frame)
