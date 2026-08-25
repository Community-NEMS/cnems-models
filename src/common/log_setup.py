"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/24/26

Shared logging formats and setup helpers.


"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from logging import Handler
from pathlib import Path

from definitions import PROJECT_ROOT

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s :: %(message)s'
LOG_DATE_FORMAT = '%d-%b-%y %H:%M:%S'


LIBRARY_LEVELS: dict[str, int] = {
    'pyomo': logging.INFO,  # <-- Allows solver "pass-throughs" via pyomo logging
    'pandas': logging.WARNING,
    'matplotlib': logging.WARNING,
}

# handlers this module put on the root logger, so a later call can take them back off again
_installed_handlers: list[logging.Handler] = []


def build_formatter() -> logging.Formatter:
    """Build a formatter using the project's standard record and date formats.

    Returns
    -------
    logging.Formatter
        A formatter over :data:`LOG_FORMAT` and :data:`LOG_DATE_FORMAT`.
    """
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def _retire_previous_setup() -> None:
    """Undo whatever the last call to this module installed, leaving foreign handlers alone."""
    root_logger = logging.getLogger()
    while _installed_handlers:
        prior = _installed_handlers.pop()
        root_logger.removeHandler(prior)
        prior.close()


def setup_control_loop_logging(
    log_file: Path,
    level: int = logging.INFO,
    append: bool = False,
    console_stream: bool = False,
) -> None:
    """Configure the root logger for a run's control process: a log file plus the console.

    Handlers go on the *root* logger, so everything emitted anywhere in the process during the
    run is captured -- project modules, pyomo, and any third-party library alike -- rather than
    only the trees a caller thought to name.  This is the counterpart to the per-model scenario
    log the worker processes install for themselves, which is deliberately scoped and quiet.

    Call once per run.  Handlers a caller installed itself are left alone, but a second call
    here retires the handlers the first one installed, so a process that runs several scenarios
    back to back gets one log file per run instead of each run's records also piling into its
    predecessors' files.

    Parameters
    ----------
    log_file : Path
        File the run's records are appended to.  Its parent must already exist.
    level : int, optional
        Level for the root logger and both handlers; INFO by default.
    append : bool, optional
        Append to an existing ``log_file`` rather than truncating it; False by default.
    console_stream: bool, optional
        Stream logging to console.  Defaults to ``False``.
    """
    formatter = build_formatter()

    file_handler = logging.FileHandler(log_file, mode='a' if append else 'w', encoding='utf-8')
    file_handler.setFormatter(formatter)
    handlers: list[Handler] = [file_handler]
    if console_stream:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        handlers.append(stream_handler)

    _retire_previous_setup()

    root_logger = logging.getLogger()
    for handler in handlers:
        root_logger.addHandler(handler)
        _installed_handlers.append(handler)
    root_logger.setLevel(level)

    # suppress other libraries appropriately
    for library, library_level in LIBRARY_LEVELS.items():
        logging.getLogger(library).setLevel(library_level)


def log_path(scenario_name: str, process_name: str) -> Path:
    """Build the log file path for one process of a scenario run, creating its folder.

    Parameters
    ----------
    scenario_name : str
        Names the output folder holding the run's logs.
    process_name : str
        Names the log file; ``'MAIN'`` for the control loop, otherwise the model.

    Returns
    -------
    Path
        The log file to append to.
    """
    log_file = PROJECT_ROOT / 'output' / scenario_name / f'{process_name}.log'
    log_file.parent.mkdir(parents=True, exist_ok=True)
    return log_file


_CAPTURED_LOGGERS: tuple[str, ...] = ('src', 'pyomo')


@contextmanager
def _scenario_log(scenario_name: str, process_name: str) -> Iterator[None]:
    """Route project and solver records to a scenario log file for the duration of the block.

    Attaches one file handler to each tree in :data:`_CAPTURED_LOGGERS` and detaches any
    console handler those trees already carry (pyomo installs one on stdout), then restores
    both, along with the trees' previous level and propagation, on the way out.  Scoping this to
    the block is what keeps it simple: a pool worker reused for a different model starts clean
    instead of inheriting the previous task's log file, and nothing is left behind for a host
    application to trip over.  Output goes to the file only -- no console handler is installed.

    Parameters
    ----------
    scenario_name : str
        Names the output folder and the first half of the log file name.
    process_name : str
        Names the second half of the log file name; ``'MAIN'`` for the control loop.

    Yields
    ------
    None
        The block runs with the scenario log attached.
    """
    handler = logging.FileHandler(log_path(scenario_name, process_name), mode='a', encoding='utf-8')
    handler.setFormatter(build_formatter())

    captured_loggers = [logging.getLogger(name) for name in _CAPTURED_LOGGERS]
    prior_state = [(target, target.level, target.propagate) for target in captured_loggers]
    # pyomo installs its own stdout handler, which would echo every solver record to the
    # terminal.  Detach console handlers on the captured trees for the duration -- filtering at
    # the handler rather than raising the logger's level, so the records still reach the file.
    # FileHandler subclasses StreamHandler, so it has to be excluded explicitly.
    console_handlers = [
        (target, existing)
        for target in captured_loggers
        for existing in target.handlers[:]
        if isinstance(existing, logging.StreamHandler)
        and not isinstance(existing, logging.FileHandler)
    ]
    try:
        for target, existing in console_handlers:
            target.removeHandler(existing)
        for target in captured_loggers:
            target.addHandler(handler)
            target.setLevel(logging.INFO)
            # stop here rather than propagating to root: these records belong in the scenario
            # file, not in the handlers of whatever application is hosting the run
            target.propagate = False
        yield
    finally:
        for target, level, propagate in prior_state:
            target.removeHandler(handler)
            target.setLevel(level)
            target.propagate = propagate
        for target, existing in console_handlers:
            target.addHandler(existing)
        handler.close()
