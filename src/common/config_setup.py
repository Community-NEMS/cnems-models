"""This file contains Config_settings class. It establishes the main settings used when running
the model. It takes these settings from the run_config.toml file. It has universal configurations
(e.g., configs that cut across modules and/or solve options) and module specific configs."""

###################################################################################################
# Setup

import argparse
import tomllib
import types
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from definitions import PROJECT_ROOT
from src.common.utilities import make_dir
from src.integrator.utilities import create_temporal_mapping

###################################################################################################
# Configuration Class


class Config_settings:
    """Generates the model settings that are used to solve. Settings include:  \n
    - Iterative Solve Config Settings \n
    - Spatial Config Settings \n
    - Temporal Config Settings \n
    - Electricity Config Settings \n
    - Other
    """

    def __init__(self, config_path: Path, args: argparse.Namespace | None = None, test=False):
        """Creates configuration object upon instantiation

        Parameters
        ----------
        config_path : Path
            Path to run_config.toml
        args : Namespace
            Parsed arguments fed to main.py or other parsed object
        test : bool, optional
            Used only for unit testing in unit_tests directory, by default False

        Raises
        ------
        ValueError
            No modules turned on; check run_config.toml
        ValueError
            sw_expansion: Must turn RM switch off if no expansion
        ValueError
            sw_expansion: Must turn learning switch off if no expansion

        """
        # __INIT__: Grab arguments namespace and set paths
        self.missing_checks = set()
        self.test = test
        self.args = args
        if not args:
            self.args = types.SimpleNamespace()
            self.args.op_mode = None
            self.args.debug = False
        self.PROJECT_ROOT = PROJECT_ROOT

        # __INIT__: Dump toml, sse args to set mode
        with open(config_path, 'rb') as src:
            config = tomllib.load(src)

        ############################################################################################
        # Universal Configs

        # __INIT__: Solve Mode
        self.default_mode = config['default_mode']
        if not self.args.op_mode:
            print('No mode arg passed, therefore...')
            self.selected_mode = self.default_mode
            print(f'using mode {self.default_mode} specified in run_config file')
        else:
            self.selected_mode = self.args.op_mode

        # __INIT__: Setting output paths
        # Setup the output directory and write out its name for other scripts to grab
        if test:
            OUTPUT_ROOT = Path(PROJECT_ROOT, 'unit_tests', 'test_logs')
        else:
            output_folder_name = f'{self.selected_mode}_{datetime.now().strftime("%Y_%m_%d_%H%Mh")}'
            OUTPUT_ROOT = Path(PROJECT_ROOT / 'output' / output_folder_name)
        self.OUTPUT_ROOT = OUTPUT_ROOT
        make_dir(self.OUTPUT_ROOT)

        #####
        ### __INIT__: Methods and Modules Configuration
        #####

        # __INIT__: Set modules from config
        self.electricity = config['electricity']
        self.hydrogen = config['hydrogen']
        self.residential = config['residential']

        # __INIT__: Redirects and raises based on conditions
        if (not any((self.electricity, self.hydrogen, self.residential))) and (
            self.selected_mode in ('unified-combo', 'gs-combo', 'standalone')
        ):
            raise ValueError('No modules turned on; check run_config.toml')

        # __INIT__: Single module case
        if [self.electricity, self.hydrogen, self.residential].count(True) == 1:
            print('Only one module is turned on; running standalone mode')
            self.run_method = 'run_standalone'

        # __INIT__: Combinations of Modules and Mode --> run guidance
        match self.selected_mode:
            case 'unified-combo':
                # No elec case
                if self.hydrogen and self.residential and not self.electricity:
                    print('not an available option, running default version')
                    print(
                        'running unified-combo with electricity, hydrogen, and residential modules'
                    )
                    self.electricity = True

                # else, assign method as gs and set options
                self.run_method = 'run_unified'
                self.method_options = {}
            case 'gs-combo':
                # No elec case
                if self.hydrogen and self.residential and not self.electricity:
                    print('not an available option, running default version')
                    print('running gs-combo with electricity, hydrogen, and residential modules')
                    self.electricity = True

                # else, assign method as gs and set options
                self.run_method = 'run_gs'
                self.method_options = {
                    'update_h2_price': self.hydrogen,
                    'update_elec_price': True,
                    'update_h2_demand': self.hydrogen,
                    'update_load': self.residential,
                    'update_elec_demand': False,
                }
            case 'standalone':
                self.run_method = 'run_standalone'
            case 'elec':
                self.run_method = 'run_elec_solo'
            case 'h2':
                self.run_method = 'run_h2_solo'
            case 'residential':
                self.run_method = 'run_residential_solo'
            case _:
                raise ValueError('Mode: Nonexistant mode specified')

        # __INIT__: Iterative Solve Configs
        self.tol = config['tol']
        self.force_10 = config['force_10']
        self.max_iter = config['max_iter']

        # __INIT__: Spatial Configs
        self.regions = config['regions']

        # __INIT__:  Temporal Configs
        self.sw_temporal = config['sw_temporal']
        self.cw_temporal = create_temporal_mapping(self.sw_temporal)

        # __INIT__:  Temporal Configs -
        self.sw_agg_years = config['sw_agg_years']
        self.years = config['years']
        if self.sw_agg_years == 1:
            self.start_year = config['start_year']
        else:
            self.start_year = self.years[0]

        ############################################################################################
        # __INIT__:  Electricity Configs
        self.sw_trade = config['sw_trade']
        self.sw_rm = config['sw_rm']
        self.sw_ramp = config['sw_ramp']
        self.sw_reserves = config['sw_reserves']
        self.sw_learning = config['sw_learning']
        self.sw_expansion = config['sw_expansion']

        ############################################################################################
        # __INIT__: Residential Configs
        self.scale_load = config['scale_load']

        if not test:
            self.view_regions = config['view_regions']
            self.view_years = config['view_years']
            self.sensitivity = config['sensitivity']
            self.change_var = config['change_var']
            self.percent_change = config['percent_change']
            self.complex = config['complex']

        ############################################################################################
        # __INIT__: Hydrogen Configs
        self.h2_data_folder = self.PROJECT_ROOT / config['h2_data_folder']

    ################################################################################################
    # Set Attributes Update

    # Runs configuration checks when you set attributes
    def __setattr__(self, name, value):
        """Update to generic setattr function that includes checks for appropriate attribute values

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value
        """
        super().__setattr__(name, value)

        # __SETATTR__: dictionary of check methods and setting attributes to pass thru those methods
        check_dict = {
            '_check_regions': {'region'},
            '_check_res_settings': {'view_regions', 'view_years', 'regions', 'years'},
            '_check_int': {'max_iter', 'start_year', 'sw_learning', 'percent_change'},
            '_check_elec_expansion_settings': {'sw_expansion', 'sw_rm', 'sw_learning'},
            '_additional_year_settings': {'sw_agg_years', 'years', 'start_year'},
            '_check_true_false': {
                'electricity',
                'hydrogen',
                'residential',
                'force_10',
                'sensitivity',
                'complex',
            },
            '_check_zero_one': {
                'sw_trade',
                'sw_expansion',
                'sw_rm',
                'sw_ramp',
                'sw_reserves',
                'sw_agg_years',
            },
        }

        # __SETATTR__: Create empty all_check_sets
        all_check_sets = set()

        # __SETATTR__: For each check method pass the setting attributes through
        for check_method in check_dict.keys():
            # __SETATTR__: Add checks to all_check_sets
            all_check_sets.union(check_dict[check_method])

            # __SETATTR__: If set value in check dictionary, run check method
            if name in check_dict[check_method]:
                method = getattr(self, check_method)
                method(name, value)

        # __SETATTR__: Create a list of all the items not being checked
        if name not in all_check_sets:
            self.missing_checks.add(name)

    ################################################################################################
    # Check Methods

    def _has_all_attributes(self, attrs: set):
        """Determines if all attributes within the set exist or not

        Parameters
        ----------
        attrs : set
            set of setting attributes

        Returns
        -------
        bool
            True or False
        """
        return all(hasattr(self, attr) for attr in attrs)

    def _additional_year_settings(self, name, value):
        """Checks year related settings to see if values are within expected ranges and updates
        other settings linked to years if years is changed.

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        # Years settings
        if hasattr(self, 'years'):
            if not isinstance(self.years, list):
                raise TypeError('years: must be a list')
            if not all(isinstance(year, int) for year in self.years):
                raise TypeError('years: only include years (integers) in year list')
            if min(self.years) < 1900:
                raise TypeError('years: should probably only include years after 1900')
            if max(self.years) > 2200:
                raise TypeError('years: should probably only include years before 2200')

        # Years related settings
        if self._has_all_attributes({'sw_agg_years', 'years', 'start_year'}):
            if self.sw_agg_years == 1:
                all_years_list = list(range(self.start_year, max(self.years) + 1))
            else:
                all_years_list = self.years
            solve_array = np.array(self.years)
            mapped_list = [solve_array[solve_array >= year].min() for year in all_years_list]
            self.year_map = pd.DataFrame({'year': all_years_list, 'Map_year': mapped_list})
            self.WeightYear = (
                self.year_map.groupby(['Map_year'])
                .agg('count')
                .reset_index()
                .rename(columns={'Map_year': 'year', 'year': 'WeightYear'})
            )

    # TODO: no hard coded values! regions should be flexible, come up with a better check
    def _check_regions(self, name, value):
        """Checks to see if region is between the current default values of 1 and 25.

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        if not isinstance(value, list):
            raise TypeError('regions: must be a list')
        if min(value) < 1:
            raise ValueError('regions: Nonexistant region specified')
        if max(value) > 25:
            raise ValueError('regions: Nonexistant region specified')

    def _check_elec_expansion_settings(self, name, value):
        """Checks that switches for reserve margin and learning are on only if expansion is on.

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        # You cannot run with a reserve margin without expansion
        if self._has_all_attributes({'sw_expansion', 'sw_rm'}):
            if self.sw_expansion == 0:  # if expansion is off
                if self.sw_rm == 1:
                    raise ValueError('sw_expansion: Must turn RM switch off if no expansion')

        # You cannot run with learning without expansion
        if self._has_all_attributes({'sw_expansion', 'sw_learning'}):
            if self.sw_expansion == 0:  # if expansion is off
                if self.sw_learning == 1:
                    raise ValueError('sw_expansion: Must turn learning switch off if no expansion')

    def _check_true_false(self, name, value):
        """Checks if attribute is either true or false

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        if value is True:
            pass
        elif value is False:
            pass
        else:
            raise ValueError(f'{name}: Must be either true or false')

    def _check_int(self, name, value):
        """Checks if attribute is an integer

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        if not isinstance(value, int):
            raise ValueError(f'{name}: Must be an integer')

    def _check_zero_one(self, name, value):
        """Checks if attribute is either zero or one

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        if (value == 0) or (value == 1):
            pass
        else:
            raise ValueError(f'{name}: Must be either 0 or 1')

    def _check_res_settings(self, name, value):
        """Checks if view year or region settings are subsets of year or region

        Parameters
        ----------
        name : str
            attribute name
        value : _type_
            attribute value

        Raises
        ------
        TypeError
            Error
        """
        if self.residential:
            if hasattr(self, 'view_regions'):
                if not isinstance(self.view_regions, list):
                    raise ValueError(f'{name}: Must be a list')
                if not set(self.view_regions).issubset(self.regions):
                    raise ValueError('view_regions: Must be a subset of regions')
            if hasattr(self, 'view_years'):
                if not isinstance(self.view_years, list):
                    raise ValueError(f'{name}: Must be a list')
                if not set(self.view_years).issubset(self.years):
                    raise ValueError('view_years: Must be a subset of years')
