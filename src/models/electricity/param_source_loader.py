"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/5/26

Loader for parameter-source schema metadata from param_sources.toml.

Reads the fixed, developer-owned mapping of PARAM_SOURCES keys to their backing CSV filename,
index columns, value column, and whether the source is required regardless of ElecConfig switches.
"""

from logging import getLogger
from pathlib import Path

import tomllib
from pydantic import BaseModel, ValidationError

logger = getLogger(__name__)


class ParamSource(BaseModel):
    """Schema metadata for one parameter-source CSV consumed by data_ingestor.py.

    Parameters
    ----------
    key : str
        The lookup key used elsewhere (e.g. in ``PARAM_SOURCES``, ``param_data.py``).
    filename : str
        CSV filename, relative to the electricity input directory.
    index_cols : tuple[str, ...]
        Column names to use as the composite index, in order.
    value_col : str
        Column holding the parameter's value.
    required : bool
        Whether this file is needed regardless of ElecConfig switches (True), or only under a
        specific feature configuration (False).
    """

    key: str
    filename: str
    index_cols: tuple[str, ...]
    value_col: str
    required: bool = True


class _ParamSourceFile(BaseModel):
    """Top-level shape of param_sources.toml: a list of [[param_source]] tables."""

    param_source: list[ParamSource]


def load_param_sources(path: Path) -> dict[str, ParamSource]:
    """Read param_sources.toml and build a dict keyed by each entry's `key`.

    Parameters
    ----------
    path : Path
        Path to the TOML file (array-of-tables under ``[[param_source]]``).

    Returns
    -------
    dict[str, ParamSource]
        Entry key -> ParamSource.
    """
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    try:
        parsed = _ParamSourceFile(**data)
    except KeyError:
        logger.error('[param_source] tables not found in TOML: %s', path)
        raise
    except ValidationError as e:
        for error in e.errors():
            logger.error(error)
        raise
    return {source.key: source for source in parsed.param_source}
