"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  10/2/26

A basic test run for C-CDM, in the spirit of tests/electric/test_basic_run.py: it runs the model
from tests/cdm/basic_cdm_config.toml and checks the results against the copies in
tests/cdm/expected/, so that an unintended change to the code or the inputs shows up as a
failure. A second test pins how closely the modeled servers follow AEO2026 in both cases.

dev notes:
1.  The expected files were captured from the standalone run on the inputs in input/cdm/. If
    those inputs change on purpose, re-capture the files and say why in the commit.
2.  The electricity and natural gas files are compared after summing over services, which keeps
    the expected files small; the summary still checks every service nationally.
3.  The server ratios are not tuned. They drift from 0.999 in 2025 to about 0.957 in 2050. C-CDM
    keeps each building type's 2018 division shares, while NEMS also applies its price response
    to servers; what causes the drift is not established. A change in the ratios means the server
    equation, the floorspace or the inputs moved.
"""

from pathlib import Path

import pandas as pd
import pytest

from definitions import PROJECT_ROOT
from src.common.common_config import CommonConfig
from src.models.cdm.cdm_config import CDMConfig
from src.models.cdm.cdm_model import CDMModel
from src.models.cdm.data import load_all
from src.models.cdm.sequencer import CDMSequencer

CONFIG_PATH = Path(PROJECT_ROOT, 'tests/cdm/basic_cdm_config.toml')
EXPECTED_DIR = Path(PROJECT_ROOT, 'tests/cdm/expected')
BY_BUILDING = {
    'cdm_electricity.csv': ('cdm_electricity_by_building.csv', 'electricity_twh'),
    'cdm_natural_gas.csv': ('cdm_natural_gas_by_building.csv', 'natural_gas_tbtu'),
}


@pytest.fixture(scope='module')
def result_dir(tmp_path_factory) -> Path:
    """Run C-CDM once from the test config and write its results to a temporary folder."""
    common_config, remainder = CommonConfig.from_toml(CONFIG_PATH)
    common_config = common_config.model_copy()
    common_config.output_path = tmp_path_factory.mktemp('cdm_output')
    common_config.make_scenario_dir()
    sequencer = CDMSequencer()
    sequencer.build_model(common_config, CDMConfig(**remainder.pop('cdm')))
    sequencer.solve_model()
    sequencer.full_postprocess()
    return common_config.output_folder / 'cdm'


def _by_building(path: Path, value: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df.groupby(['region', 'building_type', 'year'], as_index=False)[value].sum()


@pytest.mark.parametrize(
    'name', ['cdm_summary.csv', 'cdm_data_centers.csv', *BY_BUILDING], ids=lambda n: n[:-4]
)
def test_basic_run(result_dir: Path, name: str) -> None:
    """Each result file matches its expected copy to a relative 1e-6."""
    if name in BY_BUILDING:
        expected_name, value = BY_BUILDING[name]
        found = _by_building(result_dir / name, value)
        expected = pd.read_csv(EXPECTED_DIR / expected_name)
    else:
        found = pd.read_csv(result_dir / name)
        expected = pd.read_csv(EXPECTED_DIR / name)
    pd.testing.assert_frame_equal(found, expected, check_exact=False, rtol=1e-6)


@pytest.mark.parametrize(
    'case,ratios',
    [
        ('reference', {2025: 0.9994, 2030: 0.9898, 2040: 0.9744, 2050: 0.9568}),
        ('high_electricity_demand', {2025: 0.9994, 2030: 0.9899, 2040: 0.9747, 2050: 0.9575}),
    ],
)
def test_servers_follow_aeo(case: str, ratios: dict[int, float]) -> None:
    """National modeled servers over AEO2026 Table 5 'Data Center Servers', per year."""
    config = CDMConfig(input_path=Path('input/cdm'), case=case)
    model = CDMModel(load_all(config.input_path), config)
    res = model.run()
    for year, ratio in ratios.items():
        servers = sum(
            v for (_r, _b, s, y), v in res.electricity.items()
            if s == 'data_center_servers' and y == year
        )  # fmt: skip
        aeo = 1000.0 * model.data.aeo_consumption[case, 'electricity', 'data_center_servers', year]
        assert servers / aeo == pytest.approx(ratio, abs=1e-4), f'{case} {year}'
