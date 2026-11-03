"""
Created as part of the C-NEMS Project.

Written by:  Sauleh Siddiqui
Contact:  sauleh@american.edu
Created on:  9/22/26

Settings for C-HSM, read from the ``[hsm]`` section of a run config TOML.
"""

import math
import tomllib
from logging import getLogger
from pathlib import Path

from pydantic import ValidationError, model_validator

from definitions import PROJECT_ROOT
from src.common.common_config import ModelConfig

logger = getLogger(__name__)


class HSMConfig(ModelConfig):
    """Settings from the ``[hsm]`` TOML section, controlling C-HSM.

    Attributes
    ----------
    input_path : Path
        The C-HSM input folder (``input/hsm``), relative to the project root.
    ng_input_path : Path
        C-NGMM's input folder (``input/natural_gas``). C-HSM reads the supply regions and the
        base capacity of each cost tier from here, so the two models always agree on them.
    henry_hub_file : str
        A Henry Hub price path in ``input_path`` (1987 $/MMBtu by calendar year), used as the
        price C-HSM responds to. It must give a price for every model year. Defaults to the
        reference path, ``hh_reference_path.csv``.
    brent_multiplier : float
        Scales the whole Brent price path, for oil price scenarios. 1.0 leaves it unchanged.
    onshore_engine_file : str or None
        Settings file in ``input_path`` for the optional well-level engine (``us_onshore.py``),
        normally ``us_onshore_engine.csv``. Left out (the default), C-HSM runs the reduced form.
    calibration_file : str
        The ``(k, g, c)`` calibration in ``input_path``. The reduced form and the engine each need
        their own fit: ``us_gas_calibration.csv`` (default) and ``us_gas_calibration_engine.csv``.
    coupling_reference_file : str
        The reference Henry Hub path in ``input_path``, which a coupled run scales by C-NGMM's hub
        price over the reference price:  ``year``, ``hh_1987_usd_per_mmbtu``, and
        ``hh_<year>usd_per_mmbtu`` in ``coupling_reference_dollar_year`` USD.
    coupling_reference_dollar_year : int
        The dollar year of ``hh_<year>usd_per_mmbtu``; a coupled run requires
        ``PRICE_DOLLAR_YEAR`` (``src/models/natural_gas/constants.py``).
    """

    input_path: Path
    ng_input_path: Path
    henry_hub_file: str = 'hh_reference_path.csv'
    brent_multiplier: float = 1.0
    onshore_engine_file: str | None = None
    calibration_file: str = 'us_gas_calibration.csv'
    coupling_reference_file: str = 'henry_hub_path_aeo2026_1987usd.csv'
    coupling_reference_dollar_year: int = 2025

    @model_validator(mode='after')
    def check_paths(self) -> HSMConfig:
        """Resolve both folders against PROJECT_ROOT and check that each file setting exists.

        The file settings are ``henry_hub_file``, ``calibration_file``, ``onshore_engine_file``
        (when set) and ``coupling_reference_file``, each a file name inside ``input_path``.
        """
        self.input_path = PROJECT_ROOT / self.input_path
        if not self.input_path.is_dir():
            raise ValueError(f'Input path {self.input_path} is not a directory')
        self.ng_input_path = PROJECT_ROOT / self.ng_input_path
        if not self.ng_input_path.is_dir():
            raise ValueError(f'Natural gas input path {self.ng_input_path} is not a directory')
        for name in (
            self.henry_hub_file,
            self.calibration_file,
            self.onshore_engine_file,
            self.coupling_reference_file,
        ):
            if name is None:
                continue
            if Path(name).is_absolute():
                raise ValueError(f'{name} must be a file name inside {self.input_path}')
            if not (self.input_path / name).is_file():
                raise ValueError(f'{name} not found in {self.input_path}')
        return self

    @model_validator(mode='after')
    def check_brent_multiplier(self):
        """A price multiplier has to be a finite, positive number."""
        if not math.isfinite(self.brent_multiplier) or self.brent_multiplier <= 0:
            raise ValueError(
                f'brent_multiplier must be finite and positive, got {self.brent_multiplier}'
            )
        return self

    @classmethod
    def from_toml(cls, path: Path) -> HSMConfig:
        """Parse the ``[hsm]`` section of ``path`` into an ``HSMConfig``."""
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        try:
            config = HSMConfig(**data['hsm'])
        except KeyError:
            logger.error('[hsm] section not found in %s', path)
            raise
        except ValidationError as e:
            for error in e.errors():
                logger.error(error)
            raise
        logger.info('Created HSMConfig object from TOML file: %s', path)
        return config
