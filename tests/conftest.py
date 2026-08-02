"""
Re-useable fixtures etc. for tests

See https://docs.pytest.org/en/7.1.x/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import requests
from loguru import logger

from openscm_zenodo.zenodo import ZenodoDomain, get_token_env_vars, resolve_token

if TYPE_CHECKING:
    import _pytest

TEST_DATA_ROOT_DIR = Path(__file__).parent / "test-data"


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    if not TEST_DATA_ROOT_DIR.exists():
        pytest.skip("test data required")

    return TEST_DATA_ROOT_DIR


ZENODO_TOKEN_ENV_VARS = get_token_env_vars(ZenodoDomain.sandbox)
"""
Environment variables a token for the tests which write can come from

Everything which writes goes to the sandbox, so this asks the library where a
*sandbox* token comes from rather than naming a variable itself. Naming one
would mean the skip condition could disagree with `resolve_token` about whether
we have a token, which shows up as the whole suite skipping in silence.
"""

ZENODO_TOKEN_AVAILABLE = (
    resolve_token(zenodo_domain=ZenodoDomain.sandbox).token is not None
)


def pytest_runtest_setup(item: _pytest.python.Function) -> None:
    for mark in item.iter_markers():
        if mark.name == "zenodo_token" and not ZENODO_TOKEN_AVAILABLE:
            pytest.skip(
                "no Zenodo sandbox token: set "
                + " or ".join(f"`{var}`" for var in ZENODO_TOKEN_ENV_VARS)
            )


def build_response(  # noqa: PLR0913
    status_code=200,
    json_body=None,
    text=None,
    content=None,
    url="https://zenodo.org/api/records/1234",
    method="GET",
):
    """
    Build a response, as if it had come back from Zenodo
    """
    response = requests.models.Response()
    response.status_code = status_code
    response.url = url
    response.reason = "Made up"
    response.request = requests.models.Request(method=method, url=url).prepare()

    if json_body is not None:
        response._content = json.dumps(json_body).encode()
        response.headers["Content-Type"] = "application/json"

    elif text is not None:
        response._content = text.encode()

    elif content is not None:
        response._content = content

    else:
        response._content = b""

    # Tells `iter_content` to serve the body we just set,
    # rather than trying to read from a raw stream which is not there
    response._content_consumed = True

    return response


class RecordingSession(requests.Session):
    """
    Session which records the requests it is given and returns canned responses

    This lets us check what we sent to Zenodo without sending anything.
    """

    def __init__(self, responses=None):
        super().__init__()
        self.calls = []
        self.responses = list(responses) if responses is not None else []

    def request(self, method, url, **kwargs):
        """
        Record a request and return the next canned response
        """
        self.calls.append({"method": method, "url": url, **kwargs})

        if self.responses:
            return self.responses.pop(0)

        return build_response(url=url, method=method)


@pytest.fixture
def make_response():
    """
    Get a factory for responses, as if they had come back from Zenodo
    """
    return build_response


@pytest.fixture
def make_recording_session():
    """
    Get a factory for sessions which record requests and return canned responses
    """
    return RecordingSession


@pytest.fixture
def log_messages():
    """
    Capture the messages logged by `openscm_zenodo`

    We use loguru, which does not go through the standard library's logging,
    so pytest's `caplog` does not see our messages.
    """
    res = []

    handler_id = logger.add(res.append, level="DEBUG", format="{message}")
    logger.enable("openscm_zenodo")

    yield res

    logger.disable("openscm_zenodo")
    logger.remove(handler_id)


@pytest.fixture
def no_token_in_env(monkeypatch):
    """
    Make sure no ambient token leaks into a test
    """
    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    monkeypatch.delenv("ZENODO_SANDBOX_TOKEN", raising=False)
