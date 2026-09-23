"""
Created as part of the C-NEMS Project.

Canada submodule for C-HSM.

Adapted from ``models/hsm/canada.py`` of EIA's National Energy Modeling System (NEMS),
https://github.com/EIAgov/NEMS, release tag ``AEO2025-Public-Release``, licensed under the
Apache License 2.0.
This file has been modified from the NEMS original; the changes are listed below.

It takes baseline production, decline curves and well and price sensitivities from the Canada
Energy Regulator's "Canada Energy Future" report and projects Canadian gas production
available for export to the United States.

Changes from the NEMS original:

* Imports point at ``src.models.hsm`` (``names as nam`` and so on).
* No pickle files between years: state is kept in ``self.parent.hsm_vars``, a
  ``types.SimpleNamespace``.
* ``report_results_unf`` writes to the ``self.parent.results_canada_*`` tables instead of the
  NEMS restart file, and the Canada pipeline price is read from ``self.parent.ogsmout_ogcnpprd``
  instead of ``self.restart.ogsmout_ogcnpprd``.
* Old ``groupby(..., axis=0)`` calls are written as ``groupby(...)`` for current pandas.
* In ``calculate_wells``, the well counts are rounded and negative ones set to zero once, after the
  loop over regions and gas types, instead of after every step of it; the result is the same.
* Formatted to the C-NEMS code style, with comments and docstrings rewritten.

Canadian production responds to the Brent price but not to the Henry Hub price C-HSM is given:
the Henry Hub terms here come from the benchmark file ``can_benchmark_prices.csv``, as in the
NEMS AEO2025 release.
"""

# The NEMS code this follows reads single values with .at and arrays with .values;
# they are kept as in NEMS.
# ruff: noqa: PD008, PD011

import warnings

import pandas as pd

from src.models.hsm import names as nam
from src.models.hsm import submodule as sub


class Canada(sub.Submodule):
    """Canada submodule for HSM.

    Parameters
    ----------
    parent : HSMModule
        Pointer to the parent orchestrator.
    """

    def __init__(self, parent):
        super().__init__(parent, submodule_name='canada')

        # Input tables
        self.benchmark_prices = pd.DataFrame()
        self.natgas_production = pd.DataFrame()
        self.natgas_baseline_prod = pd.DataFrame()
        self.natgas_dc_vars = pd.DataFrame()
        self.natgas_poly_eqs = pd.DataFrame()
        self.elasticity_ad_gas = pd.DataFrame()
        self.can_wells = pd.DataFrame()
        self.natgas_no_export_prod = pd.DataFrame()

        # Benchmark years setup
        self.zero_year = 0
        self.input_years = [*range(2005, 2051)]
        self.base_year = self.input_years[0]
        self.output_start_year = 1990
        self.model_start_year = 2005
        self.output_end_year = 2051

        # Scalar variables
        self.bench_brent = 0.0
        self.bench_ng_prod = 0.0
        self.b1 = 0.0
        self.b0 = 0.0
        self.bench_hen_hub = 0.0
        self.can_well_v_HH = [0, 0.85]

        # DataFrames
        self.fixed_vols = pd.DataFrame()
        self.ad_prod = pd.DataFrame()
        self.na_prod = pd.DataFrame()
        self.na_prod_new = pd.DataFrame()
        self.na_prod_sum = pd.DataFrame()
        self.can_wells = pd.DataFrame()
        self.cnenagprd = pd.DataFrame()
        self.cnadgprd = pd.DataFrame()
        self.cnrnagprd = pd.DataFrame()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self, setup_filename):
        """Set up Canada submodule: load tables and intermediate variables.

        Parameters
        ----------
        setup_filename : str
            CSV filename (relative to ``input_path``) for this submodule's
            setup table.
        """
        super().setup(setup_filename)

        self.zero_year = int(self.setup_table.at[nam.zero_year, nam.filename])

        if self.parent.aeo_year - 2 != self.zero_year:
            warnings.warn('self.aeo_year - 2 != self.history_year', UserWarning, stacklevel=2)

        # Load input files
        self.benchmark_prices = super()._load_dataframe(
            self.canada_input_path, nam.can_benchmark_prices, skiprows=0, index_col=nam.year
        )

        self.natgas_dc_vars = super()._load_dataframe(
            self.canada_input_path,
            nam.can_natgas_dc_vars,
            skiprows=0,
            index_col=[nam.region, nam.gas_type],
        )

        self.elasticity_ad_gas = super()._load_dataframe(
            self.canada_input_path,
            nam.can_elasticity_ad_gas,
            skiprows=0,
            index_col=[nam.year, nam.region],
        )

        self.natgas_baseline_prod = super()._load_dataframe(
            self.canada_input_path, nam.can_natgas_baseline_prod, skiprows=0
        )

        self.can_wells = super()._load_dataframe(self.canada_input_path, nam.can_wells, skiprows=0)

        self.natgas_poly_eqs = super()._load_dataframe(
            self.canada_input_path,
            nam.can_natgas_poly_eqs,
            skiprows=0,
            index_col=[nam.region, nam.gas_type],
        )

        self.natgas_no_export_prod = super()._load_dataframe(
            self.canada_input_path, nam.can_natgas_no_export_prod, skiprows=0, index_col=[nam.year]
        )

        # Common indices
        self.index_std = [nam.year, nam.region, nam.gas_type]

        self.na_index = [
            ('Alberta', 'Non Associated'),
            ('Alberta', 'Tight'),
            ('Alberta', 'Shale'),
            ('Alberta', 'Coalbed Methane'),
            ('British Columbia', 'Tight'),
            ('British Columbia', 'Shale'),
            ('British Columbia', 'Non Associated'),
            ('Saskatchewan', 'Tight'),
            ('Saskatchewan', 'Non Associated'),
        ]

        # Load intermediate tables (in-memory, populated by write_intermediate_variables)
        if (self.rest_curcalyr > self.zero_year) and (self.parent.integrated_switch is True):
            self.na_prod = self.parent.hsm_vars.can_na_prod.copy()
            self.na_prod_new = self.parent.hsm_vars.can_na_prod_new.copy()
            self.na_prod_sum = self.parent.hsm_vars.can_na_prod_sum.copy()
            self.ad_prod = self.parent.hsm_vars.can_ad_prod.copy()
            self.ad_prod_sum = self.parent.hsm_vars.can_ad_prod_sum.copy()
            self.can_wells = self.parent.hsm_vars.can_can_wells.copy()
            self.prod_profile = self.parent.hsm_vars.can_prod_profile.copy()

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        """Run Canada submodule for one model year."""
        self.logger.debug('running canada natural gas submodule year=%d', self.rest_curcalyr)
        super().run()

        self.rest_curcalyr = int(self.rest_curcalyr)

        if self.rest_curcalyr == self.zero_year:
            self.logger.info('Run Canada Setup Functions')
            self.canada_base_prod_setup()
            self.calibrate_ad_gas()
            self.wells_setup()
            self.prod_profile_setup()

        if self.rest_curcalyr >= self.zero_year:
            self.logger.info('Run Canada Model')
            self.calculate_wells()
            self.calculate_na_prod()
            self.sum_and_merge_production()

        # Persist intermediate variables for next model year
        if self.parent.integrated_switch and (
            self.parent.param_fcrl == 1 or self.parent.param_ncrl == 1
        ):
            self.logger.info('Run Canada Intermediate Variables')
            self.write_intermediate_variables()

    # ------------------------------------------------------------------
    # Year-1 functions
    # ------------------------------------------------------------------

    def canada_base_prod_setup(self):
        """Split AD and NA gas into separate tables; assign to regions."""
        regions_dict = {
            'Alberta': 'Alberta',
            'British Columbia': 'British Columbia',
            'Saskatchewan': 'Saskatchewan',
            'Nova Scotia': 'Other',
            'New Brunswick': 'Other',
            'Ontario': 'Other',
            'Yukon': 'Other',
            'Northwest Territories': 'Other',
            'Quebec': 'Other',
            'Newfoundland and Labrador': 'Other',
        }

        # NA gas
        na_base = self.natgas_baseline_prod.copy()
        na_base = na_base[
            na_base[nam.gas_type].isin(['Tight', 'Shale', 'Coalbed Methane', 'Non Associated'])
        ]
        na_base = na_base[~na_base[nam.region].isin(['Canada', 'WCSB'])]
        na_base[nam.region] = na_base[nam.region].map(regions_dict)
        na_base = na_base.rename(columns={nam.baseline_prod: nam.fixed_prod})
        na_base = na_base.groupby([nam.region, nam.gas_type, nam.year]).sum()
        self.na_prod = na_base.copy()

        # AD gas
        ad_base_wcsb = self.natgas_baseline_prod.copy()
        ad_base_wcsb = ad_base_wcsb[ad_base_wcsb[nam.gas_type].isin(['Solution'])]

        ad_base_small = self.natgas_baseline_prod.copy()
        ad_base_small = ad_base_small[
            ad_base_small[nam.region].isin(
                [
                    'Nova Scotia',
                    'New Brunswick',
                    'Quebec',
                    'Ontario',
                    'Yukon',
                    'Northwest Territories',
                ]
            )
        ]

        ad_base = pd.concat([ad_base_wcsb, ad_base_small], ignore_index=False)
        ad_base[nam.gas_type] = 'Associated Dissolved'
        ad_base = ad_base.groupby([nam.region, nam.gas_type, nam.year]).sum()
        ad_base = ad_base.rename(columns={nam.baseline_prod: nam.total_prod})
        self.ad_prod = ad_base.copy()

    def calibrate_ad_gas(self):
        """Calibrate AD gas production based on Brent price."""
        brent_df = pd.DataFrame()
        brent_df[nam.brent_int] = self.parent.rest_brent_price[nam.value].copy()
        brent_df[nam.brent_can] = self.benchmark_prices[nam.brent_1987].copy()

        ad_prod_elas_df = self.ad_prod.copy().reset_index(drop=False)
        ad_prod_elas_df = ad_prod_elas_df.merge(
            self.elasticity_ad_gas, how='left', on=[nam.year, nam.region]
        ).fillna(0.0)
        ad_prod_elas_df = ad_prod_elas_df.merge(
            brent_df, how='left', left_on=nam.year, right_index=True
        )

        high_price_mask = ad_prod_elas_df[nam.brent_int] > ad_prod_elas_df[nam.brent_can]

        high_price_df = ad_prod_elas_df[high_price_mask].copy()
        high_price_df[nam.total_prod] = (
            high_price_df[nam.total_prod]
            * (high_price_df[nam.brent_int] / high_price_df[nam.brent_can]) ** high_price_df['high']
        )
        ad_prod_elas_df.update(high_price_df)

        low_price_df = ad_prod_elas_df[~high_price_mask].copy()
        low_price_df[nam.total_prod] = (
            low_price_df[nam.total_prod]
            * (low_price_df[nam.brent_int] / low_price_df[nam.brent_can]) ** low_price_df['low']
        )
        ad_prod_elas_df.update(low_price_df)

        ad_prod_elas_df = ad_prod_elas_df.set_index([nam.region, nam.gas_type, nam.year])
        self.ad_prod.update(ad_prod_elas_df)

    def wells_setup(self):
        """Reset Canada well counts to 0 for all model years."""
        temp_wells = self.can_wells.copy()
        temp_wells = temp_wells.loc[temp_wells[nam.year] > self.parent.history_year]
        temp_wells[nam.wells] = 0
        self.can_wells.update(temp_wells, overwrite=True, errors='ignore')
        self.can_wells = self.can_wells.set_index([nam.region, nam.gas_type, nam.year])

    def prod_profile_setup(self):
        """Solve for NA production profiles; create net-new production DataFrame."""
        self.na_prod_new = pd.DataFrame(index=self.na_prod.index)

        profile_chunks = []
        for index in self.na_index:
            dr = self.natgas_dc_vars.at[index, 'dr']
            b = self.natgas_dc_vars.at[index, 'b']
            ipr = self.natgas_dc_vars.at[index, 'ipr']

            denom_b = b if b > 0 else b + 0.01
            curve_vals = (
                ipr
                * 365
                / (1 + dr * b * (pd.Series(self.input_years).values - self.input_years[0] + 1))
                ** (1 / denom_b)
            )

            decline_curves = pd.DataFrame(
                {nam.decline_curve: curve_vals, nam.region: index[0], nam.gas_type: index[1]},
            )
            decline_curves.index.names = ['index_year']
            decline_curves.at[0, nam.decline_curve] = decline_curves.at[0, nam.decline_curve] * 0.5

            profile_chunks.append(decline_curves)

        self.prod_profile = pd.concat(profile_chunks, ignore_index=False)
        self.prod_profile = self.prod_profile.set_index([nam.region, nam.gas_type])

    # ------------------------------------------------------------------
    # Every-year functions
    # ------------------------------------------------------------------

    def calculate_wells(self):
        """Solve for number of producing wells by region, gas type, and year."""
        self.benchmark_prices[nam.hh_cal] = (
            self.benchmark_prices[nam.henry_hub_1987] + self.can_well_v_HH[0]
        )

        if self.parent.current_year > self.parent.history_year:
            brent_int = self.parent.rest_brent_price.at[self.rest_curcalyr, nam.value]
            brent_can = self.benchmark_prices.at[int(self.parent.current_year), nam.brent_1987]
            hh_int = self.benchmark_prices.at[
                self.rest_curcalyr, 'Henry Hub - US$/MMBTU (calibrated)'
            ]

            # Canada pipeline price, read from the parent instead of the NEMS restart file
            can_ng_price_bench = self.parent.ogsmout_ogcnpprd.copy()
            can_ng_price_bench = can_ng_price_bench.xs(2, level=0, drop_level=True).copy()
            can_ng_price_bench = can_ng_price_bench + (
                0.96 / self.parent.rest_mc_jpgdp.at[2016, nam.value]
            )

            for index in self.na_index:
                b3 = self.natgas_poly_eqs.at[index, 'b3']
                b2 = self.natgas_poly_eqs.at[index, 'b2']
                b1 = self.natgas_poly_eqs.at[index, 'b1']
                b0 = self.natgas_poly_eqs.at[index, 'b0']

                if brent_int > brent_can:
                    calibration_var = (
                        self.benchmark_prices.at[int(self.parent.current_year), nam.hh_cal]
                        * (brent_int / brent_can) ** self.natgas_poly_eqs.at[index, 'oilprc_high']
                    )
                else:
                    calibration_var = (
                        self.benchmark_prices.at[int(self.parent.current_year), nam.hh_cal]
                        * (brent_int / brent_can) ** self.natgas_poly_eqs.at[index, 'oilprc_low']
                    )

                # New wells = polynomial in the benchmark Henry Hub price x the pipeline price
                # benchmark (can_ng_price_bench) x calibration_var^0.75, where calibration_var is
                # the Henry Hub benchmark (hh_cal) times the Brent term. This is the NEMS AEO2025
                # formula; the AEO2026 release drops hh_cal and can_ng_price_bench from it. All the
                # Henry Hub terms come from fixed inputs, not from the price C-HSM is given.
                self.can_wells.at[
                    (index[0], index[1], int(self.parent.current_year)), nam.wells
                ] = (
                    (b3 * hh_int**3 + b2 * hh_int**2 + b1 * hh_int + b0)
                    * can_ng_price_bench.at[self.rest_curcalyr, nam.value]
                    * (calibration_var**0.75)
                )

                # Floor at 0
                if (  # noqa: PLR1730 - kept in the NEMS form
                    self.can_wells.at[
                        (index[0], index[1], int(self.parent.current_year)), nam.wells
                    ]
                    < 0
                ):
                    self.can_wells.at[
                        (index[0], index[1], int(self.parent.current_year)), nam.wells
                    ] = 0

            self.can_wells[nam.wells] = self.can_wells[nam.wells].round()
            self.can_wells.loc[self.can_wells[nam.wells] < 0, nam.wells] = 0

            if self.parent.debug_switch:
                self.can_wells.to_csv(
                    self.output_path + 'module_results_debug/hsm_can_wells_debug.csv'
                )

    def calculate_na_prod(self):
        """Determine production from net new drilling using production profiles."""
        self.na_prod_new['new_drill_prod'] = 0.0
        self.na_prod_new[self.rest_curcalyr] = 0.0

        for index in self.na_index:
            tech = self.natgas_dc_vars.at[index, nam.tech]
            well_count = self.can_wells.at[(index[0], index[1], self.rest_curcalyr), nam.wells]

            curve_mask = self.prod_profile.index == (index[0], index[1])
            curve_temp = self.prod_profile[curve_mask].copy()
            curve_temp = curve_temp[nam.decline_curve].tolist()
            zero_list = [0] * (self.rest_curcalyr - self.base_year)
            curve_temp = zero_list + curve_temp
            splice = self.base_year - self.rest_curcalyr
            curve_temp = curve_temp[:splice]

            temp_df = pd.DataFrame(index=self.input_years)
            temp_df.index.names = [nam.year]
            temp_df[self.rest_curcalyr] = curve_temp
            temp_df[self.rest_curcalyr] = temp_df[self.rest_curcalyr].apply(
                lambda well_prod: well_prod * well_count  # noqa: B023 - used at once, in this loop
            ) * ((1 + tech) ** (self.parent.current_year - self.base_year))

            temp_df[nam.region] = index[0]
            temp_df[nam.gas_type] = index[1]
            temp_df = temp_df.set_index([nam.region, nam.gas_type], append=True)
            temp_df = temp_df.reorder_levels([1, 2, 0])

            self.na_prod_new.update(temp_df, overwrite=True, errors='ignore')

    def sum_and_merge_production(self):
        """Aggregate NA and AD production into restart-file-format DataFrames."""
        self.na_prod[self.rest_curcalyr] = self.na_prod_new[self.rest_curcalyr]
        self.na_prod[self.rest_curcalyr] = self.na_prod[self.rest_curcalyr].fillna(0)

        if self.parent.debug_switch:
            self.na_prod.to_csv(self.output_path + 'module_results_debug/hsm_can_drill_debug.csv')

        # West Canada NA
        temp_west_na_df = self.na_prod.copy()
        temp_west_na_df = temp_west_na_df[
            temp_west_na_df.index.get_level_values(0).isin(
                ['Alberta', 'British Columbia', 'Saskatchewan']
            )
        ]
        temp_west_na_df = temp_west_na_df.copy().groupby(nam.year).sum()
        temp_west_na_df = temp_west_na_df.rename({nam.total_prod: nam.value}, axis=1)
        temp_west_na_df['NUMCAN'] = 2
        temp_west_na_df = temp_west_na_df.set_index(['NUMCAN'], append=True)
        temp_west_na_df = temp_west_na_df.reorder_levels([1, 0])

        if self.na_prod_sum.empty:
            self.na_prod_sum = temp_west_na_df.copy()
        else:
            self.na_prod_sum[self.rest_curcalyr] = temp_west_na_df[self.rest_curcalyr].copy()

        # East Canada NA
        temp_east_na_df = self.na_prod.copy()
        temp_east_na_df = temp_east_na_df[
            ~temp_east_na_df.index.get_level_values(0).isin(
                ['Alberta', 'British Columbia', 'Saskatchewan']
            )
        ]

        if temp_east_na_df.empty:
            temp_east_na_df = pd.DataFrame(index=temp_west_na_df.index.get_level_values(nam.year))
            temp_east_na_df['NUMCAN'] = 1
            temp_east_na_df[nam.fixed_prod] = 0
            temp_east_na_df[self.rest_curcalyr] = 0
            temp_east_na_df = temp_east_na_df.set_index(['NUMCAN'], append=True)
            temp_east_na_df = temp_east_na_df.reorder_levels([1, 0])
            self.na_prod_sum = pd.concat([self.na_prod_sum, temp_east_na_df], ignore_index=False)
            self.na_prod_sum = self.na_prod_sum.groupby(level=[0, 1]).sum()
        else:
            temp_east_na_df = temp_east_na_df.reset_index(drop=False)
            temp_east_na_df['NUMCAN'] = 1
            temp_east_na_df = temp_east_na_df.set_index(['NUMCAN', nam.year])
            self.na_prod_sum = pd.concat([self.na_prod_sum, temp_east_na_df], ignore_index=False)
            self.na_prod_sum = self.na_prod_sum.groupby(level=[0, 1]).sum()

        # West Canada AD
        temp_west_ad_df = self.ad_prod.copy()
        temp_west_ad_df = temp_west_ad_df[
            temp_west_ad_df.index.get_level_values(nam.region).isin(
                ['Alberta', 'British Columbia', 'Saskatchewan']
            )
        ]
        temp_west_ad_df = temp_west_ad_df[[nam.total_prod]].copy().groupby(nam.year).sum()
        temp_west_ad_df = temp_west_ad_df.rename({nam.total_prod: nam.value}, axis=1)
        temp_west_ad_df['NUMCAN'] = 2
        temp_west_ad_df = temp_west_ad_df.set_index(['NUMCAN'], append=True)
        temp_west_ad_df = temp_west_ad_df.reorder_levels([1, 0])
        self.ad_prod_sum = temp_west_ad_df.copy()

        # East Canada AD
        temp_east_ad_df = self.ad_prod.copy()
        temp_east_ad_df = temp_east_ad_df[
            ~temp_east_ad_df.index.get_level_values(nam.region).isin(
                ['Alberta', 'British Columbia', 'Saskatchewan']
            )
        ]
        temp_east_ad_df = temp_east_ad_df[[nam.total_prod]].copy().groupby(nam.year).sum()
        temp_east_ad_df = temp_east_ad_df.rename({nam.total_prod: nam.value}, axis=1)
        temp_east_ad_df['NUMCAN'] = 1
        temp_east_ad_df = temp_east_ad_df.set_index(['NUMCAN'], append=True)
        temp_east_ad_df = temp_east_ad_df.reorder_levels([1, 0])
        self.ad_prod_sum = pd.concat([self.ad_prod_sum, temp_east_ad_df], ignore_index=False)

    def write_intermediate_variables(self):
        """Persist in-memory state to parent.hsm_vars for next model year."""
        self.parent.hsm_vars.can_na_prod = self.na_prod.copy()
        self.parent.hsm_vars.can_na_prod_new = self.na_prod_new.copy()
        self.parent.hsm_vars.can_na_prod_sum = self.na_prod_sum.copy()
        self.parent.hsm_vars.can_ad_prod = self.ad_prod.copy()
        self.parent.hsm_vars.can_ad_prod_sum = self.ad_prod_sum.copy()
        self.parent.hsm_vars.can_can_wells = self.can_wells.copy()
        self.parent.hsm_vars.can_prod_profile = self.prod_profile.copy()

        if self.parent.hsm_var_debug_switch:
            self.na_prod.to_csv(self.parent.hsm_var_output_path + 'hsm_can_na_prod.csv')
            self.na_prod_new.to_csv(self.parent.hsm_var_output_path + 'hsm_can_na_prod_new.csv')
            self.na_prod_sum.to_csv(self.parent.hsm_var_output_path + 'hsm_can_na_prod_sum.csv')
            self.ad_prod.to_csv(self.parent.hsm_var_output_path + 'hsm_can_ad_prod.csv')
            self.ad_prod_sum.to_csv(self.parent.hsm_var_output_path + 'hsm_can_ad_prod_sum.csv')
            self.can_wells.to_csv(self.parent.hsm_var_output_path + 'hsm_can_wells.csv')
            self.prod_profile.to_csv(self.parent.hsm_var_output_path + 'hsm_can_prod_profile.csv')

    def report_results_unf(self):
        """Write results to parent DataFrames (replaces restart-file writes).

        Populates
        ---------
        self.parent.results_canada_na_prod
            Expected NA gas production (1=east, 2=west) BCF/yr.
        self.parent.results_canada_ad_prod
            AD gas production (1=east, 2=west) BCF/yr.
        self.parent.results_canada_realized_na_prod
            Realized NA gas production (copy of expected).
        """
        if self.parent.debug_switch:
            self.na_prod_sum.to_csv(self.output_path + 'module_results_debug/hsm_can_na_prod.csv')
            self.ad_prod.to_csv(self.output_path + 'module_results_debug/hsm_can_ad_prod.csv')

        if self.rest_curcalyr >= self.parent.steo_years[0]:
            # NA production total by NUMCAN
            temp_na = self.na_prod_sum.copy()
            temp_na['value'] = temp_na.sum(axis=1)
            temp_na = temp_na[['value']]

            # AD production
            temp_ad = self.ad_prod_sum.copy()
            temp_ad = temp_ad[['value']]

            # Remove production not available for export (West Canada only)
            temp_no_export = temp_na.loc[[2]].copy()
            temp_no_export.loc[:, 'no_export_prod'] = self.natgas_no_export_prod[
                'prod'
            ].values.copy()
            temp_no_export[nam.value] = (
                temp_no_export[nam.value] - temp_no_export['no_export_prod'] * 365
            )
            temp_no_export = temp_no_export.drop('no_export_prod', axis=1)
            temp_no_export.loc[temp_no_export[nam.value] < 0, nam.value] = 0
            temp_na.update(temp_no_export)

            # Write to parent result containers (replaces restart file)
            if self.parent.results_canada_na_prod.empty:
                self.parent.results_canada_na_prod = temp_na.copy()
            else:
                self.parent.results_canada_na_prod.at[(1, self.rest_curcalyr), 'value'] = (
                    temp_na.at[(1, self.rest_curcalyr), 'value']
                )
                self.parent.results_canada_na_prod.at[(2, self.rest_curcalyr), 'value'] = (
                    temp_na.at[(2, self.rest_curcalyr), 'value']
                )

            if self.parent.results_canada_ad_prod.empty:
                self.parent.results_canada_ad_prod = temp_ad.copy()
            else:
                self.parent.results_canada_ad_prod.at[(1, self.rest_curcalyr), 'value'] = (
                    temp_ad.at[(1, self.rest_curcalyr), 'value']
                )
                self.parent.results_canada_ad_prod.at[(2, self.rest_curcalyr), 'value'] = (
                    temp_ad.at[(2, self.rest_curcalyr), 'value']
                )

            self.parent.results_canada_realized_na_prod = self.parent.results_canada_na_prod.copy()
