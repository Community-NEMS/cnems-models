"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Run the Jacobi and Gauss-Seidel iterators on one config and compare them.

Both run at the same stopping settings, so the comparison is like for like:  the final objective
values should agree within the tolerance, and the time taken to reach them is the difference.
Writes ``comparison.md`` and ``comparison.json`` to ``output/<scenario>_compare_<n>``.

Usage, from the repository root inside ``pixi shell``::

    python -m analysis_tools.compare_iterators run_configs/gs_compare.toml
    python -m analysis_tools.compare_iterators run_configs/gs_compare.toml --epsilon 1e-4 --limit 20
    python -m analysis_tools.compare_iterators run_configs/gs_compare.toml --only gs
"""

import argparse
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from src.common.common_config import parse_config_file
from src.common.integrated_model_sequencer import IterationResult
from src.common.iterative_sequencer import IterativeSequencer
from src.common.models_modes import ModelType
from src.integrator.gauss_seidel.gs_iterator import GaussSeidelIterator, exchange_summary
from src.integrator.jacobi.jacobi_iterator import JacobiIterator

logger = logging.getLogger(__name__)

# the settings that decide when a run stops; both schemes get the same values
STOPPING_KEYS = ('iteration_limit', 'epsilon', 'convergence_iterations')

SCHEMES: dict[str, type[IterativeSequencer[Any]]] = {
    'jacobi': JacobiIterator,
    'gs': GaussSeidelIterator,
}


def shared_stopping(overrides: dict[str, Any]) -> dict[str, Any]:
    """Return the stopping settings both schemes run with.

    They start from the Jacobi iterator's settings file, so Gauss-Seidel stops by exactly the
    values Jacobi uses, and the command-line overrides apply to both.

    Parameters
    ----------
    overrides : dict
        Any of ``iteration_limit``, ``epsilon`` and ``convergence_iterations``.

    Returns
    -------
    dict
        The three stopping settings.
    """
    jacobi = JacobiIterator().config
    return {**{key: getattr(jacobi, key) for key in STOPPING_KEYS}, **overrides}


def run_scheme(scheme: str, config_path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    """Run one iterator on a config and summarise the run.

    Parameters
    ----------
    scheme : str
        ``'jacobi'`` or ``'gs'``.
    config_path : Path
        The run config; its ``mode`` is ignored, the scheme decides.
    settings : dict
        Stopping settings both schemes share, from :func:`shared_stopping`.

    Returns
    -------
    dict
        Status, iteration count, wall seconds, output folder, and per iteration the objectives,
        statuses and step timings of each model, plus the final burn and price summaries.
    """
    common_config, remainder = parse_config_file(config_path)
    common_config = common_config.model_copy(
        update={'scenario_name': f'{common_config.scenario_name}_{scheme}'}
    )
    iterator = SCHEMES[scheme]()
    # re-validated, so a bad setting fails here rather than mid-run
    iterator._config = type(iterator.config)(**{**iterator.config.model_dump(), **settings})
    start = perf_counter()
    status, results = iterator.run(common_config, remainder)
    wall = perf_counter() - start
    return {
        'scheme': scheme,
        'status': status.name,
        'iterations': len(results),
        'wall_seconds': wall,
        'output_folder': str(common_config.output_folder),
        'settings': iterator.config.model_dump(mode='json'),
        'per_iteration': {
            str(i): [
                {
                    'model': r.model_type.value,
                    'status': r.status.name,
                    'objective': r.objective_value,
                    'timings': r.timings,
                }
                for r in iteration_results
            ]
            for i, iteration_results in results.items()
        },
        # the values behind the monitor's entry counts:  burn and mean gas price by year
        'exchange': {
            str(i): _keyed_by_str(
                exchange_summary(pkg for r in iteration_results for pkg in r.update_packages)
            )
            for i, iteration_results in results.items()
        },
        'final': _final_summary(results),
    }


def _keyed_by_str(summary: dict[str, dict[int, float]]) -> dict[str, dict[str, float]]:
    """Return an :func:`exchange_summary` with string years, as JSON keys must be."""
    return {key: {str(y): v for y, v in by_year.items()} for key, by_year in summary.items()}


def _final_summary(results: dict[int, list[IterationResult]]) -> dict[str, Any]:
    """Final objectives, and the burn and gas price each model last sent, by year.

    Parameters
    ----------
    results : dict of int to list of IterationResult
        The run's results.

    Returns
    -------
    dict
        ``objectives`` by model, ``burn_bcf_by_year`` (total over gas regions) and
        ``mean_price_by_year`` (simple mean over electricity regions).
    """
    last = results[max(results)]
    exchanged = _keyed_by_str(exchange_summary(pkg for r in last for pkg in r.update_packages))
    summary: dict[str, Any] = {'objectives': {r.model_type.value: r.objective_value for r in last}}
    if 'burn_bcf' in exchanged:
        summary['burn_bcf_by_year'] = exchanged['burn_bcf']
    if 'mean_price' in exchanged:
        summary['mean_price_by_year'] = exchanged['mean_price']
    return summary


def _iteration_table(runs: list[dict[str, Any]]) -> list[str]:
    """Rows of objectives, burn and mean gas price per iteration, one row per scheme.

    Parameters
    ----------
    runs : list of dict
        Summaries from :func:`run_scheme`.

    Returns
    -------
    list of str
        Markdown table lines.  Values by year are joined with ``/`` in year order.
    """
    header = (
        '| Iteration | Scheme | Electricity objective | Gas objective | Burn, Bcf '
        '| Mean gas price, $/MMBtu |'
    )
    rows = [header, '|---|---|---|---|---|---|']
    longest = max(r['iterations'] for r in runs)
    for i in range(1, longest + 1):
        for run in runs:
            records = run['per_iteration'].get(str(i))
            if records is None:
                continue
            objective = {rec['model']: rec['objective'] for rec in records}
            exchanged = run['exchange'][str(i)]
            rows.append(
                f'| {i} | {run["scheme"]} '
                f'| {_fmt_objective(objective.get(ModelType.ELECTRICITY.value))} '
                f'| {_fmt_objective(objective.get(ModelType.NATURAL_GAS.value))} '
                f'| {_join_by_year(exchanged.get("burn_bcf", {}), ",.1f")} '
                f'| {_join_by_year(exchanged.get("mean_price", {}), ".3f")} |'
            )
    return rows


def _fmt_objective(value: float | None) -> str:
    """An objective in scientific notation, or ``n/a`` for a failed solve."""
    return 'n/a' if value is None else f'{value:.6e}'


def _join_by_year(values: dict[str, float], fmt: str) -> str:
    """Values in year order joined with `` / ``, or ``n/a`` when there are none."""
    return ' / '.join(format(values[y], fmt) for y in sorted(values)) or 'n/a'


def _step_seconds(run: dict[str, Any]) -> dict[str, float]:
    """Total seconds per step over the run, summed across models and iterations."""
    totals: dict[str, float] = {}
    for iteration_results in run['per_iteration'].values():
        for record in iteration_results:
            for step, seconds in record['timings'].items():
                totals[step] = totals.get(step, 0.0) + seconds
    return totals


def write_report(runs: list[dict[str, Any]], out_dir: Path) -> str:
    """Write ``comparison.md`` and ``comparison.json`` and return the markdown.

    Parameters
    ----------
    runs : list of dict
        Summaries from :func:`run_scheme`.
    out_dir : Path
        Folder to write to.

    Returns
    -------
    str
        The markdown table.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'comparison.json').write_text(json.dumps(runs, indent=2))
    lines = ['| | ' + ' | '.join(r['scheme'] for r in runs) + ' |']
    lines.append('|---|' + '---|' * len(runs))
    lines.append('| Status | ' + ' | '.join(r['status'] for r in runs) + ' |')
    lines.append('| Iterations | ' + ' | '.join(str(r['iterations']) for r in runs) + ' |')
    lines.append('| Wall time, s | ' + ' | '.join(f'{r["wall_seconds"]:.0f}' for r in runs) + ' |')
    steps = sorted({s for r in runs for s in _step_seconds(r)})
    for step in steps:
        lines.append(
            f'| Sum of {step} time over models, s | '
            + ' | '.join(f'{_step_seconds(r).get(step, 0.0):.1f}' for r in runs)
            + ' |'
        )
    for model in (ModelType.ELECTRICITY.value, ModelType.NATURAL_GAS.value):
        values = [r['final']['objectives'].get(model) for r in runs]
        lines.append(
            f'| Final {model} objective | '
            + ' | '.join('n/a' if v is None else f'{v:.6e}' for v in values)
            + ' |'
        )
    for key, label in (
        ('burn_bcf_by_year', 'Final burn, Bcf'),
        ('mean_price_by_year', 'Final mean gas price, $/MMBtu'),
    ):
        years = sorted({y for r in runs for y in r['final'].get(key, {})})
        for year in years:
            lines.append(
                f'| {label}, {year} | '
                + ' | '.join(f'{r["final"].get(key, {}).get(year, float("nan")):.4g}' for r in runs)
                + ' |'
            )
    if len(runs) == 2:
        for model in (ModelType.ELECTRICITY.value, ModelType.NATURAL_GAS.value):
            a, b = (r['final']['objectives'].get(model) for r in runs)
            if a is not None and b is not None:
                kind, diff = ('Relative', abs(a - b) / abs(a)) if a else ('Absolute', abs(a - b))
                lines.append(f'| {kind} difference, {model} objective | {diff:.2e} | |')
    lines.append('')
    lines.append(
        'Jacobi solves its models in parallel worker processes and Gauss-Seidel solves them in '
        "turn, so wall time is the fair measure; the step sums add each model's time.  "
        "Gauss-Seidel's wall time includes writing its result files at the end, which Jacobi does "
        'not do.  Stopping settings:  '
        + ', '.join(f'{key} {runs[0]["settings"][key]}' for key in STOPPING_KEYS)
        + '.'
    )
    lines.extend(['', '## By iteration', ''] + _iteration_table(runs))
    text = '\n'.join(lines) + '\n'
    (out_dir / 'comparison.md').write_text(text)
    return text


def main() -> None:
    """Parse the command line, run the schemes, and write the comparison."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[1] if __doc__ else None)
    parser.add_argument('config_path', type=Path)
    parser.add_argument('--epsilon', type=float, help='convergence tolerance for both schemes')
    parser.add_argument('--limit', type=int, help='iteration limit for both schemes')
    parser.add_argument('--stable', type=int, help='convergence_iterations for both schemes')
    parser.add_argument('--only', choices=sorted(SCHEMES), help='run one scheme only')
    args = parser.parse_args()
    overrides: dict[str, Any] = {}
    if args.epsilon is not None:
        overrides['epsilon'] = args.epsilon
    if args.limit is not None:
        overrides['iteration_limit'] = args.limit
    if args.stable is not None:
        overrides['convergence_iterations'] = args.stable
    settings = shared_stopping(overrides)
    schemes = [args.only] if args.only else ['jacobi', 'gs']
    runs = [run_scheme(scheme, args.config_path, settings) for scheme in schemes]
    # beside the run folders, named after the last one, so repeated comparisons never collide
    last = Path(runs[-1]['output_folder'])
    out_dir = last.with_name(f'{last.name}_compare')
    print(write_report(runs, out_dir))
    print(f'Written to {out_dir}')


if __name__ == '__main__':
    main()
