"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

Tests for the text monitor of the integrated control loop.

"""

from pathlib import Path

import pandas as pd
import pytest

from src.common.integrated_model_sequencer import IterationResult, IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_VALUE,
    NG_PRICE_INDEX,
    NG_PRICE_VALUE,
    NGDemandPackage,
    NGElectricalDemandPackage,
    NGPricePackage,
)
from src.integrator.iteration_monitor import (
    BROKEN_LIFELINE,
    DEFAULT_STYLE_PATH,
    LIFELINE,
    NO_OBJECTIVE,
    DeltaMode,
    IterationMonitor,
    load_style,
)

CIRCUIT = (ModelType.ELECTRICITY, ModelType.NATURAL_GAS, ModelType.MAGIC)


def region_year_frame(value_col: str, n: int, region: str = 'r') -> pd.DataFrame:
    """A (region, year) frame with ``n`` rows."""
    index = pd.MultiIndex.from_tuples(
        [(f'{region}{i}', 2025) for i in range(n)], names=NG_PRICE_INDEX
    )
    return pd.DataFrame({value_col: [1.0] * n}, index=index)


def result(
    model: ModelType,
    objective: float | None,
    packages=(),
    label: str = '',
    status: IterationStatus = IterationStatus.BEST,
) -> IterationResult:
    """A result for ``model`` carrying ``packages``."""
    return IterationResult(
        model_type=model,
        status=status,
        objective_value=objective,
        update_packages=list(packages),
        label=label,
    )


@pytest.fixture
def monitor() -> IterationMonitor:
    """A monitor over the three-model circuit."""
    return IterationMonitor(CIRCUIT)


def test_header_uses_model_labels_once(monitor: IterationMonitor) -> None:
    """The first block carries the header with the models' own labels; later blocks do not."""
    first = monitor.record(1, [result(ModelType.ELECTRICITY, 1.0, label='Electricity')]).plain
    second = monitor.record(2, [result(ModelType.ELECTRICITY, 2.0, label='Electricity')]).plain

    top, mid, bottom = first.splitlines()[:3]
    assert '| Electricity |' in mid
    assert '| natural_gas |' in mid  # no result seen yet: falls back to the type
    assert mid.lstrip().startswith('it')
    assert '+-------------+' in top and '+-------------+' in bottom
    assert 'Electricity' not in second


def test_deltas_start_then_change_and_flag_no_objective(monitor: IterationMonitor) -> None:
    """Iteration 1 shows the starting value, later ones the signed change; no objective is N/C."""
    first = monitor.record(
        1, [result(ModelType.ELECTRICITY, 1000.0), result(ModelType.NATURAL_GAS, -500.0)]
    ).plain
    second = monitor.record(
        2, [result(ModelType.ELECTRICITY, 1250.5), result(ModelType.NATURAL_GAS, -600.0)]
    ).plain

    assert '(start 1,000.00)' in first
    assert '(start -500.00)' in first
    assert first.count(NO_OBJECTIVE) == 1  # magic sent no result at all
    assert '(+250.50)' in second
    assert '(-100.00)' in second
    # the iteration number sits in the leading column of the delta row
    delta_row = next(line for line in second.splitlines() if '(+250.50)' in line)
    assert delta_row.lstrip().startswith('2')


def test_rays_run_between_the_right_columns(monitor: IterationMonitor) -> None:
    """A rightward ray ends with '>' before the receiver's rail; a leftward one starts after it."""
    demand = NGElectricalDemandPackage(
        elements=region_year_frame(NG_ELEC_DEMAND_VALUE, 4), source=ModelType.ELECTRICITY
    )
    price = NGPricePackage(
        elements=region_year_frame(NG_PRICE_VALUE, 50), source=ModelType.NATURAL_GAS
    )
    block = monitor.record(
        1,
        [
            result(ModelType.ELECTRICITY, 1.0, [demand]),
            result(ModelType.NATURAL_GAS, 2.0, [price]),
            result(ModelType.MAGIC, None),
        ],
    )
    lines = block.plain.splitlines()
    elec, ng, magic = (monitor._center(i) for i in range(3))

    demand_line = next(line for line in lines if ' NG Demand [4] ' in line)
    assert demand_line[elec] == '+'  # the ray leaves from the sender's lifeline
    assert demand_line[ng - 1] == '>'  # and its head touches the receiver's
    assert demand_line[ng] == '|'
    assert demand_line[magic] == '|'  # untouched column keeps its lifeline

    price_line = next(line for line in lines if ' NG Prices [50] ' in line)
    assert price_line[elec] == '|'
    assert price_line[elec + 1] == '<'
    assert price_line[ng] == '+'  # the ray leaves from the sender's lifeline
    assert price_line[magic] == '|'


def test_ray_to_all_fans_out_and_crosses_columns(monitor: IterationMonitor) -> None:
    """A package addressed to ALL draws one ray per other model, overwriting crossed rails."""
    package = NGDemandPackage(scalar=1.1, receivers=(ModelType.ALL,), source=ModelType.ELECTRICITY)
    block = monitor.record(1, [result(ModelType.ELECTRICITY, 1.0, [package])])
    rays = [line for line in block.plain.splitlines() if 'NG Demand Scaler [1]' in line]
    assert len(rays) == 2
    magic = monitor._center(2)
    to_magic = next(line for line in rays if line[magic - 1] == '>')
    assert to_magic[monitor._center(1)] != '|'  # the natural gas lifeline is crossed
    assert to_magic[magic] == '|'


def test_width_grows_with_the_circuit() -> None:
    """Every rendered line fits the declared width, which scales per model."""
    small = IterationMonitor(CIRCUIT[:2])
    large = IterationMonitor(CIRCUIT)
    assert large.width - small.width == large.column_width
    block = large.record(1, [result(m, 1.0) for m in CIRCUIT])
    assert all(len(line) <= large.width for line in block.plain.splitlines())
    assert large.width <= 120


def test_empty_circuit_rejected() -> None:
    """A monitor needs at least one column."""
    with pytest.raises(ValueError):
        IterationMonitor(())


def test_percent_mode_is_relative_to_previous_magnitude() -> None:
    """Percent deltas divide by |previous| so a falling negative objective still reads negative."""
    monitor = IterationMonitor(CIRCUIT, delta_mode=DeltaMode.PERCENT)
    monitor.record(
        1, [result(ModelType.ELECTRICITY, 1000.0), result(ModelType.NATURAL_GAS, -500.0)]
    )
    second = monitor.record(
        2, [result(ModelType.ELECTRICITY, 1250.0), result(ModelType.NATURAL_GAS, -600.0)]
    ).plain
    assert '(+25.0000%)' in second
    assert '(-20.0000%)' in second
    third = monitor.record(3, [result(ModelType.ELECTRICITY, 0.0)]).plain
    fourth = monitor.record(4, [result(ModelType.ELECTRICITY, 5.0)]).plain
    assert '(-100.0000%)' in third
    assert '(n/a %)' in fourth  # no percentage of a zero previous value


class TestStyles:
    """The colour scheme comes from the TOML beside the module and is applied to the output."""

    def test_shipped_style_file_loads_and_is_used(self, monitor: IterationMonitor) -> None:
        """The default file parses, and the model style from it colours the header."""
        style = load_style()
        assert DEFAULT_STYLE_PATH.is_file()
        block = monitor.record(1, [result(ModelType.ELECTRICITY, 1.0, label='Electricity')])
        applied = {str(span.style) for span in block.spans}
        assert style.get('model', 'electricity') in applied
        assert style.get('objective', 'start') in applied
        assert style.get('connector', 'lifeline') in applied

    def test_user_file_overrides_and_falls_back(self, tmp_path: Path) -> None:
        """A partial user file overrides what it names and keeps defaults for the rest."""
        path = tmp_path / 'style.toml'
        path.write_text('[model]\nelectricity = "bold red"\n[package]\n"NG Prices" = "blue"\n')
        style = load_style(path)
        assert style.get('model', 'electricity') == 'bold red'
        assert style.get('model', 'magic') == style.get('model', 'default')
        assert style.get('package', 'NG Prices') == 'blue'
        assert style.get('package', 'anything else') == style.get('package', 'default')

    def test_missing_file_warns_and_uses_defaults(self, tmp_path: Path, caplog) -> None:
        """No file is not an error; the built-in colours apply."""
        with caplog.at_level('WARNING', logger='src.integrator.iteration_monitor'):
            style = load_style(tmp_path / 'absent.toml')
        assert style.get('connector', 'lifeline')
        assert any('not found' in r.getMessage() for r in caplog.records)

    def test_invalid_style_string_raises(self, tmp_path: Path) -> None:
        """A style rich cannot parse is reported with its section and key."""
        path = tmp_path / 'style.toml'
        path.write_text('[objective]\nincrease = "not a colour at all"\n')
        with pytest.raises(ValueError, match=r"\[objective\] 'increase'"):
            load_style(path)


def test_rays_are_separated_by_a_blank_lifeline_row(monitor: IterationMonitor) -> None:
    """Consecutive rays have a bare lifeline row between them, and none after the last."""
    demand = NGElectricalDemandPackage(
        elements=region_year_frame(NG_ELEC_DEMAND_VALUE, 4), source=ModelType.ELECTRICITY
    )
    price = NGPricePackage(
        elements=region_year_frame(NG_PRICE_VALUE, 50), source=ModelType.NATURAL_GAS
    )
    block = monitor.record(
        1,
        [
            result(ModelType.ELECTRICITY, 1.0, [demand]),
            result(ModelType.NATURAL_GAS, 2.0, [price]),
        ],
    ).plain
    lines = block.splitlines()
    first = next(i for i, line in enumerate(lines) if 'NG Demand [4]' in line)
    second = next(i for i, line in enumerate(lines) if 'NG Prices [50]' in line)
    assert second == first + 2
    assert set(lines[first + 1].strip()) == {'|', ' '}
    assert lines[-1] is lines[second]


def test_unacceptable_status_shows_name_and_dashes_rail_to_next_solve(
    monitor: IterationMonitor,
) -> None:
    """A rejected solve shows its status name and a dashed rail until that model's next solve."""
    monitor.record(1, [result(ModelType.NATURAL_GAS, -500.0)])
    failed = monitor.record(2, [result(ModelType.NATURAL_GAS, -1.0, status=IterationStatus.ERROR)])
    recovered = monitor.record(3, [result(ModelType.NATURAL_GAS, -600.0)])
    ng = monitor._center(1)

    failed_lines = failed.plain.splitlines()
    delta_row = next(i for i, line in enumerate(failed_lines) if '(ERROR)' in line)
    assert failed_lines[delta_row - 1][ng] == LIFELINE  # the rail into the failed solve
    assert all(line[ng] == BROKEN_LIFELINE for line in failed_lines[delta_row + 1 :])
    assert monitor.style.get('connector', 'broken_lifeline') in {
        str(span.style) for span in failed.spans
    }

    recovered_lines = recovered.plain.splitlines()
    assert recovered_lines[0][ng] == BROKEN_LIFELINE  # dashed down to the next solve...
    assert recovered_lines[-1][ng] == LIFELINE  # ...and solid again after a good one
    assert '(-100.00)' in recovered.plain  # the failed solve's objective is not the baseline


def test_packages_from_unacceptable_solve_are_not_drawn(monitor: IterationMonitor) -> None:
    """Only packages the control loop would route get a ray."""
    demand = NGElectricalDemandPackage(
        elements=region_year_frame(NG_ELEC_DEMAND_VALUE, 4), source=ModelType.ELECTRICITY
    )
    price = NGPricePackage(
        elements=region_year_frame(NG_PRICE_VALUE, 50), source=ModelType.NATURAL_GAS
    )
    block = monitor.record(
        1,
        [
            result(ModelType.ELECTRICITY, 1.0, [demand]),
            result(ModelType.NATURAL_GAS, 2.0, [price], status=IterationStatus.ERROR),
        ],
    ).plain
    assert 'NG Demand [4]' in block
    assert 'NG Prices' not in block
