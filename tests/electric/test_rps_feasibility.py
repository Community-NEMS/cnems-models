"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests of the RPS feasibility report (``rps_feasibility.py``) on the mini model, and of its
``main`` dispatch (``RunMode.RPS_FEASIBILITY``).
"""

import logging
from pathlib import Path

import pytest

import main
from src.common.models_modes import ModelType
from src.models.electricity.rps_feasibility import (
    EXISTING_ONLY,
    NO_GROUP_CAPACITY,
    RPS_FEASIBILITY_FILE,
    FeasibilityReport,
    _btm_status,
    rps_feasibility_report,
)
from src.models.electricity.sequencer import ElectricitySequencer
from tests.electric.mini_model import (
    MiniModelData,
    add_solar_expansion,
    mini_configs,
    patch_loaders,
)

RPS_ON = {'capacity_expansion': True, 'rps_required': True}


def _report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: MiniModelData
) -> FeasibilityReport:
    """Load the mini data (validation non-strict, as the run mode does) and report on it."""
    patch_loaders(monkeypatch, data)
    common_config, elec_config = mini_configs(tmp_path, data.years, **RPS_ON)
    model_sets, param_data = ElectricitySequencer().load_data(
        common_config, elec_config, strict=False
    )
    return rps_feasibility_report(model_sets, param_data)


def _row(text: str, *leading: str) -> list[str]:
    """The cells of the one table row whose leading cells are ``leading``."""
    rows = [
        cells
        for line in text.splitlines()
        if line.startswith('|')
        and (cells := [c.strip() for c in line.strip('|').split('|')])[: len(leading)]
        == list(leading)
    ]
    assert len(rows) == 1, (leading, rows)
    return rows[0]


@pytest.fixture
def rps_data(mini_data: MiniModelData) -> MiniModelData:
    """``add_solar_expansion`` plus an unbuildable Wind with no capacity; ``clean`` is the VRE.

    Existing generator MW:  R1 100 Gas + 50 Solar; R2 100 Gas.  Requirements:  RLL 0.25 in R1
    from 2030, superseded in 2031 by a zero BTM; BTM 0.3 in R2 from 2031; RLL 0.5 over ``all``
    from 2030 (existing share 50 / 250, not applied).
    """
    add_solar_expansion(mini_data)
    mini_data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    mini_data.add_rps_group('clean', tech_groups=['is_vre'])
    mini_data.add_rps_requirement('clean', 'R1', 'RLL', 2030, 0.25)
    mini_data.add_rps_requirement('clean', 'R1', 'BTM', 2031, 0.0)
    mini_data.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.3)
    mini_data.add_rps_requirement('clean', 'all', 'RLL', 2030, 0.5)
    return mini_data


def test_requirement_statuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """Each requirement in force is listed with its existing share and status."""
    report = _report(monkeypatch, tmp_path, rps_data)

    assert report.requirements == 5  # R1 x 2 years, R2 2031, all x 2 years
    assert _row(report.text, 'clean', 'R1', '2030')[3:] == [
        'RLL',
        '2030',
        '0.250',
        '0.333',
        'floor 37.5 MW',
    ]
    assert _row(report.text, 'clean', 'R1', '2031')[-1] == 'no constraint (minimum is 0)'
    # R2 holds no group capacity yet:  0.3 / 0.7 x 100 MW Gas = 42.9 MW short
    r2 = _row(report.text, 'clean', 'R2', '2031')
    assert r2[6] == '0.000'
    assert r2[7].startswith('needs builds (>= 42.9 MW more')
    for year in ('2030', '2031'):
        assert _row(report.text, 'clean', 'all', year)[6:] == [
            '0.200',
            'not applied (existing share below minimum)',
        ]
    assert report.needs_builds == 1
    assert report.needs_retirements == 0
    assert report.no_group_capacity == 0


def test_btm_coverage_flags_unavailable_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """Only the applied BTM (R2) is covered:  Solar is buildable there, Wind is unavailable."""
    report = _report(monkeypatch, tmp_path, rps_data)

    # existing MW, buildable in, existing only in, unavailable in, flag
    assert _row(report.text, 'clean', 'R2', 'R2', 'Solar')[4:] == ['0.0', 'all', '-', '-', '']
    assert _row(report.text, 'clean', 'R2', 'R2', 'Wind')[4:] == [
        '0.0',
        '-',
        '-',
        'all',
        'WARNING',
    ]
    assert report.unavailable_members == 1
    assert report.existing_only_members == 0
    # the zero BTM in R1 writes no constraint, so R1 has no coverage rows
    assert not any(' R1 ' in line and ' Solar ' in line for line in report.text.splitlines())


def test_btm_without_group_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A BTM no group member can meet fails validation, but is still reported (non-strict)."""
    rps_data.add_rps_group('wind', techs=['Wind'])
    rps_data.add_rps_requirement('wind', 'R1', 'BTM', 2030, 0.1)
    report = _report(monkeypatch, tmp_path, rps_data)

    for year in ('2030', '2031'):
        assert _row(report.text, 'wind', 'R1', year)[-1] == NO_GROUP_CAPACITY
    assert report.no_group_capacity == 2


def test_btm_with_existing_only_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A short BTM whose only member is existing, not buildable, needs other generators retired.

    R1 holds 50 MW Solar against 100 MW Gas (share 0.333).  With R1 Solar unbuildable, a 0.5 share
    needs the Gas cut to 50 MW:  50 MW retired.
    """
    rps_data.drop_rows('capital_cost', [('R1', 'Solar', 1, y) for y in rps_data.years])
    rps_data.add_rps_group('solar', techs=['Solar'])
    rps_data.add_rps_requirement('solar', 'R1', 'BTM', 2030, 0.5)
    report = _report(monkeypatch, tmp_path, rps_data)

    for year in ('2030', '2031'):
        assert _row(report.text, 'solar', 'R1', year)[6:] == [
            '0.333',
            'no group member buildable:  needs >= 50.0 MW of other generators retired',
        ]
    assert report.needs_retirements == 2
    assert report.needs_builds == 1  # the R2 BTM, unchanged
    # existing MW, buildable in, existing only in, unavailable in, flag
    assert _row(report.text, 'solar', 'R1', 'R1', 'Solar')[4:] == [
        '50.0',
        '-',
        'all',
        '-',
        EXISTING_ONLY,
    ]
    assert report.existing_only_members == 1


@pytest.mark.parametrize(
    ('share', 'group_mw', 'total_mw', 'buildable', 'expected'),
    [
        (0.3, 50.0, 100.0, False, ('met by existing', None)),
        (0.5, 50.0, 150.0, True, ('needs builds (>= 50.0 MW more, others at existing)', 'builds')),
        (
            0.5,
            50.0,
            150.0,
            False,
            (
                'no group member buildable:  needs >= 50.0 MW of other generators retired',
                'retirements',
            ),
        ),
        (0.5, 0.0, 100.0, False, ('needs all other generators retired', 'retirements')),
        (1.0, 50.0, 150.0, True, ('needs all other generators retired', 'retirements')),
    ],
    ids=['met', 'builds', 'retirements', 'empty_group', 'full_share'],
)
def test_btm_status(
    share: float,
    group_mw: float,
    total_mw: float,
    buildable: bool,
    expected: tuple[str, str | None],
) -> None:
    """The BTM status and shortfall kind against the existing capacity."""
    assert _btm_status(share, group_mw, total_mw, buildable) == expected


# ---------------------------------------------------------------- main dispatch


def test_run_mode_writes_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rps_data: MiniModelData,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The run mode writes the report to the scenario folder; other models are skipped."""
    patch_loaders(monkeypatch, rps_data)
    common_config, elec_config = mini_configs(tmp_path, rps_data.years, **RPS_ON)
    common_config.models_to_run = [ModelType.ELECTRICITY, ModelType.MAGIC]
    common_config.make_scenario_dir()

    with caplog.at_level(logging.INFO):
        path = main._run_rps_feasibility(common_config, {'elec_config': elec_config.model_dump()})

    assert path == common_config.output_folder / RPS_FEASIBILITY_FILE
    assert path.read_text().startswith('RPS feasibility report')
    assert 'ModelType.MAGIC has no RPS feasibility check' in caplog.text
    assert '5 RPS requirement(s) in force' in caplog.text


@pytest.mark.parametrize(
    ('models', 'switches', 'match'),
    [
        ([ModelType.MAGIC], RPS_ON, 'needs electricity in models_to_run'),
        ([ModelType.ELECTRICITY], {'capacity_expansion': True}, 'needs rps_required'),
    ],
    ids=['no_electricity', 'rps_off'],
)
def test_run_mode_errors(
    tmp_path: Path, models: list[ModelType], switches: dict, match: str
) -> None:
    """The run mode needs electricity, with RPS on."""
    common_config, elec_config = mini_configs(tmp_path, [2030], **switches)
    common_config.models_to_run = models
    with pytest.raises(ValueError, match=match):
        main._run_rps_feasibility(common_config, {'elec_config': elec_config.model_dump()})
