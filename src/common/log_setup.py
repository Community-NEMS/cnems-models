"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  8/24/26

Shared logging formats and setup helpers.

The formats here are the single definition of what a project log record looks like; anything
that installs a handler should build its formatter from them so files written by different
processes of the same run stay readable side by side.

"""

import logging
import sys
from pathlib import Path

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s :: %(message)s'
LOG_DATE_FORMAT = '%d-%b-%y %H:%M:%S'


def build_formatter() -> logging.Formatter:
    """Build a formatter using the project's standard record and date formats.

    Returns
    -------
    logging.Formatter
        A formatter over :data:`LOG_FORMAT` and :data:`LOG_DATE_FORMAT`.
    """
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def setup_control_loop_logging(
    log_file: Path, level: int = logging.INFO, append: bool = False
) -> None:
    """Configure the root logger for a run's control process: a log file plus the console.

    Handlers go on the *root* logger, so everything emitted anywhere in the process during the
    run is captured -- project modules, pyomo, and any third-party library alike -- rather than
    only the trees a caller thought to name.  This is the counterpart to the per-model scenario
    log the worker processes install for themselves, which is deliberately scoped and quiet.

    Call once, at the start of execution.  Handlers are added, not replaced, so a second call in
    the same process would double every record.

    Parameters
    ----------
    log_file : Path
        File the run's records are appended to.  Its parent must already exist.
    level : int, optional
        Level for the root logger and both handlers; INFO by default.
    """
    formatter = build_formatter()

    file_handler = logging.FileHandler(log_file, mode='a' if append else 'w', encoding='utf-8')
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    root_logger.setLevel(level)
