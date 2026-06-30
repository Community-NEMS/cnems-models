"""
Created as part of the C-NEMS Project

Written by:  J. F. Hyink
Contact:  jeff@westernspark.us
Created on:  6/16/26

This is the top-level configuration parser.

The general concept is to read a config toml file by segments as needed.  This module will read
the "common" section which determines the run mode and which modules to run etc.

the remainder of the namespace gathered from input is then passed to subsequent modules for
parsing.  Doing this:
- allows for independent validation of config objects
- independence of configs that may grow fairly large in the future

"""

from pathlib import Path

from src.common.common_config import CommonConfig


def parse_config_file(path_to_config: Path) -> tuple[CommonConfig, dict]:
    """
    Parse a config file on the path given.  Render the "common" config and retain the remainder
    for subsequent parsing as needed by individual modules.
    """
    common_config, remainder = CommonConfig.from_toml(path_to_config)

    return common_config, remainder
