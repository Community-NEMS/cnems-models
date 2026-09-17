"""Gauss-Seidel coupling between the natural gas model and an electricity model.

Two quantities cross the boundary each iteration:

    electricity -> gas    gas burn by gas-fired generation, aggregated to gas regions
    gas -> electricity    the regional gas price, as a fuel-cost adjustment

The electricity side of that exchange mirrors the pattern the electricity model already uses
for hydrogen: a mutable price parameter written between solves, entering the dispatch cost.

What the electricity model must expose
--------------------------------------
``generation_total``, ``weight_day`` and ``map_hour_day``, all of which already exist, plus a
``ng_fuel_adj`` Param indexed exactly like ``supply_price``
(region, tech, step, year, season) and declared ``within=Reals, mutable=True``. It carries a
DELTA against a reference gas price, not a price level, so it goes negative whenever gas is
cheaper than its reference. Add it to the dispatch-cost term alongside ``supply_price``;
both are $/GWh, so no conversion is needed. ``check_coupling_contract`` validates all four up front.

Index order is DECLARED, not discovered
--------------------------------------
``generation_total`` is built at src/models/electricity/model_sets.py:224-229 as

    sorted((idx.region, idx.tech, idx.step, idx.year, hr) for ...)

so its order is fixed by construction, and ``ng_fuel_adj`` mirrors ``supply_price``. Both are
declared in ``GENERATION_INDEX`` and ``FUEL_ADJ_INDEX`` below.

Call ``check_coupling_contract`` once at setup to fail loudly if the electricity model is
missing something rather than discovering it mid-iteration.
"""

import csv
import logging
from collections import defaultdict
from pathlib import Path

from pyomo.environ import value

from definitions import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Gas-fired technologies whose generation consumes natural gas, and the representative heat
# rates used to convert generation to gas volume. VERIFY these against the technology table of
# the electricity model you are coupling to before trusting any result.
#
# Wrong tech numbers fail in one of two ways, neither of which raises: numbers that match no
# technology give zero gas demand, and numbers that match the wrong technology attribute coal
# or nuclear generation to the gas market. For cnems-models these are correct, see
# src/models/electricity/README.md lines 84-85, which name '3' and '4' exactly.
#
# The heat rates are representative constants carried in from the source distribution, NOT
# derived from this repo's data. CT is the less efficient of the two, hence the higher figure.
# They set the scale of everything crossing the boundary, so a run whose polled demand is
# consistently biased (rather than wrong by orders of magnitude) should suspect these first.
NG_GAS_TECHS: set[str] = {'3', '4'}  # '3' = gas CT, '4' = gas CC
NG_HEAT_RATE_MMBTUPERMWH: dict[str, float] = {'3': 9.51, '4': 7.12}

# Position of each role in the index tuples this module reads and writes. Fixed by construction
# in the electricity model, so declared here rather than discovered:
#   generation_total  src/models/electricity/model_sets.py:224-229
#   ng_fuel_adj       mirrors supply_price, src/models/electricity/electricity_model.py:304-312
GENERATION_INDEX: dict[str, int] = {'region': 0, 'tech': 1, 'step': 2, 'year': 3, 'hour': 4}
FUEL_ADJ_INDEX: dict[str, int] = {'region': 0, 'tech': 1, 'step': 2, 'year': 3, 'season': 4}

_DEFAULT_REGION_MAP = PROJECT_ROOT / 'input' / 'natural_gas' / 'elec_to_ng_region_map.csv'


# ---------------------------------------------------------------------------
# Region crosswalk
# ---------------------------------------------------------------------------


def load_ng_region_map(path: str | Path | None = None) -> dict[str, str]:
    """Load the electricity-region -> gas-region crosswalk.

    Both columns are read as strings and stripped, matching the electricity model, whose region
    ids are strings.

    Returns
    -------
    dict[str, str]
        {elec_region: ng_region}
    """
    p = Path(path) if path is not None else _DEFAULT_REGION_MAP
    with p.open(newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
        # Validate the header before reading rows. DictReader takes whatever the first line is
        # as the header, so a file carrying a leading '#' note yields a KeyError naming a
        # column that is plainly present in the file. This reader has no comment support.
        missing = sorted({'elec_region', 'ng_region'}.difference(reader.fieldnames or ()))
        if missing:
            raise ValueError(
                f'{p} is missing required column(s) {missing}; header reads '
                f'{list(reader.fieldnames or [])}. This file must be plain CSV with no '
                f'comment rows, since a leading "#" line is parsed as the header.'
            )
        out = {row['elec_region'].strip(): row['ng_region'].strip() for row in reader}
    if not out:
        raise ValueError(f'{p} has a valid header but no region rows')
    logger.info('Loaded electricity->gas region map: %d electricity regions', len(out))
    return out


def check_coupling_contract(elec_model) -> None:
    """Validate that the electricity model exposes everything the coupling needs.

    Call once at setup. Raises with an actionable message rather than failing mid-iteration,
    which is the point: a half-wired electricity model should fail before the first expensive
    solve, not on the first write.

    Raises
    ------
    RuntimeError
        If a required component is missing, or ``ng_fuel_adj`` is present but not mutable.
    """
    problems = []
    for attr, why in (
        ('generation_total', 'generation by region/tech/step/year/hour, read to compute gas burn'),
        ('weight_day', 'representative-day weights, converts hourly generation to annual'),
        ('map_hour_day', 'hour -> representative day'),
        (
            'ng_fuel_adj',
            (
                'mutable gas fuel-cost adjustment, WRITTEN each iteration. Declare it '
                'indexed like supply_price (region, tech, step, year, season), '
                'within=Reals, mutable=True'
            ),
        ),
    ):
        if not hasattr(elec_model, attr):
            problems.append(f'  missing {attr}: {why}')

    adj = getattr(elec_model, 'ng_fuel_adj', None)
    if adj is not None and not adj.mutable:
        problems.append(
            '  ng_fuel_adj exists but is not mutable=True; it cannot be updated between solves'
        )

    if problems:
        raise RuntimeError(
            'Electricity model does not satisfy the gas coupling contract:\n' + '\n'.join(problems)
        )

    logger.info('Coupling contract satisfied.')


# ---------------------------------------------------------------------------
# electricity -> gas
# ---------------------------------------------------------------------------


def poll_ng_gas_demand(elec_model, elec_to_ng: dict[str, str]) -> dict:
    """Compute annual gas demand [Bcf] by gas region from an electricity solution.

    Sums day-weighted generation for gas technologies, converts to Bcf with representative heat
    rates, and aggregates electricity regions onto gas regions.

        Bcf = GWh x MMBtu/MWh x 1000 MWh/GWh / 1e6 MMBtu/Bcf
            = GWh x MMBtu/MWh / 1000

    Returns
    -------
    dict
        {GI(region, year): gas demand in Bcf/yr}
    """
    from src.models.natural_gas.ng_model import GI

    i_r, i_t, i_y, i_h = (
        GENERATION_INDEX['region'],
        GENERATION_INDEX['tech'],
        GENERATION_INDEX['year'],
        GENERATION_INDEX['hour'],
    )

    res: dict = defaultdict(float)
    skipped_regions: set[str] = set()

    for idx in elec_model.generation_total.index_set():
        tech = idx[i_t]
        if tech not in NG_GAS_TECHS:
            continue
        ng_region = elec_to_ng.get(idx[i_r])
        if ng_region is None:
            skipped_regions.add(idx[i_r])
            continue
        # Scale the representative hour up to its share of the year. generation_total is the
        # value for ONE representative hour; weight_day says how many real days the day that
        # hour belongs to stands for. Omitting this weight understates annual gas burn by
        # roughly two orders of magnitude while leaving the regional shares looking right.
        hr = idx[i_h]
        # pyrefly: ignore[unsupported-operation]  - pyomo's value() will not be None in solved mdl
        gen_gwh = value(elec_model.generation_total[idx]) * value(
            elec_model.weight_day[elec_model.map_hour_day[hr]]
        )
        # GWh -> Bcf. GWh x MMBtu/MWh gives thousands of MMBtu (since 1 GWh = 1000 MWh), and
        # 1 Bcf ~ 1e6 MMBtu, so the two powers of ten collapse to a single division by 1e3.
        # Accumulate onto (gas region, year): many electricity regions map to one gas region.
        res[GI(region=ng_region, year=int(idx[i_y]))] += (
            gen_gwh * NG_HEAT_RATE_MMBTUPERMWH[tech] / 1e3
        )

    if skipped_regions:
        logger.warning(
            'No gas region mapped for electricity regions %s, their gas burn is '
            'excluded from gas demand.',
            sorted(skipped_regions),
        )
    logger.debug('Polled gas demand for %d region-years', len(res))
    return dict(res)


# ---------------------------------------------------------------------------
# gas -> electricity
# ---------------------------------------------------------------------------


def update_ng_fuel_adj(
    elec_model,
    ng_prices: dict,
    elec_to_ng: dict[str, str],
    base_ng_prices: dict,
    alpha: float = 1.0,
) -> int:
    """Write the gas-price signal into the electricity model's ng_fuel_adj parameter.

    The value written is a DELTA against a reference price captured at the first gas solve, not
    an absolute price:

        ng_fuel_adj = (p - p_ref) [$/MMBtu] x heat rate [MMBtu/MWh] x 1000 [MWh/GWh]  ->  $/GWh

    Passing a delta rather than a level preserves whatever fuel cost is already calibrated into
    the electricity model's own supply price: at convergence, if the gas market reproduces its
    reference prices, the adjustment is zero and the electricity model is exactly as calibrated.
    Writing an absolute price would overwrite that calibration on the first iteration.

    Under-relaxation damps Gauss-Seidel oscillation:

        adj_new = alpha x adj_full + (1 - alpha) x adj_current

    Returns
    -------
    int
        Number of parameter entries updated.
    """
    from src.models.natural_gas.ng_model import GI

    # Validated rather than clamped. Above 1.0 the branch below is skipped entirely and the full
    # adjustment is written, silently ignoring the value passed; below 0.0 it extrapolates away
    # from the current adjustment instead of damping toward it. Neither raises on its own.
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f'alpha must be in [0.0, 1.0], got {alpha}')

    i_r, i_t, i_y = FUEL_ADJ_INDEX['region'], FUEL_ADJ_INDEX['tech'], FUEL_ADJ_INDEX['year']

    updated = 0
    for key in elec_model.ng_fuel_adj:
        # Three guards, each leaving the entry at its previous value (0.0 on the first pass):
        # a non-gas technology, an electricity region outside the crosswalk, or a region-year
        # the gas model did not price. Skipping rather than writing zero matters, because a
        # zero would be indistinguishable from "gas is exactly at its reference price".
        tech = key[i_t]
        if tech not in NG_HEAT_RATE_MMBTUPERMWH:
            continue
        ng_region = elec_to_ng.get(key[i_r])
        if ng_region is None:
            continue
        yr = key[i_y]
        gi = GI(region=ng_region, year=int(yr))
        if gi not in ng_prices or gi not in base_ng_prices:
            continue

        # The delta, converted to the electricity objective's units:
        # ($/MMBtu) x (MMBtu/MWh) = $/MWh, then x1000 MWh/GWh = $/GWh.
        # base_ng_prices must come from the FIRST gas solve and never be reassigned. Recapture
        # it each iteration and this difference is identically zero every time, the coupling
        # transmits nothing, converges immediately, and looks healthy.
        adj_full = (ng_prices[gi] - base_ng_prices[gi]) * NG_HEAT_RATE_MMBTUPERMWH[tech] * 1000.0
        # Under-relaxation blends toward the value already in the parameter. Gas price and
        # gas-fired dispatch drive each other hard, so alpha=1.0 tends to oscillate; the loop
        # should damp here as well as on both gas-side demand updates.
        if alpha < 1.0:
            # pyrefly: ignore[unsupported-operation]  - pyomo's value() will not be None in solved mdl
            adj = alpha * adj_full + (1.0 - alpha) * value(elec_model.ng_fuel_adj[key])
        else:
            adj = adj_full
        elec_model.ng_fuel_adj[key] = adj
        updated += 1

    logger.debug('Updated %d ng_fuel_adj entries (alpha=%.2f)', updated, alpha)
    return updated
