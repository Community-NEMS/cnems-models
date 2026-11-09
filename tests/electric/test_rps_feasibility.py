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
from typing import Any

import pytest

import main
from src.common.models_modes import ModelType
from src.models.electricity.rps_feasibility import (
    DISJOINT_SHARES_OVER_1,
    EXISTING_ONLY,
    NO_GROUP_CAPACITY,
    NO_MEMBER_BUILDABLE,
    PRIOR_BUILDS_ONLY,
    RLL_HOLDS_RETIREMENTS,
    RPS_FEASIBILITY_FILE,
    UNAVAILABLE,
    FeasibilityReport,
    _ranges,
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: MiniModelData, **switches: Any
) -> FeasibilityReport:
    """Load the mini data (validation non-strict, as the run mode does) and report on it."""
    patch_loaders(monkeypatch, data)
    common_config, elec_config = mini_configs(tmp_path, data.years, **(RPS_ON | switches))
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

    Existing generator MW:  R1 100 Gas + 50 Solar; R2 100 Gas.  Solar is buildable in both
    regions and years (2030, 2031).  Requirements:  RLL 0.25 in R1 from 2030, superseded in 2031
    by a zero BTM; BTM 0.3 in R2 from 2031; RLL 0.5 over ``all`` from 2030 (existing share
    50 / 250, a retirement hold).
    """
    add_solar_expansion(mini_data)
    mini_data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    mini_data.add_rps_group('clean', techs=['Solar', 'Wind'])
    mini_data.add_rps_requirement('clean', 'R1', 'RLL', 2030, 0.25)
    mini_data.add_rps_requirement('clean', 'R1', 'BTM', 2031, 0.0)
    mini_data.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.3)
    mini_data.add_rps_requirement('clean', 'all', 'RLL', 2030, 0.5)
    return mini_data


@pytest.mark.parametrize(
    ('years', 'expected'),
    [
        ([], '-'),
        ([2030], '2030'),
        ([2025, 2030, 2035, 2045], '2025–2035, 2045'),
        ([2035, 2025], '2025, 2035'),
    ],
    ids=['none', 'single', 'runs', 'gap'],
)
def test_ranges(years: list[int], expected: str) -> None:
    """Years are written as runs of consecutive model years."""
    assert _ranges(years, [2025, 2030, 2035, 2040, 2045, 2050]) == expected


def test_requirements_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """Every row is listed with its years in force and why it is not in force."""
    rps_data.add_rps_requirement('clean', 'R2', 'RLL', 2031, 0.1)  # the R2 BTM wins
    rps_data.add_rps_requirement('clean', 'R2', 'BTM', 2040, 0.5)
    report = _report(monkeypatch, tmp_path, rps_data)

    # min share, in force, note
    expected = {
        ('R1', 'RLL', '2030'): ['0.250', '2030', 'superseded from 2031 by BTM from 2031'],
        ('R1', 'BTM', '2031'): ['0.000', '2031', 'minimum 0:  no constraint'],
        ('R2', 'BTM', '2031'): ['0.300', '2031', ''],
        ('R2', 'RLL', '2031'): ['0.100', '-', 'never in force:  BTM from 2031 in force'],
        ('R2', 'BTM', '2040'): ['0.500', '-', 'starts after the last model year'],
        ('all', 'RLL', '2030'): ['0.500', '2030–2031', ''],
    }
    for leading, cells in expected.items():
        assert _row(report.text, 'clean', *leading)[4:] == cells
    assert report.requirement_rows == 6
    assert report.in_force == 5  # R1 x 2 years, R2 2031, all x 2 years


def test_filtered_region_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A row for a region filtered out of the run is listed, never in force."""
    report = _report(monkeypatch, tmp_path, rps_data, region_filter=['R1'])

    assert _row(report.text, 'clean', 'R2', 'BTM', '2031')[5:] == ['-', 'region not in this run']


def test_issues_and_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """Only problems and holds are listed:  the RLL hold and the unbuildable Wind in R2."""
    report = _report(monkeypatch, tmp_path, rps_data)

    # issues:  group, region key, type, years, status
    assert _row(report.text, 'clean', 'all', 'RLL', '2030–2031')[-1] == RLL_HOLDS_RETIREMENTS
    # coverage:  region, tech, not buildable in, status, required by
    assert _row(report.text, 'R2', 'Wind') == ['R2', 'Wind', '2031', UNAVAILABLE, 'clean@R2']
    # R2 Solar is buildable in every required year:  not listed
    assert not [
        line for line in report.text.splitlines() if line.startswith('| R2 ') and 'Solar' in line
    ]
    assert report.issues == {
        NO_GROUP_CAPACITY: 0,
        NO_MEMBER_BUILDABLE: 0,
        DISJOINT_SHARES_OVER_1: 0,
        RLL_HOLDS_RETIREMENTS: 2,
    }
    assert report.coverage == {PRIOR_BUILDS_ONLY: 0, EXISTING_ONLY: 0, UNAVAILABLE: 1}
    # both problem tables carry a legend
    assert len([line for line in report.text.splitlines() if line.startswith('| status ')]) == 2


def test_no_member_buildable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A short BTM whose only member is existing, not buildable, is an issue.

    R1 holds 50 MW Solar against 100 MW Gas (share 0.333), short of 0.5, and R1 Solar is made
    unbuildable.
    """
    rps_data.drop_rows('capital_cost', [('R1', 'Solar', 1, y) for y in rps_data.years])
    rps_data.add_rps_group('solar', techs=['Solar'])
    rps_data.add_rps_requirement('solar', 'R1', 'BTM', 2030, 0.5)
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _row(report.text, 'solar', 'R1', 'BTM', '2030–2031')[-1] == NO_MEMBER_BUILDABLE
    assert _row(report.text, 'R1', 'Solar') == [
        'R1',
        'Solar',
        '2030–2031',
        EXISTING_ONLY,
        'solar@R1',
    ]
    assert report.issues[NO_MEMBER_BUILDABLE] == 2


def test_prior_builds_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A member buildable only before a required year can meet it by building earlier."""
    rps_data.drop_rows('capital_cost', [('R2', 'Solar', 1, 2031)])
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _row(report.text, 'R2', 'Solar') == [
        'R2',
        'Solar',
        '2031',
        PRIOR_BUILDS_ONLY,
        'clean@R2',
    ]
    assert report.issues[NO_MEMBER_BUILDABLE] == 0  # an earlier build counts


def test_no_group_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A BTM no member can meet fails validation, but is still reported (non-strict)."""
    rps_data.add_rps_group('wind', techs=['Wind'])
    rps_data.add_rps_requirement('wind', 'R1', 'BTM', 2030, 0.1)
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _row(report.text, 'wind', 'R1', 'BTM', '2030–2031')[-1] == NO_GROUP_CAPACITY
    assert _row(report.text, 'R1', 'Wind')[2:] == ['2030–2031', UNAVAILABLE, 'wind@R1']
    assert report.issues[NO_GROUP_CAPACITY] == 2


def test_disjoint_shares_over_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """BTMs on disjoint groups summing past 1 (0.3 clean + 0.8 firm in R2) list both groups."""
    rps_data.add_rps_group('firm', techs=['Gas'])
    rps_data.add_rps_requirement('firm', 'R2', 'BTM', 2031, 0.8)
    report = _report(monkeypatch, tmp_path, rps_data)

    # issues:  group, region key, type, years, status
    for group in ('clean', 'firm'):
        _row(report.text, group, 'R2', 'BTM', '2031', DISJOINT_SHARES_OVER_1)
    assert report.issues[DISJOINT_SHARES_OVER_1] == 2


def test_zero_existing_member_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A 0 MW existing row, with nothing buildable, holds no capacity:  unavailable."""
    rps_data.add_capacity('Wind', 0.0, regions=['R2'])
    rps_data.add_operating_costs('Wind', generation_cost=0.0, fom_cost=5.0)
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _row(report.text, 'R2', 'Wind')[3] == UNAVAILABLE


def test_clean_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mini_data: MiniModelData
) -> None:
    """With nothing to flag, each problem table is replaced by a line saying so."""
    add_solar_expansion(mini_data)
    mini_data.add_rps_group('solar', techs=['Solar'])
    mini_data.add_rps_requirement('solar', 'R2', 'BTM', 2030, 0.3)
    report = _report(monkeypatch, tmp_path, mini_data)

    assert 'No requirement issues found.' in report.text
    assert 'Every BTM group member is buildable in every year it is required.' in report.text
    assert not [line for line in report.text.splitlines() if line.startswith('| status ')]
    assert sum(report.issues.values()) == sum(report.coverage.values()) == 0


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
    assert '4 RPS requirement row(s), 5 (group, region, year) in force' in caplog.text


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
