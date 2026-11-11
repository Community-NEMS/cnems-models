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
from src.models.electricity.data_validation import (
    validate_all,
    validate_rps_btm_sources,
    validate_rps_disjoint_shares,
)
from src.models.electricity.elec_config import RPSRequirementType
from src.models.electricity.model_sets import ModelSets
from src.models.electricity.param_data import ParamData
from src.models.electricity.rps import (
    RPSRequirement,
    disjoint_share_conflicts,
    region_members,
    resolve_groups,
    resolve_requirements,
)
from tests.electric.mini_model import (
    MiniModelData,
    add_solar_expansion,
    mini_configs,
    patch_loaders,
)

BTM = RPSRequirementType.BTM

TECHS = ['Gas', 'Solar', 'Wind', 'Batt']
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
    """A group is the union of its rows."""
    rows = [('clean', 'Solar'), ('clean', 'Wind'), ('gas', 'Gas')]
    assert resolve_groups(rows, TECHS, GENERATORS) == {
        'clean': frozenset({'Solar', 'Wind'}),
        'gas': frozenset({'Gas'}),
    }


@pytest.mark.parametrize(
    ('row', 'match'),
    [
        (('', 'Solar'), 'missing group_id'),
        (('g', ''), 'missing tech'),
        (('g', 'Coal'), "unknown tech 'Coal'"),
        (('g', 'Batt'), r"non-generator members \['Batt'\]"),
    ],
    ids=['no_group', 'no_tech', 'unknown_tech', 'non_generator'],
)
def test_resolve_groups_errors(row: tuple, match: str) -> None:
    """Each bad group row or group is reported."""
    with pytest.raises(ValueError, match=match):
        resolve_groups([row], TECHS, GENERATORS)


def test_resolve_groups_reports_every_problem() -> None:
    """All problems are raised together, not just the first."""
    rows = [('g', 'Coal'), ('h', '')]
    with pytest.raises(ValueError, match=r'2 problem\(s\) in rps_group'):
        resolve_groups(rows, TECHS, GENERATORS)


# ---------------------------------------------------------------- resolve_requirements


@pytest.mark.parametrize(
    ('rows', 'expected'),
    [
        ({2031: 0.2}, {2031: 0.2, 2032: 0.2}),
        ({2030: 0.2, 2031: 0.3}, {2030: 0.2, 2031: 0.3, 2032: 0.3}),
        ({2030: 0.4, 2032: 0.1}, {2030: 0.4, 2031: 0.4, 2032: 0.1}),
    ],
    ids=['from_first_year', 'later_raises', 'later_lowers'],
)
def test_requirement_in_force(rows: dict, expected: dict) -> None:
    """The row with the latest first_year at or before the year is in force."""
    raw = {('clean', 'R1', 'BTM', year): share for year, share in rows.items()}
    resolved = _resolve(raw)
    assert all(r.requirement_type is BTM for r in resolved.values())
    assert {year: r.min_share for (_, _, year), r in resolved.items()} == expected
    assert {(g, rk) for g, rk, _ in resolved} == {('clean', 'R1')}


def test_superseded_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A displaced requirement is logged at INFO."""
    raw = {('clean', 'R1', 'BTM', 2030): 0.2, ('clean', 'R1', 'btm', 2031): 0.3}
    with caplog.at_level(logging.INFO, logger='src.models.electricity.rps'):
        _resolve(raw)
    assert (
        'BTM requirement (group clean, region R1, from 2030) is superseded from 2031 by the BTM '
        'requirement from 2031'
    ) in caplog.text


def test_region_keys_are_independent() -> None:
    """A later BTM over ``all`` does not supersede an earlier requirement on one region.

    A region and ``all`` are separate requirements for the same group:  both stay in force.
    """
    raw = {('clean', 'R1', 'BTM', 2030): 0.2, ('clean', 'all', 'BTM', 2031): 0.1}
    region_req = RPSRequirement(BTM, 0.2, 2030)
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
        ({('clean', 'R1', 'XYZ', 2030): 0.2}, "unsupported requirement_type 'XYZ'"),
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


# ---------------------------------------------------------------- region keys and validation


def test_region_members() -> None:
    """``all`` covers every analyzed region; a region covers itself."""
    assert region_members(['all', 'R1'], ['R2', 'R1']) == {'all': ('R1', 'R2'), 'R1': ('R1',)}


def test_validate_rps_btm_sources() -> None:
    """Only a positive-share BTM with no group generation source fails."""
    sources = {('g', 'R1', 2030): [('R1', 'Solar', 1)], ('g', 'R2', 2030): []}
    assert validate_rps_btm_sources({('g', 'R1', 2030): 0.5, ('g', 'R2', 2030): 0.0}, sources)
    assert not validate_rps_btm_sources({('g', 'R2', 2030): 0.5}, sources)


DISJOINT_GROUPS = {'a': {'Gas'}, 'b': {'Solar'}, 'c': {'Wind'}, 'ab': {'Gas', 'Solar'}}


@pytest.mark.parametrize(
    ('shares', 'expected'),
    [
        ({'a': 0.6, 'b': 0.6}, (['a', 'b'], 1.2)),
        ({'a': 0.5, 'b': 0.5}, None),
        ({'a': 0.4, 'b': 0.4, 'c': 0.4}, (['a', 'b', 'c'], 1.2)),
        ({'a': 0.6, 'ab': 0.6}, None),
        ({'ab': 0.7, 'c': 0.2, 'a': 0.1, 'b': 0.1}, None),
        ({'ab': 0.5, 'c': 0.6, 'a': 0.3}, (['ab', 'c'], 1.1)),
    ],
    ids=['pair', 'sum-1', 'triple', 'shared-member', 'heaviest-under-1', 'heaviest-over-1'],
)
def test_disjoint_share_conflicts(
    shares: dict[str, float], expected: tuple[list[str], float] | None
) -> None:
    """Only pairwise-disjoint groups add; the heaviest such set is reported if over 1."""
    min_share = {(g, 'R1', 2030): s for g, s in shares.items()}
    conflicts = disjoint_share_conflicts(min_share, DISJOINT_GROUPS)
    if expected is None:
        assert conflicts == {}
    else:
        chosen, total = conflicts['R1', 2030]
        assert (chosen, total) == (expected[0], pytest.approx(expected[1]))


def test_disjoint_shares_by_region_key(caplog: pytest.LogCaptureFixture) -> None:
    """Shares add only within one region key and year; a conflict warns but stays valid."""
    split = {('a', 'R1', 2030): 0.6, ('b', 'all', 2030): 0.6, ('b', 'R1', 2031): 0.6}
    assert disjoint_share_conflicts(split, DISJOINT_GROUPS) == {}
    with caplog.at_level(logging.WARNING, logger='src.models.electricity.data_validation'):
        assert validate_rps_disjoint_shares(split, DISJOINT_GROUPS)
        assert not caplog.records
        assert validate_rps_disjoint_shares(split | {('b', 'R1', 2030): 0.6}, DISJOINT_GROUPS)
    assert 'shares summing to 1.200, above 1' in caplog.text


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

    Group ``clean`` is Solar.  Requirements:  BTM 0.3 in R2 from 2031; BTM 0.1 over ``all``
    from 2030; a zero BTM in R1 from 2030 (in force, but no constraint).
    """
    add_solar_expansion(mini_data)
    mini_data.add_rps_group('clean', techs=['Solar'])
    mini_data.add_rps_requirement('clean', 'R2', 'BTM', 2031, 0.3)
    mini_data.add_rps_requirement('clean', 'all', 'BTM', 2030, 0.1)
    mini_data.add_rps_requirement('clean', 'R1', 'BTM', 2030, 0.0)
    return mini_data


def test_mini_rps_sets_and_params(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """The mini model's RPS sets and params, and they pass validation."""
    model_sets, param_data = _build(monkeypatch, tmp_path, rps_data)

    assert model_sets.rps_group_techs == {'clean': frozenset({'Solar'})}
    assert model_sets.rps_region_members == {'R1': ('R1',), 'R2': ('R2',), 'all': ('R1', 'R2')}
    assert model_sets.rps_btm_index == [
        ('clean', 'R2', 2031),
        ('clean', 'all', 2030),
        ('clean', 'all', 2031),
    ]
    # the zero BTM is in force but writes nothing
    assert model_sets.rps_requirements[('clean', 'R1', 2030)].min_share == 0.0

    assert param_data.param_dicts['rps_min_share'] == {
        ('clean', 'R2', 2031): 0.3,
        ('clean', 'all', 2030): 0.1,
        ('clean', 'all', 2031): 0.1,
    }

    # step-blind summation sets of the group's generators, drawn from the capacity index
    assert model_sets.rps_group_sources[('clean', 'R2', 2031)] == [('R2', 'Solar', 1)]
    assert sorted(model_sets.rps_group_sources[('clean', 'all', 2030)]) == [
        ('R1', 'Solar', 1),
        ('R2', 'Solar', 1),
    ]
    validate_all(model_sets, param_data)


def test_mini_rps_off_ignores_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rps_data: MiniModelData
) -> None:
    """With rps_required off, the RPS tables are not processed."""
    model_sets, param_data = _build(monkeypatch, tmp_path, rps_data, rps=False)
    assert model_sets.rps_group_techs == {}
    assert model_sets.rps_btm_index == []
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
    """A BTM group with no existing or buildable generation source fails validate_all."""
    rps_data.add_tech('Wind', flags=['is_renewable', 'is_vre', 'is_wind', 'is_generator'])
    rps_data.add_rps_group('wind', techs=['Wind'])
    rps_data.add_rps_requirement('wind', 'R1', 'BTM', 2030, 0.1)
    model_sets, param_data = _build(monkeypatch, tmp_path, rps_data)
    with pytest.raises(DataValidationError):
        validate_all(model_sets, param_data)
