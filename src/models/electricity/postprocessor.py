"""Postprocessor for the electricity model.

Legacy section (deprecated): the original postprocessor relied on ``instance.cols_dict``, a
column-naming lookup only ever populated by the deprecated ``src.common.model.Model`` base class.
``PowerModel`` no longer subclasses ``Model``, so these functions are kept only for the legacy
hydrogen-integrated callers (``src.integrator.unified``, ``src.integrator.gaussseidel``) and should
not be used for new work.

New section: ``extract_all_variables``/``export_variables_to_csv`` derive one DataFrame per
``pyo.Var`` directly from the model's own pyomo index-set structure (via ``Set.subsets()``), with no
dependency on any precomputed column-naming dict.
"""

###################################################################################################
# Setup

from logging import getLogger
from pathlib import Path

import pandas as pd
import pyomo.environ as pyo
from pyomo.core.base.indexed_component import IndexedComponent

from definitions import PROJECT_ROOT

# Establish logger
logger = getLogger(__name__)

storage_index_names = ['region', 'tech', 'step', 'year', 'hour']

# mapping the names of the indices to the core variables to use as labels in extraction
core_variable_indices = {
    'generation_total': ['region', 'tech', 'step', 'year', 'hour'],
    'capacity_builds': ['region', 'tech', 'step', 'year'],
    'capacity_retirements': ['region', 'tech', 'step', 'year'],
    'capacity_total': ['region', 'tech', 'step', 'year'],
    'storage_inflow': storage_index_names,
    'storage_outflow': storage_index_names,
    'storage_level': storage_index_names,
    'trade_interregional': ['region_destination', 'region_source', 'year', 'hour'],
    'trade_international': ['region_domestic', 'region_international', 'step', 'year', 'hour'],
    'unmet_load': ['region', 'year', 'hour'],
}


def get_known_column_names(var: pyo.Var | str) -> list[str]:
    """Get the known column names for a given variable name."""
    name = var.local_name if isinstance(var, pyo.Var) else var
    return core_variable_indices.get(name, [])


def _derive_column_names(component: IndexedComponent) -> list[str]:
    """Derive DataFrame column names for an indexed component's index dimensions.

    Works for any ``IndexedComponent`` (``pyo.Var``, ``pyo.Param``, ``pyo.Set``, ...). Uses
    ``Set.subsets()`` to recover the named component Sets that were crossed (e.g. via
    ``A * B * C``) to build the component's index. Components indexed by a flat/raw tuple list (a
    single enumerated Set, not a true ``SetProduct``) have no queryable per-dimension names at
    runtime, so those fall back to generic ``idx_0, idx_1, ...`` names sized to the actual tuple
    arity.

    Parameters
    ----------
    component : IndexedComponent
        Pyomo indexed component (indexed or scalar).

    Returns
    -------
    list[str]
        One column name per index dimension; empty list for a scalar (unindexed) component.
    """
    if not component.is_indexed():
        return []

    subsets = list(component.index_set().subsets(expand_all_set_operators=True))
    names: list[str]
    if len(subsets) > 1:
        names = [s.local_name for s in subsets]
    elif subsets[0].dimen == 1:
        names = [subsets[0].local_name]
    else:
        # Flat/raw-tuple-indexed component: no real per-dimension names available. Derive arity
        # from an actual index entry rather than trusting Set.dimen, which may be unset for some
        # Sets.
        sample = next(iter(component), None)
        arity = len(sample) if isinstance(sample, tuple) else 1
        names = [f'idx_{i}' for i in range(arity)]

    return _dedupe_names(names)


def _dedupe_names(names: list[str]) -> list[str]:
    """Suffix repeated names with their 1-based occurrence index so columns never collide.

    Parameters
    ----------
    names : list[str]
        Candidate column names, possibly containing duplicates (e.g. a trade variable crossed
        against the same region Set twice).

    Returns
    -------
    list[str]
        Names with duplicates suffixed ``_1``, ``_2``, ...; names occurring once are unchanged.
    """
    counts = {name: names.count(name) for name in names}
    seen: dict[str, int] = {}
    deduped = []
    for name in names:
        if counts[name] == 1:
            deduped.append(name)
            continue
        seen[name] = seen.get(name, 0) + 1
        deduped.append(f'{name}_{seen[name]}')
    return deduped


def variable_to_dataframe(var: pyo.Var) -> pd.DataFrame:
    """Convert a solved pyomo Var into a DataFrame, one row per index, one column per dimension.

    Assumes ``var`` belongs to a solved model. Column names for each index dimension are derived
    via :func:`_derive_column_names`; the value column (named after ``var.local_name``) is
    extracted with ``pyo.value(..., exception=False)``, so unsolved/uninitialized entries appear
    as ``None`` rather than raising.

    Parameters
    ----------
    var : pyo.Var
        Pyomo Var component (indexed or scalar).

    Returns
    -------
    pd.DataFrame
        Columns are the derived index-dimension names followed by ``var.local_name``. Empty
        (0-row) with the same columns if ``var`` has no elements.
    """
    columns = get_known_column_names(var)
    if not columns:
        logger.debug(
            'Variable name %s is not recognized in the set of core vars in postprocessor... '
            'inferring names',
            var.local_name,
        )
        columns = _derive_column_names(var)

    value_col = var.local_name
    rows = []
    for idx in var:
        idx_tuple = idx if isinstance(idx, tuple) else (idx,)
        row = dict(zip(columns, idx_tuple, strict=True)) if columns else {}
        row[value_col] = pyo.value(var[idx], exception=False)
        rows.append(row)

    df = pd.DataFrame(rows, columns=[*columns, value_col])
    if df.empty:
        logger.debug('Electricity Model: variable %s is empty.', var.local_name)
    return df


def extract_all_variables(
    model: pyo.ConcreteModel, core_only: bool = True
) -> dict[str, pd.DataFrame]:
    """Extract active pyomo Vars on ``model`` into a DataFrame, keyed by variable name.

    Iterates ``model.component_objects(pyo.Var, active=True)`` directly, so Vars that only exist
    conditionally (e.g. ``trade_interregional``, ``capacity_builds``, ramping/reserve variables,
    depending on ``ElecConfig`` switches) are picked up automatically when present and simply
    absent otherwise -- no hardcoded variable-name list is used.

    Parameters
    ----------
    model : pyo.ConcreteModel
        A solved electricity model (any ``pyo.ConcreteModel``; no particular base class assumed).
    core_only : bool, optional
        If True (default), only extract Vars whose name is a key in ``core_variable_indices``
        (the recognized, model-defined outputs); other active Vars (e.g. ``storage_avail_cap``)
        are skipped. If False, every active Var on the model is extracted.

    Returns
    -------
    dict[str, pd.DataFrame]
        Variable name (``local_name``) mapped to its extracted DataFrame.
    """
    result = {}
    for var in model.component_objects(pyo.Var, active=True):
        if core_only and var.local_name not in core_variable_indices:
            continue
        result[var.local_name] = variable_to_dataframe(var)
        logger.info('Extracted variable %s: %d rows', var.local_name, len(result[var.local_name]))
    return result


def export_variables_to_csv(
    model: pyo.ConcreteModel, output_dir: Path | str | None = None, core_only: bool = True
) -> dict[str, pd.DataFrame]:
    """Extract Vars on ``model`` and write each DataFrame to its own CSV file.

    Parameters
    ----------
    model : pyo.ConcreteModel
        A solved electricity model.
    output_dir : Path | str | None, optional
        Directory to write one ``{variable_name}.csv`` file per variable into (created if
        missing). Defaults to ``PROJECT_ROOT / 'outputs/temp/electricity/variables'``.
    core_only : bool, optional
        Passed through to :func:`extract_all_variables`. If True (default), only the recognized
        "core" Vars (keys of ``core_variable_indices``) are extracted and written; if False,
        every active Var on the model is written.

    Returns
    -------
    dict[str, pd.DataFrame]
        Same return value as :func:`extract_all_variables`, so callers get the in-memory frames
        without a second extraction pass.
    """
    out_dir = (
        Path(output_dir)
        if output_dir is not None
        else PROJECT_ROOT / 'outputs/temp/electricity/variables'
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    dfs = extract_all_variables(model, core_only=core_only)
    for name, df in dfs.items():
        csv_path = out_dir / f'{name}.csv'
        df.to_csv(csv_path, index=False)
        logger.info('Wrote %s', csv_path)
    return dfs
