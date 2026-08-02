"""
Tests of how the command-line interface resolves tokens

These do not hit Zenodo:
the interactor is replaced so we can see the token it was handed.

**TO BE REWRITTEN, not deleted** (plan Part 8). What these test — the
`--token` → `ZENODO_SANDBOX_TOKEN` → `ZENODO_TOKEN` → `.env` precedence chain —
is behaviour the trimmed CLI keeps, so the coverage has to survive. What has to
change is the scaffolding: every test drives `remove-files`, which is removed,
and patches `ZenodoInteractor`, which goes with it. Point them at a retained
command and at `ZenodoClient` instead.
"""

from __future__ import annotations

import importlib

import pytest
from typer.testing import CliRunner

from openscm_zenodo.cli.app import app

# `openscm_zenodo.cli.app` is both a module and the `Typer` instance
# exported by `openscm_zenodo.cli`, so we have to be explicit about which we want.
cli_app_module = importlib.import_module("openscm_zenodo.cli.app")

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    # New typer version, no mix_stderr argument
    runner = CliRunner()


@pytest.fixture
def captured_token(monkeypatch):
    """Capture the token handed to the interactor, without hitting Zenodo"""
    captured = {}

    class FakeInteractor:
        def __init__(self, token, zenodo_domain):
            captured["token"] = token
            captured["zenodo_domain"] = zenodo_domain

        def remove_all_files(self, deposition_id):
            captured["deposition_id"] = deposition_id

    monkeypatch.setattr(cli_app_module, "ZenodoInteractor", FakeInteractor)

    return captured


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A `.env` file holding a token, with no ambient token in the environment"""
    res = tmp_path / ".env"
    res.write_text("ZENODO_TOKEN=from-dot-env\n")

    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    monkeypatch.delenv("ZENODO_SANDBOX_TOKEN", raising=False)

    return res


def test_token_option(captured_token, env_file):
    res = runner.invoke(
        app,
        ["remove-files", "1234", "--all", "--token", "from-the-option"],
    )

    assert res.exit_code == 0, res.output
    assert captured_token["token"] == "from-the-option"  # noqa: S105


def test_token_from_the_environment(captured_token, env_file, monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    res = runner.invoke(app, ["remove-files", "1234", "--all"])

    assert res.exit_code == 0, res.output
    assert captured_token["token"] == "from-the-environment"  # noqa: S105


def test_token_from_the_sandbox_environment_variable(
    captured_token, env_file, monkeypatch
):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")
    monkeypatch.setenv("ZENODO_SANDBOX_TOKEN", "from-the-sandbox-environment")

    res = runner.invoke(
        app,
        [
            "remove-files",
            "1234",
            "--all",
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
        ],
    )

    assert res.exit_code == 0, res.output
    assert captured_token["token"] == "from-the-sandbox-environment"  # noqa: S105


def test_token_from_an_env_file(captured_token, env_file):
    res = runner.invoke(
        app,
        ["--env-file", str(env_file), "remove-files", "1234", "--all"],
    )

    assert res.exit_code == 0, res.output
    assert captured_token["token"] == "from-dot-env"  # noqa: S105


def test_env_file_does_not_override_the_environment(
    captured_token, env_file, monkeypatch
):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    res = runner.invoke(
        app,
        ["--env-file", str(env_file), "remove-files", "1234", "--all"],
    )

    assert res.exit_code == 0, res.output
    assert captured_token["token"] == "from-the-environment"  # noqa: S105


def test_env_file_discovered_from_the_working_directory(
    captured_token, env_file, monkeypatch
):
    monkeypatch.chdir(env_file.parent)

    res = runner.invoke(app, ["remove-files", "1234", "--all"])

    assert res.exit_code == 0, res.output
    assert captured_token["token"] == "from-dot-env"  # noqa: S105


def test_missing_env_file(captured_token, tmp_path):
    res = runner.invoke(
        app,
        ["--env-file", str(tmp_path / "nope.env"), "remove-files", "1234", "--all"],
    )

    assert res.exit_code != 0
    assert "token" not in captured_token
