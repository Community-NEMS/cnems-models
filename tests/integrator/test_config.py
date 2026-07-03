from logging import getLogger
from pathlib import Path

import tomllib

from definitions import PROJECT_ROOT

logger = getLogger(__name__)


def test_config_setup():
    """test to ensure changes to configurations are consistent"""

    # list of keys in run_config
    config_path = Path(PROJECT_ROOT, 'src/common', 'run_config.toml')
    with open(config_path, 'rb') as f:
        data = tomllib.load(f)
    config_list = []
    for key in data.keys():
        config_list.append(key)

    # list of keys in run_config_template
    template_path = Path(PROJECT_ROOT, 'src/common', 'run_config_template.toml')
    with open(template_path, 'rb') as f:
        data = tomllib.load(f)
    template_list = []
    for key in data.keys():
        template_list.append(key)

    assert config_list == template_list
