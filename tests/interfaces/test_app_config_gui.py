"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/15/26

Tests for src.common.config_gui: the pydantic-model-driven form builder and JSON save/load logic
backing app.py's config editor. Only pure functions are tested here -- app.py itself is not
imported, since importing it spawns a docs HTTP server subprocess as a module-level side effect.
"""

import pytest
from pydantic import ValidationError

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.common.config_gui import (
    ConfigValidationError,
    build_config_form,
    parse_form_values,
    save_configs,
)
from src.models.electricity.elec_config import ElecConfig

BASIC_CONFIG_TOML = PROJECT_ROOT / 'tests/electric/basic_elec_config.toml'


@pytest.fixture
def config_pair() -> tuple[CommonConfig, ElecConfig]:
    """The (CommonConfig, ElecConfig) pair parsed from the basic test TOML."""
    common_config, remainder = parse_config_file(BASIC_CONFIG_TOML)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    return common_config, elec_config


def _find_value(input_ids: list[dict], input_values: list, section: str, field: str):
    for id_dict, value in zip(input_ids, input_values, strict=True):
        if id_dict['section'] == section and id_dict['field'] == field:
            return value
    raise KeyError((section, field))


def _to_ids_and_values(
    common_config: CommonConfig, elec_config: ElecConfig
) -> tuple[list[dict], list]:
    """Flatten the form built for `config_pair` into positional (ids, values) lists."""
    form = build_config_form(common_config, elec_config)
    input_ids, input_values = [], []
    for row in form:
        # skip the section H5 headers, which have no `children` list of [Label, Input]
        if not hasattr(row, 'children') or not isinstance(row.children, list):
            continue
        component = row.children[1]
        input_ids.append(component.id)
        input_values.append(component.value)
    return input_ids, input_values


def test_build_form_reflects_field_edit(config_pair):
    """Editing one field's value in the parsed form should change only that field."""
    common_config, elec_config = config_pair
    input_ids, input_values = _to_ids_and_values(common_config, elec_config)

    idx = next(
        i
        for i, id_dict in enumerate(input_ids)
        if id_dict['section'] == 'common' and id_dict['field'] == 'scenario_name'
    )
    input_values[idx] = 'updated_scenario'

    common_raw, elec_raw = parse_form_values(input_ids, input_values)

    assert common_raw['scenario_name'] == 'updated_scenario'
    updated = CommonConfig(**common_raw)
    assert updated.scenario_name == 'updated_scenario'
    assert updated.model_copy(update={'scenario_name': common_config.scenario_name}) == (
        common_config
    )

    # elec_config should be untouched
    assert ElecConfig(**elec_raw) == elec_config


def test_save_load_json_roundtrip(config_pair, tmp_path):
    """Saving edited raw values then reloading the JSON file yields equal config instances."""
    common_config, elec_config = config_pair
    input_ids, input_values = _to_ids_and_values(common_config, elec_config)
    common_raw, elec_raw = parse_form_values(input_ids, input_values)

    target = tmp_path / 'last_app_config.json'
    saved_common, saved_elec = save_configs(common_raw, elec_raw, path=target)
    assert saved_common == common_config
    assert saved_elec == elec_config

    reloaded_common, remainder = parse_config_file(target)
    reloaded_elec = ElecConfig(**remainder.pop('elec_config'))

    assert reloaded_common == common_config
    assert reloaded_elec == elec_config


def test_save_configs_surfaces_validation_error(config_pair, tmp_path):
    """An invalid field value raises ConfigValidationError and leaves the target file untouched."""
    common_config, elec_config = config_pair
    input_ids, input_values = _to_ids_and_values(common_config, elec_config)
    common_raw, elec_raw = parse_form_values(input_ids, input_values)

    common_raw['scenario_name'] = 'ab'  # violates the length->=4 validator

    target = tmp_path / 'last_app_config.json'
    with pytest.raises(ConfigValidationError) as exc_info:
        save_configs(common_raw, elec_raw, path=target)

    assert exc_info.value.errors
    assert not target.exists()

    # confirm this really is the underlying pydantic failure, for sanity
    with pytest.raises(ValidationError):
        CommonConfig(**common_raw)
