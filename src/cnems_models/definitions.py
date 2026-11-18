"""Module to hold some top-level constants."""

# Import packages
from pathlib import Path

# an absolute locator to the top level of the project (the repo root, two levels above this
# package in the src layout) for use as a navigation aid in other modules
# TODO:  valid only for an editable install; a regular install resolves this into site-packages.
#        Replace once the scenario data (``input/``, ``run_configs/``) moves out of the repo.
#        clean-up action required when we "re-home" the data folders or finalize that plan.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
