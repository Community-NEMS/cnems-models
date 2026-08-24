"""C-NGMM: natural gas market model for the C-NEMS project.

Natural gas market model aligned with EIA's Natural Gas Market Module (NGMM) for the
National Energy Modeling System (NEMS), AEO 2025 documentation.

Naming: C-NGMM is this model; NGMM is EIA's module in NEMS. References of the form
"NGMM Eq 7" cite the source documentation, not this code.

A QP that mirrors the NGMM mathematical formulation. The model still operates
on 9 EIA census divisions and annual time steps (the integrator wiring depends
on this), but the economic structure matches NGMM:

  * Quadratic objective: maximizes consumer + producer surplus minus transport
    cost (NGMM Eq 7).
  * Piecewise-linear elastic supply curve per region (NGMM Eq 1-5, Fig 3.4)
    with 5 segments built around an expected production point (Q0, P0) using
    step elasticities (ELAS, AEO footnote values 0.8/0.7/0.5/0.3/0.2)
    and segment volume adjustments (CRV).
  * Piecewise-linear LNG export demand curve (NGMM Eq 14, Fig 3.6): LNG export
    is a price-responsive *variable* on a downward-sloping demand curve anchored
    at the world LNG price.
  * Piecewise-linear pipeline tariff curve (NGMM Eq 6, Fig 3.5): tariff rises
    sharply approaching 100 % utilization, encoding the NGMM hurdle-rate
    behaviour without the separate capacity-expansion QP.
  * Pipeline fuel loss on hub-to-hub flow, distribution / intrastate / storage
    losses on the demand balance, plant-fuel and gathering charges (NGMM Eq
    10, 11 and the gathering term in Eq 7).

What is *not* implemented (Tier 2/3 items not in scope of this rewrite):
  * State-level hubs (NGMM uses 50 + 3 Texas hubs; this model keeps the
    9 census-division grouping because the unified/Gauss-Seidel integrators
    rely on it via load_ng_region_map).
  * Monthly time resolution (NGMM solves each month independently).
  * STEO benchmarking and the separate capacity-expansion QP run.
  * NA/AD supply-type separation (the model keeps a single supply curve per
    region; HSM updates that distinguish NA from AD are aggregated into Q0).

After solving, shadow prices on the regional demand-balance constraints
(self.demand_balance) serve as regional citygate gas prices, the GS integrator
extracts them via poll_gas_price().

This module only builds the model. Solving is done by
``sequencer.py::NGSequencer``, and result extraction / reporting by
``postprocessor.py``.

Usage (standalone):
    python -m src.models.natural_gas.sequencer

That entry point reads ``run_configs/basic_ng_config.toml``; the years, the region
subset, and the output location are set there rather than on a command line. The
solver is probed for convex-QP capability at solve time, or forced with
``NGSequencer.solve_model(solver_name=...)``.

References
----------
    EIA Natural Gas Market Module of NEMS: Model Documentation 2025
        (NGMM_AEO2025.pdf alongside this file). Equation references in
        the code below cite this document.
    EIA Annual Energy Outlook 2025, Natural Gas Supply & Demand tables
    EIA Natural Gas Annual 2024, State & regional consumption tables
    EIA Underground Natural Gas Storage, Annual capacity data
"""

###############################################################################
# Setup
###############################################################################

from __future__ import annotations

import logging
from collections import defaultdict, namedtuple
from warnings import deprecated

from pyomo.environ import (
    ConcreteModel,
    Constraint,
    Expression,  # QP uses Expressions for derived production
    NonNegativeReals,
    Objective,
    Param,
    Set,
    Suffix,
    Var,
    minimize,
    quicksum,  # Fast linear sums in QP construction
    value,
)

from src.common.common_config import CommonConfig
from src.common.integrated_model import IntegratedModel
from src.common.models_modes import RunMode
from src.common.validators import region_check
from src.models.natural_gas.data import NGData
from src.models.natural_gas.ng_config import NGConfig

logger = logging.getLogger(__name__)

# Named index used when exchanging prices/quantities with other BlueSky models
GI = namedtuple('GI', ['region', 'year'])


###############################################################################
# EIA NGMM Reference Data
# ---------------------------------------------------------------------------
# All quantities in BCF/year (billion cubic feet per year).
# All prices / costs in $/MMBtu.
# Conversion: 1 BCF ≈ 1.02 × 10^6 MMBtu  (used implicitly; costs reported
#   as $/MMBtu for interpretability and scaled internally in the objective).
#
# Data sources:
#   Production capacities: EIA AEO 2023 Natural Gas Supply Module
#   Demand: EIA Natural Gas Annual 2022, disaggregated to census divisions
#   Pipeline capacities: EIA Natural Gas Compendium of Interstate Pipelines
#   Storage: EIA Underground Natural Gas Storage (Form EIA-191)
###############################################################################


# ── Supply Curves ────────────────────────────────────────────────────────────

# ── US LNG Export Demand ──────────────────────────────────────────────────────


def _interp_lng_export(
    all_demand_table: dict[str, dict[int, float]], region: str, year: int
) -> float:
    """Linearly interpolate LNG export demand for any year from table breakpoints."""
    table = all_demand_table.get(region, {})
    if not table:
        return 0.0
    years_sorted = sorted(table)
    if year <= years_sorted[0]:
        return table[years_sorted[0]]
    if year >= years_sorted[-1]:
        return table[years_sorted[-1]]
    for k in range(len(years_sorted) - 1):
        y0, y1 = years_sorted[k], years_sorted[k + 1]
        if y0 <= year <= y1:
            t = (year - y0) / (y1 - y0)
            return table[y0] + t * (table[y1] - table[y0])
    return 0.0


# ── NGMM AEO2025 QP parameters ────────────────────────────────────────────────
# New module-level constants for the
# quadratic-program rewrite. All loaded from CSV via data.py with hardcoded
# fallbacks defined there. References below cite NGMM_AEO2025.pdf.


###############################################################################
# NGMM supply-curve breakpoint helpers (NGMM Eq 2-5)
# Used by NGModel.__init__ to build the
# elastic piecewise-linear supply curve around an expected (Q0, P0) anchor.
###############################################################################


def _supply_qbase(q0: float, k: int, crv_below: list, crv_above: list) -> float:
    """Compute QBASE_k for the NGMM supply curve (NGMM Eq 2 and 4).

    Note the two branches run their products in opposite directions. For k <= 3 the loop
    starts at index k-1 and runs to the end, so breakpoint 1 accumulates all three downward
    factors and sits furthest BELOW the anchor, while breakpoint 3 accumulates only the last
    one and sits just below it. For k > 3 the loop starts at 0 and runs k-3 times, so
    breakpoint 6 accumulates all three upward factors and sits furthest above.

    Reversing either direction produces a curve that is still monotonic and still spans a
    plausible range, so nothing downstream complains, the quantities are attached to
    the wrong prices.


    Breakpoints 1-3 sit below the anchor (Q0, P0); 4-6 sit above. Given:
        crv_below = [c1, c2, c3]  (volume drop fractions for steps 1, 2, 3)
        crv_above = [c1, c2, c3]  (volume rise fractions for steps 4, 5, 6)

    Returns the cumulative-product breakpoint:
        k = 1 -> Q0 × ∏_{i=1..3} (1 − crv_below[i])
        k = 2 -> Q0 × ∏_{i=2..3} (1 − crv_below[i])
        k = 3 -> Q0 × (1 − crv_below[3])
        k = 4 -> Q0 × (1 + crv_above[1])
        k = 5 -> Q0 × (1 + crv_above[1])(1 + crv_above[2])
        k = 6 -> Q0 × (1 + crv_above[1])(1 + crv_above[2])(1 + crv_above[3])
    """
    if k <= 3:
        f = 1.0
        for i in range(k - 1, 3):
            f *= 1.0 - crv_below[i]
        return q0 * f
    else:
        f = 1.0
        for i in range(k - 3):
            f *= 1.0 + crv_above[i]
        return q0 * f


def _supply_pbase(p0: float, k: int, crv_below: list, crv_above: list, elas: list) -> float:
    """Compute PBASE_k for the NGMM supply curve.

    NGMM AEO2025 Eq 3 and Eq 5, as written
    in the PDF, give a non-monotonic price curve with the AEO 2022 default
    elasticities (0.2-0.8 < 1).  The literal formula is

        PBASE_step = P0 × ∏ (1 ± CRV_step) / ELAS_step

    which divides (1 ± CRV) by an elasticity < 1, exploding upward and
    producing prices that *fall* between adjacent steps below Q0 (verified
    with WSC test: PBASE_2 = 4.36 > PBASE_3 = 3.59, wrong direction).

    The economically-correct form, derived from the elasticity definition
    ε = (dQ/Q) / (dP/P) ⇒ dP/P = (dQ/Q)/ε ⇒ ΔPBASE/PBASE = ±CRV/ELAS, is

        PBASE_step = P0 × ∏ (1 ± CRV_step / ELAS_step)

    This is consistent with the in-step price formula (Eq 1)

        P(Q) = PBASE × (1 + (1/ELAS) × (Q - QBASE)/QBASE),

    which at Q = QBASE_{k+1} gives PBASE_{k+1} = PBASE_k × (1 + CRV/ELAS).
    We use the elasticity-correct form here; the literal-PDF form is
    preserved in the docstring above for documentation.

    REVIEW NOTE. This is a deliberate departure from the published NGMM specification, and it
    changes every price the model produces. The argument for it is that the published form is
    internally inconsistent, it contradicts NGMM's own in-step price formula (Eq 1) and is
    non-monotonic with NGMM's own default elasticities, but it is a departure nonetheless
    and should be flagged in any comparison against NGMM results.

    Note the elasticity indexing differs between branches: the k <= 3 branch reads elas[i] on
    the same index as crv_below[i], while the k > 3 branch reads elas[2 + i], continuing into
    the upper half of the five-element elasticity vector. Elasticities decline across the five
    segments (0.8 -> 0.2), so dividing by a smaller number above the anchor is what makes the
    curve steepen: supply gets progressively harder to expand.
    """
    if k <= 3:
        f = 1.0
        for i in range(k - 1, 3):
            f *= 1.0 - crv_below[i] / elas[i]
        return p0 * f
    else:
        f = 1.0
        for i in range(k - 3):
            f *= 1.0 + crv_above[i] / elas[2 + i]
        return p0 * f


###############################################################################
# Natural Gas Market Model
###############################################################################


class NGModel(ConcreteModel, IntegratedModel):
    """Pyomo model for the EIA-style Natural Gas Market Module.

    Minimizes total annual cost of supplying natural gas across 9 US census
    divisions subject to:
      - Step supply-curve capacity limits per region and NGMM step
      - Directed pipeline arc capacity limits
      - Underground storage balance (annual net injection = withdrawal)
      - Demand satisfaction in every region and year (with optional LNG backstop)

    Shadow prices on the demand-balance constraints are the regional gas prices
    returned to coupled models (electricity, hydrogen).

    Parameters
    ----------
    years : list[int]
        Planning years to include in the optimisation.
    mode : str
        'standard', standalone with built-in demand projections.
        'integrated', mutable demand/price params updated by the integrator.
    demand_override : dict[(region, sector, year), float] | None
        If provided, replaces internal demand projections.
    elec_demand_override : dict[GI, float] | None
        Regional annual electric-power gas demand from the electricity model.
    """

    # Added `regions` (default None = all nine, so every
    def __init__(
        self,
        model_data: NGData,
        common_config: CommonConfig,
        ng_config: NGConfig,
        demand_override: dict | None = None,
        elec_demand_override: dict | None = None,
        *args,
        **kwargs,
    ):
        """Build the QP, aligned with the NGMM AEO 2025 mathematical formulation.

        See module docstring for the list
        of NGMM features implemented (Tier 1) and the ones intentionally skipped
        (Tier 2/3). Equation numbers below cite NGMM_AEO2025.pdf §3.
        """
        ConcreteModel.__init__(self, *args, **kwargs)

        # Region subsetting. `region_list` is the single
        # source of truth from here down; `is_region_subset` gates the unserved-demand backstop
        # so the full nine-region model is untouched (see the backstop block below).

        if common_config.mode not in {RunMode.STANDALONE}:
            raise NotImplementedError('Only standalone mode is implemented.')

        # ── SUPPORTING DATA ───────────────────────────────────────────────────

        analysis_regions = model_data['regions_analyze']
        self.region_labels = model_data['region_labels']
        LNG_EXPORT_DEMAND_BCF: dict[str, dict[int, float]] = model_data['lng_export']
        self.DEMAND_PRICE_ELASTICITY: dict[str, float] = model_data['demand_elasticity']
        # ── Pipeline Network ─────────────────────────────────────────────────────────
        PIPELINE_ARCS_RAW = model_data['pipeline_arcs']

        # ── Underground Storage ───────────────────────────────────────────────────────
        STORAGE = model_data['storage']
        STORAGE_OPEX = model_data['storage_opex']
        # Supply-curve shape (NGMM Eq 1-5, Fig 3.4). Built around an expected (Q0, P0)
        # anchor with per-step elasticities and CRV breakpoint adjustments.
        self.SUPPLY_CURVE_SHAPE = model_data['supply_curve_shape']

        # Pipeline tariff curve shape (NGMM Eq 6, Fig 3.5). Utilisation breakpoints and
        # tariff multipliers on the base tariff per arc.
        TARIFF_CURVE_SHAPE = model_data['tariff_curve_shape']

        # LNG export demand curve shape (NGMM Eq 14, Fig 3.6). World LNG price and
        # downward-sloping demand factors over fractional capacity.
        LNG_DEMAND_CURVE_SHAPE = model_data['lng_demand_curve']

        # Per-region losses (NGMM Eq 10, 11): distribution, intrastate, storage, and
        # plant-fuel fraction. {region: {distribution_loss, intrastate_loss,
        # storage_loss, plant_fuel_frac}}.
        LOSSES = model_data['losses']

        # Per-region gathering charges in $/MMBtu (NGMM Eq 7 term).
        GATHERING_CHARGES = model_data['gathering']

        # Per-arc pipeline fuel-loss fractions (NGMM Eq 11 f^pip). Sparse, arcs not
        # listed use the QP scalar default (~0.005).
        PIPE_LOSS_BY_ARC = model_data['pipe_loss']

        # Other NGMM-QP scalars (default values, overridable via ng_scalars.csv).
        self.QP_SCALARS = model_data['qp_scalars']

        # Supply-curve breakpoint count (5 segments → 6 breakpoints, matches NGMM
        # AEO 2022 default; see SUPPLY_CURVE_SHAPE for the elasticities). Kept as a
        # module constant because constraint indexing depends on it.
        # $/MMBtu penalty on unserved demand in region-subset runs
        # (see NGModel.__init__). Set ~100x any plausible gas price so the backstop
        # is never economic and only relieves a genuine shortfall created by dropping a
        # subset's supplying neighbours.
        UNSERVED_PENALTY = 1000.0

        self.SUPPLY_BREAK_IDS = [1, 2, 3, 4, 5, 6]
        self.SUPPLY_STEP_IDS = [1, 2, 3, 4, 5]  # 5 segments between 6 breakpoints

        # Tariff-curve segments (one fewer than the number of breakpoints in TARIFF_CURVE_SHAPE).
        TARIFF_SEGMENTS = list(range(1, len(TARIFF_CURVE_SHAPE['util_break'])))

        # LNG demand-curve segments.
        LNG_SEGMENTS = list(range(1, len(LNG_DEMAND_CURVE_SHAPE['q_frac'])))

        projected_demand = model_data['demand']

        if demand_override:
            projected_demand.update(demand_override)

        # if the electricity model passes updated elec-power gas demand, apply it
        if elec_demand_override:
            for gi, qty in elec_demand_override.items():
                # TODO:  I don't like this literal here....perhaps make a static constant or such?
                old_qty = projected_demand.get((gi.region, 'electric_power', gi.year), None)
                if not old_qty:
                    logger.error(
                        'Could not locate prior demand for region %s and year %d',
                        gi.region,
                        gi.year,
                    )
                    logger.error('Check index and ensure "electric_power" column is present.')

                projected_demand[gi.region, 'electric_power', gi.year] = qty

        # ── build pipeline arc index ─────────────────────────────────────────
        # Keep only arcs INTERNAL to the active regions.
        # An arc with one endpoint outside the subset has no counterparty balance constraint, so
        # leaving it in would let gas appear from or vanish into a region the model no longer
        _active = set(analysis_regions)
        _arcs_raw = [
            (o, d, cap, tar)
            for o, d, cap, tar in PIPELINE_ARCS_RAW
            if o in _active and d in _active
        ]
        arc_list = [(o, d) for o, d, _, _ in _arcs_raw]
        arc_cap = {(o, d): cap for o, d, cap, _ in _arcs_raw}
        arc_tariff = {(o, d): tar for o, d, _, tar in _arcs_raw}

        # LNG export regions: only those listed in LNG_EXPORT_DEMAND_BCF carry an
        # endogenous LNG demand curve (NGMM Fig 3.6).  All other regions still
        # have lng_export[r, y] but it is bounded at zero.
        # Intersect with the active regions.
        lng_regions_list = [r for r in LNG_EXPORT_DEMAND_BCF if r in _active]

        # -

        # ── SETS ──────────────────────────────────────────────────────────────
        # Region subsetting. Every region-keyed Param below
        # is built from a rule function indexed off this Set, so subsetting here propagates
        # automatically; only arcs, LNG regions, and _base_demand needed explicit filtering.
        # TODO:  The set "S" appears unused?  Probably "step", but....  is it deleteable?
        self.S = Set(initialize=[1, 2, 3])
        # Master region list and its two subsets, mirroring electricity_model.py. region_analyze
        # is the subset actually solved: ng_config.region_filter when given, all domestic
        # regions otherwise (see resolve_regions).
        self.region = Set(initialize=model_data['regions'], validate=region_check)
        self.region_dom = Set(initialize=model_data['regions_domestic'], within=self.region)
        self.region_analyze = Set(initialize=analysis_regions, within=self.region_dom)
        self.is_region_subset = len(self.region_analyze) < len(self.region_dom)
        self.region_int = Set(initialize=model_data['regions_international'], within=self.region)

        # NGMM vocabulary, used consistently throughout this model:
        # STEP, an elastic piece of the supply curve that carries volume. NGMM's SSTEP
        # (Eq 7, Eq 8: PROD = sum_step SSTEP + QMIN). There are five.
        # BREAK, an endpoint bounding those steps; the index of NGMM's QBASE/PBASE. Six.
        # Six breaks bound five steps, so every loop over steps reads break k and break k+1.
        #
        # These are NOT the three low/medium/high cost tiers in the input file. Those have no
        # NGMM counterpart at all (NGMM's supply dimension is (suptype, qps)) and are collapsed
        # into a single (Q0, P0) anchor below. update_supply_capacity() still accepts the legacy
        # three-key form from the HSM integrator and aggregates it into Q0.
        self.steps = Set(initialize=[f'step{k}' for k in self.SUPPLY_STEP_IDS])  # 5 NGMM steps
        self.supply_breaks = Set(initialize=self.SUPPLY_BREAK_IDS, ordered=True)  # 6 breakpoints
        self.tariff_segs = Set(initialize=TARIFF_SEGMENTS, ordered=True)
        self.tariff_breaks = Set(
            initialize=list(range(1, len(TARIFF_CURVE_SHAPE['util_break']) + 1)), ordered=True
        )
        self.lng_regions = Set(initialize=lng_regions_list)
        self.lng_segs = Set(initialize=LNG_SEGMENTS, ordered=True)
        self.lng_breaks = Set(
            initialize=list(range(1, len(LNG_DEMAND_CURVE_SHAPE['q_frac']) + 1)), ordered=True
        )
        self.sectors = Set(initialize=model_data['sectors'])
        self.arcs = Set(
            initialize=arc_list, dimen=2, within=self.region_analyze * self.region_analyze
        )
        self.year = Set(initialize=model_data['years'], ordered=True)

        # Suffix for dual (shadow price) extraction
        self.dual = Suffix(direction=Suffix.IMPORT)

        # ── PARAMETERS ────────────────────────────────────────────────────────
        #
        # NGMM-style elastic supply curve (Eq 1-5).  Each region/year has 6
        # breakpoints (Q, P) defining 5 linear segments.  Q0[r, y] = expected
        # production point, set initially from the legacy SUPPLY_COST_TIERS totals
        # and refreshed each iteration by update_supply_capacity() (HSM output).
        # The QBASE/PBASE breakpoints are mutable Params so the curve can be
        # rebuilt without rebuilding the model.

        # Initial Q0 from the aggregated input cost tiers (sum of capacities)
        # The (Q0, P0) anchors now follow the
        # optional YEAR-VARYING path in SUPPLY_ANCHORS (AEO production/supply-price paths,
        # normalized to 2025). The params were already (region, year)-indexed; only the
        # initialization ignored y, which froze the curve and made Henry Hub rise monotonically
        # (+20% by 2050 vs AEO's hump peaking ~2040). Missing entries multiply by 1.0 = the
        # original static behaviour.

        SUPPLY_COST_TIERS = model_data['supply_cost_tiers']
        # Optional year-varying anchor path
        # {(region, year): (q0_mult, p0_mult)}; empty dict -> static anchors (previous behaviour).
        SUPPLY_ANCHORS = model_data.get('supply_anchors', {})

        def _q0_init(m, r, y):
            return (
                sum(cap for cap, _ in SUPPLY_COST_TIERS[r])
                * SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[0]
            )

        # Initial P0 from the quantity-weighted average of the input cost-tier costs. This is
        # where the three tiers stop existing: they become one price, and the five NGMM steps
        # are built around it.
        def _p0_init(m, r, y):
            cost_tiers = SUPPLY_COST_TIERS[r]
            tot_q = sum(c for c, _ in cost_tiers)
            if tot_q <= 0:
                return 3.0
            return (
                sum(c * p for c, p in cost_tiers)
                / tot_q
                * SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[1]
            )

        self.Q0 = Param(self.region_analyze, self.year, initialize=_q0_init, mutable=True)
        self.P0 = Param(self.region_analyze, self.year, initialize=_p0_init, mutable=True)

        # Supply-curve QBASE / PBASE breakpoints (NGMM Eq 2-5).
        # We compute initial breakpoint values from Q0/P0 and the SUPPLY_CURVE_SHAPE
        # constants here; update_supply_capacity() refreshes them whenever Q0 changes.
        crv_below = self.SUPPLY_CURVE_SHAPE['crv_below']  # [c1, c2, c3] for steps 1, 2, 3 below
        crv_above = self.SUPPLY_CURVE_SHAPE['crv_above']  # [c1, c2, c3] for steps 4, 5, 6 above
        elas = self.SUPPLY_CURVE_SHAPE['elas']  # [e1..e5] for segments 1-5

        # Same year-varying anchor multipliers as
        # _q0_init/_p0_init above (these recompute q0/p0 inline). Originals had no
        # SUPPLY_ANCHORS term.
        def _qbase_init(m, r, k, y):
            q0 = (
                sum(cap for cap, _ in SUPPLY_COST_TIERS[r])
                * SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[0]
            )
            return _supply_qbase(q0, k, crv_below, crv_above)

        def _pbase_init(m, r, k, y):
            tr = SUPPLY_COST_TIERS[r]
            tot_q = sum(c for c, _ in tr)
            p0 = sum(c * p for c, p in tr) / tot_q if tot_q > 0 else 3.0
            p0 *= SUPPLY_ANCHORS.get((r, y), (1.0, 1.0))[1]
            return _supply_pbase(p0, k, crv_below, crv_above, elas)

        self.QBASE = Param(
            self.region_analyze, self.supply_breaks, self.year, initialize=_qbase_init, mutable=True
        )
        self.PBASE = Param(
            self.region_analyze, self.supply_breaks, self.year, initialize=_pbase_init, mutable=True
        )

        # QMIN: committed production (NGMM Eq 8): the "wells already drilled"
        # floor.  Treated as a fraction of Q0 (NGMM uses an exogenous PEMEX /
        # historical-floor input; we use qmin_fraction × Q0 as a proxy).
        qmin_frac = self.QP_SCALARS.get('supply_curve_qmin_fraction', 0.20)
        self.QMIN = Param(
            self.region_analyze,
            self.year,
            initialize=lambda m, r, y: qmin_frac * value(m.Q0[r, y]),
            mutable=True,
        )

        # Gathering charge (NGMM Eq 7 P^gath term, $/MMBtu)
        # Indexed directly, not .get(): ng_gathering.csv is a required input covering every
        # region, so a missing one is a broken input file rather than a case to default away.
        self.gathering_charge = Param(
            self.region_analyze,
            initialize=lambda m, r: GATHERING_CHARGES[r],
        )

        # LNG import availability (coastal regions only), high-cost backstop supply
        LNG_IMPORT = model_data['lng_import']
        # LNG backstop import (existing 3-region exogenous capacity)
        self.lng_capacity = Param(
            self.region_analyze,
            initialize=lambda m, r: LNG_IMPORT.get(r, (0, 0))[0],
        )
        self.lng_cost = Param(
            self.region_analyze,
            initialize=lambda m, r: LNG_IMPORT.get(r, (0, 0))[1],
        )

        # Pipeline tariff curve (NGMM Eq 6, Fig 3.5).  PTAR[o, d, k] / QTAR[o, d, k]
        # are 7 breakpoint pairs per directed arc, computed from the base tariff
        # and capacity using TARIFF_CURVE_SHAPE multipliers.  Quadratic on each
        # segment between consecutive breakpoints (NGMM Fig 3.5 hurdle behaviour).
        util_breaks = TARIFF_CURVE_SHAPE['util_break']
        tariff_mults = TARIFF_CURVE_SHAPE['tariff_mult']

        def _qtar_init(m, o, d, k, y):
            return arc_cap[(o, d)] * util_breaks[k - 1]

        def _ptar_init(m, o, d, k, y):
            return arc_tariff[(o, d)] * tariff_mults[k - 1]

        self.QTAR = Param(
            self.arcs, self.tariff_breaks, self.year, initialize=_qtar_init, mutable=False
        )
        self.PTAR = Param(
            self.arcs, self.tariff_breaks, self.year, initialize=_ptar_init, mutable=False
        )

        # Pipeline fuel-loss fraction per directed arc (NGMM Eq 11 f^pip)
        pipe_loss_scalar = self.QP_SCALARS['pipe_fuel_loss']
        self.pipe_loss = Param(
            self.arcs,
            initialize=lambda m, o, d: PIPE_LOSS_BY_ARC.get((o, d), pipe_loss_scalar),
        )

        # Pipeline network base info (kept for reporting and capacity bounds)
        self.pipe_capacity = Param(self.arcs, initialize=lambda m, o, d: arc_cap[(o, d)])
        self.pipe_tariff = Param(self.arcs, initialize=lambda m, o, d: arc_tariff[(o, d)])

        # LNG export demand curve (NGMM Fig 3.6).  Per LNG export region and year,
        # PLNG / QLNG breakpoints span a linear demand curve from world price up
        # to max_factor × world price at zero export volume.  The QLNG anchor is
        # the legacy LNG_EXPORT_DEMAND_BCF capacity for that (region, year).
        lng_q_frac = LNG_DEMAND_CURVE_SHAPE['q_frac']
        lng_p_factor = LNG_DEMAND_CURVE_SHAPE['p_factor']
        lng_world_p = LNG_DEMAND_CURVE_SHAPE['world_price']

        def _qlng_init(m, r, k, y):
            cap = _interp_lng_export(LNG_EXPORT_DEMAND_BCF, r, y)
            return cap * lng_q_frac[k - 1]

        def _plng_init(m, r, k, y):
            return lng_world_p * lng_p_factor[k - 1]

        self.QLNG = Param(
            self.lng_regions, self.lng_breaks, self.year, initialize=_qlng_init, mutable=True
        )
        self.PLNG = Param(
            self.lng_regions, self.lng_breaks, self.year, initialize=_plng_init, mutable=True
        )

        # Storage
        def _stor_working(m, r):
            return STORAGE[r]['working']

        def _stor_inject(m, r):
            return STORAGE[r]['inject']

        def _stor_withdraw(m, r):
            return STORAGE[r]['withdraw']

        self.storage_working_cap = Param(self.region_analyze, initialize=_stor_working)
        self.storage_inject_cap = Param(self.region_analyze, initialize=_stor_inject)
        self.storage_withdraw_cap = Param(self.region_analyze, initialize=_stor_withdraw)
        self.storage_opex = Param(initialize=STORAGE_OPEX)

        # NGMM losses (Eq 10, 11): distribution, intrastate, storage, plant fuel
        self.distribution_loss = Param(
            self.region_analyze,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'distribution_loss', self.QP_SCALARS['distribution_loss']
            ),
        )
        self.intrastate_loss = Param(
            self.region_analyze,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'intrastate_loss', self.QP_SCALARS['intrastate_loss']
            ),
        )
        self.storage_loss = Param(
            self.region_analyze,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'storage_loss', self.QP_SCALARS['storage_loss']
            ),
        )
        self.plant_fuel_frac = Param(
            self.region_analyze,
            initialize=lambda m, r: LOSSES.get(r, {}).get(
                'plant_fuel_frac', self.QP_SCALARS['plant_fuel_frac']
            ),
        )

        # Demand, mutable so the integrator can update it each iteration
        self.demand = Param(
            self.region_analyze,
            self.sectors,
            self.year,
            initialize=lambda m, r, s, y: projected_demand.get((r, s, y), 0.0),
            mutable=True,
        )

        # Conversion factor: BCF → MMBtu (objective scaling).  1 BCF = 1e6 MMBtu;
        # we divide by 1e3 so the objective reads in $-thousands per unit-step.
        # (Gurobi/HiGHS handle absolute scale fine; this is just for readability.)
        self.bcf_to_mmbtu = Param(initialize=1e3)

        # Canadian gas imports, mutable so the HSM integrator can update each iteration
        self.canada_supply = Param(
            self.region_analyze,
            self.year,
            initialize=0.0,
            mutable=True,
        )

        # ── Base demand snapshot for price-elasticity updates ──────────────────
        # Stores the initial (no-price-adjustment) projected demand.
        # update_demand_from_price() modifies self.demand relative to this base.
        self._base_demand: dict[tuple[str, str, int], float] = {
            (r, s, y): projected_demand.get((r, s, y), 0.0)
            # Active regions only, so the elasticity update
            # cannot reference a region this model does not carry.
            for r in self.region_analyze
            for s in self.sectors
            for y in self.year
        }
        # Reference gas prices, set after first GS solve via set_reference_prices().
        # Before being set, price-responsive demand has no effect (prices=reference).
        self._ref_prices: dict[tuple[str, int], float] = {}

        # ── VARIABLES ─────────────────────────────────────────────────────────
        # Per-segment supply volume (NGMM SSTEP decision variable, Eq 8).
        # Indexed by region × segment × year; bounded above by the segment
        # width via supply_step_cap_con.
        self.sstep = Var(self.region_analyze, self.steps, self.year, within=NonNegativeReals)

        # The legacy attribute name
        # ``production`` is intentionally NOT defined.  Pyomo forbids aliasing
        # one Var component to two block attributes, and a grep of the
        # codebase (integrators, sensitivity/babymodel, hydrogen, hsm, comm)
        # confirms no caller indexes ng_model.production[r, t, y] directly.
        # Callers that previously wanted "production by cost tier" should now
        # read ``sstep`` (NGMM step volume) or ``production_total`` (per-region
        # year-total Expression, including the QMIN floor).

        # Total production per (region, year), Expression rather than Var so the
        # objective and demand-balance terms reference the step sum directly (NGMM Eq 8).
        # Equals QMIN (committed floor) + Σ_k sstep[r, k, y]  (NGMM Eq 8).
        def _prod_total_rule(m, r, y):
            return m.QMIN[r, y] + quicksum(m.sstep[r, t, y] for t in m.steps)

        self.production_total = Expression(self.region_analyze, self.year, rule=_prod_total_rule)

        # LNG export per-step volume (NGMM Eq 14): price-responsive variable on
        # the LNG demand curve.  Indexed by LNG region × segment × year.
        self.lng_export_step = Var(
            self.lng_regions,
            self.lng_segs,
            self.year,
            within=NonNegativeReals,
        )

        # Total LNG export per (region, year), derived from segments.
        def _lng_export_total_rule(m, r, y):
            if r in m.lng_regions:
                return quicksum(m.lng_export_step[r, k, y] for k in m.lng_segs)
            return 0.0

        self.lng_export_demand = Expression(
            self.region_analyze, self.year, rule=_lng_export_total_rule
        )

        # Pipeline tariff-curve per-step volume (NGMM Eq 15)
        self.tar_step = Var(self.arcs, self.tariff_segs, self.year, within=NonNegativeReals)

        # Pipeline flow per (arc, year), derived from tariff-curve segments
        # (NGMM Eq 15: FLOWH2H = Σ_step TAR_step).
        def _pipe_flow_rule(m, o, d, y):
            return quicksum(m.tar_step[o, d, k, y] for k in m.tariff_segs)

        self.pipe_flow = Expression(self.arcs, self.year, rule=_pipe_flow_rule)

        # LNG backstop import (non-negative; zero for landlocked regions via capacity=0)
        self.lng_import = Var(self.region_analyze, self.year, within=NonNegativeReals)

        # Storage injection / withdrawal [BCF/yr seasonal cycle]
        self.stor_inject = Var(self.region_analyze, self.year, within=NonNegativeReals)
        self.stor_withdraw = Var(self.region_analyze, self.year, within=NonNegativeReals)

        # Slack demand variable (for integration: allows other models to add load)
        self.var_demand = Var(
            self.region_analyze, self.year, within=NonNegativeReals, initialize=0.0
        )

        # UNSERVED-DEMAND BACKSTOP, subset runs only.
        # `var_demand` is NonNegative and sits on the DEMAND side of demand_balance, so it can
        # only absorb surplus supply, never cover a shortfall. That makes any region subset that
        # is a net importer INFEASIBLE once its supplying neighbours are dropped, verified:
        # regions=['new_england'] has no production and no arcs and fails to solve at all.
        # Failing with an opaque Gurobi infeasibility would make this feature unusable in
        # practice, so subset runs get an explicit unserved-demand variable priced far above any
        # real gas price. It is only created when a strict subset is active, so the nine-region
        # model has no new variable and no new objective term (regression-safe by construction).
        # NB: `self.unserved` is only ever CREATED for a subset, do not pre-assign None here.
        # Assigning None first makes it a plain attribute on the Pyomo block, and attaching the
        # Var afterwards trips "Reassigning the non-component attribute unserved". Gate on
        # `is_region_subset` (a plain bool, never a component) instead.
        if self.is_region_subset:
            self.unserved = Var(
                self.region_analyze, self.year, within=NonNegativeReals, initialize=0.0
            )

        # ── CONSTRAINTS ───────────────────────────────────────────────────────

        # (NGMM Eq 18) Supply-curve segment range: 0 ≤ SSTEP_k ≤ QBASE_{k+1} − QBASE_k
        def supply_step_cap_rule(m, r, t, y):
            k = int(t.replace('step', ''))  # segment index 1..5
            seg_width = m.QBASE[r, k + 1, y] - m.QBASE[r, k, y]
            return m.sstep[r, t, y] <= seg_width

        self.supply_step_cap_con = Constraint(
            self.region_analyze,
            self.steps,
            self.year,
            rule=supply_step_cap_rule,
        )

        # Backward-compat: total production must not exceed Σ_k segment widths
        # plus QMIN, automatically implied by supply_step_cap above, but kept
        # for explicit integrator-side capacity reads.  Implemented as a Param
        # rather than a constraint to avoid double-counting.
        self.supply_capacity = Param(
            self.region_analyze,
            self.steps,
            self.year,
            initialize=lambda m, r, t, y: value(
                m.QBASE[r, int(t.replace('step', '')) + 1, y]
                - m.QBASE[r, int(t.replace('step', '')), y]
            ),
            mutable=True,
        )

        # (NGMM Eq 19) Tariff-curve segment range: 0 ≤ TAR_k ≤ QTAR_{k+1} − QTAR_k
        def tariff_step_cap_rule(m, o, d, k, y):
            return m.tar_step[o, d, k, y] <= m.QTAR[o, d, k + 1, y] - m.QTAR[o, d, k, y]

        self.tariff_step_cap_con = Constraint(
            self.arcs,
            self.tariff_segs,
            self.year,
            rule=tariff_step_cap_rule,
        )

        # (NGMM Eq 21) Total pipeline flow ≤ arc capacity.  Implied by tariff
        # step caps for the standard 6-segment curve (final breakpoint is at
        # 140 % capacity, allowing virtual capacity-expansion flow), but we keep
        # an explicit cap at 100 % when capacity expansion is disabled.  Skipped
        # by default; uncomment if you want hard capacity cuts.
        # def pipe_total_cap_rule(m, o, d, y):
        #     return m.pipe_flow[o, d, y] <= m.pipe_capacity[o, d]
        # self.pipe_cap_con = Constraint(self.arcs, self.year, rule=pipe_total_cap_rule)

        # (NGMM Eq 20) LNG demand-curve segment range
        def lng_step_cap_rule(m, r, k, y):
            return m.lng_export_step[r, k, y] <= m.QLNG[r, k + 1, y] - m.QLNG[r, k, y]

        self.lng_step_cap_con = Constraint(
            self.lng_regions,
            self.lng_segs,
            self.year,
            rule=lng_step_cap_rule,
        )

        # (legacy) LNG backstop capacity
        def lng_cap_rule(m, region, year):
            return m.lng_import[region, year] <= m.lng_capacity[region]

        self.lng_cap_con = Constraint(self.region_analyze, self.year, rule=lng_cap_rule)

        # Storage injection / withdrawal capacities
        def inject_cap_rule(m, r, y):
            return m.stor_inject[r, y] <= m.storage_inject_cap[r]

        self.inject_cap_con = Constraint(self.region_analyze, self.year, rule=inject_cap_rule)

        def withdraw_cap_rule(m, r, y):
            return m.stor_withdraw[r, y] <= m.storage_withdraw_cap[r]

        self.withdraw_cap_con = Constraint(self.region_analyze, self.year, rule=withdraw_cap_rule)

        # Annual storage balance, net seasonal cycle closes within each year.
        def storage_balance_rule(m, r, y):
            return m.stor_inject[r, y] == m.stor_withdraw[r, y]

        self.storage_balance_con = Constraint(
            self.region_analyze, self.year, rule=storage_balance_rule
        )

        # Precompute arc adjacency for the demand-balance closure (NGMM Eq 10, 11).
        _inc: dict = defaultdict(list)
        _out: dict = defaultdict(list)
        for o, d in arc_list:
            _inc[d].append((o, d))
            _out[o].append((o, d))

        # (NGMM Eq 10 + 11 combined into the regional demand balance, with the
        # 9-region structure replacing NGMM's separate hub + demand-node layers.
        # The dual of this constraint is the regional citygate gas price.)
        #
        # LHS (sources arriving in region r):
        #     production × (1 − intrastate_loss)        ← Eq 11 f^pip on intra-region
        #   + LNG backstop import
        #   + Canada supply
        #   + Σ pipe_in × (1 − pipe_loss)                ← Eq 11 fuel loss on inbound arcs
        #   + storage withdrawal × (1 − storage_loss)    ← Eq 10 Q^store on withdrawn gas
        #
        # RHS (uses):
        #     Σ_sector demand                           ← Eq 10 CONS_d
        #   + distribution_loss × (residential + commercial)  ← Eq 10 Q^dist_d
        #   + plant_fuel_frac × total demand            ← Eq 10 PLT_d
        #   + Σ pipe_out                                ← Eq 11 outbound to other hubs
        #   + storage injection                         ← Eq 11 hub-to-storage
        #   + Σ_k lng_export_step                       ← Eq 14 hub-to-LNG demand
        #   + var_demand                                 ← integration slack (kept)
        # THE PRICE-FORMING CONSTRAINT. Everything else in the model exists to give this one
        # equality something to balance. Three things to hold in mind reading it:
        #
        # 1. It is an EQUALITY, and its dual is the regional gas price, the marginal cost of
        # one more unit delivered into region r in year y. poll_gas_price() reads exactly
        # these duals, and they are what crosses into the electricity model. Relaxing this
        # to an inequality would destroy the price signal, not merely loosen the model.
        # 2. Losses are applied ASYMMETRICALLY, and correctly: gas ARRIVING is derated
        # (inbound pipe flow, storage withdrawal, production leaving the field), gas
        # LEAVING is not (outbound flow, injection). The loss is on delivery, so charging
        # it at both ends would double-count.
        # 3. NGMM splits this across three constraints, supply mass balance (Eq 9), hub flow
        # balance (Eq 11), demand mass balance (Eq 10), because it models supply regions,
        # hubs, and demand regions as separate layers. Here each census division is all
        # three at once, so the three equations merge into this one. The consequence is
        # that there is no separate hub price: this dual does the job NGMM's hub-balance
        # dual does for Henry Hub.
        def demand_balance_rule(m, r, y):
            prod = m.production_total[r, y]
            lng_b = m.lng_import[r, y]
            pipe_in_eff = quicksum(
                m.pipe_flow[o, d, y] * (1.0 - m.pipe_loss[o, d]) for (o, d) in _inc[r]
            )
            pipe_out = quicksum(m.pipe_flow[o, d, y] for (o, d) in _out[r])
            wd_eff = m.stor_withdraw[r, y] * (1.0 - m.storage_loss[r])
            inj = m.stor_inject[r, y]

            sector_demand = quicksum(m.demand[r, s, y] for s in m.sectors)
            res_comm = m.demand[r, 'residential', y] + m.demand[r, 'commercial', y]
            dist_loss_term = m.distribution_loss[r] * res_comm
            plant_fuel = m.plant_fuel_frac[r] * sector_demand
            lng_export = m.lng_export_demand[r, y]

            # Unserved demand joins the SUPPLY side for
            # subset runs only (see the declaration above); zero for the full nine-region model.
            unserved = m.unserved[r, y] if m.is_region_subset else 0.0

            return (
                prod * (1.0 - m.intrastate_loss[r])
                + lng_b
                + m.canada_supply[r, y]
                + pipe_in_eff
                + wd_eff
                + unserved
                == sector_demand
                + dist_loss_term
                + plant_fuel
                + pipe_out
                + inj
                + lng_export
                + m.var_demand[r, y]
            )

        self.demand_balance = Constraint(self.region_analyze, self.year, rule=demand_balance_rule)

        # ── OBJECTIVE (NGMM Eq 7) ────────────────────────────────────────────
        # max  consumer_surplus(LNG export) + producer_surplus
        #         - gathering_cost - LNG_backstop_cost - transport_cost
        #         - storage_cost
        #
        # We express the QP as MINIMISE total_cost = (positive costs)
        # − (LNG consumer surplus) so that the integrator's existing
        # convergence checks (which expect a positive scalar) still work, and
        # so that any code that does `meta.obj = ... + ng_model.total_cost`
        # keeps the right sign.  Each piecewise segment contributes
        # PBASE_k · q_k + 0.5 · q_k² · slope_k  (the area under the segment
        # between (QBASE_k, PBASE_k) and (QBASE_{k+1}, PBASE_{k+1})), which is
        # quadratic in the segment volume, the source of the QP non-linearity.
        #
        # Note: the BCF×$/MMBtu factor is bcf_to_mmbtu = 1e3, applied uniformly.

        bcf = self.bcf_to_mmbtu

        # 1) Producer cost, area under the supply curve (subtracted from
        #    surplus = added to total_cost in minimisation form, NGMM Eq 7).
        # Evaluate widths/slopes as
        # numeric values from the breakpoint Params at construction time and
        # skip zero-width segments to avoid ZeroDivisionError on regions with
        # Q0 = 0 (none in current data, but defensive).
        # WHY THIS IS A QP, AND WHY THE SLOPES ARE PYTHON FLOATS.
        # Each segment contributes the trapezoid area under the supply curve between
        # (QBASE_k, PBASE_k) and (QBASE_k+1, PBASE_k+1), which integrates to
        # PBASE_k * q + 0.5 * slope * q^2
        # , linear plus quadratic in the segment volume q. That q^2 is the entire source of
        # the model's non-linearity, and it is why appsi_highs cannot carry this model.
        #
        # Note value() on the breakpoints: the widths and slopes are evaluated NUMERICALLY at
        # construction, so they enter the expression as constants and only q stays symbolic.
        # That keeps the Hessian constant and the problem a genuine convex QP rather than a
        # general nonlinear program. Rewriting these to read the Params symbolically would
        # still "work" but would hand the solver a harder problem.
        #
        # Note also the k and k+1 reads: SIX breakpoints bound FIVE segments, so every loop
        # here spans a pair. Mixing up breakpoint and segment indexing is the classic
        # off-by-one in this formulation.
        prod_cost = 0
        for r in self.region_analyze:
            for y in self.year:
                for k_seg in range(1, len(self.SUPPLY_STEP_IDS) + 1):
                    tname = f'step{k_seg}'
                    qb_k_v = value(self.QBASE[r, k_seg, y])
                    qb_k1_v = value(self.QBASE[r, k_seg + 1, y])
                    width_v = qb_k1_v - qb_k_v
                    # Zero-width segments are skipped, not divided by. They arise wherever a
                    # region has no capacity of a given type (all breakpoints collapse onto
                    # the same value), and without this guard the slope below is a 0/0.
                    if width_v <= 1e-9:
                        continue
                    pb_k_v = value(self.PBASE[r, k_seg, y])
                    pb_k1_v = value(self.PBASE[r, k_seg + 1, y])
                    slope_v = (pb_k1_v - pb_k_v) / width_v
                    q = self.sstep[r, tname, y]
                    prod_cost = prod_cost + (pb_k_v * q + 0.5 * slope_v * q * q) * bcf

        # 2) Gathering charge (NGMM Eq 7, P^gath term)
        gathering_cost = quicksum(
            self.gathering_charge[r] * self.production_total[r, y] * bcf
            for r in self.region_analyze
            for y in self.year
        )

        # 3) LNG backstop import cost (legacy exogenous import term)
        lng_backstop_cost = quicksum(
            self.lng_import[r, y] * self.lng_cost[r] * bcf
            for r in self.region_analyze
            for y in self.year
        )

        # 4) Transport cost, area under the pipeline tariff curve (NGMM Eq 7)
        transport_cost = 0
        for o, d in arc_list:
            for y in self.year:
                for k_seg in self.tariff_segs:
                    qt_k_v = value(self.QTAR[o, d, k_seg, y])
                    qt_k1_v = value(self.QTAR[o, d, k_seg + 1, y])
                    width_v = qt_k1_v - qt_k_v
                    if width_v <= 1e-9:
                        continue
                    pt_k_v = value(self.PTAR[o, d, k_seg, y])
                    pt_k1_v = value(self.PTAR[o, d, k_seg + 1, y])
                    slope_v = (pt_k1_v - pt_k_v) / width_v
                    q = self.tar_step[o, d, k_seg, y]
                    transport_cost = transport_cost + (pt_k_v * q + 0.5 * slope_v * q * q) * bcf

        # 5) Storage opex (linear)
        storage_cost = quicksum(
            self.stor_inject[r, y] * self.storage_opex * bcf
            for r in self.region_analyze
            for y in self.year
        )

        # 6) LNG consumer surplus, area under the LNG export demand curve
        #    (NGMM Eq 7, LNG block).  Demand-curve slope is negative
        #    (PLNG decreases with QLNG), so the quadratic term contributes a
        #    *concave* surplus that we ADD to consumer surplus and therefore
        #    SUBTRACT from total_cost.
        # Skip zero-width LNG segments
        # for (region, year) pairs with no LNG capacity (e.g. pacific 2025,
        # whose LNG_EXPORT_DEMAND_BCF entry is 0).  Without the guard, all
        # QLNG[k] = 0 → /0 in slope computation.
        lng_consumer_surplus = 0
        for r in self.lng_regions:
            for y in self.year:
                for k_seg in self.lng_segs:
                    ql_k_v = value(self.QLNG[r, k_seg, y])
                    ql_k1_v = value(self.QLNG[r, k_seg + 1, y])
                    width_v = ql_k1_v - ql_k_v
                    if width_v <= 1e-9:
                        continue
                    pl_k_v = value(self.PLNG[r, k_seg, y])
                    pl_k1_v = value(self.PLNG[r, k_seg + 1, y])
                    slope_v = (pl_k1_v - pl_k_v) / width_v
                    q = self.lng_export_step[r, k_seg, y]
                    lng_consumer_surplus = (
                        lng_consumer_surplus + (pl_k_v * q + 0.5 * slope_v * q * q) * bcf
                    )

        # Price the unserved-demand backstop for subset
        # runs. UNSERVED_PENALTY is ~100x any plausible gas price, so the solver uses it only
        # when the subset cannot source the gas, and the demand-balance dual in a
        # short region comes back at the penalty level, an unmistakable "this subset is
        # supply-short" signal instead of an opaque infeasibility. Zero for the full model.
        unserved_cost = 0
        if self.is_region_subset:
            unserved_cost = quicksum(
                self.unserved[r, y] * UNSERVED_PENALTY * bcf
                for r in self.region_analyze
                for y in self.year
            )

        # Original objective preserved (unserved_cost is identically 0 for the full model):
        # self.total_cost = Objective(
        #     expr=(prod_cost + gathering_cost + lng_backstop_cost
        #           + transport_cost + storage_cost - lng_consumer_surplus),
        #     sense=minimize)
        # THE OBJECTIVE IS LEGITIMATELY NEGATIVE (about -371.8M at full resolution). The LNG
        # consumer-surplus term is subtracted and dominates the positive cost terms. This is
        # not a sign error: NGMM maximises surplus, and minimising the negative of it is the
        # same optimisation, chosen so an integrator that expects a scalar to minimise, and
        # code that writes `meta.obj = ... + ng_model.total_cost`, both keep working.
        #
        # Only prod_cost, transport_cost and lng_consumer_surplus carry quadratic terms; the
        # other three blocks are linear.
        self.total_cost = Objective(
            expr=(
                prod_cost
                + gathering_cost
                + lng_backstop_cost
                + transport_cost
                + storage_cost
                - lng_consumer_surplus
                + unserved_cost
            ),
            sense=minimize,
        )

    # ── Integration interface ─────────────────────────────────────────────────
    #
    # The eight methods below are the ENTIRE surface a Gauss-Seidel loop drives. They work
    # because a handful of Params were declared mutable=True during construction, Q0, P0,
    # demand, and canada_supply. Everything else is fixed once the model is built.
    #
    # Inbound: update_demand, update_demand_from_price, update_canada_supply,
    # update_supply_capacity, set_reference_prices
    # Outbound: poll_gas_price (the duals), poll_total_gas_demand
    #
    # No method here re-solves. The caller owns the loop and decides when to solve, which is
    # what makes the ordering in docs/COUPLING.md the caller's responsibility rather than
    # something enforced here.

    def set_reference_prices(self, prices: dict[GI, float]) -> None:
        """Store solved gas prices as the reference baseline for demand elasticities.

        Call once after the first Gauss-Seidel solve so that subsequent calls
        to ``update_demand_from_price()`` compute demand adjustments relative
        to this equilibrium, not relative to an arbitrary initial price.

        Parameters
        ----------
        prices : dict[GI, float]
            Shadow prices from ``poll_gas_price()`` in $/MMBtu.
        """
        for gi, p in prices.items():
            self._ref_prices[(gi.region, gi.year)] = p
        logger.debug('C-NGMM: reference prices set for %d (region, year) pairs', len(prices))

    def update_demand_from_price(
        self,
        solved_prices: dict[GI, float],
        alpha: float = 1.0,
    ) -> None:
        """Adjust sector demands using own-price elasticities (NEMS NGMM demand blocks).

        For each region, year, and sector:

            demand_new = base_demand × (price / ref_price) ^ elasticity

        where ``base_demand`` is the initial AEO-projected demand (stored at
        construction) and ``ref_price`` is the reference shadow price set by
        ``set_reference_prices()``.  Under-relaxation is applied when alpha < 1.

        Has no effect until ``set_reference_prices()`` has been called (reference
        prices default to empty → price ratio = 1 → no adjustment).

        Parameters
        ----------
        solved_prices : dict[GI, float]
            Current shadow prices from ``poll_gas_price()`` in $/MMBtu.
        alpha : float
            Under-relaxation factor (0 < alpha ≤ 1).
        """
        # SILENT NO-OP IF THE REFERENCE WAS NEVER SET. Deliberate, it lets the first
        # iteration run before any price exists, but it means forgetting the
        # set_reference_prices() call disables price-responsive demand for the whole run with
        # no warning. If the non-electric sectors are not moving between iterations, check
        # this first.
        if not self._ref_prices:
            return  # no reference set yet; skip silently

        n_updated = 0
        for r in self.region_analyze:
            for y in self.year:
                price = solved_prices.get(GI(region=r, year=y))
                if price is None:
                    continue
                ref_p = self._ref_prices.get((r, y))
                if ref_p is None or ref_p < 1e-6:
                    continue
                price_ratio = max(price, 1e-6) / ref_p

                for sector in self.sectors:
                    elas = self.DEMAND_PRICE_ELASTICITY.get(sector, 0.0)
                    if abs(elas) < 1e-9:
                        continue
                    base_d = self._base_demand.get((r, sector, y), 0.0)
                    new_d = base_d * (price_ratio**elas)
                    if alpha < 1.0:
                        current = value(self.demand[r, sector, y])
                        new_d = alpha * new_d + (1.0 - alpha) * current
                    self.demand[r, sector, y].set_value(max(new_d, 0.0))
                    n_updated += 1

        logger.debug(
            'C-NGMM.update_demand_from_price: updated %d demand entries (alpha=%.2f)',
            n_updated,
            alpha,
        )

    def update_demand(
        self,
        new_demand: dict[GI, float],
        sector: str = 'electric_power',
        alpha: float = 1.0,
    ) -> None:
        """Update a single sector's demand from an external model.

        Called by the integrator to pass electricity-model gas-demand updates.
        When ``alpha < 1`` the update is blended with the current value to
        damp GS oscillations:  qty_new = alpha * qty + (1 - alpha) * qty_old.

        Parameters
        ----------
        new_demand : dict[GI, float]
            {GI(region, year): demand_BCF_per_year}
        sector : str
            Demand sector to update (default: 'electric_power').
        alpha : float
            Under-relaxation factor in (0, 1].  1.0 = full replacement.
        """
        valid_regions = set(self.region_analyze)
        for gi, qty in new_demand.items():
            if gi.region not in valid_regions:
                logger.debug('C-NGMM.update_demand: unknown region %s, skipped', gi.region)
                continue
            if alpha < 1.0:
                current = value(self.demand[gi.region, sector, gi.year])
                qty = alpha * qty + (1.0 - alpha) * current
            self.demand[gi.region, sector, gi.year].set_value(qty)

    def update_canada_supply(self, supply: dict[GI, float]) -> None:
        """Update Canadian gas imports by region and year.

        Parameters
        ----------
        supply : dict[GI, float]
            {GI(region, year): supply_BCF_per_year}
        """
        valid_regions = set(self.region_analyze)
        for gi, qty in supply.items():
            if gi.region not in valid_regions:
                logger.debug('C-NGMM.update_canada_supply: unknown region %s, skipped', gi.region)
                continue
            self.canada_supply[gi.region, gi.year].set_value(qty)

    def update_supply_capacity(
        self,
        capacity_updates: dict,  # {(region_str, cost_tier_str, year_int): bcf_float}
        alpha: float = 1.0,
    ) -> None:
        """Rebuild the NGMM supply curve from externally supplied capacity.

        THE MIDDLE ELEMENT OF EACH KEY IS DISCARDED. The HSM integrator passes a three-key
        {(region, cost_tier, year): bcf} dict using the legacy low_cost / medium_cost /
        high_cost labels. The NGMM curve is built around a single expected production point
        Q0[r, y], not three independent caps, so this method sums across whatever cost tiers
        appear for a (region, year) and uses only the total. Passing one combined row per
        (region, year) gives an identical result to passing three.

        The tuple shape is retained deliberately, so existing callers keep working; it is not
        evidence that the model represents cost tiers. See the note on `self.steps` in
        __init__ for the NGMM vocabulary.

        Having aggregated to a new Q0, this alpha-blends it against the current Q0, rebuilds
        the breakpoints (QBASE, PBASE) via the NGMM Eq 2-5 helpers, and refreshes the
        step-width ``supply_capacity`` Param. The objective's quadratic supply-cost expression
        picks the new curve up on the next solve.

        Parameters
        ----------
        capacity_updates : dict
            {(region, cost_tier, year): capacity_BCF}. The cost_tier element is ignored;
            values are summed per (region, year).
        alpha : float
            Under-relaxation factor (0 < alpha <= 1.0)
        """
        # Step 1: aggregate to per-(region, year) totals. `_cost_tier` is unused by design --
        # see the docstring above; the underscore marks it as deliberately discarded.
        agg: dict[tuple[str, int], float] = defaultdict(float)
        for (region, _cost_tier, year), cap in capacity_updates.items():
            if region not in self.region_analyze:
                continue
            agg[(region, int(year))] += float(cap)

        if not agg:
            return

        # Step 2: blend new Q0 with current Q0 (under-relaxation) and rebuild
        # the breakpoints for that (region, year) pair.
        crv_below = self.SUPPLY_CURVE_SHAPE['crv_below']
        crv_above = self.SUPPLY_CURVE_SHAPE['crv_above']
        elas = self.SUPPLY_CURVE_SHAPE['elas']
        qmin_frac = self.QP_SCALARS.get('supply_curve_qmin_fraction', 0.20)
        rebuilt = 0
        for (region, year), new_q0 in agg.items():
            if alpha < 1.0:
                current_q0 = value(self.Q0[region, year])
                new_q0 = alpha * new_q0 + (1.0 - alpha) * current_q0
            new_q0 = max(new_q0, 1.0)  # numerical floor to keep breakpoints non-degenerate
            self.Q0[region, year].set_value(new_q0)
            self.QMIN[region, year].set_value(qmin_frac * new_q0)

            p0 = value(self.P0[region, year])
            for k in self.SUPPLY_BREAK_IDS:  # 1..6
                self.QBASE[region, k, year].set_value(
                    _supply_qbase(new_q0, k, crv_below, crv_above)
                )
                self.PBASE[region, k, year].set_value(
                    _supply_pbase(p0, k, crv_below, crv_above, elas)
                )
            # Refresh the legacy-shape ``supply_capacity`` Param (segment widths)
            for k_seg in self.SUPPLY_STEP_IDS:  # 1..5
                tname = f'step{k_seg}'
                width = value(self.QBASE[region, k_seg + 1, year] - self.QBASE[region, k_seg, year])
                self.supply_capacity[region, tname, year].set_value(max(width, 0.0))
            rebuilt += 1

        logger.debug(
            'C-NGMM.update_supply_capacity: rebuilt curve for %d (region, year) pairs (alpha=%.2f)',
            rebuilt,
            alpha,
        )

    def poll_gas_price(self) -> dict[GI, float]:
        """Return solved regional gas prices (shadow prices of demand balance).

        Returns
        -------
        dict[GI, float]
            {GI(region, year): gas_price_$/MMBtu}
        """
        prices: dict[GI, float] = {}
        for r in self.region_analyze:
            for y in self.year:
                try:
                    # Dual of the demand_balance constraint.
                    # The LP sign of the dual depends on which side of the
                    # equality is binding (supply-abundant vs demand-deficit
                    # regions get opposite signs in the degenerate dual
                    # solution).  The absolute value recovers the correct
                    # locational marginal price: price spreads between
                    # adjacent regions equal pipeline tariffs exactly.
                    p = self.dual[self.demand_balance[r, y]]
                    prices[GI(region=r, year=y)] = abs(p) / value(self.bcf_to_mmbtu)
                except KeyError:
                    prices[GI(region=r, year=y)] = 0.0
        return prices

    def poll_total_gas_demand(self) -> dict[GI, float]:
        """Return total gas consumption by region and year after solve.

        Returns
        -------
        dict[GI, float]
            {GI(region, year): total_gas_demand_BCF_per_year}
        """
        result: dict[GI, float] = defaultdict(float)
        for r in self.region_analyze:
            for s in self.sectors:
                for y in self.year:
                    result[GI(region=r, year=y)] += value(self.demand[r, s, y])
        return dict(result)

    @deprecated('reporting now does this w/o attaching to model')
    def attach_results(self) -> None:
        """Populate result DataFrames after an iterative solve.

        Called by the integrator after convergence to make result tables
        available for reporting without re-solving.  Equivalent to the
        table-attachment step inside the standalone ``solve()`` function.
        """
        # Imported here rather than at module scope: postprocessor imports NGModel
        # for type checking, so a top-level import would be circular.
        from src.models.natural_gas.postprocessor import (
            _extract_balance,
            _extract_flows,
            _extract_prices,
            _extract_production,
            _extract_storage,
        )

        self.results_production = _extract_production(self)
        self.results_flows = _extract_flows(self)
        self.results_prices = _extract_prices(self)
        self.results_storage = _extract_storage(self)
        self.results_balance = _extract_balance(self)


###############################################################################
# Solve & Report
###############################################################################

# dev note:  the solve procedure is now resident in the NGSequencer, and result
# extraction / reporting now lives in postprocessor.py
