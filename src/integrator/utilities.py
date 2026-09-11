"""
A gathering of utility functions for dealing with model interconnectivity.

Dev Note:  At some review point, some decisions may move these back & forth with parent
models after it is decided if it is a utility job to do .... or a class method.

Additionally, there is probably some renaming due here for consistency
"""

import typing

# Import packages
from collections import defaultdict, namedtuple
from logging import getLogger
from pathlib import Path

import pandas as pd
import pyomo.opt as pyo
from pyomo.environ import ConcreteModel
from pyomo.opt import OptSolver

# Import python modules
from definitions import PROJECT_ROOT

if typing.TYPE_CHECKING:
    from src.models.electricity.electricity_model import PowerModel

    # pyrefly: ignore[missing-import]  - hydrogen module removed from this fork
    from src.models.hydrogen.model.h2_model import H2Model

# Establish logger
logger = getLogger(__name__)


# TODO:  This might be a good use case for a persistent solver (1-each) for both the
#        elec & hyd...  hmm
def simple_solve(m: ConcreteModel):
    """A simple solve routine."""
    # Note:  this is a prime candidate to split into 2 persistent solvers!!
    # TODO:  experiment with pyomo's persistent solver interface, one for each ELEC, H2
    opt = select_solver(m)
    res = opt.solve(m)
    if pyo.check_optimal_termination(res):
        return
    raise RuntimeError('failed solve in iterator')


def simple_solve_no_opt(m: ConcreteModel, opt: OptSolver):
    """Solve concrete model using solver factory object.

    Parameters
    ----------
    m : ConcreteModel
        Pyomo model
    opt: OptSolver
        Solver object initiated prior to solve
    """
    # Note:  this is a prime candidate to split into 2 persistent solvers!!
    # TODO:  experiment with pyomo's persistent solver interface, one for each ELEC, H2
    logger.info('solving w/ solver-factory object instantiated outside of loop')
    res = opt.solve(m)
    if pyo.check_optimal_termination(res):
        return
    raise RuntimeError('failed solve in iterator')


def select_solver(instance: ConcreteModel, nonlinear: bool = False) -> OptSolver:
    """Select solver based on learning method.

    Parameters
    ----------
    instance : PowerModel
        electricity pyomo model
    nonlinear : bool, default False
        select the nonlinear solver.  Callers on the electricity path pass
        ``expansion_learning_type is ExpansionLearningType.NONLINEAR``.

    Returns
    -------
    solver type (?)
        The pyomo solver
    """
    # default = linear solver
    solver_name = 'appsi_highs'
    opt = pyo.SolverFactory(solver_name)

    if nonlinear:  # if nonlinear learning, set to ipopt
        solver_name = 'ipopt'
        opt = pyo.SolverFactory(solver_name, tee=True)  # , tee=True
        # Select options. The prefix "OF_" tells pyomo to create an options file
        opt.options['OF_mu_strategy'] = 'adaptive'
        opt.options['OF_num_linear_variables'] = 100000
        opt.options['OF_mehrotra_algorithm'] = 'yes'
        # Ask IPOPT to print options so you can confirm that they were used by the solver
        opt.options['print_user_options'] = 'yes'

    logger.info('Using Solver: ' + solver_name)

    return opt


# a named tuple for common electric model index structure (EI=Electrical Index)
EI = namedtuple('EI', ['region', 'year', 'hour'])
"""(region, year, hour)"""
HI = namedtuple('HI', ['region', 'year'])
"""(region, year)"""


def get_elec_price(instance: PowerModel | ConcreteModel, block=None) -> pd.DataFrame:
    """Pulls hourly electricity prices from completed PowerModel and de-weights them.

    Prices from the duals are weighted by the day and year weights applied in the OBJ function
    This function retrieves the prices for all hours and removes the day and annual weights to
    return raw prices (and the day weights to use as needed)

    Parameters
    ----------
    instance : PowerModel
        solved electricity model

    block: ConcreteModel
        reference to the block if the electricity model is a block within a larger model

    Returns
    -------
    pd.DataFrame
        df of raw prices and the day weights to re-apply (if needed)
        columns: [r, y, hour, day_weight, raw_price]
    """
    if block:
        c = block.demand_balance
        model = block
    else:
        c = instance.demand_balance
        model = instance

    # get electricity price duals and de-weight them (costs in the OBJ are up-weighted
    # by the day weight and year weight)
    records = []
    # pyrefly: ignore[not-iterable]  - pyomo's IndexedComponent.__iter__ is untyped
    for index in c:
        # pyrefly: ignore[not-iterable]  - index is a pyomo key tuple, untyped
        ei = EI(*index)
        # pyrefly: ignore[bad-index, bad-argument-type]  - pyomo Suffix/Constraint access is untyped
        weighted_value = float(instance.dual[c[index]])

        # gather the weights for this hour
        # pyrefly: ignore[bad-index]  - pyomo params attached at runtime read as Component
        day = model.map_hour_day[ei.hour]
        # pyrefly: ignore[bad-index]  - pyomo params attached at runtime read as Component
        day_wt = model.weight_day[day]
        # pyrefly: ignore[bad-index]  - pyomo params attached at runtime read as Component
        year_wt = model.weight_year[ei.year]

        # remove the weighting & record
        # pyrefly: ignore[unsupported-operation]  - pyomo ParamData arithmetic is untyped
        unweighted_cost = weighted_value / day_wt / year_wt
        records.append((*ei, day_wt, unweighted_cost))

    res = pd.DataFrame.from_records(
        data=records, columns=['region', 'year', 'hour', 'day_weight', 'raw_price']
    )
    return res


def get_annual_wt_avg(elec_price: pd.DataFrame) -> pd.DataFrame:
    """Takes annual weighted average of hourly electricity prices.

    Parameters
    ----------
    elec_price : pd.DataFrame
        hourly electricity prices

    Returns
    -------
    pd.DataFrame
        annual weighted average electricity prices, indexed by (region, year) with a single
        ``weighted_ave_price`` column
    """

    def my_agg(x):
        """Aggregate average price based on day weights.

        Parameters
        ----------
        x : pd.DataFrame.groupby
            original price frame

        Returns
        -------
        pd.Series
            series containing average price based on day weights
        """
        names = {
            'weighted_ave_price': (x['day_weight'] * x['raw_price']).sum() / x['day_weight'].sum()
        }
        return pd.Series(names, index=['weighted_ave_price'])

    # find annual weighted average, weight by day weights
    elec_price_ann = elec_price.groupby(['region', 'year']).apply(my_agg)

    return elec_price_ann


def regional_annual_prices(m: PowerModel | ConcreteModel, block=None) -> dict[HI, float]:
    """Pulls all regional annual weighted electricity prices.

    Parameters
    ----------
    m : typing.Union['PowerModel', ConcreteModel]
        solved PowerModel
    block :  optional
        solved block model if applicable, by default None

    Returns
    -------
    dict[HI, float]
        dict with regional annual electricity prices
    """
    ep = get_elec_price(m, block)
    ap = get_annual_wt_avg(ep)

    # convert from dataframe to dictionary
    lut = {}
    for r in ap.to_records():
        region, year, price = r
        lut[HI(region=region, year=year)] = price

    return lut


def convert_elec_price_to_lut(prices: list[tuple[EI, float]]) -> dict[EI, float]:
    """Convert electricity prices to dictionary, look up table.

    Parameters
    ----------
    prices : list[tuple[EI, float]]
        list of prices

    Returns
    -------
    dict[EI, float]
        dict of prices
    """
    res = {}
    for row in prices:
        ei, price = row
        res[ei] = price
    return res


def poll_hydrogen_price(model: H2Model | ConcreteModel, block=None) -> list[tuple[HI, float]]:
    """Retrieve the price of H2 from the H2 model.

    Parameters
    ----------
    model : H2Model
        the model to poll
    block: optional
        block model to poll

    Returns
    -------
    list[tuple[HI, float]]
        list of H2 Index, price tuples
    """
    # ensure valid class
    if not isinstance(model, ConcreteModel):
        raise TypeError('invalid input')

    # TODO:  what should happen if there is no entry for a particular region (no hubs)?
    if block:
        demand_constraint = block.demand_constraint
    else:
        demand_constraint = model.demand_constraint
    # print('************************************\n')
    # print(list(demand_constraint.index_set()))
    # print(list(model.dual.keys()))

    # type: ignore[bad-index, missing-attribute]
    rows = [(HI(*k), model.dual[v]) for k, v in demand_constraint.items()]
    logger.debug('current h2 prices:  %s', rows)
    return rows  # type: ignore[bad-return]


def convert_h2_price_records(records: list[tuple[HI, float]]) -> dict[HI, float]:
    """Simple coversion from list of records to a dictionary LUT.

    Repeat entries should not occur and will generate an error.
    """
    res = {}
    for hi, price in records:
        if hi in res:
            logger.error('Duplicate index for h2 price received in coversion: %s', hi)
            raise ValueError('duplicate index received see log file.')
        res[hi] = price

    return res


def poll_year_avg_elec_price(price_list: list[tuple[EI, float]]) -> dict[HI, float]:
    """Retrieve a REPRESENTATIVE price at the annual level from a listing of prices.

    This function computes the AVERAGE elec price for each region-year combo

    Parameters
    ----------
    price_list : list[tuple[EI, float]]
        input price list

    Returns
    -------
    dict[HI, float]
        a dictionary of (region, year): price
    """
    year_region_records = defaultdict(list)
    res = {}
    for ei, price in price_list:
        year_region_records[HI(region=ei.region, year=ei.year)].append(price)

    # now gather the averages...
    for hi in year_region_records:
        res[hi] = sum(year_region_records[hi]) / len(year_region_records[hi])

    logger.debug('Computed these region-year averages for elec price: \n\t %s', res)
    return res


def create_temporal_mapping(temporal_resolution):
    """Combines the electricity model input mapping files into a master temporal mapping frame.

    The df is used to build multiple temporal parameters used within the  model. It creates a
    single dataframe that has 8760 rows for each hour in the year. Each hour in the year is
    assigned a season type, day type, and hour type used in the model. This defines the number of
    time periods the model will use based on cw_s_day and cw_hr inputs.

    Parameters
    ----------
    temporal_resolution : str
        ``CommonConfig.temporal_resolution``; ``'default'`` reads the base crosswalks, any
        other value selects the matching pair under ``temporal_mapping/``.

    Returns
    -------
    dataframe
        a dataframe with 8760 rows that include each hour, hour type, day, day type, and season.
        It also includes the weights for each day type and hour type.
    """
    # Temporal Sets - read data
    # SD = season/day; hr = hour
    data_root = Path(PROJECT_ROOT, 'input/integrator')
    if temporal_resolution == 'default':
        sd_file = pd.read_csv(data_root / 'cw_s_day.csv')
        hr_file = pd.read_csv(data_root / 'cw_hr.csv')
    else:
        cw_s_day = 'cw_s_day_' + temporal_resolution + '.csv'
        cw_hr = 'cw_hr_' + temporal_resolution + '.csv'
        sd_file = pd.read_csv(data_root / 'temporal_mapping' / cw_s_day)
        hr_file = pd.read_csv(data_root / 'temporal_mapping' / cw_hr)

    # set up mapping for seasons and days
    df1 = sd_file
    df4 = df1.groupby(by=['Map_day'], as_index=False).count()
    df4 = df4.rename(columns={'Index_day': 'WeightDay'}).drop(columns=['Map_s'])
    df1 = pd.merge(df1, df4, how='left', on=['Map_day'])

    # set up mapping for hours
    df2 = hr_file
    df3 = df2.groupby(by=['Map_hour'], as_index=False).count()
    df3 = df3.rename(columns={'Index_hour': 'WeightHour'})
    df2 = pd.merge(df2, df3, how='left', on=['Map_hour'])

    # combine season, day, and hour mapping
    df = pd.merge(df1, df2, how='cross')
    df['hour'] = df.index
    df['hour'] = df['hour'] + 1
    df['Map_hour'] = (df['Map_day'] - 1) * df['Map_hour'].max() + df['Map_hour']
    # df.to_csv(data_root/'temporal_map.csv',index=False)

    # convert the Season to string
    df['Map_s'] = df['Map_s'].astype(str)

    return df
