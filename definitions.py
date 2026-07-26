"""
Module to hold some top-level constants.
"""

# Import packages
import os
from pathlib import Path

# an absolute locator to the top level of the project for use as a navigation aid in other modules
PROJECT_ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
