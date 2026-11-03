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

# Expected reference, per the C-EMM data-needs sheet: each file is named for its key and the
# membership flags read as ``is_*``.  The hydro and solar sub-flags are not on the sheet; they
# follow the same convention.
_ORIGINAL_PROPERTY_SOURCES = {
    'tech': (
        'tech.csv',
        [
            'tech',
            'is_conventional',
            'is_renewable',
            'is_hydro',
            'is_hydro_seasonal',
            'is_hydro_regular',
            'is_storage',
            'is_vre',
            'is_wind',
            'is_solar',
            'is_solar_utility',
            'is_solar_end_use',
            'is_hydrogen',
            'is_dispatchable',
            'is_generator',
        ],
        ('tech',),
    ),
    'tech_build': ('tech_build.csv', ['is_buildable'], ('tech', 'step')),
    'tech_retire': ('tech_retire.csv', ['is_retirable'], ('tech', 'step')),
    'region': ('region.csv', ['region', 'is_domestic', 'is_international'], ('region',)),
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
        ('tech', ('steps', 'label', 'abbreviation', 'color')),
        ('tech_build', ()),
        ('tech_retire', ()),
        ('region', ('label',)),
    ],
)
def test_attribute_cols(
    loaded_property_sources: dict[str, PropertySource], key: str, expected: tuple[str, ...]
) -> None:
    """The tech and region sources declare descriptive columns; the others have none."""
    assert loaded_property_sources[key].attribute_cols == expected
