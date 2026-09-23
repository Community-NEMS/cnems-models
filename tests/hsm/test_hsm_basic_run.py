"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

A basic test run for C-HSM, in the spirit of tests/natural_gas/test_ng_basic_run.py: it runs the
model from tests/hsm/basic_hsm_config.toml and checks all six result files against the copies
in tests/hsm/expected/, so that an unintended change to the code or the inputs shows up as a
failure.

dev notes:
1.  The expected files were captured from the standalone C-HSM run on the inputs in input/hsm/
    and C-NGMM's input/natural_gas/ng_supply_cost_tiers.csv, with the AEO2026 reference Henry
    Hub path. If any of those inputs change on purpose, re-capture the files and say why in the
    commit.
2.  C-HSM reads C-NGMM's cost tiers, so a change to input/natural_gas/ng_supply_cost_tiers.csv
    moves these results too. That is intended: the two models must share one set of tiers.
"""

from pathlib import Path

import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.common.integrated_model_sequencer import IterationStatus
from src.models.hsm.data import load_henry_hub_path, load_ng_supply_side
from src.models.hsm.hsm_config import HSMConfig
from src.models.hsm.hsm_model import HSMModel
from src.models.hsm.sequencer import HSMSequencer, main
from src.models.hsm.us_gas import USGasModule

CONFIG_PATH = Path(PROJECT_ROOT, 'tests/hsm/basic_hsm_config.toml')
EXPECTED_DIR = Path(PROJECT_ROOT, 'tests/hsm/expected')
RESULT_FILES = [
    'hsm_us_gas_capacity.csv',
    'hsm_us_crude.csv',
    'hsm_us_legacy_gas.csv',
    'hsm_canada_na_prod.csv',
    'hsm_canada_ad_prod.csv',
    'hsm_canada_realized_na_prod.csv',
]


@pytest.fixture(scope='module')
def config_set() -> tuple[CommonConfig, HSMConfig]:
    """The (CommonConfig, HSMConfig) pair from the test config."""
    common_config, remainder = CommonConfig.from_toml(CONFIG_PATH)
    return common_config, HSMConfig(**remainder.pop('hsm'))


@pytest.fixture(scope='module')
def result_dir(config_set, tmp_path_factory) -> Path:
    """Run C-HSM once and write its results to a temporary folder."""
    common_config, hsm_config = config_set
    common_config = common_config.model_copy()
    common_config.output_path = tmp_path_factory.mktemp('hsm_output')
    common_config.make_scenario_dir()
    sequencer = HSMSequencer()
    sequencer.build_model(common_config, hsm_config)
    status = sequencer.solve_model()
    assert status is IterationStatus.USABLE, f'run failed with status {status}'
    sequencer.full_postprocess()
    return sequencer.output_dir()


class TestHSMBasicRun:
    """The standalone run reproduces the expected results."""

    @pytest.mark.parametrize('filename', RESULT_FILES)
    def test_result_matches_expected(self, result_dir: Path, filename: str) -> None:
        """Same columns, same rows, and values equal to within 1e-9.

        Parameters
        ----------
        result_dir : Path
            Folder holding this run's results.
        filename : str
            The result file to check.
        """
        got = pd.read_csv(result_dir / filename)
        want = pd.read_csv(EXPECTED_DIR / filename)
        assert list(got.columns) == list(want.columns), f'{filename}: columns differ'
        assert len(got) == len(want), f'{filename}: {len(got)} rows, expected {len(want)}'

        # Match rows on the non-float columns, then compare the float columns.
        keys = [c for c in want.columns if not pd.api.types.is_float_dtype(want[c])]
        values = [c for c in want.columns if c not in keys]
        merged = want.merge(got, on=keys, how='outer', suffixes=('_want', '_got'), indicator=True)
        unmatched = merged[merged['_merge'] != 'both']
        assert unmatched.empty, f'{filename}: {len(unmatched)} rows do not match on {keys}'
        for col in values:
            assert merged[f'{col}_got'].to_numpy() == pytest.approx(
                merged[f'{col}_want'].to_numpy(), rel=1e-9, abs=1e-9
            ), f'{filename}: column {col} differs'


class TestInputChecks:
    """Bad inputs stop the run instead of producing quietly wrong numbers."""

    def test_cost_tiers_must_have_three_tiers(self) -> None:
        """A region with a missing cost tier is rejected rather than shifting the others."""
        with pytest.raises(ValueError, match='expected 3'):
            USGasModule(
                regions=['new_england'],
                supply_cost_tiers={'new_england': [(22.5, 2.33), (17.5, 3.26)]},
            )

    def test_henry_hub_path_must_cover_every_year(self, config_set, tmp_path: Path) -> None:
        """A price path with a gap is rejected; the module would otherwise use 3.5 there."""
        _, hsm_config = config_set
        regions, supply_cost_tiers = load_ng_supply_side(hsm_config.ng_input_path)
        prices = {y: 1.6 for y in range(2023, 2051) if y != 2030}
        with pytest.raises(ValueError, match='missing model years'):
            HSMModel(hsm_config, regions, supply_cost_tiers, prices, output_path=tmp_path)

    def test_price_update_must_cover_every_year(self, config_set, tmp_path: Path) -> None:
        """A partial price update is rejected; an empty one means the reference path."""
        _, hsm_config = config_set
        regions, supply_cost_tiers = load_ng_supply_side(hsm_config.ng_input_path)
        full = load_henry_hub_path(hsm_config.input_path / hsm_config.henry_hub_file)
        model = HSMModel(hsm_config, regions, supply_cost_tiers, full, output_path=tmp_path)
        with pytest.raises(ValueError, match='Henry Hub path is missing'):
            model.update_prices({2023: 1.6}, {})
        with pytest.raises(ValueError, match='Brent path is missing'):
            model.update_prices({}, {2023: 30.0})
        with pytest.raises(ValueError, match='non-finite prices'):
            model.update_prices({**full, 2030: float('nan')}, {})
        model.update_prices({}, {})  # both reference paths

    @pytest.mark.parametrize(
        'body,message',
        [
            ('year,hh_1987_usd_per_mmbtu\n', 'has no prices'),
            ('year,hh_1987_usd_per_mmbtu\n2023,1.6\n2024,\n', 'non-finite prices'),
            ('year,price\n2023,1.6\n', 'no Henry Hub price column'),
            ('year,hh_1987_usd_per_mmbtu\n2023,\n2023,1.6\n', 'repeats years'),
        ],
        ids=['no rows', 'blank price', 'wrong column', 'repeated year'],
    )
    def test_henry_hub_file_is_checked(self, tmp_path: Path, body: str, message: str) -> None:
        """An empty file, a blank price or a wrong column name is rejected when read.

        Parameters
        ----------
        tmp_path : Path
            Temporary folder for the test file.
        body : str
            The CSV text.
        message : str
            Text the error must contain.
        """
        path = tmp_path / 'hh.csv'
        path.write_text(body, encoding='utf-8')
        with pytest.raises(ValueError, match=message):
            load_henry_hub_path(path)

    @pytest.mark.parametrize(
        'body,message',
        [
            ('region,k\nnew_england,1.0\n', "has no \\['g'\\] column"),
            ('region,k,g,c\nnew_england,1.0,0.0,nan\n', 'non-finite'),
            ('region,k,g,c\nnew_england,1.0,0.0,0.0\n', 'has no row for'),
        ],
        ids=['missing column', 'non-finite value', 'missing region'],
    )
    def test_calibration_file_is_checked(self, tmp_path: Path, body: str, message: str) -> None:
        """A calibration file with a missing column, a bad value or a missing region is rejected.

        Parameters
        ----------
        tmp_path : Path
            Temporary folder for the test file.
        body : str
            The CSV text.
        message : str
            Text the error must contain.
        """
        path = tmp_path / 'calibration.csv'
        path.write_text(body, encoding='utf-8')
        with pytest.raises(ValueError, match=message):
            USGasModule(
                regions=['new_england', 'pacific'],
                supply_cost_tiers={
                    'new_england': [(22.5, 2.33), (17.5, 3.26), (10.0, 3.83)],
                    'pacific': [(48.4, 3.0), (37.6, 4.2), (21.5, 4.93)],
                },
                calibration_path=str(path),
            )

    def test_file_settings_must_be_names_in_the_input_folder(self, tmp_path: Path) -> None:
        """An absolute path for a C-HSM input file is rejected at the config."""
        with pytest.raises(ValueError, match='must be a file name'):
            HSMConfig(
                input_path='input/hsm',
                ng_input_path='input/natural_gas',
                calibration_file=str(Path(PROJECT_ROOT, 'input/hsm/us_gas_calibration.csv')),
            )

    @pytest.mark.parametrize('multiplier', [0.0, -1.0, float('nan'), float('inf')])
    def test_brent_multiplier_must_be_finite_and_positive(self, multiplier: float) -> None:
        """Zero, negative, NaN and infinite multipliers are all rejected."""
        with pytest.raises(ValueError, match='finite and positive'):
            HSMConfig(
                input_path='input/hsm',
                ng_input_path='input/natural_gas',
                brent_multiplier=multiplier,
            )


class TestSequencerMain:
    """``main()`` runs the model and writes results."""

    def test_returns_zero_and_postprocesses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The standalone entry point finishes and calls the postprocessor once."""
        touched: list[str] = []
        monkeypatch.setattr(
            HSMSequencer, 'full_postprocess', lambda self, **kwargs: touched.append('postprocess')
        )
        assert main() == 0
        assert touched == ['postprocess']
