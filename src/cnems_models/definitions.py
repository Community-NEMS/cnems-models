"""Module to hold some top-level constants."""

# Import packages
from pathlib import Path

# an absolute locator to the top level of the project (the repo root, two levels above this
# package in the src layout) for use as a navigation aid in other modules
PROJECT_ROOT = Path(__file__).resolve().parents[2]
