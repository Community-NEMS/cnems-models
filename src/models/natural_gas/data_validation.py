"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  10/6/26

A set of independent functions that validate the loaded natural gas data (``NGData``) before the
model is built.  Each of the model's three piecewise-linear curves (supply, pipeline tariff, LNG
export demand) needs breakpoints that are strictly increasing in quantity and a slope sign that
keeps the QP convex.  Data that breaks either rule does not fail at build time.  A segment of
negative width makes its cap constraint infeasible, a zero-width one is silently dropped, and a
wrong-signed slope makes the objective non-convex.  These checks turn all three into a named
error before the build.

Validations can run independently or in concert, and report failures to the log; ``validate_all``
halts on any failure when ``strict`` is set.
"""

import logging
import math
from collections.abc import Sequence
from itertools import pairwise
from typing import cast

from src.common.exceptions import DataValidationError
from src.models.natural_gas.data import NGData

logger = logging.getLogger(__name__)


def validate_all(data: NGData, strict: bool = True) -> None:
    """Run all validations on the loaded natural gas data.

    Every validation is run so that a single pass reports all problems before halting.

    Parameters
    ----------
    data : NGData
        Loaded data from ``load_all``, after any inbound update packages are applied.
    strict : bool
        True => raise on validation failure; False => log it and continue.

    Raises
    ------
    DataValidationError
        If any validation failed and ``strict`` is set.
    """
    all_valid = True
    supply_shape = data['supply_curve_shape']

    all_valid &= validate_supply_curve_shape(supply_shape)
    all_valid &= validate_qmin_fraction(
        data['qp_scalars']['supply_curve_qmin_fraction'], supply_shape['crv_below']
    )
    all_valid &= validate_supply_anchors(
        data['supply_cost_tiers'], data['supply_anchors'], data['regions_analyze'], data['years']
    )
    all_valid &= validate_tariff_curve_shape(data['tariff_curve_shape'])
    all_valid &= validate_pipeline_arcs(data['pipeline_arcs'])
    all_valid &= validate_lng_demand_curve(data['lng_demand_curve'])
    all_valid &= validate_lng_export(data['lng_export'])

    if not all_valid:
        message = 'Natural gas data validation failed.  See log for details.'
        if strict:
            raise DataValidationError(message)
        logger.error('%s  Continuing, since strict validation is off.', message)


def _strictly_increasing(values: Sequence[float]) -> bool:
    """True if every value is greater than the one before it."""
    return all(b > a for a, b in pairwise(values))


def validate_supply_curve_shape(shape: dict[str, list[float]]) -> bool:
    """Validate the supply-curve shape vectors (``ng_supply_curve_shape.csv``).

    Checked, so that every breakpoint quantity and price is positive and strictly increasing:

    * every ``crv_below`` in (0, 1):  0 gives a zero-width segment, 1 or more a breakpoint at or
      below zero quantity
    * every ``crv_above`` > 0:  0 gives a zero-width segment
    * every ``elas`` > 0
    * every ``crv_below[i] / elas[i]`` < 1:  the below-anchor price factor ``1 - crv/elas`` must
      stay positive, or the breakpoint price is zero or negative

    Parameters
    ----------
    shape : dict[str, list[float]]
        ``crv_below`` (3), ``crv_above`` (3) and ``elas`` (5), as loaded by
        ``load_supply_curve_shape``.

    Returns
    -------
    bool
        True if all checks pass.  Each failure is logged as an error.
    """
    valid = True
    crv_below, crv_above, elas = shape['crv_below'], shape['crv_above'], shape['elas']
    for i, c in enumerate(crv_below, start=1):
        if not 0.0 < c < 1.0:
            valid = False
            logger.error('Supply curve crv_below step %d is %s; must be in (0, 1)', i, c)
    for i, c in enumerate(crv_above, start=1):
        if not c > 0.0:
            valid = False
            logger.error('Supply curve crv_above step %d is %s; must be > 0', i, c)
    for i, e in enumerate(elas, start=1):
        if not e > 0.0:
            valid = False
            logger.error('Supply curve elas segment %d is %s; must be > 0', i, e)
    # below the anchor crv_below[i] pairs with elas[i] (see data.supply_pbase)
    for i, (c, e) in enumerate(zip(crv_below, elas, strict=False), start=1):
        if e > 0.0 and not c / e < 1.0:
            valid = False
            logger.error(
                'Supply curve crv_below step %d / elas %d = %s / %s >= 1; the breakpoint price '
                'would be zero or negative',
                i,
                i,
                c,
                e,
            )
    return valid


def validate_qmin_fraction(qmin_fraction: float, crv_below: list[float]) -> bool:
    """Validate ``supply_curve_qmin_fraction`` against the supply-curve shape.

    ``QBASE_1`` is set to ``qmin_fraction * Q0`` (see ``NGModel._supply_qbase_at``), so it must sit
    strictly below ``QBASE_2 = Q0 * (1 - crv_below[1]) * (1 - crv_below[2])`` for supply segment 1
    to have positive width.

    Parameters
    ----------
    qmin_fraction : float
        ``supply_curve_qmin_fraction`` from ``ng_scalars.csv``.
    crv_below : list[float]
        The three below-anchor ``crv`` values.

    Returns
    -------
    bool
        True if ``0 <= qmin_fraction < QBASE_2 / Q0``.  A failure is logged as an error.
    """
    qbase_2_frac = (1.0 - crv_below[1]) * (1.0 - crv_below[2])
    if 0.0 <= qmin_fraction < qbase_2_frac:
        return True
    logger.error(
        'supply_curve_qmin_fraction is %s; must be in [0, %.4f) so that supply segment 1 '
        '(QMIN to QBASE_2) has positive width',
        qmin_fraction,
        qbase_2_frac,
    )
    return False


def validate_supply_anchors(
    cost_tiers: dict[str, list[tuple[float, float]]],
    anchors: dict[tuple[str, int], tuple[float, float]],
    regions: Sequence[str],
    years: Sequence[int],
) -> bool:
    """Validate the supply anchor (Q0, P0) for every analysis region and year.

    Q0 is the summed tier capacity times ``q0_mult``, and P0 the capacity-weighted tier cost times
    ``p0_mult``.  Q0 = 0 is allowed, and is how a region with no production is expressed:  the
    model gives its supply segments zero width and (0, 0) cost coefficients.  A negative or
    non-finite tier capacity or Q0 is not.  P0 must be positive wherever Q0 > 0, or the curve
    prices at zero; with Q0 = 0 the model ignores the tier costs (P0 falls back to 3.0), so P0 is
    not checked.  A region with no tiers at all is flagged too:  that is more likely an omission
    than a deliberate zero, and the model build would fail on it.

    Parameters
    ----------
    cost_tiers : dict[str, list[tuple[float, float]]]
        ``{region: [(capacity_bcf, cost_per_mmbtu), ...]}`` from ``ng_supply_cost_tiers.csv``.
    anchors : dict[tuple[str, int], tuple[float, float]]
        ``{(region, year): (q0_mult, p0_mult)}`` from ``ng_supply_anchors.csv``; missing entries
        mean (1.0, 1.0), as in the model.
    regions : Sequence[str]
        Analysis regions.
    years : Sequence[int]
        Model years.

    Returns
    -------
    bool
        True if every region has tiers, every tier capacity is finite and >= 0, and every year
        has a finite Q0 >= 0 and, where Q0 > 0, a positive P0.  Each failure is logged as an
        error.
    """
    valid = True
    for r in regions:
        tiers = cost_tiers.get(r)
        if not tiers:
            valid = False
            logger.error('Region %s has no supply cost tiers', r)
            continue
        bad_caps = [cap for cap, _ in tiers if not (math.isfinite(cap) and cap >= 0.0)]
        if bad_caps:
            valid = False
            logger.error(
                'Region %s has supply tier capacities %s; must be finite and >= 0', r, bad_caps
            )
            continue
        total_q = sum(cap for cap, _ in tiers)
        weighted_p = sum(cap * cost for cap, cost in tiers) / total_q if total_q > 0 else 0.0
        for y in years:
            q0_mult, p0_mult = anchors.get((r, y), (1.0, 1.0))
            q0, p0 = total_q * q0_mult, weighted_p * p0_mult
            if not (math.isfinite(q0) and q0 >= 0.0):
                valid = False
                logger.error(
                    'Supply anchor Q0 for (%s, %d) is %s; must be finite and >= 0', r, y, q0
                )
            elif q0 > 0.0 and not (math.isfinite(p0) and p0 > 0.0):
                valid = False
                logger.error('Supply anchor P0 for (%s, %d) is %s; must be > 0', r, y, p0)
    return valid


def validate_tariff_curve_shape(shape: dict[str, list[float]]) -> bool:
    """Validate the pipeline tariff-curve shape (``ng_tariff_curve_shape.csv``).

    ``util_break`` must start at or above zero and be strictly increasing (the loader sorts it, so
    a repeated value is the failure that shows up, as a zero-width segment).  ``tariff_mult`` must
    be non-decreasing:  a falling tariff gives a negative slope and a non-convex transport cost.

    Parameters
    ----------
    shape : dict[str, list[float]]
        ``util_break`` and ``tariff_mult``, as loaded by ``load_tariff_curve_shape``.

    Returns
    -------
    bool
        True if all checks pass.  Each failure is logged as an error.
    """
    valid = True
    util, mult = shape['util_break'], shape['tariff_mult']
    if util[0] < 0.0:
        valid = False
        logger.error('Tariff curve util_break starts at %s; must be >= 0', util[0])
    if not _strictly_increasing(util):
        valid = False
        logger.error('Tariff curve util_break %s is not strictly increasing', util)
    if any(b < a for a, b in pairwise(mult)):
        valid = False
        logger.error('Tariff curve tariff_mult %s is not non-decreasing', mult)
    return valid


def validate_pipeline_arcs(arcs: list[tuple[str, str, float, float]]) -> bool:
    """Validate pipeline arc capacities and tariffs (``ng_pipeline_arcs.csv``).

    Every tariff-curve breakpoint quantity is the arc capacity times a ``util_break``, so a zero
    or negative capacity collapses or inverts every segment on that arc.  A negative base tariff
    inverts the tariff curve.

    Parameters
    ----------
    arcs : list[tuple[str, str, float, float]]
        ``(origin, destination, capacity_bcf, tariff_per_mmbtu)`` per directed arc.

    Returns
    -------
    bool
        True if every arc has capacity > 0 and tariff >= 0.  Each failure is logged as an error.
    """
    valid = True
    for origin, destination, capacity, tariff in arcs:
        if not capacity > 0.0:
            valid = False
            logger.error(
                'Pipeline arc %s -> %s has capacity %s; must be > 0', origin, destination, capacity
            )
        if tariff < 0.0:
            valid = False
            logger.error(
                'Pipeline arc %s -> %s has tariff %s; must be >= 0', origin, destination, tariff
            )
    return valid


def validate_lng_demand_curve(shape: dict[str, list[float] | float]) -> bool:
    """Validate the LNG export demand-curve shape (``ng_lng_demand_curve.csv`` + scalars).

    ``q_frac`` must start at or above zero and be strictly increasing (the loader sorts it, so a
    repeated value shows up as a zero-width segment).  ``p_factor`` must be non-increasing and
    non-negative, and the world LNG price positive:  a demand curve that slopes upward makes the
    consumer-surplus term, and so the objective, non-convex.

    Parameters
    ----------
    shape : dict[str, list[float] | float]
        ``q_frac``, ``p_factor`` and ``world_price``, as loaded by ``load_lng_demand_curve``.

    Returns
    -------
    bool
        True if all checks pass.  Each failure is logged as an error.
    """
    valid = True
    q_frac = cast(list[float], shape['q_frac'])
    p_factor = cast(list[float], shape['p_factor'])
    world_price = cast(float, shape['world_price'])
    if q_frac[0] < 0.0:
        valid = False
        logger.error('LNG demand curve q_frac starts at %s; must be >= 0', q_frac[0])
    if not _strictly_increasing(q_frac):
        valid = False
        logger.error('LNG demand curve q_frac %s is not strictly increasing', q_frac)
    if any(b > a for a, b in pairwise(p_factor)):
        valid = False
        logger.error('LNG demand curve p_factor %s is not non-increasing', p_factor)
    if any(p < 0.0 for p in p_factor):
        valid = False
        logger.error('LNG demand curve p_factor %s has a negative entry', p_factor)
    if not world_price > 0.0:
        valid = False
        logger.error('lng_world_price_per_mmbtu is %s; must be > 0', world_price)
    return valid


def validate_lng_export(lng_export: dict[str, dict[int, float]]) -> bool:
    """Validate LNG export capacities (``ng_lng_export.csv``).

    Zero is allowed:  it is a real state (an export terminal not yet online), and the model gives
    its segments zero width and zero surplus coefficients.  A negative capacity is not, since it
    inverts the curve and makes its segment caps infeasible.

    Parameters
    ----------
    lng_export : dict[str, dict[int, float]]
        ``{region: {year: demand_bcf}}``.

    Returns
    -------
    bool
        True if every capacity is >= 0.  Each failure is logged as an error.
    """
    valid = True
    for region, table in lng_export.items():
        for year, bcf in table.items():
            if bcf < 0.0:
                valid = False
                logger.error(
                    'LNG export capacity for (%s, %d) is %s; must be >= 0', region, year, bcf
                )
    return valid
