"""
Tests of how the command-line interface resolves tokens

These do not hit Zenodo: the session is replaced, so what we assert on is the
`Authorization` header the CLI would have sent. That is a step further than
reading the token off the client — it is the thing which actually decides
whether Zenodo lets us in.

The command used is `retrieve-citation`, because it is a read and needs no
files. Which command it is does not matter: the token chain lives in
`resolve_token`, which every command reaches the same way, through
`ZenodoClient`.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from openscm_zenodo import zenodo as zenodo_module
from openscm_zenodo.cli.app import app

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    # New typer version, no mix_stderr argument
    runner = CliRunner()


@pytest.fixture
def sent(monkeypatch, make_recording_session, make_response):
    """
    Capture what the CLI would send to Zenodo, without sending it

    The real client is built, so the token chain runs for real; only the
    transport is replaced.
    """
    session = make_recording_session([make_response(text="@dataset{fake}")])
    monkeypatch.setattr(zenodo_module, "build_session", lambda: session)

    return session


def sent_token(session):
    """Get the token from the one request the session was given"""
    (call,) = session.calls
    authorization = call["headers"].get("Authorization")
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")
    assert scheme == "Bearer", authorization

    return token


@pytest.fixture
def env_file(tmp_path, no_token_in_env):
    """A `.env` file holding a token, with no ambient token in the environment"""
    res = tmp_path / ".env"
    res.write_text("ZENODO_TOKEN=from-dot-env\n")

    return res


def test_token_option(sent, env_file):
    res = runner.invoke(
        app,
        ["retrieve-citation", "1234", "--token", "from-the-option"],
    )

    assert res.exit_code == 0, res.output
    assert sent_token(sent) == "from-the-option"


def test_token_from_the_environment(sent, env_file, monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert sent_token(sent) == "from-the-environment"


def test_token_from_the_sandbox_environment_variable(sent, env_file, monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")
    monkeypatch.setenv("ZENODO_SANDBOX_TOKEN", "from-the-sandbox-environment")

    res = runner.invoke(
        app,
        [
            "retrieve-citation",
            "1234",
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
        ],
    )

    assert res.exit_code == 0, res.output
    assert sent_token(sent) == "from-the-sandbox-environment"
    # The sandbox token is only the right answer because we asked for the
    # sandbox, so check we really did
    (call,) = sent.calls
    assert call["url"].startswith("https://sandbox.zenodo.org")


def test_token_from_an_env_file(sent, env_file):
    res = runner.invoke(
        app,
        ["--env-file", str(env_file), "retrieve-citation", "1234"],
    )

    assert res.exit_code == 0, res.output
    assert sent_token(sent) == "from-dot-env"


def test_env_file_does_not_override_the_environment(sent, env_file, monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    res = runner.invoke(
        app,
        ["--env-file", str(env_file), "retrieve-citation", "1234"],
    )

    assert res.exit_code == 0, res.output
    assert sent_token(sent) == "from-the-environment"


def test_env_file_discovered_from_the_working_directory(sent, env_file, monkeypatch):
    monkeypatch.chdir(env_file.parent)

    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert sent_token(sent) == "from-dot-env"


def test_missing_env_file(sent, tmp_path):
    res = runner.invoke(
        app,
        ["--env-file", str(tmp_path / "nope.env"), "retrieve-citation", "1234"],
    )

    assert res.exit_code != 0
    assert not sent.calls


def test_no_token_at_all_still_reads(sent, no_token_in_env, tmp_path, monkeypatch):
    """A public record is readable with no token, so nothing is sent"""
    # Somewhere with no `.env` file to find
    monkeypatch.chdir(tmp_path)

    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert sent_token(sent) is None
