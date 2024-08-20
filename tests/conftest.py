"""
Re-useable fixtures etc. for tests

See https://docs.pytest.org/en/7.1.x/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import _pytest

TEST_DATA_ROOT_DIR = Path(__file__).parent / "test-data"


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    if not TEST_DATA_ROOT_DIR.exists():
        pytest.skip("test data required")

    return TEST_DATA_ROOT_DIR


ZENODO_TOKEN_AVAILABLE = "ZENODO_TOKEN" in os.environ


def pytest_runtest_setup(item: _pytest.python.Function) -> None:
    for mark in item.iter_markers():
        if mark.name == "zenodo_token" and not ZENODO_TOKEN_AVAILABLE:
            pytest.skip("`ZENODO_TOKEN` environment variable not set")
