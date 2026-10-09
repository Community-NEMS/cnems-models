"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/17/26

Generic regional crosswalks between models.

The weights bridging one model's regions to another's are CSV files under
``input/integrator/population_weighted_region_xwalk`` (see the README there for provenance),
registered here by the unordered pair of ``ModelType`` they connect.  Each pair registers two files,
one normalised over each model's regions; both are loaded and checked against each other on every
request, so a region or link present in only one direction is caught before any data moves.
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

# {model pair: {source model: file}}.  Each file's weights sum to 1.0 over each region of its
# source model, i.e. the share of a source region that lands in each region of the other model.
# Every pair registers both directions; lookup is order-free.  Add an entry per model pair.
REGION_CROSSWALKS: dict[frozenset[ModelType], dict[ModelType, Path]] = {
    frozenset({ModelType.ELECTRICITY, ModelType.NATURAL_GAS}): {
        ModelType.ELECTRICITY: XWALK_DIR / 'elec_to_ng_crosswalk.csv',
        ModelType.NATURAL_GAS: XWALK_DIR / 'ng_to_elec_crosswalk.csv',
    },
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


def _read_weights(path: Path, source: ModelType, destination: ModelType) -> pd.DataFrame:
    """Read one crosswalk file and check its weights are normalised over ``source`` regions.

    Parameters
    ----------
    path : Path
        The crosswalk CSV.
    source : ModelType
        Model whose regions the file's weights are normalised over.
    destination : ModelType
        The other model in the file.

    Returns
    -------
    pd.DataFrame
        Columns ``[REGION_COLUMN[source], REGION_COLUMN[destination], 'weight']`` with both region
        ids as stripped strings.

    Raises
    ------
    ValueError
        If a required column is absent, or a source region's weights do not sum to 1.0 within
        ``_WEIGHT_SUM_TOL``.
    """
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
        'Loaded %s -> %s region weights from %s: %d rows, %d source regions',
        source.value,
        destination.value,
        path.name,
        len(weights),
        len(sums),
    )
    return weights


def _check_reciprocal(
    forward: pd.DataFrame,
    reverse: pd.DataFrame,
    source: ModelType,
    destination: ModelType,
    paths: tuple[Path, Path],
) -> None:
    """Check two crosswalk directions name the same regions and the same links.

    Only rows with positive weight count.  A region or link present in one direction but not the
    other is isolated:  allocated (extensive) and averaged (intensive) values would travel over
    different connections, or a region would get no data at all.

    Parameters
    ----------
    forward : pd.DataFrame
        The ``source -> destination`` weights, as returned by :func:`_read_weights`.
    reverse : pd.DataFrame
        The ``destination -> source`` weights, as returned by :func:`_read_weights`.
    source : ModelType
        Source model of ``forward``.
    destination : ModelType
        Destination model of ``forward``.
    paths : tuple of Path
        The ``(forward, reverse)`` files, for messages.

    Raises
    ------
    ValueError
        If either model's region set differs between the directions, or, with matching regions,
        the sets of ``(source region, destination region)`` links differ.
    """
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
    fwd = forward[forward[WEIGHT_COLUMN] > 0.0]
    rev = reverse[reverse[WEIGHT_COLUMN] > 0.0]
    fwd_path, rev_path = paths

    problems: list[str] = []
    for model, col in ((source, src_col), (destination, dst_col)):
        in_fwd, in_rev = set(fwd[col]), set(rev[col])
        if in_fwd - in_rev:
            problems.append(
                f'{model.value} region(s) {sorted(in_fwd - in_rev)} are in {fwd_path.name} '
                f'but not {rev_path.name}'
            )
        if in_rev - in_fwd:
            problems.append(
                f'{model.value} region(s) {sorted(in_rev - in_fwd)} are in {rev_path.name} '
                f'but not {fwd_path.name}'
            )
    if problems:
        raise ValueError(
            f'Region crosswalk {source.value} <-> {destination.value} is not reciprocal:  '
            + '; '.join(problems)
        )

    fwd_links = set(zip(fwd[src_col], fwd[dst_col], strict=True))
    rev_links = set(zip(rev[src_col], rev[dst_col], strict=True))
    if fwd_links != rev_links:
        raise ValueError(
            f'Region crosswalk {source.value} <-> {destination.value} links differ by direction '
            f'({src_col}, {dst_col}):  only in {fwd_path.name} {sorted(fwd_links - rev_links)}; '
            f'only in {rev_path.name} {sorted(rev_links - fwd_links)}'
        )


def load_region_weights(source: ModelType, destination: ModelType) -> pd.DataFrame:
    """Load the registered weights from ``source`` regions to ``destination`` regions.

    Both directions of the pair are read and checked against each other with
    :func:`_check_reciprocal`; only the requested direction is returned.

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
        If an endpoint is invalid, a required column is absent, a source region's weights do not
        sum to 1.0 within ``_WEIGHT_SUM_TOL``, or the two directions disagree on regions or links.
    KeyError
        If no crosswalk is registered for the pair, or the pair lacks a file for either direction.
    """
    _check_endpoints(source, destination)
    try:
        files = REGION_CROSSWALKS[frozenset({source, destination})]
        paths = (files[source], files[destination])
    except KeyError:
        registered = [' <-> '.join(sorted(m.value for m in pair)) for pair in REGION_CROSSWALKS]
        raise KeyError(
            f'No region crosswalk registered for {source.value} -> {destination.value}; '
            f'registered pairs (each needs a file per direction): {registered}'
        ) from None
    forward = _read_weights(paths[0], source, destination)
    reverse = _read_weights(paths[1], destination, source)
    _check_reciprocal(forward, reverse, source, destination, paths)
    return forward


# TODO:  As this crosswalk matures, we will transition it to a stateful class so that we can
#        Build it once and re-use within the UpdatePackage writers.  By-use rebuild OK for now.
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
