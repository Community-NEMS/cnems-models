"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

Run the Jacobi and Gauss-Seidel iterators on one config and compare them.

Both run at the same stopping settings, so the comparison is like for like:  the final objective
values should agree within the tolerance, and the time taken to reach them is the difference.
Each comparison gets one folder, ``output/<scenario>_comparison`` (``_1``, ``_2``, ... if taken),
holding the two runs' folders, ``jacobi/`` and ``gauss_seidel/``, beside ``comparison.md``,
``comparison.json`` and two figures, ``objectives.png`` and ``exchange.png``.

Usage, from the repository root inside ``pixi shell``::

    python -m analysis_tools.compare_iterators run_configs/gs_compare.toml
    python -m analysis_tools.compare_iterators run_configs/gs_compare.toml --epsilon 1e-4 --limit 20
    python -m analysis_tools.compare_iterators run_configs/gs_compare.toml --only gs
    python -m analysis_tools.compare_iterators --from-json output/<folder>/comparison.json
"""

import argparse
import json
import logging
import math
import re
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from integrator.convergence import relative_change
from src.common.common_config import parse_config_file
from src.common.integrated_model_sequencer import ALLOW_OUTBOUND_UPDATES, IterationResult
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
# each scheme's run folder inside the comparison's folder (scenario names need 4+ characters)
RUN_FOLDERS = {'jacobi': 'jacobi', 'gs': 'gauss_seidel'}

# one colour and marker per scheme, the same in every figure
SCHEME_STYLE: dict[str, dict[str, str]] = {
    'jacobi': {'label': 'Jacobi', 'color': 'tab:blue', 'marker': 'o'},
    'gs': {'label': 'Gauss-Seidel', 'color': 'tab:orange', 'marker': 's'},
}
MODEL_NAMES = {
    ModelType.ELECTRICITY.value: 'Electricity',
    ModelType.NATURAL_GAS.value: 'Natural gas',
}
# the exchanged quantities drawn per year:  key in exchange_summary, panel title, unit
EXCHANGED = (
    ('burn_bcf', 'Power-sector burn', 'Bcf/yr'),
    ('mean_price', 'Mean gas price', 'USD/MMBtu'),
)
# statuses whose objective the stopping rule uses, by name as a saved summary records them
USABLE_STATUSES = frozenset(status.name for status in ALLOW_OUTBOUND_UPDATES)


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


def claim_comparison_folder(config_path: Path) -> Path:
    """Claim a new ``<output_path>/<scenario>_comparison`` folder for one comparison.

    It is claimed as a run's folder is, by ``CommonConfig.make_scenario_dir``, so an existing
    folder is never reused and a taken name gets a ``_<n>`` suffix.

    Parameters
    ----------
    config_path : Path
        The run config, which names the output path and the scenario.

    Returns
    -------
    Path
        The new, empty folder.
    """
    common_config, _ = parse_config_file(config_path)
    common_config = common_config.model_copy(
        update={'scenario_name': f'{common_config.scenario_name}_comparison'}
    )
    return common_config.make_scenario_dir()


def run_scheme(
    scheme: str, config_path: Path, settings: dict[str, Any], comparison_folder: Path
) -> dict[str, Any]:
    """Run one iterator on a config and summarise the run.

    Parameters
    ----------
    scheme : str
        ``'jacobi'`` or ``'gs'``.
    config_path : Path
        The run config; its ``mode`` is ignored, the scheme decides.
    settings : dict
        Stopping settings both schemes share, from :func:`shared_stopping`.
    comparison_folder : Path
        From :func:`claim_comparison_folder`; the run writes to its ``RUN_FOLDERS`` subfolder.

    Returns
    -------
    dict
        Status, iteration count, wall seconds, output folder, and per iteration the objectives,
        statuses and step timings of each model, plus the final burn and price summaries.
    """
    common_config, remainder = parse_config_file(config_path)
    common_config = common_config.model_copy(
        update={'output_path': comparison_folder, 'scenario_name': RUN_FOLDERS[scheme]}
    )
    iterator = SCHEMES[scheme]()
    # re-validated, so a bad setting fails here rather than mid-run
    iterator._config = type(iterator.config)(**{**iterator.config.model_dump(), **settings})
    start = perf_counter()
    status, results = iterator.run(common_config, remainder)
    wall = perf_counter() - start
    return {
        'scheme': scheme,
        'config': str(config_path),
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
        '| Iteration | Scheme | Electricity objective | Gas objective | Burn, Bcf/yr '
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
            # summaries written before exchange data was recorded have none
            exchanged = run.get('exchange', {}).get(str(i), {})
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


def _objectives(run: dict[str, Any], model: str) -> tuple[list[int], list[float]]:
    """Each iteration's objective of one model in one run, NaN where it is not usable.

    Usable means what the stopping rule uses:  a status in ``ALLOW_OUTBOUND_UPDATES`` and an
    objective.  A NaN leaves a gap in the plotted line.
    """
    xs: list[int] = []
    ys: list[float] = []
    for i in sorted(run['per_iteration'], key=int):
        for record in run['per_iteration'][i]:
            if record['model'] != model:
                continue
            usable = record['status'] in USABLE_STATUSES and record['objective'] is not None
            xs.append(int(i))
            ys.append(record['objective'] if usable else math.nan)
    return xs, ys


def _changes(xs: list[int], ys: list[float]) -> tuple[list[int], list[float]]:
    """Each iteration's relative objective change, as the stopping rule measures it.

    As in ``ConvergenceTracker``, a usable objective is compared with the last usable one, so a
    failed iteration in between is skipped over.  The first usable iteration has no change.  An
    unusable iteration, and a change of exactly zero, which a log scale cannot draw, are NaN
    and leave a gap.
    """
    out_x: list[int] = []
    out_y: list[float] = []
    previous: float | None = None
    for x, y in zip(xs, ys, strict=True):
        if math.isnan(y):
            out_x.append(x)
            out_y.append(math.nan)
            continue
        if previous is not None:
            change = relative_change(previous, y)
            out_x.append(x)
            out_y.append(change if change > 0 else math.nan)
        previous = y
    return out_x, out_y


def _exchanged(run: dict[str, Any], key: str, year: str) -> tuple[list[int], list[float]]:
    """Each iteration's value of one exchanged quantity in one year, NaN where none was sent."""
    xs: list[int] = []
    ys: list[float] = []
    exchange = run.get('exchange', {})
    for i in sorted(run['per_iteration'], key=int):
        value = exchange.get(i, {}).get(key, {}).get(year)
        xs.append(int(i))
        ys.append(math.nan if value is None else value)
    return xs, ys


def _case_name(runs: list[dict[str, Any]]) -> str:
    """The compared config's name, from the summary or, for older summaries, the run folder."""
    if 'config' in runs[0]:
        return Path(runs[0]['config']).stem
    return re.sub(r'_(jacobi|gs)(_\d+)?$', '', Path(runs[0]['output_folder']).name)


def _save(fig: Figure, path: Path) -> Path:
    """Label every panel's iteration axis, add one legend for the figure, and save it.

    Parameters
    ----------
    fig : Figure
        The drawn figure.
    path : Path
        Where to write the PNG.

    Returns
    -------
    Path
        ``path``.
    """
    legend: dict[str, Any] = {}
    for ax in fig.axes:
        ax.set_xlabel('Iteration')
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.grid(True, alpha=0.3)
        for handle, label in zip(*ax.get_legend_handles_labels(), strict=True):
            legend.setdefault(label, handle)
    fig.legend(
        list(legend.values()),
        list(legend),
        loc='outside lower center',
        ncols=len(legend),
        frameon=False,
    )
    fig.savefig(path, dpi=150)
    return path


def plot_comparison(runs: list[dict[str, Any]], out_dir: Path) -> list[Path]:
    """Draw the objectives and the exchanged values against iteration, the schemes overlaid.

    ``objectives.png`` has each model's objective, and below it the relative change from the
    previous iteration on a log scale with the stopping tolerance marked.  ``exchange.png`` has
    the power-sector burn and the mean gas price exchanged in each iteration, one panel per year.
    Matplotlib's object interface is used without ``pyplot``, so no window or GUI backend is
    involved.

    Parameters
    ----------
    runs : list of dict
        Summaries from :func:`run_scheme`, or loaded from a ``comparison.json``.
    out_dir : Path
        Folder to write to.

    Returns
    -------
    list of Path
        The figures written.  ``exchange.png`` is left out for summaries without exchange data.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = runs[0]['settings']
    case = (
        f'{_case_name(runs)}, epsilon {settings["epsilon"]:g}, '
        f'{settings["convergence_iterations"]} stable iterations'
    )

    fig = Figure(figsize=(11, 7.5), layout='constrained')
    axes = fig.subplots(2, 2)
    fig.suptitle(f'Objectives by iteration ({case})')
    for col, (model, name) in enumerate(MODEL_NAMES.items()):
        value_ax, change_ax = axes[0][col], axes[1][col]
        for run in runs:
            style = SCHEME_STYLE[run['scheme']]
            xs, ys = _objectives(run, model)
            value_ax.plot(xs, ys, **style)
            change_ax.plot(*_changes(xs, ys), **style)
        value_ax.set_title(f'{name} objective')
        value_ax.set_ylabel('Objective')
        change_ax.axhline(
            settings['epsilon'], color='grey', linestyle='--', linewidth=1, label='epsilon'
        )
        change_ax.set_yscale('log')
        change_ax.set_title(f'{name}, relative change from the previous iteration')
        change_ax.set_ylabel('Relative change')
    figures = [_save(fig, out_dir / 'objectives.png')]

    years = sorted(
        {
            year
            for run in runs
            for sent in run.get('exchange', {}).values()
            for key in sent
            for year in sent[key]
        }
    )
    if years:
        fig = Figure(figsize=(5.5 * len(years), 7.5), layout='constrained')
        axes = fig.subplots(len(EXCHANGED), len(years), squeeze=False)
        fig.suptitle(f'Exchanged values by iteration ({case})')
        for row, (key, name, unit) in enumerate(EXCHANGED):
            for col, year in enumerate(years):
                ax = axes[row][col]
                for run in runs:
                    ax.plot(*_exchanged(run, key, year), **SCHEME_STYLE[run['scheme']])
                ax.set_title(f'{name}, {year}')
                ax.set_ylabel(unit)
        figures.append(_save(fig, out_dir / 'exchange.png'))
    return figures


def write_report(runs: list[dict[str, Any]], out_dir: Path, figures: Sequence[Path] = ()) -> str:
    """Write ``comparison.md`` and ``comparison.json`` and return the markdown.

    Parameters
    ----------
    runs : list of dict
        Summaries from :func:`run_scheme`.
    out_dir : Path
        Folder to write to.
    figures : sequence of Path
        Figures in ``out_dir`` to link at the end, from :func:`plot_comparison`.

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
        ('burn_bcf_by_year', 'Final burn, Bcf/yr'),
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
    if figures:
        lines.extend(['', '## Figures', ''])
        lines.extend(f'![{figure.stem}]({figure.name})' for figure in figures)
        caption = (
            'A change of exactly zero cannot be drawn on the log scale of the relative-change '
            'panels, so it shows as a gap, as does an iteration whose solve was not usable.  '
            'Mean gas prices are averaged over electricity regions without weights.'
        )
        lines.extend(['', caption])
    text = '\n'.join(lines) + '\n'
    (out_dir / 'comparison.md').write_text(text)
    return text


def main() -> None:
    """Parse the command line, run the schemes (or load a saved comparison), and write it out."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[1] if __doc__ else None)
    parser.add_argument('config_path', type=Path, nargs='?', help='the run config to compare on')
    parser.add_argument('--epsilon', type=float, help='convergence tolerance for both schemes')
    parser.add_argument('--limit', type=int, help='iteration limit for both schemes')
    parser.add_argument('--stable', type=int, help='convergence_iterations for both schemes')
    parser.add_argument('--only', choices=sorted(SCHEMES), help='run one scheme only')
    parser.add_argument(
        '--from-json',
        type=Path,
        help='redraw the table and figures from a saved comparison.json instead of running; '
        'takes no other options',
    )
    args = parser.parse_args()
    if args.from_json is not None:
        run_options = (args.config_path, args.epsilon, args.limit, args.stable, args.only)
        if any(option is not None for option in run_options):
            parser.error('--from-json redraws a saved comparison and takes no other options')
        runs = json.loads(args.from_json.read_text())
        out_dir = args.from_json.parent
    else:
        if args.config_path is None:
            parser.error('give a config path, or --from-json with a saved comparison')
        overrides: dict[str, Any] = {}
        if args.epsilon is not None:
            overrides['epsilon'] = args.epsilon
        if args.limit is not None:
            overrides['iteration_limit'] = args.limit
        if args.stable is not None:
            overrides['convergence_iterations'] = args.stable
        settings = shared_stopping(overrides)
        schemes = [args.only] if args.only else ['jacobi', 'gs']
        # one folder per comparison, holding both runs' folders and the report
        out_dir = claim_comparison_folder(args.config_path)
        runs = [run_scheme(scheme, args.config_path, settings, out_dir) for scheme in schemes]
    figures = plot_comparison(runs, out_dir)
    print(write_report(runs, out_dir, figures))
    print(f'Written to {out_dir}')


if __name__ == '__main__':
    main()
