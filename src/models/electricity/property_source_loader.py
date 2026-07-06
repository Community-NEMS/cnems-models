"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Loader for property-source schema metadata from property_sources.toml.

Reads the fixed, developer-owned mapping of PROPERTY_SOURCES keys to their backing CSV filename,
the property columns to pivot into truthy-membership sets, and the basis/index columns.
"""

from logging import getLogger
from pathlib import Path

import tomllib
from pydantic import BaseModel, ValidationError

logger = getLogger(__name__)


class PropertySource(BaseModel):
    """Schema metadata for one property-source CSV consumed by data_ingestor.py.

    Parameters
    ----------
    key : str
        The lookup key used elsewhere (e.g. in ``PROPERTY_SOURCES``, ``model_sets.py``).
    filename : str
        CSV filename, relative to the electricity input directory.
    property_cols : tuple[str, ...]
        Columns to pivot into truthy-membership sets (see ``read_property_csv``).
    index_cols : tuple[str, ...]
        Basis/index column(s) identifying each row.
    """

    key: str
    filename: str
    property_cols: tuple[str, ...]
    index_cols: tuple[str, ...]


class _PropertySourceFile(BaseModel):
    """Top-level shape of property_sources.toml: a list of [[property_source]] tables."""

    property_source: list[PropertySource]


def load_property_sources(path: Path) -> dict[str, PropertySource]:
    """Read property_sources.toml and build a dict keyed by each entry's `key`.

    Parameters
    ----------
    path : Path
        Path to the TOML file (array-of-tables under ``[[property_source]]``).

    Returns
    -------
    dict[str, PropertySource]
        Entry key -> PropertySource.
    """
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    try:
        parsed = _PropertySourceFile(**data)
    except KeyError:
        logger.error('[property_source] tables not found in TOML: %s', path)
        raise
    except ValidationError as e:
        for error in e.errors():
            logger.error(error)
        raise
    return {source.key: source for source in parsed.property_source}
