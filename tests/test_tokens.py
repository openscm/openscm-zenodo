"""
Tests of token resolution and `.env` handling
"""

from __future__ import annotations

import os

import pytest

from openscm_zenodo.exceptions import MissingTokenError
from openscm_zenodo.zenodo import (
    ZenodoDomain,
    get_zenodo_domain_url,
    load_env_file,
    resolve_token,
)

PROD_ENV = {"ZENODO_TOKEN": "prod-token"}
BOTH_ENV = {"ZENODO_TOKEN": "prod-token", "ZENODO_SANDBOX_TOKEN": "sandbox-token"}


@pytest.mark.parametrize(
    "token, zenodo_domain, env, exp",
    (
        pytest.param(
            "explicit",
            ZenodoDomain.production,
            BOTH_ENV,
            "explicit",
            id="explicit-beats-everything",
        ),
        pytest.param(
            "explicit",
            ZenodoDomain.sandbox,
            BOTH_ENV,
            "explicit",
            id="explicit-beats-everything-sandbox",
        ),
        pytest.param(
            None,
            ZenodoDomain.sandbox,
            BOTH_ENV,
            "sandbox-token",
            id="sandbox-prefers-sandbox-token",
        ),
        pytest.param(
            None,
            ZenodoDomain.production,
            BOTH_ENV,
            "prod-token",
            id="production-ignores-sandbox-token",
        ),
        pytest.param(
            None,
            ZenodoDomain.sandbox,
            PROD_ENV,
            "prod-token",
            id="sandbox-falls-back-to-zenodo-token",
        ),
        pytest.param(
            None,
            "https://sandbox.zenodo.org/",
            BOTH_ENV,
            "sandbox-token",
            id="sandbox-as-a-string-with-trailing-slash",
        ),
        pytest.param(
            "",
            ZenodoDomain.production,
            PROD_ENV,
            "prod-token",
            id="empty-string-is-not-a-token",
        ),
        pytest.param(
            None,
            ZenodoDomain.production,
            {},
            None,
            id="nothing-set",
        ),
        pytest.param(
            None,
            ZenodoDomain.production,
            {"ZENODO_TOKEN": ""},
            None,
            id="empty-environment-variable",
        ),
        pytest.param(
            None,
            ZenodoDomain.sandbox,
            {"ZENODO_SANDBOX_TOKEN": ""},
            None,
            id="empty-sandbox-environment-variable",
        ),
    ),
)
def test_resolve_token(token, zenodo_domain, env, exp):
    assert resolve_token(token, zenodo_domain=zenodo_domain, env=env) == exp


def test_resolve_token_uses_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "from-os-environ")

    assert resolve_token() == "from-os-environ"


def test_resolve_token_required():
    resolved = resolve_token(None, env={"ZENODO_TOKEN": "prod-token"}, required=True)

    assert resolved == "prod-token"


def test_resolve_token_required_raises():
    with pytest.raises(MissingTokenError) as exc_info:
        resolve_token(None, env={}, required=True, description="do the thing")

    msg = str(exc_info.value)
    # The error should explain the entire chain,
    # so it can be fixed without reading the docs.
    assert "do the thing" in msg
    assert "ZENODO_SANDBOX_TOKEN" in msg
    assert "ZENODO_TOKEN" in msg
    assert ".env" in msg
    assert "https://zenodo.org" in msg


def test_resolve_token_required_raises_names_the_domain():
    with pytest.raises(MissingTokenError) as exc_info:
        resolve_token(None, env={}, required=True, zenodo_domain=ZenodoDomain.sandbox)

    assert "https://sandbox.zenodo.org" in str(exc_info.value)


@pytest.mark.parametrize(
    "zenodo_domain, exp",
    (
        (ZenodoDomain.production, "https://zenodo.org"),
        (ZenodoDomain.sandbox, "https://sandbox.zenodo.org"),
        ("https://zenodo.org", "https://zenodo.org"),
        ("https://zenodo.org/", "https://zenodo.org"),
    ),
)
def test_get_zenodo_domain_url(zenodo_domain, exp):
    assert get_zenodo_domain_url(zenodo_domain) == exp


def test_load_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ZENODO_TOKEN=from-dot-env\n")

    monkeypatch.delenv("ZENODO_TOKEN", raising=False)

    assert load_env_file(env_file) == env_file
    assert os.environ["ZENODO_TOKEN"] == "from-dot-env"  # noqa: S105
    assert resolve_token() == "from-dot-env"


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    """
    A real environment variable beats a `.env` file

    This is the behaviour that puts `.env` at the bottom of the precedence chain.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("ZENODO_TOKEN=from-dot-env\n")

    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    load_env_file(env_file)

    assert os.environ["ZENODO_TOKEN"] == "from-the-environment"  # noqa: S105


def test_load_env_file_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ZENODO_TOKEN=from-dot-env\n")

    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    load_env_file(env_file, override=True)

    assert os.environ["ZENODO_TOKEN"] == "from-dot-env"  # noqa: S105


def test_load_env_file_discovery(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ZENODO_TOKEN=discovered\n")

    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_env_file() == env_file
    assert os.environ["ZENODO_TOKEN"] == "discovered"  # noqa: S105


def test_load_env_file_discovery_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert load_env_file() is None


def test_load_env_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_env_file(tmp_path / "does-not-exist.env")
