"""
Created as part of the C-NEMS Project.

Written by:  J. F. Hyink
Written with:  Claude Opus 5.5 (Anthropic)
Contact:  jeff@westernspark.us
Created on:  9/28/26

Custom exceptions shared across the models and the integrator.

Keep each one a plain subclass of a built-in exception carrying just a message:  models run in
pool workers during integrated runs, and a worker returns an exception to its caller by pickling
it, which a custom ``__init__`` signature can break.
"""


class DataValidationError(ValueError):
    """Input data failed validation; the details are in the log.

    Raised rather than exiting the process, so the failure also surfaces from a pool worker
    (where ``SystemExit`` would kill the worker silently and leave its caller waiting).
    """
