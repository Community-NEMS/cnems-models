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
one normalised over each model's regions.

A :class:`RegionCrosswalk` holds both directions of one pair and checks them on construction:
weights finite, non-negative and summing to 1.0 per source region, and the same positive-weight
links both ways.  It is frozen and picklable, so it is built once per run and handed to the update
package writers (in a :class:`CrosswalkSet`).  :meth:`RegionCrosswalk.apply` maps a
``(region, year)``-indexed series from one model's regions to the other's, allocating an extensive
quantity (demand, volume) or averaging an intensive one (price, rate), after checking the series
against the regions the sending model actually carries.

A run may restrict either model to a subset of its regions only if the subsets are closed under the
crosswalk:  every region one side reaches must draw solely on regions the other side carries.  The
sending side is checked in :meth:`RegionCrosswalk.apply`; the receiving side by the reader, which
requires the received regions to equal its own.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np
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
# index level names of the series RegionCrosswalk.apply accepts and returns
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
    """Read one crosswalk file, keeping its two region columns and the weight.

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
        ids as stripped strings.  Unchecked; :class:`RegionCrosswalk` validates on construction.

    Raises
    ------
    ValueError
        If a required column is absent.
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
    return weights


def _check_weights(
    weights: pd.DataFrame, source: ModelType, destination: ModelType, name: str
) -> None:
    """Check one direction's weights are well formed and normalised over ``source`` regions.

    Parameters
    ----------
    weights : pd.DataFrame
        Columns ``[REGION_COLUMN[source], REGION_COLUMN[destination], 'weight']``.
    source : ModelType
        Model whose regions the weights are normalised over.
    destination : ModelType
        The other model.
    name : str
        The weights' origin (usually a file name), for messages.

    Raises
    ------
    ValueError
        If a column is absent, a weight is not finite or is negative, or a source region's weights
        do not sum to 1.0 within ``_WEIGHT_SUM_TOL``.
    """
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
    missing = sorted({src_col, dst_col, WEIGHT_COLUMN}.difference(weights.columns))
    if missing:
        raise ValueError(f'{name} is missing required column(s) {missing}')
    values = pd.to_numeric(weights[WEIGHT_COLUMN], errors='coerce').to_numpy(dtype=float)
    bad = ~np.isfinite(values) | (values < 0.0)
    if bad.any():
        rows = weights.loc[bad, [src_col, dst_col, WEIGHT_COLUMN]].head(10)
        raise ValueError(
            f'{name}: weights must be finite and >= 0; {int(bad.sum())} are not '
            f'(up to 10 shown):  {rows.to_dict("records")}'
        )
    sums = weights.groupby(src_col)[WEIGHT_COLUMN].sum()
    off = sums[(sums - 1.0).abs() > _WEIGHT_SUM_TOL]
    if not off.empty:
        raise ValueError(f'{name}: weights must sum to 1.0 per {src_col}; off for {off.to_dict()}')


def _positive_links(weights: pd.DataFrame, source: ModelType, destination: ModelType) -> set:
    """Return the ``(source region, destination region)`` pairs carrying positive weight."""
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
    positive = weights[weights[WEIGHT_COLUMN] > 0.0]
    return set(zip(positive[src_col], positive[dst_col], strict=True))


def _check_reciprocal(
    forward: pd.DataFrame,
    reverse: pd.DataFrame,
    source: ModelType,
    destination: ModelType,
    names: tuple[str, str],
) -> None:
    """Check two crosswalk directions name the same regions and the same links.

    Only rows with positive weight count.  A region or link present in one direction but not the
    other is isolated:  allocated (extensive) and averaged (intensive) values would travel over
    different connections, or a region would get no data at all.

    Parameters
    ----------
    forward : pd.DataFrame
        The ``source -> destination`` weights.
    reverse : pd.DataFrame
        The ``destination -> source`` weights.
    source : ModelType
        Source model of ``forward``.
    destination : ModelType
        Destination model of ``forward``.
    names : tuple of str
        The ``(forward, reverse)`` origins (usually file names), for messages.

    Raises
    ------
    ValueError
        If either model's region set differs between the directions, or, with matching regions,
        the sets of ``(source region, destination region)`` links differ.
    """
    src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
    fwd = forward[forward[WEIGHT_COLUMN] > 0.0]
    rev = reverse[reverse[WEIGHT_COLUMN] > 0.0]
    fwd_name, rev_name = names

    problems: list[str] = []
    for model, col in ((source, src_col), (destination, dst_col)):
        in_fwd, in_rev = set(fwd[col]), set(rev[col])
        if in_fwd - in_rev:
            problems.append(
                f'{model.value} region(s) {sorted(in_fwd - in_rev)} are in {fwd_name} '
                f'but not {rev_name}'
            )
        if in_rev - in_fwd:
            problems.append(
                f'{model.value} region(s) {sorted(in_rev - in_fwd)} are in {rev_name} '
                f'but not {fwd_name}'
            )
    if problems:
        raise ValueError(
            f'Region crosswalk {source.value} <-> {destination.value} is not reciprocal:  '
            + '; '.join(problems)
        )

    fwd_links = _positive_links(forward, source, destination)
    rev_links = {(s, d) for d, s in _positive_links(reverse, destination, source)}
    if fwd_links != rev_links:
        raise ValueError(
            f'Region crosswalk {source.value} <-> {destination.value} links differ by direction '
            f'({src_col}, {dst_col}):  only in {fwd_name} {sorted(fwd_links - rev_links)}; '
            f'only in {rev_name} {sorted(rev_links - fwd_links)}'
        )


def _registered_files(source: ModelType, destination: ModelType) -> dict[ModelType, Path]:
    """Return the registered ``{source model: file}`` map for a pair.

    Raises
    ------
    ValueError
        If an endpoint is invalid.
    KeyError
        If no crosswalk is registered for the pair, or the pair lacks a file for either direction.
    """
    _check_endpoints(source, destination)
    try:
        files = REGION_CROSSWALKS[frozenset({source, destination})]
        return {source: files[source], destination: files[destination]}
    except KeyError:
        registered = [' <-> '.join(sorted(m.value for m in pair)) for pair in REGION_CROSSWALKS]
        raise KeyError(
            f'No region crosswalk registered for {source.value} -> {destination.value}; '
            f'registered pairs (each needs a file per direction): {registered}'
        ) from None


@dataclass(frozen=True)
class RegionCrosswalk:
    """Both directions of the region weights between two models, checked on construction.

    Frozen and picklable:  build it once per run (:meth:`load`) and hand it to whoever maps values
    between the two models.

    Attributes
    ----------
    weights : dict of ModelType to pd.DataFrame
        ``{source model: weights}`` for both models of the pair.  Each frame has columns
        ``[REGION_COLUMN[source], REGION_COLUMN[other], 'weight']``, normalised over the source
        model's regions.
    names : dict of ModelType to str
        ``{source model: origin}`` (usually the file name), for messages.  Optional.
    """

    weights: dict[ModelType, pd.DataFrame]
    names: dict[ModelType, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Check each direction's weights and that the two directions agree.

        Raises
        ------
        ValueError
            If the weights do not name exactly two valid models, a direction's weights are not
            finite, non-negative and normalised, or the two directions differ in regions or links.
        """
        if len(self.weights) != 2:
            raise ValueError(
                f'A region crosswalk needs weights for exactly two models; got '
                f'{sorted(m.value for m in self.weights)}'
            )
        first, second = self.models
        _check_endpoints(first, second)
        for source, destination in ((first, second), (second, first)):
            _check_weights(self.weights[source], source, destination, self._name(source))
        _check_reciprocal(
            self.weights[first],
            self.weights[second],
            first,
            second,
            (self._name(first), self._name(second)),
        )

    @classmethod
    def load(cls, source: ModelType, destination: ModelType) -> RegionCrosswalk:
        """Read and check the registered crosswalk files for a pair.

        Parameters
        ----------
        source, destination : ModelType
            The two models; order does not matter.

        Returns
        -------
        RegionCrosswalk

        Raises
        ------
        ValueError
            If an endpoint is invalid, a required column is absent, or the weights fail the checks
            in :meth:`__post_init__`.
        KeyError
            If no crosswalk is registered for the pair.
        """
        files = _registered_files(source, destination)
        weights = {
            model: _read_weights(path, model, destination if model is source else source)
            for model, path in files.items()
        }
        crosswalk = cls(weights=weights, names={model: path.name for model, path in files.items()})
        logger.debug(
            'Loaded %s <-> %s region crosswalk from %s',
            source.value,
            destination.value,
            [path.name for path in files.values()],
        )
        return crosswalk

    @property
    def models(self) -> tuple[ModelType, ModelType]:
        """The two models of the pair, ordered by enum value."""
        first, second = sorted(self.weights, key=lambda m: m.value)
        return first, second

    def other(self, model: ModelType) -> ModelType:
        """Return the pair's model that is not ``model``.

        Raises
        ------
        ValueError
            If ``model`` is not one of the pair.
        """
        first, second = self.models
        if model is first:
            return second
        if model is second:
            return first
        raise ValueError(
            f'{model.value} is not part of the {first.value} <-> {second.value} crosswalk'
        )

    def weights_from(self, source: ModelType) -> pd.DataFrame:
        """Return the weights normalised over ``source`` regions (a copy).

        Raises
        ------
        ValueError
            If ``source`` is not one of the pair.
        """
        self.other(source)
        return self.weights[source].copy()

    def regions(self, model: ModelType) -> frozenset[str]:
        """Return the regions of ``model`` carrying positive weight in the crosswalk.

        Raises
        ------
        ValueError
            If ``model`` is not one of the pair.
        """
        destination = self.other(model)
        return frozenset(s for s, _ in _positive_links(self.weights[model], model, destination))

    def apply(
        self,
        values: pd.Series,
        source: ModelType,
        kind: QuantityKind,
        model_regions: Collection[str],
    ) -> pd.Series:
        """Map a ``(region, year)``-indexed series from ``source`` regions to the other model's.

        Before mapping, the series is checked against ``model_regions``, the regions the sending
        model carries (see :meth:`_check_supplied`).

        Parameters
        ----------
        values : pd.Series
            Indexed by ``VALUE_INDEX`` (``region``, ``year``) in the source model's region ids,
            one value per region the model carries for each year.
        source : ModelType
            Model the values belong to.
        kind : QuantityKind
            ``EXTENSIVE`` allocates with the ``source``-normalised weights, so totals are
            conserved:  ``out[d, y] = sum_s w[s, d] * v[s, y]``.  ``INTENSIVE`` averages with the
            destination-normalised weights:  ``out[d, y] = sum_s w[d, s] * v[s, y]``.
        model_regions : Collection of str
            The regions the sending model carries.

        Returns
        -------
        pd.Series
            Indexed by ``VALUE_INDEX`` in the destination model's region ids, sorted, carrying the
            input's name:  every destination region linked to a supplied region, for each year.

        Raises
        ------
        ValueError
            If ``source`` is not one of the pair, the index level names are not ``VALUE_INDEX``,
            or the series fails :meth:`_check_supplied`.
        """
        destination = self.other(source)
        if list(values.index.names) != VALUE_INDEX:
            raise ValueError(
                f'values must be indexed by {VALUE_INDEX}; got {list(values.index.names)}'
            )
        src_col, dst_col = REGION_COLUMN[source], REGION_COLUMN[destination]
        name: str = str(values.name) if values.name is not None else 'value'
        frame = values.rename(name).reset_index()
        frame['region'] = frame['region'].astype(str)
        self._check_supplied(frame, source, {str(r) for r in model_regions})

        weights = (
            self.weights[source] if kind is QuantityKind.EXTENSIVE else self.weights[destination]
        )
        weights = weights[weights[WEIGHT_COLUMN] > 0.0]
        merged = weights.merge(frame, left_on=src_col, right_on='region', how='inner')
        merged['_weighted'] = merged[WEIGHT_COLUMN] * merged[name]
        out = merged.groupby([dst_col, 'year'])['_weighted'].sum()
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

    def _check_supplied(
        self, frame: pd.DataFrame, source: ModelType, model_regions: set[str]
    ) -> None:
        """Check supplied values against the sending model's regions and the crosswalk.

        Parameters
        ----------
        frame : pd.DataFrame
            The values, with ``region`` (str) and ``year`` columns.
        source : ModelType
            Model the values belong to.
        model_regions : set of str
            The regions the sending model carries.

        Raises
        ------
        ValueError
            If the model carries a region the crosswalk does not; the supplied regions are not
            exactly ``model_regions``; the values are not one per (region, year) over every
            supplied region and year; or the supplied regions are not closed under the crosswalk,
            i.e. a destination region they reach also draws on a source region not supplied.
        """
        destination = self.other(source)
        unknown = sorted(model_regions - self.regions(source))
        if unknown:
            raise ValueError(
                f'The {source.value} model carries region(s) {unknown} that the {source.value} '
                f'<-> {destination.value} crosswalk does not map'
            )
        supplied = set(frame['region'])
        extra, missing = sorted(supplied - model_regions), sorted(model_regions - supplied)
        if extra or missing:
            raise ValueError(
                f'{source.value} values sent to {destination.value} must cover exactly the '
                f'regions the model carries:  not carried {extra}; missing {missing}'
            )
        years = set(frame['year'])
        if frame.duplicated(['region', 'year']).any() or len(frame) != len(supplied) * len(years):
            raise ValueError(
                f'{source.value} values sent to {destination.value} must hold one value per '
                f'(region, year) over {len(supplied)} region(s) and {len(years)} year(s); '
                f'got {len(frame)} rows'
            )
        links = _positive_links(self.weights[source], source, destination)
        reached = {d for s, d in links if s in supplied}
        drawn_on = {s for s, d in links if d in reached}
        outside = sorted(drawn_on - supplied)
        if outside:
            partial = sorted({d for s, d in links if s in outside and d in reached})
            raise ValueError(
                f'{source.value} regions {sorted(supplied)} are not closed under the '
                f'{source.value} <-> {destination.value} crosswalk:  {destination.value} '
                f'region(s) {partial} also draw on {source.value} region(s) {outside}, which '
                f'this run does not carry'
            )

    def _name(self, source: ModelType) -> str:
        """The origin of ``source``'s weights, for messages."""
        return self.names.get(source, f'{source.value} -> {self.other(source).value} weights')


@dataclass(frozen=True)
class CrosswalkSet:
    """The region crosswalks available to a run, keyed by unordered model pair.

    Frozen and picklable, so it travels to pool workers with the rest of a model's work.

    Attributes
    ----------
    crosswalks : dict of frozenset of ModelType to RegionCrosswalk
    """

    crosswalks: dict[frozenset[ModelType], RegionCrosswalk] = field(default_factory=dict)

    @classmethod
    def for_models(cls, models: Collection[ModelType]) -> CrosswalkSet:
        """Load every registered crosswalk whose two models are both in ``models``.

        Parameters
        ----------
        models : Collection of ModelType
            The models of the run.

        Returns
        -------
        CrosswalkSet
            Empty when no registered pair is covered.

        Raises
        ------
        ValueError, KeyError
            From :meth:`RegionCrosswalk.load`.
        """
        selected = set(models)
        crosswalks: dict[frozenset[ModelType], RegionCrosswalk] = {}
        for pair in REGION_CROSSWALKS:
            if pair <= selected:
                first, second = sorted(pair, key=lambda m: m.value)
                crosswalks[pair] = RegionCrosswalk.load(first, second)
        logger.info(
            'Region crosswalks loaded for %s',
            [' <-> '.join(sorted(m.value for m in pair)) for pair in crosswalks] or 'no pairs',
        )
        return cls(crosswalks=crosswalks)

    def get(self, source: ModelType, destination: ModelType) -> RegionCrosswalk:
        """Return the crosswalk between two models; order does not matter.

        Raises
        ------
        ValueError
            If an endpoint is invalid.
        KeyError
            If this set holds no crosswalk for the pair.
        """
        _check_endpoints(source, destination)
        try:
            return self.crosswalks[frozenset({source, destination})]
        except KeyError:
            held = [' <-> '.join(sorted(m.value for m in pair)) for pair in self.crosswalks]
            raise KeyError(
                f'No region crosswalk held for {source.value} -> {destination.value}; '
                f'held pairs: {held}'
            ) from None


def load_region_weights(source: ModelType, destination: ModelType) -> pd.DataFrame:
    """Load the registered weights from ``source`` regions to ``destination`` regions.

    Both directions of the pair are read and checked (see :class:`RegionCrosswalk`); only the
    requested direction is returned.

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
    ValueError, KeyError
        From :meth:`RegionCrosswalk.load`.
    """
    return RegionCrosswalk.load(source, destination).weights_from(source)
