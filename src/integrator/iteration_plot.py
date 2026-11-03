"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Scatter plot of each model's objective value by iteration for an integrated run.
"""

from collections.abc import Mapping, Sequence

from matplotlib import pyplot as plt
from matplotlib.axes import Axes

from src.common.integrated_model_sequencer import ALLOW_OUTBOUND_UPDATES, IterationResult
from src.common.models_modes import ModelType

# how far (as a fraction of the axes width) each extra right-hand y-axis sits past the last
_SPINE_OFFSET = 0.12


def plot_objectives(results: Mapping[int, Sequence[IterationResult]], show: bool = True) -> None:
    """Scatter each model's objective value against iteration, one y-axis per model.

    A result with no objective value, or with a status outside ``ALLOW_OUTBOUND_UPDATES``, is
    skipped.  Models with no plotted points (e.g. MAGIC, which has no objective) are left off.
    The objectives can differ by orders of magnitude, so each model gets its own y-axis; the
    legend names the models by label.

    Parameters
    ----------
    results : mapping of int to sequence of IterationResult
        Each iteration's results, keyed by iteration number.
    show : bool, default True
        Call ``plt.show()`` (blocking) once the figure is drawn.
    """
    points: dict[ModelType, tuple[str, list[int], list[float]]] = {}
    for iteration, iteration_results in sorted(results.items()):
        for result in iteration_results:
            if result.objective_value is None or result.status not in ALLOW_OUTBOUND_UPDATES:
                continue
            _label, xs, ys = points.setdefault(result.model_type, (result.label, [], []))
            xs.append(iteration)
            ys.append(result.objective_value)

    if not points:
        return

    _fig, base_ax = plt.subplots()
    base_ax.set_xlabel('iteration')
    handles = []
    for n, (label, xs, ys) in enumerate(points.values()):
        ax: Axes = base_ax if n == 0 else base_ax.twinx()
        if n > 1:
            ax.spines['right'].set_position(('axes', 1 + _SPINE_OFFSET * (n - 1)))
        color = f'C{n}'
        handles.append(ax.scatter(xs, ys, color=color, label=label))
        ax.set_ylabel(f'{label} objective', color=color)
    # below the plot, clear of every axis's points; loc='best' would only avoid base_ax's
    base_ax.legend(
        handles=handles,
        loc='upper center',
        bbox_to_anchor=(0.5, -0.12),
        ncol=len(handles),
        frameon=False,
    )
    plt.tight_layout()
    if show:
        plt.show()
