"""Gauss-Seidel coupling between the natural gas model and an electricity model.

Two quantities cross the boundary each iteration:

    electricity -> gas    gas burn by gas-fired generation, aggregated to gas regions
    gas -> electricity    the regional gas price, as a fuel-cost adjustment

The electricity side of that exchange mirrors the pattern the electricity model already uses
for hydrogen: a mutable price parameter written between solves, entering the dispatch cost.
See docs/COUPLING.md for what must be added to the electricity model.

Index order is DISCOVERED, not assumed
--------------------------------------
Electricity models can disagree on the index order of ``generation_total``. Two
orderings we have used:

    (region, tech, step, year, hour)
    (tech, year, region, step, hour)

Both are five-tuples of the same types, so unpacking positionally against the wrong ordering
raises no error, it silently binds ``tech`` to a region id, filters on the wrong values, and
returns a plausible but entirely wrong gas demand. No traceback is produced; the numbers are wrong.

``resolve_generation_index`` therefore determines the position of each role from the model
itself, preferring the declared constituent sets and falling back to value membership. Call
``check_coupling_contract`` once at setup to fail loudly if the electricity model is missing
something rather than discovering it mid-iteration.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import pandas as pd
from pyomo.environ import value

logger = logging.getLogger(__name__)

# Gas-fired technologies whose generation consumes natural gas, and the representative heat
# rates used to convert generation to gas volume. VERIFY these against the technology table of
# the electricity model you are coupling to before trusting any result.
#
# Wrong tech numbers fail in one of two ways, neither of which raises: numbers that match no
# technology give zero gas demand, and numbers that match the wrong technology attribute coal
# or nuclear generation to the gas market. For cnems-models these are correct, see
# src/models/electricity/README.md lines 84-85, which name 3 and 4 exactly.
#
# The heat rates are representative constants carried in from the source distribution, NOT
# derived from this repo's data. CT is the less efficient of the two, hence the higher figure.
# They set the scale of everything crossing the boundary, so a run whose polled demand is
# consistently biased (rather than wrong by orders of magnitude) should suspect these first.
NG_GAS_TECHS: set[int] = {3, 4}  # 3 = gas CT, 4 = gas CC
NG_HEAT_RATE_MMBTUPERMWH: dict[int, float] = {3: 9.51, 4: 7.12}

# Role -> the attribute names an electricity model might use for that set.
# Strategy 1 of index discovery matches declared set names against this table, so adding a
# naming convention here is how you teach the coupling about a new electricity model without
# touching any of the logic below.
_ROLE_SET_NAMES: dict[str, tuple[str, ...]] = {
    'region': ('region', 'regions', 'region_analyze', 'r'),
    'tech': ('tech', 'techs', 'technology'),
    'step': ('step', 'steps'),
    'year': ('year', 'years'),
    'hour': ('hour', 'hours', 'hr'),
}

_DEFAULT_REGION_MAP = (
    Path(__file__).resolve().parents[2] / 'input' / 'natural_gas' / 'elec_to_ng_region_map.csv'
)


# ---------------------------------------------------------------------------
# Region crosswalk
# ---------------------------------------------------------------------------


def load_ng_region_map(path: str | Path | None = None) -> dict:
    """Load the electricity-region -> gas-region crosswalk.

    The returned mapping is keyed by BOTH the string and the integer form of each electricity
    region id, because electricity models in this lineage differ on whether region identifiers
    are strings or integers. Looking up either form succeeds.

    Returns
    -------
    dict
        {elec_region (str and int): ng_region (str)}
    """
    p = Path(path) if path is not None else _DEFAULT_REGION_MAP
    # comment='#' keeps the provenance header lines out of the frame; strip() guards against
    # trailing spaces in hand-edited CSV headers, which would otherwise break the column lookup.
    df = pd.read_csv(p, comment='#')
    df.columns = df.columns.str.strip()

    out: dict = {}
    for _, row in df.iterrows():
        ng = str(row['ng_region']).strip()
        raw = str(row['elec_region']).strip()
        # Store under the string form always, and under the int form when the id is numeric.
        # int(float(raw)) rather than int(raw) so that '7.0', which is what pandas produces
        # from a numeric column, still yields 7. A non-numeric id (e.g. 'CA') just skips the
        # int alias. This double-keying is what lets one crosswalk serve electricity models
        # that identify regions as ints and ones that use strings.
        out[raw] = ng
        try:
            out[int(float(raw))] = ng
        except TypeError, ValueError:
            pass
    logger.info('Loaded electricity->gas region map: %d electricity regions', len(df))
    return out


# ---------------------------------------------------------------------------
# Index-order discovery
# ---------------------------------------------------------------------------


def _model_role_sets(elec_model) -> dict[str, set]:
    """Collect the electricity model's sets, keyed by the role each plays."""
    found: dict[str, set] = {}
    for role, names in _ROLE_SET_NAMES.items():
        for nm in names:
            comp = getattr(elec_model, nm, None)
            if comp is None:
                continue
            try:
                members = set(comp)
            except TypeError:
                continue
            if members:
                found[role] = members
                break
    return found


def resolve_generation_index(elec_model, sample: int = 400) -> dict[str, int]:
    """Determine which position in the generation index holds each role.

    Strategy 1, declared constituent sets. If the index is a Pyomo set product, read the
    ordered constituent set names and match them to roles by name.

    Strategy 2, value membership. Sample index tuples and, for each position, find which role's
    set contains every sampled value. Positions matching exactly one role are assigned first;
    the rest are resolved by elimination.

    Returns
    -------
    dict[str, int]
        {'region': i, 'tech': j, 'step': k, 'year': l, 'hour': m}

    Raises
    ------
    RuntimeError
        If the ordering cannot be determined unambiguously. Failing here is deliberate, a
        guess would produce silently wrong results.
    """
    idx_set = elec_model.generation_total.index_set()

    # Strategy 1: named constituent sets.
    try:
        subs = list(idx_set.subsets())
        if len(subs) >= 5:
            name_to_role = {nm: role for role, names in _ROLE_SET_NAMES.items() for nm in names}
            pos = {}
            for i, s in enumerate(subs):
                role = name_to_role.get(str(s.name).lower())
                if role is not None and role not in pos:
                    pos[role] = i
            if set(pos) == set(_ROLE_SET_NAMES):
                logger.info('generation_total index order from declared sets: %s', pos)
                return pos
    except AttributeError, TypeError:
        pass

    # Strategy 2: value membership.
    role_sets = _model_role_sets(elec_model)
    missing = set(_ROLE_SET_NAMES) - set(role_sets)
    if missing:
        raise RuntimeError(
            f'Cannot resolve the generation index: the electricity model exposes no set for '
            f'{sorted(missing)}. Looked for these attribute names: '
            + '; '.join(f'{r}: {_ROLE_SET_NAMES[r]}' for r in sorted(missing))
        )

    tuples = []
    for i, t in enumerate(idx_set):
        if i >= sample:
            break
        tuples.append(t if isinstance(t, tuple) else (t,))
    if not tuples:
        raise RuntimeError('generation_total index set is empty; cannot resolve index order.')

    width = len(tuples[0])
    if width != 5:
        raise RuntimeError(f'Expected a 5-tuple generation index, found width {width}.')

    # For each position, collect every role whose set contains ALL sampled values there.
    # A position usually matches more than one role, step={1} and hour={1,2} both sit inside
    # year's range in small models, so this is a candidate set, not an answer.
    candidates: dict[int, set[str]] = {}
    for p in range(width):
        vals = {t[p] for t in tuples}
        ok = set()
        for role, members in role_sets.items():
            if all(v in members for v in vals):
                ok.add(role)
        candidates[p] = ok

    # Constraint propagation: repeatedly assign any position left with exactly one candidate,
    # remove that role from every other position, and go round again. This is the standard
    # elimination used for logic puzzles, and it terminates because each pass either assigns a
    # role (finitely many) or changes nothing and exits.
    pos: dict[str, int] = {}
    changed = True
    while changed:
        changed = False
        for p, opts in candidates.items():
            opts -= set(pos)
            if len(opts) == 1:
                role = next(iter(opts))
                if role not in pos:
                    pos[role] = p
                    changed = True

    if set(pos) != set(_ROLE_SET_NAMES):
        raise RuntimeError(
            'Could not unambiguously resolve the generation index order.\n'
            f'  resolved: {pos}\n  candidates per position: {candidates}\n'
            'Pass the ordering explicitly rather than letting this guess.'
        )
    logger.info('generation_total index order from value membership: %s', pos)
    return pos


def check_coupling_contract(elec_model) -> dict[str, int]:
    """Validate that the electricity model exposes everything the coupling needs.

    Call once at setup. Raises with an actionable message rather than failing mid-iteration.

    Returns
    -------
    dict[str, int]
        The resolved generation index order, to pass to the transfer functions.
    """
    problems = []
    for attr, why in (
        ('generation_total', 'generation by region/tech/step/year/hour, read to compute gas burn'),
        ('WeightDay', 'representative-day weights, converts hourly generation to annual'),
        ('MapHourDay', 'hour -> representative day'),
        (
            'NGFuelAdj',
            'mutable gas fuel-cost adjustment, WRITTEN each iteration; see docs/COUPLING.md',
        ),
    ):
        if not hasattr(elec_model, attr):
            problems.append(f'  missing {attr}: {why}')

    adj = getattr(elec_model, 'NGFuelAdj', None)
    if adj is not None and not adj.mutable:
        problems.append(
            '  NGFuelAdj exists but is not mutable=True; it cannot be updated between solves'
        )

    if problems:
        raise RuntimeError(
            'Electricity model does not satisfy the gas coupling contract:\n' + '\n'.join(problems)
        )

    pos = resolve_generation_index(elec_model)
    logger.info('Coupling contract satisfied.')
    return pos


# ---------------------------------------------------------------------------
# electricity -> gas
# ---------------------------------------------------------------------------


def poll_ng_gas_demand(
    elec_model, elec_to_ng: dict, index_pos: dict[str, int] | None = None
) -> dict:
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

    pos = index_pos or resolve_generation_index(elec_model)
    i_r, i_t, i_y, i_h = pos['region'], pos['tech'], pos['year'], pos['hour']

    res: dict = defaultdict(float)
    skipped_regions: set = set()

    for idx in elec_model.generation_total.index_set():
        tech = idx[i_t]
        if int(tech) not in NG_GAS_TECHS:
            continue
        ng_region = elec_to_ng.get(idx[i_r])
        if ng_region is None:
            skipped_regions.add(idx[i_r])
            continue
        # Scale the representative hour up to its share of the year. generation_total is the
        # value for ONE representative hour; WeightDay says how many real days the day that
        # hour belongs to stands for. Omitting this weight understates annual gas burn by
        # roughly two orders of magnitude while leaving the regional shares looking right.
        hr = idx[i_h]
        gen_gwh = value(elec_model.generation_total[idx]) * value(
            elec_model.WeightDay[elec_model.MapHourDay[hr]]
        )
        # GWh -> Bcf. GWh x MMBtu/MWh gives thousands of MMBtu (since 1 GWh = 1000 MWh), and
        # 1 Bcf ~ 1e6 MMBtu, so the two powers of ten collapse to a single division by 1e3.
        # Accumulate onto (gas region, year): many electricity regions map to one gas region.
        res[GI(region=ng_region, year=int(idx[i_y]))] += (
            gen_gwh * NG_HEAT_RATE_MMBTUPERMWH[int(tech)] / 1e3
        )

    if skipped_regions:
        logger.warning(
            'No gas region mapped for electricity regions %s, their gas burn is '
            'excluded from gas demand.',
            sorted(map(str, skipped_regions)),
        )
    logger.debug('Polled gas demand for %d region-years', len(res))
    return dict(res)


# ---------------------------------------------------------------------------
# gas -> electricity
# ---------------------------------------------------------------------------


def update_ng_fuel_adj(
    elec_model, ng_prices: dict, elec_to_ng: dict, base_ng_prices: dict, alpha: float = 1.0
) -> int:
    """Write the gas-price signal into the electricity model's NGFuelAdj parameter.

    The value written is a DELTA against a reference price captured at the first gas solve, not
    an absolute price:

        NGFuelAdj = (p - p_ref) [$/MMBtu] x heat rate [MMBtu/MWh] x 1000 [MWh/GWh]  ->  $/GWh

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

    pos = _resolve_fuel_adj_index(elec_model)
    i_r, i_t = pos['region'], pos['tech']

    updated = 0
    for key in elec_model.NGFuelAdj:
        # Three guards, each leaving the entry at its previous value (0.0 on the first pass):
        # a non-gas technology, an electricity region outside the crosswalk, or a region-year
        # the gas model did not price. Skipping rather than writing zero matters, because a
        # zero would be indistinguishable from "gas is exactly at its reference price".
        tech = key[i_t]
        if int(tech) not in NG_HEAT_RATE_MMBTUPERMWH:
            continue
        ng_region = elec_to_ng.get(key[i_r])
        if ng_region is None:
            continue
        yr = key[pos['year']]
        gi = GI(region=ng_region, year=int(yr))
        if gi not in ng_prices or gi not in base_ng_prices:
            continue

        # The delta, converted to the electricity objective's units:
        # ($/MMBtu) x (MMBtu/MWh) = $/MWh, then x1000 MWh/GWh = $/GWh.
        # base_ng_prices must come from the FIRST gas solve and never be reassigned. Recapture
        # it each iteration and this difference is identically zero every time, the coupling
        # transmits nothing, converges immediately, and looks healthy.
        adj_full = (
            (ng_prices[gi] - base_ng_prices[gi]) * NG_HEAT_RATE_MMBTUPERMWH[int(tech)] * 1000.0
        )
        # Under-relaxation blends toward the value already in the parameter. Gas price and
        # gas-fired dispatch drive each other hard, so alpha=1.0 tends to oscillate; the loop
        # should damp here as well as on both gas-side demand updates.
        if alpha < 1.0:
            adj = alpha * adj_full + (1.0 - alpha) * value(elec_model.NGFuelAdj[key])
        else:
            adj = adj_full
        elec_model.NGFuelAdj[key] = adj
        updated += 1

    logger.debug('Updated %d NGFuelAdj entries (alpha=%.2f)', updated, alpha)
    return updated


def _resolve_fuel_adj_index(elec_model) -> dict[str, int]:
    """Resolve role positions in the NGFuelAdj index the same way as the generation index.

    Separate from resolve_generation_index because NGFuelAdj is indexed by season rather than
    hour, and only three roles matter here: region, tech, and year. Requiring all five would
    fail on a valid parameter. Like its sibling it raises rather than guessing, an
    ambiguous resolution would write the price signal onto the wrong technologies.
    """
    idx_set = elec_model.NGFuelAdj.index_set()
    name_to_role = {nm: role for role, names in _ROLE_SET_NAMES.items() for nm in names}
    name_to_role['season'] = 'season'
    try:
        subs = list(idx_set.subsets())
        pos = {}
        for i, s in enumerate(subs):
            role = name_to_role.get(str(s.name).lower())
            if role is not None and role not in pos:
                pos[role] = i
        if {'region', 'tech', 'year'} <= set(pos):
            return pos
    except AttributeError, TypeError:
        pass

    role_sets = _model_role_sets(elec_model)
    tuples = [k if isinstance(k, tuple) else (k,) for k in list(elec_model.NGFuelAdj)[:400]]
    if not tuples:
        raise RuntimeError('NGFuelAdj index set is empty.')
    pos = {}
    for role in ('region', 'tech', 'year'):
        members = role_sets.get(role)
        if members is None:
            raise RuntimeError(f'Electricity model exposes no set for {role}.')
        hits = [p for p in range(len(tuples[0])) if all(t[p] in members for t in tuples)]
        if len(hits) != 1:
            raise RuntimeError(
                f'Cannot resolve position of {role} in the NGFuelAdj index '
                f'(candidates {hits}). Declare the index over named sets, or pass it explicitly.'
            )
        pos[role] = hits[0]
    return pos
