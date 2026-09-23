"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

HSMModule runs C-HSM one year at a time. It reads the setup table, builds the price paths, runs
the Canada submodule (``canada.py``) and the US gas supply (``us_gas.py``), and holds their
results for ``hsm_model.py`` and ``postprocessor.py``.

It does the job of ``models/hsm/module_unf.py``, the HSM driver in EIA's National Energy Modeling
System (NEMS, https://github.com/EIAgov/NEMS, release tag ``AEO2025-Public-Release``, Apache
License 2.0), and keeps its variable names, but it is written for C-HSM. The main differences:

- There is no NEMS restart file. Prices come in as ``{year: price}`` dicts and results stay in
  memory on this object.
- Values NEMS keeps between years in pickle files live in ``hsm_vars`` (a ``SimpleNamespace``).
- Only the Canada submodule and the US gas supply run. The NEMS onshore, offshore, Alaska and
  gas processing submodules are not loaded.
- The GDP deflator is a table of approximate BEA values (``_GDP_DEFLATOR``), not the NEMS
  macroeconomic model.
"""

# Single values are read with .at throughout, as in the NEMS code this follows.
# ruff: noqa: PD008

import logging
import types

import pandas as pd

from src.models.hsm import common as com
from src.models.hsm import names as nam
from src.models.hsm.canada import Canada
from src.models.hsm.us_gas import USGasModule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GDP implicit price deflator (1987 base = 1.0)
# Approximate BEA values. Canada needs 2016; the US supply uses 2023 to turn 1987 $ into 2023 $.
# ---------------------------------------------------------------------------
_GDP_DEFLATOR = {
    1987: 1.000,
    1988: 1.034,
    1989: 1.071,
    1990: 1.103,
    1991: 1.134,
    1992: 1.160,
    1993: 1.190,
    1994: 1.213,
    1995: 1.240,
    1996: 1.266,
    1997: 1.291,
    1998: 1.305,
    1999: 1.326,
    2000: 1.358,
    2001: 1.385,
    2002: 1.410,
    2003: 1.438,
    2004: 1.469,
    2005: 1.505,
    2006: 1.545,
    2007: 1.580,
    2008: 1.618,
    2009: 1.619,
    2010: 1.643,
    2011: 1.673,
    2012: 1.698,
    2013: 1.722,
    2014: 1.746,
    2015: 1.754,
    2016: 1.771,
    2017: 1.803,
    2018: 1.835,
    2019: 1.861,
    2020: 1.871,
    2021: 1.941,
    2022: 2.031,
    2023: 2.085,
    2024: 2.130,
    2025: 2.170,
    2026: 2.210,
    2027: 2.250,
    2028: 2.290,
    2029: 2.330,
    2030: 2.370,
    2031: 2.411,
    2032: 2.452,
    2033: 2.494,
    2034: 2.537,
    2035: 2.581,
    2036: 2.625,
    2037: 2.670,
    2038: 2.716,
    2039: 2.762,
    2040: 2.810,
    2041: 2.858,
    2042: 2.906,
    2043: 2.956,
    2044: 3.006,
    2045: 3.057,
    2046: 3.109,
    2047: 3.162,
    2048: 3.216,
    2049: 3.270,
    2050: 3.326,
}


class HSMModule:
    """Runs C-HSM one year at a time.

    Parameters
    ----------
    input_path : str or pathlib.Path
        The C-HSM input folder (must contain ``setup.csv``).
    output_path : str or pathlib.Path
        Folder for the optional debug files.
    regions : list[str]
        Supply regions, from C-NGMM.
    supply_cost_tiers : dict[str, list[tuple[float, float]]]
        ``{region: [(capacity_bcf, cost_per_mmbtu), ...]}`` in low, medium, high cost order,
        from C-NGMM.
    henry_hub_prices : dict[int, float], optional
        Henry Hub price (1987 $/MMBtu) by calendar year. If empty, the reference path in
        ``hh_reference_path.csv`` is used, and 3.5 for any year it lacks.
    brent_prices : dict[int, float], optional
        Brent price (1987 $/bbl) by calendar year. If empty, the reference path in
        ``brent_reference_path.csv`` is used, and 4.0 for any year it lacks.
    brent_multiplier : float, optional
        Scales the Brent path for oil price scenarios. 1.0 leaves it unchanged.
    calibration_file : str, optional
        The ``(k, g, c)`` calibration file in ``input_path``. The reduced form and the well-level
        engine each need their own fit.
    onshore_engine_config : str or pathlib.Path, optional
        Settings file for the optional well-level engine (``us_onshore.py``). None (the default)
        runs the reduced form.
    """

    def __init__(
        self,
        input_path,
        output_path,
        regions,
        supply_cost_tiers,
        henry_hub_prices=None,
        brent_prices=None,
        brent_multiplier=1.0,
        calibration_file='us_gas_calibration.csv',
        onshore_engine_config=None,
    ):
        self.logger = logger

        # Brent scenario multiplier. It scales the final Brent path, whether it came from the
        # caller or from the reference file, so it survives later update_prices() calls. The
        # reference Brent used by the NA/AD split is not scaled, so the multiplier moves oil
        # away from its reference and changes associated gas, which is what a scenario wants.
        self.brent_multiplier = float(brent_multiplier)
        if abs(self.brent_multiplier - 1.0) > 1e-12:
            logger.info('HSM: Brent scenario multiplier ACTIVE: x%.3f', self.brent_multiplier)

        # Paths
        self.input_path = str(input_path).rstrip('/\\') + '/'
        self.output_path = str(output_path).rstrip('/\\') + '/'
        self.onshore_input_path = self.input_path + 'onshore/'
        self.offshore_input_path = self.input_path + 'offshore/'
        self.alaska_input_path = self.input_path + 'alaska/'
        self.canada_input_path = self.input_path + 'canada/'
        self.ngp_input_path = self.input_path + 'ngp/'
        self.hsm_var_output_path = self.output_path  # optional debug files go here

        # Read setup table
        self.setup_table = com.read_dataframe(self.input_path + 'setup.csv', index_col=0)

        self.base_price_year = int(self.setup_table.at[nam.base_price_year, nam.filename])
        self.aeo_year = int(self.setup_table.at[nam.aeo_year, nam.filename])
        self.history_year = int(self.setup_table.at[nam.history_year, nam.filename])
        self.technology_year = int(self.setup_table.at[nam.technology_year, nam.filename])
        self.final_aeo_year = int(self.setup_table.at[nam.final_aeo_year, nam.filename])

        self.steo_years = list(range(self.aeo_year - 1, self.aeo_year + 2))

        self.debug_switch = (
            str(self.setup_table.at[nam.debug_switch, nam.filename]).upper() == 'TRUE'
        )
        self.hsm_var_debug_switch = (
            str(self.setup_table.at[nam.hsm_var_debug_switch, nam.filename]).upper() == 'TRUE'
        )

        # Run settings that NEMS takes from its run scripts; fixed here
        self.integrated_switch = True  # keep values between years in hsm_vars
        self.param_fcrl = 1  # save those values after every year
        self.param_ncrl = 0
        self.current_year = self.history_year

        # Values carried from one year to the next (NEMS keeps these in pickle files)
        self.hsm_vars = types.SimpleNamespace()

        # Price DataFrames
        self._build_price_dfs(henry_hub_prices or {}, brent_prices or {})

        # ogsmout_ogcnpprd: Canada pipeline NG price (1987 $/MMBtu)
        # Multi-index (NUMCAN, year); fixed at 4.0 for all years, since nothing in C-HSM sets it.
        years = list(range(self.history_year, self.final_aeo_year + 1))
        idx = pd.MultiIndex.from_product([[1, 2], years], names=['NUMCAN', nam.year])
        self.ogsmout_ogcnpprd = pd.DataFrame({'value': 4.0}, index=idx)

        # GDP implicit price deflator (1987 base = 1.0)
        deflator_years = list(range(self.base_price_year, self.final_aeo_year + 2))
        self.rest_mc_jpgdp = pd.DataFrame(
            {'value': [_GDP_DEFLATOR.get(y, 1.0) for y in deflator_years]}, index=deflator_years
        )
        self.rest_mc_jpgdp.index.name = nam.year

        # Result containers (filled by Canada.report_results_unf)
        self.results_canada_na_prod = pd.DataFrame()
        self.results_canada_ad_prod = pd.DataFrame()
        self.results_canada_realized_na_prod = pd.DataFrame()

        # Instantiate Canada submodule
        self.canada = Canada(self)
        self.canada_switch = True

        # US gas supply.
        #
        # base_year is history_year (2023), the year the base capacities describe. It is not
        # base_price_year (1987), which is only the price unit: counting technology growth and
        # depletion from 1987 would add 36 phantom years of both.
        #
        # The optional well-level engine (us_onshore.py) is built only when a settings file is
        # given; otherwise the reduced form runs. A settings file that cannot be used is an
        # error, not a quiet fall back to the reduced form.
        self._onshore_engine = None
        if onshore_engine_config is not None:
            from src.models.hsm.us_onshore import OnshoreEngine

            self._onshore_engine = OnshoreEngine(
                onshore_path=self.onshore_input_path,
                mapping_path=self.input_path + 'mapping.csv',
                engine_config_path=str(onshore_engine_config),
            )

        self.us_gas = USGasModule(
            regions=regions,
            supply_cost_tiers=supply_cost_tiers,
            base_year=self.history_year,
            onshore_path=self.onshore_input_path,
            mapping_path=self.input_path + 'mapping.csv',
            calibration_path=self.input_path + calibration_file,
            ad_share_path=self.input_path + 'us_ad_gas_share.csv',
            ad_elasticity_path=self.input_path + 'us_ad_elasticity.csv',
            onshore_engine=self._onshore_engine,
        )
        self.us_gas_switch = True

        # Reference price paths, used by the NA/AD split in run_year: associated-dissolved gas
        # is priced off the reference gas path and responds only to Brent against its reference.
        self._hh_reference = self._load_hh_reference()
        self._brent_reference = self._load_brent_reference()

        # Production of wells already producing, and their crude oil, for two extra outputs
        # (hsm_us_legacy_gas.csv, hsm_us_crude.csv). This does not feed the supply capacity.
        # If the engine is on, it already holds the same decks, so reuse it.
        if getattr(self, '_onshore_engine', None) is not None:
            self.onshore_legacy = self._onshore_engine
        else:
            try:
                from src.models.hsm.us_onshore import OnshoreLegacy

                self.onshore_legacy = OnshoreLegacy(
                    onshore_path=self.onshore_input_path,
                    mapping_path=self.input_path + 'mapping.csv',
                )
            except Exception as exc:  # noqa: BLE001 - the two extra outputs are optional
                logger.warning(
                    'HSM: OnshoreLegacy not available (%s) — existing-well outputs skipped', exc
                )
                self.onshore_legacy = None

        # US gas capacity, BCF: keyed (region, cost_tier, gas_type, year) with the NA/AD split,
        # which the shipped inputs switch on, or (region, cost_tier, year) without it.
        self.results_us_gas_capacity: dict = {}

        # Canada references for a coupled run. can_na_reference.csv is Canada's NA production at
        # the AEO2026 reference prices. can_us_export_share.csv is the share of extra Canadian
        # NA production that reaches the US (AEO2026 Table 61 net pipeline imports over
        # reference production). A coupled run would send export_share x (NA - reference) to
        # C-NGMM, so Canada adds nothing at reference prices: net imports are already in
        # C-NGMM's own data, and sending gross production would count them twice. If the files
        # are missing, both stay None and nothing is sent.
        self.canada_na_reference = None  # dict[(NUMCAN, year)] -> BCF, or None
        self.canada_export_share = None  # dict[year] -> share, or None
        try:
            _ref = pd.read_csv(
                self.input_path + 'can_na_reference.csv', comment='#', encoding='utf-8'
            )
            self.canada_na_reference = {
                (int(r.NUMCAN), int(r.year)): float(r.na_reference_bcf) for r in _ref.itertuples()
            }
            _sh = pd.read_csv(
                self.input_path + 'can_us_export_share.csv', comment='#', encoding='utf-8'
            )
            self.canada_export_share = {
                int(r.year): float(r.export_share) for r in _sh.itertuples()
            }
            logger.info(
                'HSM: Canada coupling reference loaded (%d rows, %d shares)',
                len(self.canada_na_reference),
                len(self.canada_export_share),
            )
        except Exception as exc:  # noqa: BLE001 - the coupling references are optional
            logger.warning(
                'HSM: Canada reference files not loaded (%s) — '
                'Canada supply will not be sent in a coupled run',
                exc,
            )

    # ------------------------------------------------------------------
    # Price helpers
    # ------------------------------------------------------------------

    def _build_price_dfs(self, henry_hub_prices, brent_prices):
        """Build ``rest_brent_price`` and ``rest_henry_hub`` from dicts."""
        years = list(range(self.history_year, self.final_aeo_year + 1))

        # Brent: if the caller gives no prices, use brent_reference_path.csv (1987 $/bbl), and
        # 4.0 for any year it lacks.
        if not brent_prices:
            bref = self._load_brent_reference()
            self.rest_brent_price = pd.DataFrame(
                {'value': [bref.get(y, 4.0) for y in years]}, index=years
            )
        else:
            self.rest_brent_price = pd.DataFrame(
                {'value': [brent_prices.get(y, 4.0) for y in years]}, index=years
            )
        # Apply the Brent scenario multiplier to the final path. The getattr default covers a
        # subclass that calls this before __init__ has set the attribute.
        _bmult = getattr(self, 'brent_multiplier', 1.0)
        if abs(_bmult - 1.0) > 1e-12:
            self.rest_brent_price['value'] = self.rest_brent_price['value'] * _bmult
        self.rest_brent_price.index.name = nam.year

        # Henry Hub: if the caller gives no prices, use the AEO2026 reference path in
        # hh_reference_path.csv (1987 $/MMBtu), and 3.5 for any year it lacks. A flat 3.5 is
        # about twice a realistic 1987 $ price, so the reference path matters.
        if not henry_hub_prices:
            ref = self._load_hh_reference()
            self.rest_henry_hub = pd.DataFrame(
                {'value': [ref.get(y, 3.5) for y in years]}, index=years
            )
        else:
            self.rest_henry_hub = pd.DataFrame(
                {'value': [henry_hub_prices.get(y, 3.5) for y in years]}, index=years
            )
        self.rest_henry_hub.index.name = nam.year

    def _load_hh_reference(self) -> dict:
        """Load the AEO2026 reference HH path (1987$); {} if the file is absent/unreadable."""
        try:
            df = pd.read_csv(
                self.input_path + 'hh_reference_path.csv', comment='#', encoding='utf-8'
            )
            ref = dict(
                zip(
                    df['year'].astype(int),
                    df['henry_hub_1987_per_mmbtu'].astype(float),
                    strict=True,
                )
            )
            logger.info('HSM: default HH prices from hh_reference_path.csv (%d years)', len(ref))
            return ref
        except Exception as exc:  # noqa: BLE001 - an unusable file falls back to 3.5
            logger.warning('HSM: no hh_reference_path.csv (%s) — flat 3.5 default retained', exc)
            return {}

    def _load_brent_reference(self) -> dict:
        """Load the AEO2026 reference Brent path (1987$/bbl); {} if the file is absent."""
        try:
            df = pd.read_csv(
                self.input_path + 'brent_reference_path.csv', comment='#', encoding='utf-8'
            )
            ref = dict(
                zip(
                    df['year'].astype(int),
                    df['brent_1987_per_bbl'].astype(float),
                    strict=True,
                )
            )
            logger.info(
                'HSM: default Brent prices from brent_reference_path.csv (%d years)', len(ref)
            )
            return ref
        except Exception:  # noqa: BLE001 - no Brent reference file is a normal case
            return {}

    def update_prices(self, henry_hub_prices, brent_prices):
        """Update price paths before the next run-year call.

        Parameters
        ----------
        henry_hub_prices : dict[int, float]
        brent_prices : dict[int, float]
        """
        self._build_price_dfs(henry_hub_prices, brent_prices)

    # ------------------------------------------------------------------
    # Setup / run
    # ------------------------------------------------------------------

    def reset_canada(self):
        """Reset the year-to-year state for a fresh run over the years (no file I/O).

        Clears ``hsm_vars``, sets the current year back to ``history_year``, and empties the
        result containers, US gas included. The Canada submodule's own tables are not reset
        here: restore them from a copy taken right after ``setup_canada()`` first.
        """
        self.hsm_vars = types.SimpleNamespace()
        self.current_year = self.history_year
        self.results_canada_na_prod = pd.DataFrame()
        self.results_canada_ad_prod = pd.DataFrame()
        self.results_canada_realized_na_prod = pd.DataFrame()
        # Reset US gas too
        if hasattr(self, 'us_gas'):
            self.reset_us_gas()

    def reset_us_gas(self):
        """Clear US gas results for a new Gauss-Seidel iteration."""
        self.us_gas.reset()
        self.results_us_gas_capacity = {}

    def setup_canada(self):
        """Call once before the year loop to initialise the Canada submodule."""
        temp_filename = self.setup_table.at[nam.can_setup, nam.filename]
        self.canada.setup(temp_filename)

    def run_year(self, year):
        """Run C-HSM for one calendar year.

        Parameters
        ----------
        year : int
            Calendar year to simulate.
        """
        self.current_year = year
        if self.canada_switch:
            self.canada.run()
            self.canada.report_results_unf()

        if self.us_gas_switch:
            # Regional wellhead price = Henry Hub + a fixed regional basis.
            hh_price = (
                self.rest_henry_hub.at[year, 'value'] if year in self.rest_henry_hub.index else 3.5
            )
            # Regional wellhead differentials ($/MMBtu relative to Henry Hub)
            REGIONAL_BASIS = {
                'new_england': +0.80,
                'middle_atlantic': +0.20,
                'east_north_central': +0.10,
                'west_north_central': +0.00,
                'south_atlantic': -0.10,
                'east_south_central': -0.20,
                'west_south_central': -0.50,  # Gulf Coast = HH minus basis
                'mountain': -0.30,
                'pacific': +0.15,
            }
            # rest_henry_hub is in 1987 $/MMBtu, but the US supply's base price
            # (BASE_WELLHEAD_PRICE_PER_MMBTU = 2.50) and REGIONAL_BASIS are 2023 $. Convert with
            # the fixed 2023 deflator so the price stays real; using each year's deflator would
            # feed inflation in as if it were a price signal. Passing 1987 $ straight in would
            # under-price US supply by about 40%.
            hh_price_2023 = hh_price * _GDP_DEFLATOR[2023]  # 1987$ -> real 2023$
            prices = {
                (r, year): hh_price_2023 + REGIONAL_BASIS.get(r, 0.0) for r in self.us_gas.regions
            }
            # The optional engine needs whole price paths, because wells drilled in one year
            # produce in later years. Build them for every year with the same conversions.
            if getattr(self, '_onshore_engine', None) is not None:
                prices = {}
                oil_prices_full = {}
                for y in self.rest_henry_hub.index:
                    hh_y = self.rest_henry_hub.at[y, 'value'] * _GDP_DEFLATOR[2023]
                    for r in self.us_gas.regions:
                        prices[(r, y)] = hh_y + REGIONAL_BASIS.get(r, 0.0)
                for y in self.rest_brent_price.index:
                    b_y = self.rest_brent_price.at[y, 'value'] * _GDP_DEFLATOR[2023]
                    if b_y >= 20.0:
                        for r in self.us_gas.regions:
                            oil_prices_full[(r, y)] = b_y
            # Brent comes in as 1987 $/bbl. Convert it with the same fixed 2023 deflator as gas
            # (BASE_OIL_PRICE_PER_BBL = 65 is 2023 $).
            brent_1987 = (
                self.rest_brent_price.at[year, 'value']
                if year in self.rest_brent_price.index
                else 4.0
            )
            brent_nominal = brent_1987 * _GDP_DEFLATOR[2023]  # 1987$ -> real 2023$
            # Only pass oil prices on if they look like real crude prices (at least $20/bbl);
            # otherwise the US supply uses its base oil price.
            if brent_nominal >= 20.0:
                oil_prices = {(r, year): brent_nominal for r in self.us_gas.regions}
            else:
                oil_prices = None
            # The engine takes the whole oil path
            if getattr(self, '_onshore_engine', None) is not None:
                oil_prices = oil_prices_full or None

            # Reference prices for the NA/AD split, built the same way as the prices above. If
            # a reference year is missing, the split uses the actual price for that year, which
            # makes it neutral.
            ref_prices = None
            if self._hh_reference and year in self._hh_reference:
                ref_hh_2023 = self._hh_reference[year] * _GDP_DEFLATOR[2023]
                ref_prices = {
                    (r, year): ref_hh_2023 + REGIONAL_BASIS.get(r, 0.0) for r in self.us_gas.regions
                }
            ref_oil_prices = None
            if self._brent_reference and year in self._brent_reference:
                ref_brent_nominal = self._brent_reference[year] * _GDP_DEFLATOR[2023]
                if ref_brent_nominal >= 20.0:  # same $20 check as the actual Brent above
                    ref_oil_prices = {(r, year): ref_brent_nominal for r in self.us_gas.regions}

            self.us_gas.run_year(
                year,
                prices,
                oil_prices=oil_prices,
                ref_prices=ref_prices,
                ref_oil_prices=ref_oil_prices,
            )
            self.results_us_gas_capacity.update(self.us_gas.get_capacity_updates([year]))
