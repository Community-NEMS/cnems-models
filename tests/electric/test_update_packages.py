"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/11/26

Tests for detecting index mismatches in updates to param_data dataframes, and for the
natural gas price update applied to supply_price.

"""

import logging

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

LOGGER_NAME = 'src.models.electricity.update_reader'


def make_index(entries: list[tuple], names: tuple[str, ...] = ('region', 'year')) -> pd.MultiIndex:
    """Build a small MultiIndex for the gap-report tests."""
    return pd.MultiIndex.from_tuples(entries, names=names)


@pytest.mark.parametrize(
    'old_entries, new_entries, expected_missing',
    [
        pytest.param(
            [('7', 2025), ('7', 2030)],
            [('7', 2025), ('7', 2030)],
            [],
            id='full_coverage',
        ),
        pytest.param(
            [('7', 2025), ('7', 2030)],
            [('7', 2025)],
            [('7', 2030)],
            id='partial_coverage',
        ),
        pytest.param(
            [('7', 2025)],
            [('7', 2025), ('8', 2025), ('8', 2030)],
            [],
            id='overage_ignored',
        ),
        pytest.param(
            [('7', 2025), ('8', 2025)],
            [('9', 2030)],
            [('7', 2025), ('8', 2025)],
            id='no_overlap',
        ),
    ],
)
def test_report_index_gaps(
    old_entries: list[tuple], new_entries: list[tuple], expected_missing: list[tuple], caplog
) -> None:
    """Missing held entries are returned and warned about; overages are not."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        missing = ElecUpdateReader._report_index_gaps(
            make_index(old_entries), make_index(new_entries), name='test_frame'
        )

    assert list(missing) == expected_missing
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert bool(warnings) == bool(expected_missing)
    if expected_missing:
        assert 'does not cover' in warnings[0].getMessage()


def test_report_index_gaps_level_mismatch_raises() -> None:
    """An index with a different number of levels cannot be aligned."""
    old = make_index([('7', 2025)])
    new = make_index([('7', 2025, 'x')], names=('region', 'year', 'extra'))
    with pytest.raises(ValueError, match='level'):
        ElecUpdateReader._report_index_gaps(old, new, name='test_frame')


def test_report_index_gaps_name_mismatch_warns_and_compares(caplog) -> None:
    """Differing level names warn but are still compared by position."""
    old = make_index([('7', 2025), ('7', 2030)])
    new = make_index([('7', 2025)], names=('destination', 'yr'))
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        missing = ElecUpdateReader._report_index_gaps(old, new, name='test_frame')

    assert list(missing) == [('7', 2030)]
    assert any('level names' in r.getMessage() for r in caplog.records)


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


def test_ng_price_package_uncovered_rows_retained(param_data: ParamData, caplog) -> None:
    """Held (region, year) pairs the package omits keep their loaded values and are warned about."""
    before = param_data.param_frames['supply_price'].copy()
    years = sorted(set(before.index.get_level_values('year')))
    assert len(years) > 1, 'test config needs at least two years'
    covered_year = years[0]
    keys = [(r, y) for r, y in held_region_years(before) if y == covered_year]
    package = make_price_package(keys, INITIAL_NG_PRICE * 2)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        ElecUpdateReader().apply_package(package, param_data)

    after = param_data.param_frames['supply_price']
    in_year = after.index.get_level_values('year') == covered_year
    linked = after.index.get_level_values('tech').isin(NG_PRICE_LINKED_TECHS)
    pd.testing.assert_frame_equal(after[~in_year], before[~in_year])
    pd.testing.assert_frame_equal(
        after[in_year & linked], before[in_year & linked] * (1 + PRICE_COST_PROPORTION)
    )
    assert any('does not cover' in r.getMessage() for r in caplog.records)


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
