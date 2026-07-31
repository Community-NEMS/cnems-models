"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Tests for src.models.electricity.property_source_loader and its resulting PROPERTY_SOURCES dict
"""

from definitions import PROJECT_ROOT
from src.models.electricity import data_ingestor
from src.models.electricity.property_source_loader import PropertySource, load_property_sources

PROPERTY_SOURCES_TOML = PROJECT_ROOT / 'src/models/electricity/property_sources.toml'

# Transcription reference: the original PROPERTY_SOURCES dict literal, verbatim, before the
# TOML migration. Used only to audit that no data was lost/reordered in the conversion.
_ORIGINAL_PROPERTY_SOURCES = {
    'tech_data': (
        'tech_data.csv',
        [
            'tech',
            'T_conv',
            'T_re',
            'T_hydro',
            'T_stor',
            'T_vre',
            'T_wind',
            'T_solar',
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


def test_load_property_sources_count_and_keys():
    """All 4 entries load, keyed by the same names as the original dict."""
    loaded = load_property_sources(PROPERTY_SOURCES_TOML)

    assert len(loaded) == len(_ORIGINAL_PROPERTY_SOURCES) == 4
    assert set(loaded.keys()) == set(_ORIGINAL_PROPERTY_SOURCES.keys())


def test_load_property_sources_transcription_audit():
    """Every filename/property_cols/index_cols matches the original tuple exactly."""
    loaded = load_property_sources(PROPERTY_SOURCES_TOML)

    for key, (filename, property_cols, index_cols) in _ORIGINAL_PROPERTY_SOURCES.items():
        source = loaded[key]
        assert source.key == key
        assert source.filename == filename
        assert source.property_cols == tuple(property_cols)
        assert source.index_cols == index_cols


def test_load_property_sources_types():
    """Every entry is a PropertySource with tuple property_cols/index_cols."""
    loaded = load_property_sources(PROPERTY_SOURCES_TOML)

    for source in loaded.values():
        assert isinstance(source, PropertySource)
        assert isinstance(source.property_cols, tuple)
        assert isinstance(source.index_cols, tuple)


def test_data_ingestor_property_sources_matches_loader():
    """data_ingestor.PROPERTY_SOURCES (built at import time) matches a direct load."""
    assert data_ingestor.PROPERTY_SOURCES == load_property_sources(PROPERTY_SOURCES_TOML)
