"""
Created as part of the C-NEMS Project.

Base class for the C-HSM submodules.

Adapted from ``models/hsm/submodule.py`` of EIA's National Energy Modeling System (NEMS),
https://github.com/EIAgov/NEMS, release tag ``AEO2025-Public-Release``, licensed under the
Apache License 2.0.
This file has been modified from the NEMS original, as described below, and formatted to the
C-NEMS code style.

The NEMS restart file is not used: ``restart`` is ``None`` and state is kept in memory on the
parent ``HSMModule``.

Convention for import alias is import submodule as sub
"""

# Single values are read with .at throughout, as in the NEMS code this follows.
# ruff: noqa: PD008

import pandas as pd

from src.models.hsm import common as com
from src.models.hsm import names as nam


class Submodule:
    """Generic submodule for HSM.

    Parameters
    ----------
    parent : HSMModule
        Pointer to the parent orchestrator.
    submodule_name : str
        Name of submodule (e.g. 'canada').
    """

    def __init__(self, parent, submodule_name):
        self.parent = parent
        self.input_path = ''
        self.onshore_input_path = ''
        self.offshore_input_path = ''
        self.alaska_input_path = ''
        self.canada_input_path = ''
        self.output_path = ''
        self.setup_table = pd.DataFrame()
        self.name = submodule_name

        # No NEMS restart file in C-HSM
        self.restart = None
        self.rest_curcalyr = 0

    def setup(self, setup_filename):
        """Set up the submodule: copy paths from parent and load setup table.

        Parameters
        ----------
        setup_filename : str
            CSV filename (relative to ``input_path``) for this submodule's
            setup table.
        """
        self.logger = self.parent.logger
        self.rest_curcalyr = int(self.parent.current_year)
        self.input_path = self.parent.input_path
        self.output_path = self.parent.output_path
        self.onshore_input_path = self.parent.onshore_input_path
        self.offshore_input_path = self.parent.offshore_input_path
        self.alaska_input_path = self.parent.alaska_input_path
        self.canada_input_path = self.parent.canada_input_path
        self.ngp_input_path = self.parent.ngp_input_path
        self.setup_table = com.read_dataframe(self.input_path + setup_filename, index_col=0)

    def run(self):
        """Update ``rest_curcalyr`` from parent's ``current_year``."""
        self.rest_curcalyr = self.parent.current_year

    def _load_dataframe(self, input_path, filename, index_col=None, skiprows=1):
        """Load an input table from CSV (looked up via the setup table).

        Parameters
        ----------
        input_path : str
            Path to the directory containing the file.
        filename : str
            Key in ``self.setup_table`` that maps to the actual filename.
        index_col : str, int, or list, optional
            Column(s) to use as the DataFrame index.
        skiprows : int, optional
            Rows to skip before the header row (default 1).

        Returns
        -------
        pd.DataFrame
        """
        temp_filename = input_path + self.setup_table.at[filename, nam.filename]
        return com.read_dataframe(temp_filename, skiprows=skiprows, index_col=index_col).copy()
