"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

Tests for the natural gas demand the electricity model reports back to the gas model.

"""

import pandas as pd
import pytest
from pyomo.common.numeric_types import value

from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.common.models_modes import ModelType
from src.common.update_package import (
    NG_ELEC_DEMAND_INDEX,
    NG_ELEC_DEMAND_VALUE,
    NGElectricalDemandPackage,
)
from src.integrator.region_crosswalk import load_region_weights
from src.models.electricity.constants import NG_HEAT_RATE_MMBTU_PER_MWH, NG_PRICE_LINKED_TECHS
from src.models.electricity.elec_config import ElecConfig
from src.models.electricity.electricity_model import PowerModel
from src.models.electricity.sequencer import (
    ElectricitySequencer,
    gas_demand_by_region,
    ng_mmbtu_per_bcf,
)


def independent_gas_demand(model: PowerModel) -> pd.Series:
    """Recompute the gas burn with pandas rather than the loop under test."""
    rows = [
        (r, t, y, h, value(var))
        for (r, t, _s, y, h), var in model.generation_total.items()
        if t in NG_HEAT_RATE_MMBTU_PER_MWH
    ]
    df = pd.DataFrame(rows, columns=['region', 'tech', 'year', 'hour', 'gwh'])
    df['days'] = [value(model.weight_day[value(model.map_hour_day[h])]) for h in df['hour']]
    df['heat_rate'] = df['tech'].map(NG_HEAT_RATE_MMBTU_PER_MWH)
    # TODO:  re-examine this x1000 multiplier after we get the model units fully standardized
    df['bcf'] = df['gwh'] * df['days'] * 1000.0 * df['heat_rate'] / ng_mmbtu_per_bcf()
    return df.groupby(['region', 'year'])['bcf'].sum().sort_index()


def test_linked_techs_are_exactly_the_heat_rate_techs() -> None:
    """The price link and the demand poll must name the same techs."""
    assert set(NG_PRICE_LINKED_TECHS) == set(NG_HEAT_RATE_MMBTU_PER_MWH)


def test_gas_demand_matches_independent_sum(
    solved_model: tuple[CommonConfig, ElecConfig, PowerModel],
) -> None:
    """The polled Bcf agrees with a pandas recomputation from the same solved variables."""
    common_config, elec_config, model = solved_model

    # poll the model for demands
    got = gas_demand_by_region(model)
    # compute them (semi-) independently
    expected = independent_gas_demand(model)

    assert list(got.index.names) == NG_ELEC_DEMAND_INDEX
    assert got.name == NG_ELEC_DEMAND_VALUE
    assert got.index.equals(expected.index)
    assert got.to_numpy() == pytest.approx(expected.to_numpy())
    assert (got >= 0).all()
    assert set(got.index.get_level_values('year')) == set(common_config.summary_years)
    assert set(got.index.get_level_values('region')) <= set(elec_config.region_filter)


class TestOutboundDemandPackage:
    """``full_run`` sends the gas burn onward, in gas regions, and only after a good solve."""

    def test_full_run_emits_crosswalked_demand(
        self, config_set: tuple[CommonConfig, ElecConfig]
    ) -> None:
        """One package, allocated onto gas regions with the burn total conserved."""
        common_config, elec_config = config_set
        sequencer = ElectricitySequencer()
        result = sequencer.full_run(common_config, elec_config)

        assert result.status is IterationStatus.BEST
        # select by type:  the outbound list may carry other package kinds in future
        demand_packages = [
            p for p in result.update_packages if isinstance(p, NGElectricalDemandPackage)
        ]
        assert len(demand_packages) == 1, result.update_packages
        package = demand_packages[0]
        assert package.source is ModelType.ELECTRICITY
        assert package.receivers == (ModelType.NATURAL_GAS,)
        sent = package.elements[NG_ELEC_DEMAND_VALUE]
        assert list(sent.index.names) == NG_ELEC_DEMAND_INDEX

        # the gas regions the run's electricity regions touch, and no others
        weights = load_region_weights(ModelType.ELECTRICITY, ModelType.NATURAL_GAS)

        # locate the NG regions linked to the electrical regions used in the filter
        # and check that they all receive data
        linked = set(weights[weights['elec_region'].isin(elec_config.region_filter)]['ng_region'])
        assert set(sent.index.get_level_values('region')) == linked

        # allocation conserves the annual total
        polled = gas_demand_by_region(sequencer.model)
        for year in common_config.summary_years:
            assert sent.xs(year, level='year').sum() == pytest.approx(
                polled.xs(year, level='year').sum(), abs=1e-6
            )

    def test_no_package_on_solve_error(
        self, config_set: tuple[CommonConfig, ElecConfig], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed solve has no generation values to read, so nothing is sent."""
        common_config, elec_config = config_set
        monkeypatch.setattr(
            ElectricitySequencer,
            'solve_model',
            lambda self, **kwargs: (ModelType.ELECTRICITY, IterationStatus.ERROR),
        )
        result = ElectricitySequencer().full_run(common_config, elec_config)

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
                {'bcf': [3.0]},
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
