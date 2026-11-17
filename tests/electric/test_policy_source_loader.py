"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests for src.models.electricity.policy_source_loader, run on scratch TOML files rather than the
shipped policy_sources.toml.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models.electricity.policy_source_loader import PolicySource, load_policy_sources

ENTRY = """
[[policy_source]]
key = "{key}"
filename = "{key}.csv"
index_cols = ["group_id", "region"]
value_col = "share_frac"
"""


def _write_toml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / 'policy_sources.toml'
    path.write_text(text)
    return path


def test_load_policy_sources(tmp_path: Path) -> None:
    """Entries parse into PolicySources keyed by ``key``, with a tuple index and required=True."""
    path = _write_toml(tmp_path, ENTRY.format(key='a') + ENTRY.format(key='b'))
    loaded = load_policy_sources(path)
    assert list(loaded) == ['a', 'b']
    assert loaded['a'] == PolicySource(
        key='a',
        filename='a.csv',
        index_cols=('group_id', 'region'),
        value_col='share_frac',
        required=True,
    )


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    """A repeated ``key`` is an error rather than a silent overwrite."""
    path = _write_toml(tmp_path, ENTRY.format(key='a') * 2)
    with pytest.raises(ValueError, match="Duplicate policy_source key 'a'"):
        load_policy_sources(path)


def test_malformed_entry_rejected(tmp_path: Path) -> None:
    """An entry missing a field fails validation."""
    path = _write_toml(tmp_path, ENTRY.format(key='a').replace('value_col = "share_frac"\n', ''))
    with pytest.raises(ValidationError):
        load_policy_sources(path)
