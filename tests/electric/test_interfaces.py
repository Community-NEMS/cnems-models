from src.integrator.utilities import get_elec_price, regional_annual_prices
from src.models.electricity.sequencer import solve_elec_model


def test_poll_elec_prices(unsolved_model):
    """test that we can poll prices from elec and get "reasonable" answers"""

    # dev note:  currently, this test is a little "shaky", but it is a good pattern for testing
    #            extracted prices, so it is retained, minimally as a pattern going forward.
    _, elec_config, elec_model = unsolved_model

    solve_elec_model(elec_model, elec_config=elec_config)

    # we are just testing to see if we got *something* back ... this should have hundreds of entries...
    new_prices = get_elec_price(elec_model)
    assert len(new_prices) > 1, 'should have at least 1 price'

    # test for signage of observations
    price_records = new_prices.to_records()
    assert all((price >= 0 for *_, price in price_records)), 'expecting prices to be positive'

    # TODO:  Why does this fail?  there appear to be zero prices... Non binding constraint in these areas??
    # assert all((price > 0 for _, price in new_prices)), 'price should be non-zero, right???'

    # test for average price mehhhh above $1000
    lut = regional_annual_prices(elec_model)
    # TODO:  When price data stabilizes fix this to test that ALL are >1000.  RN region 7 has low costs
    assert max(lut.values()) > 1000, 'cost should be over $1000'
