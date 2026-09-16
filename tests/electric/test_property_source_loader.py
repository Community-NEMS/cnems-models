"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Tests for src.models.electricity.property_source_loader and its resulting PROPERTY_SOURCES dict
"""

import pytest

from definitions import PROJECT_ROOT
from src.models.electricity import data_ingestor
from src.models.electricity.property_source_loader import PropertySource, load_property_sources

PROPERTY_SOURCES_TOML = PROJECT_ROOT / 'src/models/electricity/property_sources.toml'

# Transcription reference: the original PROPERTY_SOURCES dict literal, verbatim, before the
# TOML migration. Used only to audit that no data was lost/reordered in the conversion.
#
# 'T_hydro_seasonal'/'T_hydro_regular' post-date that snapshot: they were added when hydro was
# split into a seasonally-budgeted and an hourly-limited technology, replacing the supply-curve
# step numbers that used to select between the two hydro bounds.  'T_solar_utility' and
# 'T_solar_end_use' likewise post-date it, from the equivalent split of solar into utility-scale
# and end-use.
_ORIGINAL_PROPERTY_SOURCES = {
    'tech_data': (
        'tech_data.csv',
        [
            'tech',
            'T_conv',
            'T_re',
            'T_hydro',
            'T_hydro_seasonal',
            'T_hydro_regular',
            'T_stor',
            'T_vre',
            'T_wind',
            'T_solar',
            'T_solar_utility',
            'T_solar_end_use',
            'T_h2',
            'T_disp',
            'T_gen',
        ],
        ('tech',),
    ),
    'buildable_techs': ('build_data.csv', ['builds'], ('tech', 'step')),
    'retireable_techs': ('retire_data.csv', ['retires'], ('tech', 'step')),
    'region_data': ('region_data.csv', ['region', 'domestic', 'international'], ('region',)),
}


@pytest.fixture(scope='module')
def loaded_property_sources() -> dict[str, PropertySource]:
    """PROPERTY_SOURCES_TOML loaded once per module."""
    return load_property_sources(PROPERTY_SOURCES_TOML)


def test_load_property_sources_count_and_keys(
    loaded_property_sources: dict[str, PropertySource],
) -> None:
    """All 4 entries load, keyed by the same names as the original dict."""
    assert len(loaded_property_sources) == len(_ORIGINAL_PROPERTY_SOURCES) == 4
    assert set(loaded_property_sources.keys()) == set(_ORIGINAL_PROPERTY_SOURCES.keys())


def test_load_property_sources_transcription_audit(
    loaded_property_sources: dict[str, PropertySource],
) -> None:
    """Every filename/property_cols/index_cols matches the original tuple exactly."""
    for key, (filename, property_cols, index_cols) in _ORIGINAL_PROPERTY_SOURCES.items():
        source = loaded_property_sources[key]
        assert source.key == key
        assert source.filename == filename
        assert source.property_cols == tuple(property_cols)
        assert source.index_cols == index_cols


def test_load_property_sources_types(loaded_property_sources: dict[str, PropertySource]) -> None:
    """Every entry is a PropertySource with tuple property_cols/index_cols."""
    for source in loaded_property_sources.values():
        assert isinstance(source, PropertySource)
        assert isinstance(source.property_cols, tuple)
        assert isinstance(source.index_cols, tuple)


def test_data_ingestor_property_sources_matches_loader(
    loaded_property_sources: dict[str, PropertySource],
) -> None:
    """data_ingestor.PROPERTY_SOURCES (built at import time) matches a direct load."""
    assert data_ingestor.PROPERTY_SOURCES == loaded_property_sources


@pytest.mark.parametrize(
    ('key', 'expected'),
    [
        ('tech_data', ('steps', 'label', 'abbreviation', 'color')),
        ('buildable_techs', ()),
        ('retireable_techs', ()),
        ('region_data', ()),
    ],
)
def test_tech_data_attribute_cols(
    loaded_property_sources: dict[str, PropertySource], key: str, expected: tuple[str, ...]
) -> None:
    """tech_data declares the descriptive columns; the other sources have none."""
    assert loaded_property_sources[key].attribute_cols == expected
