"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  7/15/26

Pure, Dash-component-building logic for the config editor in app.py: renders a form from the
CommonConfig/ElecConfig pydantic models, parses edited values back into raw dicts, and loads/
saves the combined config as a single JSON file. Kept separate from app.py (which does not import
this module in reverse) so it can be imported and unit tested without triggering app.py's
module-level side effect of spawning a docs HTTP server subprocess.
"""

import json
import logging
from enum import Enum
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

import dash_bootstrap_components as dbc
from dash import dcc, html
from dash.development.base_component import Component
from pydantic import ValidationError

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig, parse_config_file
from src.models.electricity.elec_config import ElecConfig

logger = logging.getLogger(__name__)

CONFIG_JSON_PATH = (
    PROJECT_ROOT / 'run_configs/last_app_config.json'
)  # TODO:  This may migrate to /tmp or such?
DEFAULT_TOML_PATH = PROJECT_ROOT / 'run_configs/basic_elec_config.toml'

_SECTION_MODELS: dict[str, type[CommonConfig] | type[ElecConfig]] = {
    'common': CommonConfig,
    'elec_config': ElecConfig,
}


class ConfigValidationError(Exception):
    """Raised when reconstructing CommonConfig/ElecConfig from edited form values fails.

    Parameters
    ----------
    errors : list[str]
        Human-readable validation error messages, collected across both models so the caller
        can display everything wrong with the form at once.
    """

    def __init__(self, errors: list[str]) -> None:
        super().__init__('; '.join(errors))
        self.errors = errors


def _unwrap_optional(annotation: Any) -> Any:
    """Strip an `X | None` union down to `X`; return `annotation` unchanged otherwise."""
    if get_origin(annotation) is UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _display_path(path: Path) -> str:
    """Render `path` relative to PROJECT_ROOT for display, falling back to the absolute form."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _build_field_input(section: str, name: str, annotation: Any, current: Any) -> Component:
    """Build a single Dash input component for one model field, typed by its annotation.

    Parameters
    ----------
    section : str
        `'common'` or `'elec_config'` — embedded in the component id so edited values can be
        regrouped back into the correct model by `parse_form_values`.
    name : str
        Field name, also embedded in the component id.
    annotation : Any
        The field's type annotation (as found on `model_fields[name].annotation`).
    current : Any
        The field's current value, used to populate the widget.

    Returns
    -------
    Component
        A Dash component (switch, dropdown, or text/number input) suited to the field's type.
    """
    field_id = {'type': 'config-input', 'section': section, 'field': name}
    base = _unwrap_optional(annotation)

    if base is bool:
        return dbc.Switch(id=field_id, value=bool(current))

    if isinstance(base, type) and issubclass(base, Enum):
        return dcc.Dropdown(
            id=field_id,
            options=[e.value for e in base],
            value=current.value if current is not None else None,
            clearable=False,
        )

    origin = get_origin(base)
    if origin is list:
        inner_args = get_args(base)
        inner = inner_args[0] if inner_args else str
        if isinstance(inner, type) and issubclass(inner, Enum):
            return dcc.Dropdown(
                id=field_id,
                options=[e.value for e in inner],
                value=[v.value for v in current] if current else [],
                multi=True,
            )
        text_value = ', '.join(str(v) for v in current) if current else ''
        return dbc.Input(id=field_id, type='text', value=text_value, debounce=True)

    if base in (int, float):
        return dbc.Input(id=field_id, type='number', value=current, debounce=True)

    if base is Path:
        return dbc.Input(
            id=field_id,
            type='text',
            value=_display_path(current) if current is not None else '',
            debounce=True,
        )

    return dbc.Input(
        id=field_id, type='text', value='' if current is None else str(current), debounce=True
    )


def _build_section_rows(section: str, model_cls: type, instance: Any) -> list[Component]:
    """Build one labeled form row per field of `model_cls`, populated from `instance`."""
    rows = []
    for name, field in model_cls.model_fields.items():
        current = getattr(instance, name)
        component = _build_field_input(section, name, field.annotation, current)
        rows.append(
            html.Div(
                [dbc.Label(f'{name}:'), component],
                style={'marginBottom': '10px'},
            )
        )
    return rows


def build_config_form(common_config: CommonConfig, elec_config: ElecConfig) -> list[Component]:
    """Build the full config-editor form from a CommonConfig/ElecConfig pair.

    Parameters
    ----------
    common_config : CommonConfig
        Current common config values, used to populate the "Common Settings" section.
    elec_config : ElecConfig
        Current electricity config values, used to populate the "Electricity Settings" section.

    Returns
    -------
    list[Component]
        Dash components ready to place in the `config-editor` container.
    """
    rows: list[Component] = [html.H5('Common Settings')]
    rows += _build_section_rows('common', CommonConfig, common_config)
    rows.append(html.H5('Electricity Settings'))
    rows += _build_section_rows('elec_config', ElecConfig, elec_config)
    return rows


def _coerce_value(annotation: Any, value: Any) -> Any:
    """Loosely coerce a raw Dash form value toward its field's annotation before validation.

    Final type enforcement is left to pydantic; this only handles shapes pydantic can't infer
    from a Dash value on its own (e.g. splitting a comma-separated string into a list).
    """
    if value == '':
        return None

    base = _unwrap_optional(annotation)
    origin = get_origin(base)

    if origin is list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            tokens = [t.strip() for t in value.split(',')]
            return [t for t in tokens if t]
        return value

    if base in (int, float) and value is not None:
        try:
            return base(value)
        except TypeError, ValueError:
            return value

    return value


def parse_form_values(input_ids: list[dict], input_values: list) -> tuple[dict, dict]:
    """Regroup Dash pattern-matching `(id, value)` pairs back into per-model raw dicts.

    Parameters
    ----------
    input_ids : list[dict]
        Component ids of the form `{'type': 'config-input', 'section': ..., 'field': ...}`.
    input_values : list
        The corresponding component values, positionally aligned with `input_ids`.

    Returns
    -------
    tuple[dict, dict]
        `(common_raw, elec_raw)` dicts suitable for `CommonConfig(**common_raw)` /
        `ElecConfig(**elec_raw)`.
    """
    raw: dict[str, dict] = {'common': {}, 'elec_config': {}}
    for id_dict, value in zip(input_ids, input_values, strict=True):
        section = id_dict['section']
        field = id_dict['field']
        model_cls = _SECTION_MODELS[section]
        annotation = model_cls.model_fields[field].annotation
        raw[section][field] = _coerce_value(annotation, value)
    return raw['common'], raw['elec_config']


def load_config(path: Path | None = None) -> tuple[CommonConfig, ElecConfig, Path]:
    """Load the common/electricity configs to populate the GUI's config editor.

    Parameters
    ----------
    path : Path | None
        Explicit config file to load. If not given, prefers `CONFIG_JSON_PATH` if it exists,
        else falls back to `DEFAULT_TOML_PATH`. When the preferred `CONFIG_JSON_PATH` fails to
        validate, the corrupt file is deleted and loading falls back to `DEFAULT_TOML_PATH`.

    Returns
    -------
    tuple[CommonConfig, ElecConfig, Path]
        The parsed configs and the path actually loaded from.
    """
    if path is None:
        if CONFIG_JSON_PATH.exists():
            try:
                return _parse_config(CONFIG_JSON_PATH)
            except ValidationError:
                logger.warning(
                    'Config at %s failed validation; deleting it and falling back to %s',
                    CONFIG_JSON_PATH,
                    DEFAULT_TOML_PATH,
                )
                CONFIG_JSON_PATH.unlink(missing_ok=True)
        return _parse_config(DEFAULT_TOML_PATH)
    return _parse_config(path)


def _parse_config(path: Path) -> tuple[CommonConfig, ElecConfig, Path]:
    """Parse a single config file into `(CommonConfig, ElecConfig, path)`."""
    common_config, remainder = parse_config_file(path)
    elec_config = ElecConfig(**remainder.pop('elec_config'))
    return common_config, elec_config, path


def save_configs(
    common_raw: dict, elec_raw: dict, path: Path = CONFIG_JSON_PATH
) -> tuple[CommonConfig, ElecConfig]:
    """Validate edited config values and, on success, persist them as a combined JSON file.

    Parameters
    ----------
    common_raw : dict
        Raw values for `CommonConfig`, as produced by `parse_form_values`.
    elec_raw : dict
        Raw values for `ElecConfig`, as produced by `parse_form_values`.
    path : Path
        Destination JSON file. Defaults to `CONFIG_JSON_PATH`; overridable for tests.

    Returns
    -------
    tuple[CommonConfig, ElecConfig]
        The validated config instances.

    Raises
    ------
    ConfigValidationError
        If either model fails validation; `.errors` holds combined messages from both.
    """
    errors: list[str] = []
    common_config: CommonConfig | None = None
    elec_config: ElecConfig | None = None

    try:
        common_config = CommonConfig(**common_raw)
    except ValidationError as e:
        errors += [
            f'common.{".".join(str(p) for p in err["loc"])}: {err["msg"]}' for err in e.errors()
        ]

    try:
        elec_config = ElecConfig(**elec_raw)
    except ValidationError as e:
        errors += [
            f'elec_config.{".".join(str(p) for p in err["loc"])}: {err["msg"]}'
            for err in e.errors()
        ]

    if errors:
        raise ConfigValidationError(errors)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(
            {
                'common': common_config.model_dump(mode='json'),
                'elec_config': elec_config.model_dump(mode='json'),
            },
            f,
            indent=2,
        )

    return common_config, elec_config
