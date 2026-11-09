"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

Tests for the natural gas demand the electricity model reports back to the gas model.

"""

from pathlib import Path

import pandas as pd
import pytest
from pyomo.common.numeric_types import value

from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    ExchangeSpec,
    NGElectricalDemandPackage,
)
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import ElectricitySequencer
from src.models.electricity.update_writer import gas_demand_by_region
from tests.electric.mini_model import (
    GAS_CT_HEAT_RATE,
    MiniModelData,
    add_gas_ct,
    mini_configs,
    patch_loaders,
)

# the gas-burn exchange alone, with the electricity <-> natural gas crosswalk
DEMAND_EXCHANGE = ExchangeSpec.build(
    ['NGElectricalDemandPackage'], {ModelType.ELECTRICITY, ModelType.NATURAL_GAS}
)


def independent_gas_demand(model: PowerModel) -> pd.Series:
    """Recompute the gas burn with pandas rather than the loop under test."""
    rows = [
        (r, t, y, h, value(var))
        for (r, t, _s, y, h), var in model.generation_total.items()
        if t in model.tech_gas_fired
    ]
    df = pd.DataFrame(rows, columns=['region', 'tech', 'year', 'hour', 'mwh'])
    df['days'] = [value(model.weight_day[value(model.map_hour_day[h])]) for h in df['hour']]
    df['heat_rate'] = [value(model.heat_rate[t]) for t in df['tech']]
    df['mmbtu'] = df['mwh'] * df['days'] * df['heat_rate']
    return df.groupby(['region', 'year'])['mmbtu'].sum().sort_index()


def _two_tier_dispatch(data: MiniModelData) -> MiniModelData:
    """GasCT alone serves day 1's 80 MW; day 2's 150 MW also needs Gas.  Day 2 stands for 3 days."""
    add_gas_ct(data)
    for region in data.regions:
        data.set_load(region, {1: 80.0, 2: 80.0, 3: 150.0, 4: 150.0})
    cw = data.cw_temporal
    cw.loc[cw['Map_day'] == 2, 'WeightDay'] = 3.0
    return data


def test_gas_demand_hand_computed(mini_data: MiniModelData, build_mini) -> None:
    """The burn is gas-fired generation x day weight x heat rate, summed over a year's hours.

    GasCT runs at 80 MW in day 1's two hours (weight 1) and at its 100 MW limit in day 2's (weight
    3).  Gas makes up the rest of day 2 but is not gas-fired, so every (region, year) burns
    (2 x 80 x 1 + 2 x 100 x 3) x 10 = 7,600 MMBtu.
    """
    _two_tier_dispatch(mini_data)
    sequencer, model = build_mini()
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST

    got = gas_demand_by_region(model)

    expected = (2 * 80.0 * 1.0 + 2 * 100.0 * 3.0) * GAS_CT_HEAT_RATE
    assert got.to_dict() == pytest.approx(
        dict.fromkeys(mini_data.every('region', 'year'), expected)
    )


def test_gas_demand_without_gas_fired_techs(build_mini, caplog: pytest.LogCaptureFixture) -> None:
    """With no gas-fired tech, every (region, year) is sent as 0.0, with a warning."""
    _, model = build_mini()

    with caplog.at_level('WARNING', logger='src.models.electricity.update_writer'):
        got = gas_demand_by_region(model)

    assert len(got) == 4
    assert (got == 0.0).all()
    assert 'No generation rows for gas-fired techs' in caplog.text


def test_gas_demand_matches_independent_sum(mini_data: MiniModelData, build_mini) -> None:
    """The polled MMBtu agrees with a pandas recomputation from the same solved variables.

    R2 has no GasCT, so it burns no gas and is reported as 0.0.
    """
    _two_tier_dispatch(mini_data)
    mini_data.drop_rows('available_capacity', [('R2', 'GasCT', 1, y) for y in mini_data.years])
    mini_data.add_capacity('Gas', 200.0, regions=['R2'])
    sequencer, model = build_mini()
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST

    # poll the model for demands
    got = gas_demand_by_region(model)
    # compute them (semi-) independently
    expected = independent_gas_demand(model)

    assert list(got.index.names) == NG_ELEC_DEMAND_INDEX
    assert got.name == NG_ELEC_DEMAND_VALUE
    # every region the model carries, in every year, with zero where it burns no gas
    grid = pd.MultiIndex.from_product(
        [mini_data.regions, mini_data.years], names=NG_ELEC_DEMAND_INDEX
    )
    assert got.index.equals(grid)
    assert got.to_numpy() == pytest.approx(expected.reindex(grid, fill_value=0.0).to_numpy())
    assert (got.xs('R2', level='region') == 0.0).all()
    assert (got.xs('R1', level='region') > 0.0).all()


@pytest.fixture
def mini_config_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mini_data: MiniModelData
) -> tuple[CommonConfig, ElecConfig]:
    """Configs for a ``full_run`` on the mini model, with the loaders patched to read it."""
    patch_loaders(monkeypatch, mini_data)
    return mini_configs(tmp_path, mini_data.years)


class TestOutboundDemandPackage:
    """``full_run`` sends the gas burn onward, in gas regions, and only after a good solve."""

    def test_full_run_emits_crosswalked_demand(
        self, config_set: tuple[CommonConfig, ElecConfig]
    ) -> None:
        """One package, allocated onto gas regions with the burn total conserved.

        Electricity region 7 and New England cover each other exactly, so the region is closed
        under the crosswalk.
        """
        common_config, elec_config = config_set
        elec_config = elec_config.model_copy(update={'region_filter': ['7']})
        sequencer = ElectricitySequencer(DEMAND_EXCHANGE)
        result = sequencer.full_run(common_config, elec_config)

        assert result.status is IterationStatus.BEST
        assert [type(p) for p in result.update_packages] == [NGElectricalDemandPackage]
        package = result.update_packages[0]
        assert package.source is ModelType.ELECTRICITY
        assert package.receivers == (ModelType.NATURAL_GAS,)
        sent = package.elements[NG_ELEC_DEMAND_VALUE]
        assert list(sent.index.names) == NG_ELEC_DEMAND_INDEX
        assert set(sent.index.get_level_values('region')) == {'new_england'}

        # allocation conserves the annual total
        polled = gas_demand_by_region(sequencer.model)
        for year in common_config.summary_years:
            assert sent.xs(year, level='year').sum() == pytest.approx(
                polled.xs(year, level='year').sum(), abs=1e-6
            )

    def test_unclosed_regions_raise(self, config_set: tuple[CommonConfig, ElecConfig]) -> None:
        """Regions 7, 8 and 9 reach gas regions that also draw on regions the run lacks."""
        common_config, elec_config = config_set
        with pytest.raises(ValueError, match='not closed'):
            ElectricitySequencer(DEMAND_EXCHANGE).full_run(common_config, elec_config)

    def test_standalone_sends_nothing(
        self, mini_data: MiniModelData, mini_config_pair: tuple[CommonConfig, ElecConfig]
    ) -> None:
        """Without exchanges, as in a standalone run, the writer builds no package."""
        add_gas_ct(mini_data)
        common_config, elec_config = mini_config_pair
        result = ElectricitySequencer().full_run(common_config, elec_config)

        assert result.status is IterationStatus.BEST
        assert result.update_packages == []

    def test_no_package_on_solve_error(
        self,
        mini_data: MiniModelData,
        mini_config_pair: tuple[CommonConfig, ElecConfig],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed solve has no generation values to read, so nothing is sent."""
        add_gas_ct(mini_data)
        common_config, elec_config = mini_config_pair
        monkeypatch.setattr(
            ElectricitySequencer,
            'solve_model',
            lambda self, **kwargs: (ModelType.ELECTRICITY, IterationStatus.ERROR),
        )
        result = ElectricitySequencer(DEMAND_EXCHANGE).full_run(common_config, elec_config)

        assert result.status is IterationStatus.ERROR
        assert result.update_packages == []


@pytest.mark.parametrize(
    'frame',
    [
        pytest.param(
            pd.DataFrame(
                {NG_ELEC_DEMAND_VALUE: [-1.0]},
                index=pd.MultiIndex.from_tuples(
                    [('new_england', 2025)], names=NG_ELEC_DEMAND_INDEX
                ),
            ),
            id='negative_demand',
        ),
        pytest.param(
            pd.DataFrame(
                {'mmbtu': [3.0]},
                index=pd.MultiIndex.from_tuples(
                    [('new_england', 2025)], names=NG_ELEC_DEMAND_INDEX
                ),
            ),
            id='wrong_value_column',
        ),
        pytest.param(
            pd.DataFrame(
                {NG_ELEC_DEMAND_VALUE: [3.0]},
                index=pd.MultiIndex.from_tuples([('new_england', 2025)], names=['reg', 'yr']),
            ),
            id='wrong_index_names',
        ),
    ],
)
def test_demand_package_rejects_bad_frames(frame: pd.DataFrame) -> None:
    """A frame the recipient could not apply is rejected at construction."""
    with pytest.raises(ValueError):
        NGElectricalDemandPackage(elements=frame)
