"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

Session-wide test fixtures.
"""

from pathlib import Path

import pytest

from src.common.common_config import CommonConfig

_from_toml = CommonConfig.from_toml.__func__


@pytest.fixture(autouse=True)
def redirect_run_output(tmp_path, monkeypatch):
    """Send every config parsed from a TOML to a per-test output directory.

    The test config files declare ``output_path='output'``, which resolves to the repo's real
    output root: a solving test that postprocesses would otherwise write its results into the
    same ``output/<scenario>/`` directory a production run uses, mixing test artifacts with real
    results. Patching the parse (rather than a fixture) covers the tests that call
    :meth:`CommonConfig.from_toml` directly as well as those that take the config fixtures.
    """

    def from_toml(cls, path: Path) -> tuple[CommonConfig, dict]:
        config, remainder = _from_toml(cls, path)
        config.output_path = tmp_path / 'output'
        return config, remainder

    monkeypatch.setattr(CommonConfig, 'from_toml', classmethod(from_toml))
