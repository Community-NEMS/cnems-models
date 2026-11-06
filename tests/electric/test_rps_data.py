"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  11/5/26

Tests for the RPS data processing:  group and requirement resolution (``rps.py``), the RPS sets
and params built by ``ModelSets`` / ``ParamData``, and the RPS check in ``validate_all``.
"""

import logging
from pathlib import Path

import pytest

from src.common.exceptions import DataValidationError
from src.models.electricity.data_validation import validate_all, validate_rps_btm_sources
from src.models.electricity.elec_config import RPSRequirementType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.rps import (
    RPSRequirement,
    region_members,
    resolve_groups,
    resolve_requirements,
    rll_floors,
)
from tests.electric.mini_model import (
    MiniModelData,
    add_solar_expansion,
    mini_configs,
    patch_loaders,
)

BTM = RPSRequirementType.BTM
RLL = RPSRequirementType.RLL

TECH_FLAGS = {
    'tech': ['Gas', 'Solar', 'Wind', 'Batt'],
    'is_vre': ['Solar', 'Wind'],
    'is_storage': ['Batt'],
    'is_hydro': [],
}
GENERATORS = {'Gas', 'Solar', 'Wind'}

YEARS = (2030, 2031, 2032)


def _resolve(raw: dict, analyze: tuple[str, ...] = ('R1', 'R2')) -> dict:
    """Run resolve_requirements against a fixed region / group / year layout."""
    return resolve_requirements(
        raw,
        groups={'clean', 'gas'},
        regions={'R1', 'R2', 'R3', 'X'},
        domestic={'R1', 'R2', 'R3'},
        region_analyze=analyze,
        model_years=YEARS,
    )


# ---------------------------------------------------------------- resolve_groups


def test_resolve_groups_is_additive() -> None:
    """Direct techs and tech_group members union; repeats are harmless."""
    rows = [('clean', 'Solar', ''), ('clean', '', 'is_vre'), ('gas', 'Gas', '')]
    assert resolve_groups(rows, TECH_FLAGS, GENERATORS) == {
        'clean': frozenset({'Solar', 'Wind'}),
        'gas': frozenset({'Gas'}),
    }


@pytest.mark.parametrize(
    ('row', 'match'),
    [
        (('', 'Solar', ''), 'missing group_id'),
        (('g', 'Solar', 'is_vre'), 'exactly one of tech / tech_group'),
        (('g', '', ''), 'exactly one of tech / tech_group'),
        (('g', 'Coal', ''), "unknown tech 'Coal'"),
        (('g', '', 'is_nuclear'), "unknown tech_group 'is_nuclear'"),
        (('g', '', 'tech'), "unknown tech_group 'tech'"),
        (('g', 'Batt', ''), r"non-generator members \['Batt'\]"),
        (('g', '', 'is_hydro'), 'has no member techs'),
    ],
    ids=[
        'no_group',
        'both',
        'neither',
        'unknown_tech',
        'unknown_flag',
        'master_column',
        'non_generator',
        'empty_group',
    ],
)
def test_resolve_groups_errors(row: tuple, match: str) -> None:
    """Each bad group row or group is reported."""
    with pytest.raises(ValueError, match=match):
        resolve_groups([row], TECH_FLAGS, GENERATORS)


def test_resolve_groups_reports_every_problem() -> None:
    """All problems are raised together, not just the first."""
    rows = [('g', 'Coal', ''), ('h', '', 'is_nuclear')]
    with pytest.raises(ValueError, match=r'2 problem\(s\) in rps_group'):
        resolve_groups(rows, TECH_FLAGS, GENERATORS)


# ---------------------------------------------------------------- resolve_requirements


@pytest.mark.parametrize(
    ('rows', 'expected'),
    [
        ({('BTM', 2031): 0.2}, {2031: (BTM, 0.2), 2032: (BTM, 0.2)}),
        (
            {('RLL', 2030): 0.4, ('BTM', 2031): 0.2},
            {2030: (RLL, 0.4), 2031: (BTM, 0.2), 2032: (BTM, 0.2)},
        ),
        (
            {('BTM', 2030): 0.2, ('RLL', 2031): 0.4},
            {2030: (BTM, 0.2), 2031: (BTM, 0.2), 2032: (BTM, 0.2)},
        ),
        (
            {('RLL', 2030): 0.2, ('RLL', 2032): 0.4},
            {2030: (RLL, 0.2), 2031: (RLL, 0.2), 2032: (RLL, 0.4)},
        ),
        (
            {('BTM', 2030): 0.2, ('btm', 2031): 0.3},
            {2030: (BTM, 0.2), 2031: (BTM, 0.3), 2032: (BTM, 0.3)},
        ),
    ],
    ids=['from_first_year', 'btm_after_rll', 'btm_trumps_later_rll', 'later_rll', 'later_btm'],
)
def test_requirement_in_force(rows: dict, expected: dict) -> None:
    """Latest first_year wins within a type, and an in-force BTM always beats RLL."""
    raw = {('clean', 'R1', type_name, year): share for (type_name, year), share in rows.items()}
    resolved = _resolve(raw)
    assert {
        year: (r.requirement_type, r.min_share) for (_, _, year), r in resolved.items()
    } == expected
    assert {(g, rk) for g, rk, _ in resolved} == {('clean', 'R1')}


@pytest.mark.parametrize(
    ('rows', 'message'),
    [
        (
            {('RLL', 2030): 0.4, ('BTM', 2031): 0.2},
            (
                'RLL requirement (group clean, region R1, from 2030) is superseded from 2031 by '
                'the BTM requirement from 2031'
            ),
        ),
        (
            {('BTM', 2030): 0.2, ('RLL', 2031): 0.4},
            (
                'RLL requirement (group clean, region R1, from 2031) is superseded from 2031 by '
                'the BTM requirement from 2030'
            ),
        ),
    ],
    ids=['by_later_btm', 'by_earlier_btm'],
)
def test_superseded_rll_logged(caplog: pytest.LogCaptureFixture, rows: dict, message: str) -> None:
    """A displaced RLL is logged at INFO."""
    raw = {('clean', 'R1', type_name, year): share for (type_name, year), share in rows.items()}
    with caplog.at_level(logging.INFO, logger='src.models.electricity.rps'):
        _resolve(raw)
    assert message in caplog.text


@pytest.mark.parametrize('region_type', ['RLL', 'BTM'])
def test_region_keys_are_independent(region_type: str) -> None:
    """A later BTM over ``all`` does not supersede an earlier requirement on one region.

    A region and ``all`` are separate requirements for the same group:  both stay in force.
    """
    raw = {('clean', 'R1', region_type, 2030): 0.2, ('clean', 'all', 'BTM', 2031): 0.1}
    region_req = RPSRequirement(RPSRequirementType(region_type), 0.2, 2030)
    assert _resolve(raw) == {
        ('clean', 'R1', 2030): region_req,
        ('clean', 'R1', 2031): region_req,
        ('clean', 'R1', 2032): region_req,
        ('clean', 'all', 2031): RPSRequirement(BTM, 0.1, 2031),
        ('clean', 'all', 2032): RPSRequirement(BTM, 0.1, 2031),
    }


@pytest.mark.parametrize(
    ('row', 'message'),
    [
        (('clean', 'R3', 'BTM', 2030), 'region R3 is not in this run; skipped'),
        (('clean', 'R1', 'BTM', 2040), 'starts in 2040, after the last model year; no effect'),
    ],
    ids=['filtered_region', 'after_last_year'],
)
def test_requirement_without_effect_logged(
    caplog: pytest.LogCaptureFixture, row: tuple, message: str
) -> None:
    """A filtered-out region or a too-late first year yields nothing, logged at INFO."""
    with caplog.at_level(logging.INFO, logger='src.models.electricity.rps'):
        assert _resolve({row: 0.2}) == {}
    assert message in caplog.text


@pytest.mark.parametrize(
    ('raw', 'match'),
    [
        ({('clean', 'R1', 'XYZ', 2030): 0.2}, "unknown requirement_type 'XYZ'"),
        ({('solar', 'R1', 'BTM', 2030): 0.2}, "unknown group 'solar'"),
        ({('clean', 'R9', 'BTM', 2030): 0.2}, "unknown region 'R9'"),
        ({('clean', 'X', 'BTM', 2030): 0.2}, "region 'X' is not domestic"),
        ({('clean', 'R1', 'BTM', 2030): 1.5}, r'outside \[0, 1\]'),
        ({('clean', 'R1', 'BTM', 2030): -0.1}, r'outside \[0, 1\]'),
        (
            {('clean', 'R1', 'BTM', 2030): 0.2, ('clean', 'R1', 'btm', 2030): 0.3},
            'duplicates another BTM row',
        ),
        ({}, 'no RPS requirements were found'),
    ],
    ids=[
        'bad_type',
        'unknown_group',
        'unknown_region',
        'international',
        'share_high',
        'share_low',
        'case_duplicate',
        'empty',
    ],
)
def test_resolve_requirements_errors(raw: dict, match: str) -> None:
    """Each bad requirement row is reported."""
    with pytest.raises(ValueError, match=match):
        _resolve(raw)


# ---------------------------------------------------------------- region keys and RLL floors


def test_region_members() -> None:
    """``all`` covers every analyzed region; a region covers itself."""
    assert region_members(['all', 'R1'], ['R2', 'R1']) == {'all': ('R1', 'R2'), 'R1': ('R1',)}


# existing MW:  R1 Gas 100 + Solar 50 (+ storage, which is not a generator); R2 Gas 100 + 50
EXISTING = {
    ('R1', 'Gas', 1, 2030): 100.0,
    ('R1', 'Solar', 1, 2030): 50.0,
    ('R1', 'Batt', 1, 2030): 1000.0,
    ('R2', 'Gas', 1, 2030): 100.0,
    ('R2', 'Gas', 2, 2030): 50.0,
}


@pytest.mark.parametrize(
    ('region', 'share', 'floor'),
    [
        ('R1', 0.25, 37.5),  # existing share 50/150 meets 0.25:  floor 0.25 * 150
        ('R1', 0.5, None),  # 50/150 is below 0.5:  not applied
        ('all', 0.1, 30.0),  # 50/300 meets 0.1:  floor 0.1 * 300
        ('all', 0.2, None),  # 50/300 is below 0.2
        ('R2', 0.0, None),  # a zero share can never bind:  not applied
    ],
)
def test_rll_floors(region: str, share: float, floor: float | None) -> None:
    """The floor is share x existing generator MW, kept only where the existing share meets it."""
    requirements = {
        ('clean', region, 2030): RPSRequirement(RLL, share, 2030),
        ('clean', region, 2031): RPSRequirement(BTM, 0.9, 2031),  # ignored:  not RLL
    }
    floors = rll_floors(requirements, {'clean': {'Solar'}}, ['R1', 'R2'], GENERATORS, EXISTING)
    assert floors == ({} if floor is None else {('clean', region, 2030): floor})


def test_rll_floor_without_generators(caplog: pytest.LogCaptureFixture) -> None:
    """With no existing generator capacity there is no share to hold:  not applied."""
    requirements = {('clean', 'R1', 2031): RPSRequirement(RLL, 0.1, 2030)}
    with caplog.at_level(logging.INFO, logger='src.models.electricity.rps'):
        assert rll_floors(requirements, {'clean': {'Solar'}}, ['R1'], GENERATORS, EXISTING) == {}
    assert 'n/a (no generators)' in caplog.text


def test_validate_rps_btm_sources() -> None:
    """Only a positive-share BTM with no group capacity fails."""
    sources = {('g', 'R1', 2030): [('R1', 'Solar', 1)], ('g', 'R2', 2030): []}
    assert validate_rps_btm_sources({('g', 'R1', 2030): 0.5, ('g', 'R2', 2030): 0.0}, sources)
    assert not validate_rps_btm_sources({('g', 'R2', 2030): 0.5}, sources)


# ---------------------------------------------------------------- on the mini model


def _build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: MiniModelData, rps: bool = True
) -> tuple[ModelSets, ParamData]:
    """Run ModelSets and ParamData on the mini data, with expansion (and RPS) on."""
    patch_loaders(monkeypatch, data)
    common_config, elec_config = mini_configs(
        tmp_path, data.years, capacity_expansion=True, rps_required=rps
    )
    model_sets = ModelSets(common_config, elec_config)
    return model_sets, ParamData(common_config, elec_config, model_sets)


@pytest.fixture
def rps_data(mini_data: MiniModelData) -> MiniModelData:
    """Solar existing in R1 (50 MW) and buildable everywhere; Gas 100 MW in both regions.

    Group ``clean`` is the VRE techs (Solar).  Requirements:  BTM 0.3 in R2 from 2031; RLL 0.25
    in R1 from 2030 (existing share 1/3, applied); RLL 0.5 over ``all`` (existing share 0.2, not
    applied).
    """
    add_solar_expansion(mini_data)
    mini_data.add_rps_group('clean', tech_groups=['is_vre'])
    mini_data.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.3)
    mini_data.add_rps_requirement('clean', 'R1', 'RLL', 2030, 0.25)
    mini_data.add_rps_requirement('clean', 'all', 'RLL', 2030, 0.5)
    return mini_data


def test_mini_rps_sets_and_params(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """The mini model's RPS sets and params, and they pass validation."""
    model_sets, param_data = _build(monkeypatch, tmp_path, rps_data)

    assert model_sets.rps_group_techs == {'clean': frozenset({'Solar'})}
    assert model_sets.rps_region_members == {'R1': ('R1',), 'R2': ('R2',), 'all': ('R1', 'R2')}
    # the "all" RLL stays in force for reporting, but its floor does not apply
    assert ('clean', 'all', 2030) in model_sets.rps_requirements
    assert model_sets.rps_btm_index == [('clean', 'R2', 2031)]
    assert model_sets.rps_rll_index == [('clean', 'R1', 2030), ('clean', 'R1', 2031)]

    assert param_data.param_dicts['rps_min_share'] == {('clean', 'R2', 2031): 0.3}
    assert param_data.param_dicts['rps_rll_floor'] == {
        ('clean', 'R1', 2030): 0.25 * 150.0,
        ('clean', 'R1', 2031): 0.25 * 150.0,
    }

    # step-blind summation sets, drawn from the capacity index (Solar is buildable in R2)
    assert model_sets.rps_group_sources[('clean', 'R2', 2031)] == [('R2', 'Solar', 1)]
    assert sorted(model_sets.rps_gen_sources[('R2', 2031)]) == [
        ('R2', 'Gas', 1),
        ('R2', 'Solar', 1),
    ]
    validate_all(model_sets, param_data)


def test_mini_rps_off_ignores_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """With rps_required off, the RPS tables are not processed."""
    model_sets, param_data = _build(monkeypatch, tmp_path, rps_data, rps=False)
    assert model_sets.rps_group_techs == {}
    assert model_sets.rps_btm_index == model_sets.rps_rll_index == []
    assert 'rps_min_share' not in param_data.param_dicts


def test_mini_rps_bad_group_fails_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A group naming an unknown tech stops ModelSets."""
    rps_data.add_rps_group('clean', techs=['Coal'])
    with pytest.raises(ValueError, match="unknown tech 'Coal'"):
        _build(monkeypatch, tmp_path, rps_data)


def test_mini_rps_btm_without_capacity_fails_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """A BTM group with no existing or buildable capacity fails validate_all."""
    rps_data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    rps_data.add_rps_group('wind', techs=['Wind'])
    rps_data.add_rps_requirement('wind', 'R1', 'BTM', 2030, 0.1)
    model_sets, param_data = _build(monkeypatch, tmp_path, rps_data)
    with pytest.raises(DataValidationError):
        validate_all(model_sets, param_data)
