"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/29/26

Tests for the standalone dispatch in ``main.py``.
"""

from pathlib import Path
from typing import Any, ClassVar

import pytest

import main
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.common.models_modes import ModelType

CONFIG_PATH = Path(__file__).parent / 'electric' / 'basic_elec_config.toml'


class _RecordingSequencer:
    """Stand-in for ``MagicSequencer`` that records the steps run instead of solving."""

    calls: ClassVar[list[str]] = []

    def build_model(self, common_config: CommonConfig, model_config: Any) -> None:
        """Record the build."""
        self.calls.append('build_model')

    def solve_model(self) -> tuple[ModelType, IterationStatus]:
        """Record the solve and report success."""
        self.calls.append('solve_model')
        return ModelType.MAGIC, IterationStatus.USABLE

    def get_objective_value(self) -> None:
        """No objective."""
        return

    def full_postprocess(self) -> None:
        """Record the postprocess."""
        self.calls.append('full_postprocess')


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingSequencer]:
    """Swap ``MagicSequencer`` in ``main`` for :class:`_RecordingSequencer`, with a fresh log."""
    monkeypatch.setattr(_RecordingSequencer, 'calls', [])
    monkeypatch.setattr(main, 'MagicSequencer', _RecordingSequencer)
    return _RecordingSequencer


def _elec_remainder(remainder: dict, case: str) -> dict:
    """Return ``remainder`` with its ``elec_config`` section removed or made invalid."""
    remainder = dict(remainder)
    if case == 'missing':
        del remainder['elec_config']
    else:
        remainder['elec_config'] = {**remainder['elec_config'], 'capacity_expansion': 'not-a-bool'}
    return remainder


@pytest.mark.parametrize('case', ['missing', 'invalid'])
def test_bad_config_runs_nothing(
    case: str, recorder: type[_RecordingSequencer], caplog: pytest.LogCaptureFixture
) -> None:
    """A bad config for one model fails the run before any model, even a valid one, is built."""
    common_config, remainder = CommonConfig.from_toml(CONFIG_PATH)
    common_config.models_to_run = [ModelType.MAGIC, ModelType.ELECTRICITY]

    with pytest.raises(ValueError, match='no models were run'):
        main._run_standalone(common_config, _elec_remainder(remainder, case))

    assert recorder.calls == []
    assert 'ModelType.ELECTRICITY' in caplog.text


def test_good_configs_run(recorder: type[_RecordingSequencer]) -> None:
    """With every config valid, each model runs build -> solve -> postprocess."""
    common_config, remainder = CommonConfig.from_toml(CONFIG_PATH)
    common_config.models_to_run = [ModelType.MAGIC]

    main._run_standalone(common_config, remainder)

    assert recorder.calls == ['build_model', 'solve_model', 'full_postprocess']
