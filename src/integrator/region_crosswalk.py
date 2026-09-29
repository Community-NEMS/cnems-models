"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/17/26

Generic regional crosswalks between models.

The weights bridging one model's regions to another's are CSV files under
``input/integrator/population_weighted_region_xwalk`` (see the README there for provenance),
registered here by the ordered ``(source, destination)`` pair of ``ModelType`` they connect.
``crosswalk_values`` maps a ``(region, year)``-indexed series from one model's regions to the
other's, either allocating an extensive quantity (demand, volume) or averaging an intensive one
(price, rate).
"""

import logging
from enum import Enum
from pathlib import Path

import pandas as pd

from definitions import PROJECT_ROOT
from src.common.models_modes import ModelType

logger = logging.getLogger(__name__)

XWALK_DIR = PROJECT_ROOT / 'input' / 'integrator' / 'population_weighted_region_xwalk'

# the region-id column each model uses in the crosswalk files
REGION_COLUMN: dict[ModelType, str] = {
    ModelType.ELECTRICITY: 'elec_region',
    ModelType.NATURAL_GAS: 'ng_region',
}

# (source, destination) -> the file whose weights sum to 1.0 over each SOURCE region, i.e. the
# share of a source region that lands in each destination region.  Add an entry per model pair.
REGION_CROSSWALKS: dict[tuple[ModelType, ModelType], Path] = {
    (ModelType.NATURAL_GAS, ModelType.ELECTRICITY): XWALK_DIR / 'ng_to_elec_crosswalk.csv',
    (ModelType.ELECTRICITY, ModelType.NATURAL_GAS): XWALK_DIR / 'elec_to_ng_crosswalk.csv',
}

WEIGHT_COLUMN = 'weight'
# index level names of the series crosswalk_values accepts and returns
VALUE_INDEX = ['region', 'year']
_WEIGHT_SUM_TOL = 1e-3


class QuantityKind(Enum):
    """How a value behaves when its region is split or merged."""

    EXTENSIVE = 'extensive'  # allocate: a source region's total is spread over destinations
    INTENSIVE = 'intensive'  # average: a destination is the weighted mean of its sources


def _check_endpoints(source: ModelType, destination: ModelType) -> None:
    """Reject endpoints that cannot name a set of regions.

    Raises
    ------
    ValueError
        If either endpoint is the ``ModelType.ALL`` indicator or the two are the same model.
    """
    for end in (source, destination):
        if end is ModelType.ALL:
            raise ValueError(
                f'{ModelType.ALL} is an indicator for every model, not a model with regions; '
                'a crosswalk needs two specific models.'
            )
    if source is destination:
        raise ValueError(f'A crosswalk needs two different models; got {source} twice.')


def load_region_weights(source: ModelType, destination: ModelType) -> pd.DataFrame:
    """Load the registered weights from ``source`` regions to ``destination`` regions.

    Parameters
    ----------
    source : ModelType
        Model whose regions the weights are normalised over.
    destination : ModelType
        Model whose regions each source region is split across.

    Returns
    -------
    pd.DataFrame
        Columns ``[REGION_COLUMN[source], REGION_COLUMN[destination], 'weight']`` with both region
        ids as stripped strings.  Weights sum to 1.0 for every source region.

    Raises
    ------
    ValueError
        If an endpoint is invalid, a required column is absent, or a source region's weights do
        not sum to 1.0 within ``_WEIGHT_SUM_TOL``.
    KeyError
        If no crosswalk is registered for the pair.
    """
    _check_endpoints(source, destination)
    try:
        path = REGION_CROSSWALKS[(source, destination)]
    except KeyError:
        registered = [f'{s.value} -> {d.value}' for s, d in REGION_CROSSWALKS]
        raise KeyError(
            f'No region crosswalk registered for {source.value} -> {destination.value}; '
            f'registered pairs: {registered}'
        ) from None
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
    weights = pd.read_csv(path, dtype={src_col: str, dst_col: str})
    missing = sorted({src_col, dst_col, WEIGHT_COLUMN}.difference(weights.columns))
    if missing:
        raise ValueError(
            f'{path} is missing required column(s) {missing}; header reads {list(weights.columns)}'
        )
    weights = weights[[src_col, dst_col, WEIGHT_COLUMN]].copy()
    weights[src_col] = weights[src_col].str.strip()
    weights[dst_col] = weights[dst_col].str.strip()
    sums = weights.groupby(src_col)[WEIGHT_COLUMN].sum()
    off = sums[(sums - 1.0).abs() > _WEIGHT_SUM_TOL]
    if not off.empty:
        raise ValueError(f'{path}: weights must sum to 1.0 per {src_col}; off for {off.to_dict()}')
    logger.debug(
        'Loaded %s -> %s region weights: %d rows, %d source regions',
        source.value,
        destination.value,
        len(weights),
        len(sums),
    )
    return weights


def crosswalk_values(
    values: pd.Series, source: ModelType, destination: ModelType, kind: QuantityKind
) -> pd.Series:
    """Map a ``(region, year)``-indexed series from one model's regions to another's.

    Parameters
    ----------
    values : pd.Series
        Indexed by ``VALUE_INDEX`` (``region``, ``year``) in the source model's region ids.
    source : ModelType
        Model the values belong to.
    destination : ModelType
        Model whose regions to express the values in.
    kind : QuantityKind
        ``EXTENSIVE`` allocates with the ``(source, destination)`` weights, so totals are
        conserved:  ``out[d, y] = sum_s w[s, d] * v[s, y]``.  ``INTENSIVE`` averages with the
        ``(destination, source)`` weights, renormalised over the source regions actually present:
        ``out[d, y] = sum_s w[d, s] * v[s, y] / sum_s w[d, s]``.

    Returns
    -------
    pd.Series
        Indexed by ``VALUE_INDEX`` in the destination model's region ids, sorted, carrying the
        input's name.  A destination region linked to none of the supplied source regions is
        absent, not zero.

    Raises
    ------
    ValueError
        If the index level names are not ``VALUE_INDEX`` or an endpoint is invalid.

    Notes
    -----
    Source regions absent from the crosswalk are dropped with a warning.  For ``INTENSIVE`` a
    destination region whose supplied sources cover less than the full weight (a region-filtered
    run) is averaged over what is present and reported as a warning.
    """
    _check_endpoints(source, destination)
    if list(values.index.names) != VALUE_INDEX:
        raise ValueError(f'values must be indexed by {VALUE_INDEX}; got {list(values.index.names)}')
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
    if kind is QuantityKind.EXTENSIVE:
        weights = load_region_weights(source, destination)
    else:
        weights = load_region_weights(destination, source)

    name: str = str(values.name) if values.name is not None else 'value'
    frame = values.rename(name).reset_index()
    frame['region'] = frame['region'].astype(str)
    unknown = sorted(set(frame['region']).difference(weights[src_col]))
    if unknown:
        logger.warning(
            'Dropping %d %s region(s) with no %s -> %s crosswalk row:  %s',
            len(unknown),
            source.value,
            source.value,
            destination.value,
            unknown,
        )
    merged = weights.merge(frame, left_on=src_col, right_on='region', how='inner')
    merged['_weighted'] = merged[WEIGHT_COLUMN] * merged[name]
    grouped = merged.groupby([dst_col, 'year'])
    out = grouped['_weighted'].sum()
    if kind is QuantityKind.INTENSIVE:
        coverage = grouped[WEIGHT_COLUMN].sum()
        short = coverage[coverage < 1.0 - _WEIGHT_SUM_TOL]
        if not short.empty:
            logger.warning(
                'Averaging %s over partial coverage for %d %s region(s):  %s',
                name,
                short.index.get_level_values(0).nunique(),
                destination.value,
                sorted(short.index.get_level_values(0).unique()),
            )
        out = out / coverage
    out.index = out.index.set_names(VALUE_INDEX)
    out = out.sort_index().rename(name)
    logger.info(
        'Crosswalked %s from %s to %s (%s):  %d (region, year) values in, %d out',
        name,
        source.value,
        destination.value,
        kind.value,
        len(values),
        len(out),
    )
    return out
