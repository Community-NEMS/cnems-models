"""Tests for the price-extraction interface between the electricity model and the integrator."""

import pytest

from src.common.integrated_model_sequencer import IterationStatus
from src.integrator.utilities import HI, get_elec_price, regional_annual_prices
from tests.electric.mini_model import add_gas_ct


def test_poll_elec_prices(mini_data, build_mini):
    """Polled prices are the marginal generation cost, with the day and year weights removed.

    GasCT ($20/MWh, 100 MW) is marginal against the 80 MW load of day 1.  Day 2's 150 MW load
    also needs Gas ($30/MWh).  Day 2 stands for 3 days, so the annual price weights its two hours
    3:1 against day 1's:  (2 x 1 x 20 + 2 x 3 x 30) / (2 x 1 + 2 x 3) = 27.5.
    """
    add_gas_ct(mini_data)
    for region in mini_data.regions:
        mini_data.set_load(region, {1: 80.0, 2: 80.0, 3: 150.0, 4: 150.0})
    cw = mini_data.cw_temporal
    cw.loc[cw['Map_day'] == 2, 'WeightDay'] = 3.0
    sequencer, model = build_mini()
    _, status = sequencer.solve_model()
    assert status is IterationStatus.BEST

    prices = get_elec_price(model).set_index(['region', 'year', 'hour'])

    for r, y, hr in mini_data.every('region', 'year', 'hour'):
        day_two = hr in (3, 4)
        assert prices.loc[(r, y, hr), 'day_weight'] == (3.0 if day_two else 1.0)
        assert prices.loc[(r, y, hr), 'raw_price'] == pytest.approx(30.0 if day_two else 20.0)

    expected = {HI(region=r, year=y): 27.5 for r, y in mini_data.every('region', 'year')}
    assert regional_annual_prices(model) == pytest.approx(expected)
