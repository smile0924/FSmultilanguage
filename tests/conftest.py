from pathlib import Path

import pytest

from fslang.config import load_config
from fslang.excel import load_sheet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def tow_config():
    return load_config(ROOT / "configs" / "tree_of_wild.json")


@pytest.fixture(scope="session")
def tow_sheet(tow_config):
    return load_sheet(tow_config)
