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

_init = CommonConfig.__init__


@pytest.fixture(autouse=True)
def redirect_run_output(tmp_path, monkeypatch):
    """Send every config built with a relative ``output_path`` to a per-test output directory.

    The test config files declare ``output_path='output'``, which resolves to the repo's real
    output root: a solving test that postprocesses would otherwise write its results into the
    same ``output/<scenario>/`` directory a production run uses, mixing test artifacts with real
    results. The redirect must land before validation, because
    :meth:`CommonConfig.ensure_unused_scenario_dir` reserves the scenario directory on
    construction. Wrapping ``__init__`` covers :meth:`CommonConfig.from_toml` (direct callers and
    the config fixtures) and direct construction; absolute output paths (e.g. tests that pass
    their own ``tmp_path``) are left alone. ``model_validate`` bypasses ``__init__`` and is not
    redirected.
    """

    def __init__(self, **data) -> None:
        if 'output_path' in data and not Path(data['output_path']).is_absolute():
            data['output_path'] = tmp_path / 'output'
        _init(self, **data)

    monkeypatch.setattr(CommonConfig, '__init__', __init__)
