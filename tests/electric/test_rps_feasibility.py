"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests of the RPS feasibility report (``rps_feasibility.py``) on the mini model, and of its
``main`` dispatch (``RunMode.RPS_FEASIBILITY``).
"""

from pathlib import Path
from typing import Any

import pytest

import main
from src.common.models_modes import ModelType
from src.models.electricity.rps_feasibility import (
    DISJOINT_SHARES_OVER_1,
    EXISTING_ONLY,
    NEEDS_BUILDS,
    NEEDS_EARLY_BUILDS,
    NO_GROUP_CAPACITY,
    PRIOR_BUILDS_ONLY,
    UNAVAILABLE,
    UNMEETABLE,
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

    Annual load:  R1 320 MWh, R2 400 MWh.  Solar yields 1 MWh per MW a year; R1 has 50 MW of
    it, and it is buildable in both regions and years (2030, 2031).  Requirements:  BTM 0.1 in
    R1 from 2030, superseded in 2031 by a zero BTM; BTM 0.3 in R2 from 2031 (120 MWh, none
    existing); BTM 0.1 over ``all`` from 2030 (72 MWh against 50 existing).
    """
    add_solar_expansion(mini_data)
    mini_data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    mini_data.add_rps_group('clean', techs=['Solar', 'Wind'])
    mini_data.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.1)
    mini_data.add_rps_requirement('clean', 'R1', 'BTM', 2031, 0.0)
    mini_data.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.3)
    mini_data.add_rps_requirement('clean', 'all', 'BTM', 2030, 0.1)
    return mini_data


def _gap(text: str, group: str, rk: str, year: int) -> list[str]:
    """The energy gap row of (group, region key, year), after its leading cells.

    Required, existing max, gap, status, closing build; ``StopIteration`` if there is none.
    """
    return next(
        cells[3:]
        for line in text.splitlines()
        if line.startswith('|')
        and (cells := [c.strip() for c in line.strip('|').split('|')])[:3] == [group, rk, str(year)]
        and len(cells) == 8
    )


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
    rps_data.add_rps_requirement('clean', 'R2', 'BTM', 2040, 0.5)
    report = _report(monkeypatch, tmp_path, rps_data)

    # min share, in force, note
    expected = {
        ('R1', 'BTM', '2030'): ['0.100', '2030', 'superseded from 2031 by BTM from 2031'],
        ('R1', 'BTM', '2031'): ['0.000', '2031', 'minimum 0:  no constraint'],
        ('R2', 'BTM', '2031'): ['0.300', '2031', ''],
        ('R2', 'BTM', '2040'): ['0.500', '-', 'starts after the last model year'],
        ('all', 'BTM', '2030'): ['0.100', '2030–2031', ''],
    }
    for leading, cells in expected.items():
        assert _row(report.text, 'clean', *leading)[4:] == cells
    assert report.requirement_rows == 5
    assert report.in_force == 5  # R1 x 2 years, R2 2031, all x 2 years


def test_filtered_region_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A row for a region filtered out of the run is listed, never in force."""
    report = _report(monkeypatch, tmp_path, rps_data, region_filter=['R1'])

    assert _row(report.text, 'clean', 'R2', 'BTM', '2031')[5:] == ['-', 'region not in this run']


def test_energy_gaps_and_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """Only shortfalls are listed, with the build that closes them, and the unbuildable Wind.

    R1's 32 MWh is met by its 50 MW Solar:  not listed.  R2 needs 120 MWh from nothing; ``all``
    needs 72 MWh against 50.  Solar is buildable everywhere, so both need builds.
    """
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _gap(report.text, 'clean', 'R2', 2031) == [
        '120',
        '0',
        '120',
        NEEDS_BUILDS,
        '120 MW at (R2, Solar)',
    ]
    for year in rps_data.years:
        required, existing, gap, status, closing = _gap(report.text, 'clean', 'all', year)
        assert (required, existing, gap, status) == ('72', '50', '22', NEEDS_BUILDS)
        assert closing.startswith('22 MW at (') and closing.endswith(', Solar)')
    with pytest.raises(StopIteration):
        _gap(report.text, 'clean', 'R1', 2030)
    assert report.gaps == {NEEDS_BUILDS: 3, NEEDS_EARLY_BUILDS: 0, UNMEETABLE: 0}

    # coverage:  region, tech, not buildable in, status, required by
    assert _row(report.text, 'R1', 'Wind') == [
        'R1',
        'Wind',
        '2030–2031',
        UNAVAILABLE,
        'clean@R1, clean@all',
    ]
    assert _row(report.text, 'R2', 'Wind') == [
        'R2',
        'Wind',
        '2030–2031',
        UNAVAILABLE,
        'clean@R2, clean@all',
    ]
    assert report.coverage == {PRIOR_BUILDS_ONLY: 0, EXISTING_ONLY: 0, UNAVAILABLE: 2}
    assert 'No requirement issues found.' in report.text
    # the gap and coverage tables carry a legend
    assert len([line for line in report.text.splitlines() if line.startswith('| status ')]) == 2


def test_unmeetable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A shortfall with no member buildable is unmeetable.

    R1 needs 0.5 x 320 = 160 MWh of Solar; its 50 MW makes 50, and R1 Solar is made unbuildable.
    """
    rps_data.drop_rows('capital_cost', [('R1', 'Solar', 1, y) for y in rps_data.years])
    rps_data.add_rps_group('solar', techs=['Solar'])
    rps_data.add_rps_requirement('solar', 'R1', 'BTM', 2030, 0.5)
    report = _report(monkeypatch, tmp_path, rps_data)

    for year in rps_data.years:
        assert _gap(report.text, 'solar', 'R1', year) == ['160', '50', '110', UNMEETABLE, '']
    assert _row(report.text, 'R1', 'Solar') == [
        'R1',
        'Solar',
        '2030–2031',
        EXISTING_ONLY,
        'clean@R1, clean@all, solar@R1',
    ]
    assert report.gaps[UNMEETABLE] == 2


def test_needs_early_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A member buildable only before a required year can close the gap by building earlier."""
    rps_data.drop_rows('capital_cost', [('R2', 'Solar', 1, 2031)])
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _gap(report.text, 'clean', 'R2', 2031)[3:] == [
        NEEDS_EARLY_BUILDS,
        '120 MW at (R2, Solar)',
    ]
    assert _row(report.text, 'R2', 'Solar') == [
        'R2',
        'Solar',
        '2031',
        PRIOR_BUILDS_ONLY,
        'clean@R2, clean@all',
    ]


def test_without_expansion_nothing_is_buildable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """With the fleet fixed, every shortfall is unmeetable."""
    report = _report(monkeypatch, tmp_path, rps_data, capacity_expansion=False)

    assert _gap(report.text, 'clean', 'R2', 2031)[3] == UNMEETABLE
    assert _gap(report.text, 'clean', 'all', 2030)[3] == UNMEETABLE
    assert report.gaps[NEEDS_BUILDS] == report.gaps[NEEDS_EARLY_BUILDS] == 0


def test_no_group_capacity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A BTM no member can generate for fails validation, but is still reported (non-strict)."""
    rps_data.add_rps_group('wind', techs=['Wind'])
    rps_data.add_rps_requirement('wind', 'R1', 'BTM', 2030, 0.1)
    report = _report(monkeypatch, tmp_path, rps_data)

    assert _row(report.text, 'wind', 'R1', '2030–2031')[-1] == NO_GROUP_CAPACITY
    assert _gap(report.text, 'wind', 'R1', 2030) == ['32', '0', '32', UNMEETABLE, '']
    assert report.issues[NO_GROUP_CAPACITY] == 2


def test_disjoint_shares_over_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """BTMs on disjoint groups summing past 1 (0.3 clean + 0.8 firm in R2) list both groups."""
    rps_data.add_rps_group('firm', techs=['Gas'])
    rps_data.add_rps_requirement('firm', 'R2', 'BTM', 2031, 0.8)
    report = _report(monkeypatch, tmp_path, rps_data)

    # issues:  group, region key, years, status
    for group in ('clean', 'firm'):
        _row(report.text, group, 'R2', '2031', DISJOINT_SHARES_OVER_1)
    assert report.issues[DISJOINT_SHARES_OVER_1] == 2


def test_clean_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mini_data: MiniModelData
) -> None:
    """With nothing to flag, each problem table is replaced by a line saying so."""
    add_solar_expansion(mini_data)
    mini_data.add_rps_group('solar', techs=['Solar'])
    mini_data.add_rps_requirement('solar', 'R1', 'BTM', 2030, 0.1)
    report = _report(monkeypatch, tmp_path, mini_data)

    assert 'No requirement issues found.' in report.text
    assert 'The existing group fleet can generate every requirement in force.' in report.text
    assert 'Every BTM group member is buildable in every year it is required.' in report.text
    assert not [line for line in report.text.splitlines() if line.startswith('| status ')]
    assert sum(report.issues.values()) == sum(report.gaps.values()) == 0
    assert sum(report.coverage.values()) == 0


# ---------------------------------------------------------------- main dispatch


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
