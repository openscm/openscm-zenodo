import os.path

import pytest

TEST_DATA_ROOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "test-data"
)


@pytest.fixture(scope="session")
def test_data_dir():
    if not os.path.isdir(TEST_DATA_ROOT_DIR):
        pytest.skip("test data required")
    return TEST_DATA_ROOT_DIR


ZENODO_TOKEN_AVAILABLE = "ZENODO_TOKEN" in os.environ


def pytest_runtest_setup(item):
    for mark in item.iter_markers():
        if mark.name == "zenodo_token" and not ZENODO_TOKEN_AVAILABLE:
            pytest.skip("`ZENODO_TOKEN` environment variable not set")
