"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Fable 5.1 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/18/26

A text monitor of the integrated control loop.

Laid out like a sequence diagram:  one lifeline per model in the circuit under a boxed name,
plus a leading column for the completed iteration number.  After each iteration the monitor
renders the change in every model's objective and then one horizontal ray per package
delivery, leaving the sender's lifeline at a ``+`` and arriving at the receiver's with an
arrowhead, annotated with the package label and its entry count in square brackets.  Blocks
cascade down the page as iterations complete::

               +-------------+            +-------------+            +-------+
      it       | Electricity |            | Natural Gas |            | Magic |
               +-------------+            +-------------+            +-------+
                      |                          |                          |
       1    (start 3,669,432,143.12)  (start -102,297,705,807.74)         (N/C)
                      |                          |                          |
                      +----- NG Demand [4] ----->|                          |
                      |                          |                          |
                      |<---- NG Prices [50] -----+                          |

Output is a ``rich`` ``Text`` coloured by the scheme in the adjacent ``monitor_style.toml``;
its ``.plain`` attribute is the uncoloured string for log files.
"""

import logging
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from rich.errors import StyleSyntaxError
from rich.style import Style
from rich.text import Text

from src.common.integrated_model_sequencer import IterationResult
from src.common.models_modes import ModelType
from src.common.update_package import UpdatePackage

logger = logging.getLogger(__name__)

# the value shown for a model that reports no objective ("not computed")
NO_OBJECTIVE = '(N/C)'
DEFAULT_STYLE_PATH = Path(__file__).with_name('monitor_style.toml')

# built-in styles, used for any section or key the TOML omits
_DEFAULT_STYLES: dict[str, dict[str, str]] = {
    'model': {'default': 'bold white'},
    'package': {'default': 'bold yellow'},
    'connector': {'lifeline': 'grey50', 'ray': 'grey70', 'box': 'grey70'},
    'objective': {
        'start': 'bold white',
        'increase': 'red',
        'decrease': 'green',
        'unchanged': 'grey62',
        'none': 'grey50',
    },
    'iteration': {'number': 'bold white'},
}


class DeltaMode(Enum):
    """How the change in a model's objective is shown."""

    ABSOLUTE = 'absolute'  # (+1,234.56)
    PERCENT = 'percent'  # (+0.0034%), relative to the magnitude of the previous value


@dataclass(frozen=True)
class MonitorStyle:
    """The colour scheme:  ``sections[section][key]`` is a rich style string."""

    sections: dict[str, dict[str, str]]

    def get(self, section: str, key: str) -> str:
        """Style for ``key`` in ``section``, falling back to the section's ``default`` entry.

        Raises
        ------
        KeyError
            If neither the key nor a ``default`` is defined for the section.
        """
        entries = self.sections.get(section, {})
        if key in entries:
            return entries[key]
        if 'default' in entries:
            return entries['default']
        raise KeyError(f'No monitor style for [{section}] {key!r} and no default for the section')


def load_style(path: Path | None = None) -> MonitorStyle:
    """Load the colour scheme, layering the TOML at ``path`` over the built-in defaults.

    Parameters
    ----------
    path : Path, optional
        TOML file of ``[section]`` tables of ``key = "rich style"``; ``DEFAULT_STYLE_PATH``
        when omitted.  A missing file leaves the defaults in force with a warning.

    Returns
    -------
    MonitorStyle

    Raises
    ------
    ValueError
        If any entry is not a valid rich style string, naming the section and key.
    """
    path = DEFAULT_STYLE_PATH if path is None else path
    sections: dict[str, dict[str, str]] = {k: dict(v) for k, v in _DEFAULT_STYLES.items()}
    if path.is_file():
        with path.open('rb') as fh:
            loaded = tomllib.load(fh)
        for section, entries in loaded.items():
            sections.setdefault(section, {}).update({k: str(v) for k, v in entries.items()})
        logger.debug('Loaded monitor styles from %s', path)
    else:
        logger.warning('Monitor style file %s not found; using built-in colours', path)
    for section, entries in sections.items():
        for key, style in entries.items():
            try:
                Style.parse(style)
            except StyleSyntaxError as exc:
                raise ValueError(
                    f'Invalid rich style {style!r} for [{section}] {key!r} in {path}: {exc}'
                ) from exc
    return MonitorStyle(sections)


class _Line:
    """A fixed-width line of characters with the styled spans written onto it."""

    def __init__(self, width: int):
        self.chars = [' '] * width
        self.spans: list[tuple[int, int, str]] = []

    def put(self, start: int, text: str, style: str) -> None:
        """Write ``text`` at ``start`` in ``style``, clipped to the line."""
        lo, hi = max(start, 0), min(start + len(text), len(self.chars))
        for pos in range(lo, hi):
            self.chars[pos] = text[pos - start]
        if hi > lo:
            self.spans.append((lo, hi, style))

    def render(self) -> Text:
        """The line as rich ``Text``, trailing blanks dropped, later spans on top."""
        text = Text(''.join(self.chars).rstrip())
        for lo, hi, style in self.spans:
            if lo < len(text):
                text.stylize(style, lo, min(hi, len(text)))
        return text


class IterationMonitor:
    """Render the model-to-model traffic of a control loop, one block per iteration.

    Parameters
    ----------
    circuit : Sequence[ModelType]
        The models in the run, in column order.
    delta_mode : DeltaMode, default ABSOLUTE
        Show objective changes as absolute differences or as a percentage of the previous
        value's magnitude.
    style : MonitorStyle, optional
        The colour scheme; loaded from ``DEFAULT_STYLE_PATH`` when omitted.
    column_width : int, default 26
        Width of each model column.  With the 6-wide iteration column, three models fit in 84
        characters; the display grows by ``column_width`` per extra model.
    """

    ITER_WIDTH = 6

    def __init__(
        self,
        circuit: Sequence[ModelType],
        delta_mode: DeltaMode = DeltaMode.ABSOLUTE,
        style: MonitorStyle | None = None,
        column_width: int = 26,
    ):
        if len(circuit) == 0:
            raise ValueError('The circuit needs at least one model')
        self.circuit: tuple[ModelType, ...] = tuple(circuit)
        self.delta_mode = delta_mode
        self.style = style if style is not None else load_style()
        self.column_width = column_width
        self._labels: dict[ModelType, str] = {m: m.value for m in self.circuit}
        self._previous: dict[ModelType, float | None] = {}
        self._header_done = False

    # ------------------------------------------------------------------ geometry
    @property
    def width(self) -> int:
        """Total character width of a rendered line."""
        return self.ITER_WIDTH + self.column_width * len(self.circuit)

    def _center(self, column: int) -> int:
        """Character position of the lifeline of model column ``column``."""
        return self.ITER_WIDTH + column * self.column_width + self.column_width // 2

    def _blank(self) -> _Line:
        """A line holding only the lifelines."""
        line = _Line(self.width)
        lifeline = self.style.get('connector', 'lifeline')
        for i in range(len(self.circuit)):
            line.put(self._center(i), '|', lifeline)
        return line

    # ------------------------------------------------------------------ pieces
    def header(self) -> list[Text]:
        """Boxed model names over their lifelines, using the labels learned from results so far."""
        top, mid, bottom = _Line(self.width), _Line(self.width), _Line(self.width)
        mid.put(0, 'it'.center(self.ITER_WIDTH - 1), self.style.get('iteration', 'number'))
        box = self.style.get('connector', 'box')
        for i, model in enumerate(self.circuit):
            name = self._labels[model][: self.column_width - 4]
            rule = '+' + '-' * (len(name) + 2) + '+'
            start = self._center(i) - len(rule) // 2
            top.put(start, rule, box)
            mid.put(start, '|', box)
            mid.put(start + 1, f' {name} ', self.style.get('model', model.value))
            mid.put(start + len(rule) - 1, '|', box)
            bottom.put(start, rule, box)
        return [top.render(), mid.render(), bottom.render()]

    def _delta(self, model: ModelType, objective: float | None) -> tuple[str, str]:
        """The objective-change text for ``model`` and the objective style key it takes."""
        if objective is None:
            return NO_OBJECTIVE, 'none'
        previous = self._previous.get(model)
        if previous is None:
            return f'(start {objective:,.2f})', 'start'
        delta = objective - previous
        kind = 'increase' if delta > 0 else 'decrease' if delta < 0 else 'unchanged'
        if self.delta_mode is DeltaMode.PERCENT:
            if previous == 0:
                return '(n/a %)', kind
            return f'({delta / abs(previous):+.4%})', kind
        return f'({delta:+,.2f})', kind

    def _ray(self, line: _Line, source: int, receiver: int, package: UpdatePackage) -> None:
        """Draw a labelled ray from column ``source`` to column ``receiver`` on ``line``."""
        s, r = self._center(source), self._center(receiver)
        ray = self.style.get('connector', 'ray')
        if source < receiver:
            # +----- label [n] ----->|
            line.put(s, '+' + '-' * (r - s - 2) + '>', ray)
        else:
            # |<----- label [n] -----+
            line.put(r + 1, '<' + '-' * (s - r - 2) + '+', ray)
        text = f' {package.label} [{package.size}] '
        line.put((s + r) // 2 - len(text) // 2, text, self.style.get('package', package.label))
        line.put(r, '|', self.style.get('connector', 'lifeline'))

    def _deliveries(
        self, results: Iterable[IterationResult]
    ) -> list[tuple[int, int, UpdatePackage]]:
        """List ``(source column, receiver column, package)`` for every delivery this iteration."""
        index = {m: i for i, m in enumerate(self.circuit)}
        rows: list[tuple[int, int, UpdatePackage]] = []
        for result in results:
            src = index.get(result.model_type)
            if src is None:
                continue
            for package in result.update_packages:
                receivers = set(package.receivers)
                to_all = ModelType.ALL in receivers
                for model, col in index.items():
                    if col != src and (to_all or model in receivers):
                        rows.append((src, col, package))
        return sorted(rows, key=lambda row: (row[0], row[1], row[2].label))

    # ------------------------------------------------------------------ main entry
    def record(self, iteration: int, results: Iterable[IterationResult]) -> Text:
        """Render the block for one completed iteration and remember its objectives.

        Parameters
        ----------
        iteration : int
            The iteration number just completed.
        results : Iterable[IterationResult]
            That iteration's results, one per model.

        Returns
        -------
        Text
            The coloured block, preceded by the header on the first call.  ``.plain`` is the
            uncoloured string.
        """
        results = list(results)
        by_model = {result.model_type: result for result in results}
        for result in results:
            if result.label:
                self._labels[result.model_type] = result.label
        lines: list[Text] = []
        if not self._header_done:
            lines.extend(self.header())
            self._header_done = True

        lines.append(self._blank().render())
        delta = self._blank()
        delta.put(
            0, str(iteration).rjust(self.ITER_WIDTH - 2), self.style.get('iteration', 'number')
        )
        for i, model in enumerate(self.circuit):
            result = by_model.get(model)
            objective = result.objective_value if result is not None else None
            text, kind = self._delta(model, objective)
            delta.put(self._center(i) - len(text) // 2, text, self.style.get('objective', kind))
            self._previous[model] = objective
        lines.append(delta.render())
        lines.append(self._blank().render())

        for n, (src, dst, package) in enumerate(self._deliveries(results)):
            if n:  # a bare lifeline row between rays opens the cascade up vertically
                lines.append(self._blank().render())
            line = self._blank()
            self._ray(line, src, dst, package)
            lines.append(line.render())

        return Text('\n').join(lines)
