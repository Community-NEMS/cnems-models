"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Loader for policy-source schema metadata from policy_sources.toml.
This mirrors the ParamSource loader and will likely grow

Policy tables (e.g. RPS requirements) live in ``ElecConfig.policy_path`` and share the
parameter-source shape:  a composite index mapped to one value column.
"""

import tomllib
from logging import getLogger
from pathlib import Path

from pydantic import BaseModel, ValidationError

from src.models.electricity.param_source_loader import ParamSource

logger = getLogger(__name__)


class PolicySource(ParamSource):
    """Schema metadata for one policy-source CSV consumed by data_ingestor.py.

    Same fields as ``ParamSource``; ``filename`` is relative to ``ElecConfig.policy_path``.
    """


class _PolicySourceFile(BaseModel):
    """Top-level shape of policy_sources.toml: a list of [[policy_source]] tables."""

    policy_source: list[PolicySource]


def load_policy_sources(path: Path) -> dict[str, PolicySource]:
    """Read policy_sources.toml and build a dict keyed by each entry's `key`.

    Parameters
    ----------
    path : Path
        Path to the TOML file (array-of-tables under ``[[policy_source]]``).

    Returns
    -------
    dict[str, PolicySource]
        Entry key -> PolicySource.

    Raises
    ------
    ValueError
        if two entries share a ``key``.
    """
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    try:
        parsed = _PolicySourceFile(**data)
    except ValidationError as e:
        for error in e.errors():
            logger.error(error)
        raise
    res: dict[str, PolicySource] = {}
    for source in parsed.policy_source:
        if source.key in res:
            msg = f'Duplicate policy_source key {source.key!r} in {path}'
            logger.error(msg)
            raise ValueError(msg)
        res[source.key] = source
    return res
