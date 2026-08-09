"""
Tests of the command-line interface's three commands

The CLI's job is translation: turn flags into a call on
[`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient] and report failures as a
message rather than a traceback. So most of these replace the method being
wrapped and assert on what it was handed, which is exactly the boundary the CLI
owns. The error-reporting tests go one level lower and replace the session, so a
real `ZenodoHTTPError` comes back out of the transport.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from loguru import logger
from typer.testing import CliRunner

from openscm_zenodo import zenodo as zenodo_module
from openscm_zenodo.cli.app import app
from openscm_zenodo.zenodo import CitationFormat, ZenodoClient

# `openscm_zenodo.cli.app` is both a module and the `Typer` instance
# exported by `openscm_zenodo.cli`, so we have to be explicit about which we want.
cli_app_module = importlib.import_module("openscm_zenodo.cli.app")

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    # New typer version, no mix_stderr argument
    runner = CliRunner()


@pytest.fixture
def calls(monkeypatch):
    """
    Record the calls the CLI makes on the client, instead of making them

    Every file method and `get_citation` is replaced, so a test which expects
    one of them can also assert that the others were left alone. That is the
    point for `--mirror`, where calling the wrong one deletes files.
    """
    recorded = []

    def record(name, result=None):
        def method(self, *args, **kwargs):
            recorded.append({"method": name, "args": args, "kwargs": kwargs})

            return result

        return method

    for name in ("upload_files", "mirror_files"):
        monkeypatch.setattr(ZenodoClient, name, record(name, result={}))

    monkeypatch.setattr(
        ZenodoClient, "upload_files_as_zip", record("upload_files_as_zip")
    )
    monkeypatch.setattr(ZenodoClient, "download_files", record("download_files", []))
    monkeypatch.setattr(
        ZenodoClient, "get_citation", record("get_citation", "@dataset{fake}")
    )

    return recorded


@pytest.fixture
def local_files(tmp_path):
    """Two files to upload"""
    res = []
    for name in ("a.nc", "b.nc"):
        path = tmp_path / name
        path.write_text(name)
        res.append(path)

    return res


def only_call(calls):
    """Get the single call the CLI made, insisting that there was only one"""
    (call,) = calls

    return call


def test_upload_files(calls, local_files):
    res = runner.invoke(
        app,
        ["upload-files", "1234", *(str(path) for path in local_files)],
    )

    assert res.exit_code == 0, res.output

    call = only_call(calls)
    assert call["method"] == "upload_files"
    assert call["args"] == ("1234", local_files)
    assert call["kwargs"] == {
        "n_threads": 4,
        "progress": True,
        "warn_path_stripped": True,
    }


def test_upload_files_mirror(calls, local_files):
    res = runner.invoke(
        app,
        ["upload-files", "1234", *(str(path) for path in local_files), "--mirror"],
    )

    assert res.exit_code == 0, res.output

    call = only_call(calls)
    # `upload_files` never deletes and `mirror_files` does, so which one is
    # called is the whole meaning of the flag
    assert call["method"] == "mirror_files"
    assert call["args"] == ("1234", local_files)


def test_upload_files_options_are_passed_through(calls, local_files):
    res = runner.invoke(
        app,
        [
            "upload-files",
            "1234",
            *(str(path) for path in local_files),
            "--n-threads",
            "2",
            "--no-progress",
            "--no-warn-path-stripped",
        ],
    )

    assert res.exit_code == 0, res.output
    assert only_call(calls)["kwargs"] == {
        "n_threads": 2,
        "progress": False,
        "warn_path_stripped": False,
    }


def test_upload_files_zip(calls, local_files, tmp_path):
    res = runner.invoke(
        app,
        [
            "upload-files",
            "1234",
            *(str(path) for path in local_files),
            "--zip",
            "bundle.zip",
            "--zip-base-dir",
            str(tmp_path),
        ],
    )

    assert res.exit_code == 0, res.output

    call = only_call(calls)
    assert call["method"] == "upload_files_as_zip"
    assert call["args"] == ("1234", local_files)
    assert call["kwargs"] == {
        "zip_name": "bundle.zip",
        "base_dir": tmp_path,
        "progress": True,
    }


def test_upload_files_zip_and_mirror_is_refused(calls, local_files):
    res = runner.invoke(
        app,
        [
            "upload-files",
            "1234",
            *(str(path) for path in local_files),
            "--zip",
            "bundle.zip",
            "--mirror",
        ],
    )

    assert res.exit_code == 1
    assert "cannot be combined" in res.stderr
    # Half-doing this would mean uploading an archive and deleting nothing,
    # or deleting everything and uploading one file
    assert not calls


def test_upload_files_zip_base_dir_without_zip_is_refused(calls, local_files, tmp_path):
    res = runner.invoke(
        app,
        [
            "upload-files",
            "1234",
            *(str(path) for path in local_files),
            "--zip-base-dir",
            str(tmp_path),
        ],
    )

    assert res.exit_code == 1
    assert "only applies with `--zip`" in res.stderr
    assert not calls


def test_upload_files_needs_files(calls):
    res = runner.invoke(app, ["upload-files", "1234"])

    assert res.exit_code != 0
    assert not calls


def test_upload_files_rejects_a_missing_file(calls, tmp_path):
    res = runner.invoke(app, ["upload-files", "1234", str(tmp_path / "nope.nc")])

    assert res.exit_code != 0
    assert not calls


def test_download_files_all_of_them(calls, tmp_path):
    res = runner.invoke(app, ["download-files", "1234", "--dest-dir", str(tmp_path)])

    assert res.exit_code == 0, res.output

    call = only_call(calls)
    assert call["method"] == "download_files"
    assert call["args"] == ("1234", tmp_path)
    # No filenames means every file on the record
    assert call["kwargs"]["filenames"] is None


def test_download_files_a_subset(calls, tmp_path):
    res = runner.invoke(
        app,
        ["download-files", "1234", "a.nc", "b.nc", "--dest-dir", str(tmp_path)],
    )

    assert res.exit_code == 0, res.output
    assert only_call(calls)["kwargs"]["filenames"] == ["a.nc", "b.nc"]


def test_download_files_defaults_to_the_working_directory(calls):
    res = runner.invoke(app, ["download-files", "1234"])

    assert res.exit_code == 0, res.output
    assert only_call(calls)["args"] == ("1234", Path())


def test_download_files_options_are_passed_through(calls, tmp_path):
    res = runner.invoke(
        app,
        [
            "download-files",
            "1234",
            "--dest-dir",
            str(tmp_path),
            "--n-threads",
            "2",
            "--overwrite",
            "--no-verify-checksum",
            "--no-progress",
        ],
    )

    assert res.exit_code == 0, res.output
    assert only_call(calls)["kwargs"] == {
        "filenames": None,
        "n_threads": 2,
        "verify_checksum": False,
        "overwrite": True,
        "progress": False,
    }


def test_retrieve_citation_defaults_to_bibtex(calls):
    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert res.stdout == "@dataset{fake}\n"

    call = only_call(calls)
    assert call["method"] == "get_citation"
    assert call["args"] == ("1234",)
    assert call["kwargs"]["fmt"] == CitationFormat.bibtex


@pytest.mark.parametrize(
    "fmt",
    (CitationFormat.citation, CitationFormat.datacite_json, CitationFormat.dublin_core),
)
def test_retrieve_citation_format(calls, fmt):
    res = runner.invoke(app, ["retrieve-citation", "1234", "--format", fmt.value])

    assert res.exit_code == 0, res.output
    assert only_call(calls)["kwargs"]["fmt"] == fmt


def test_retrieve_citation_style_and_locale(calls):
    res = runner.invoke(
        app,
        [
            "retrieve-citation",
            "1234",
            "--format",
            "citation",
            "--style",
            "nature",
            "--locale",
            "en-GB",
        ],
    )

    assert res.exit_code == 0, res.output

    kwargs = only_call(calls)["kwargs"]
    assert kwargs["style"] == "nature"
    assert kwargs["locale"] == "en-GB"


def test_retrieve_citation_rejects_an_unknown_format(calls):
    res = runner.invoke(app, ["retrieve-citation", "1234", "--format", "not-a-format"])

    assert res.exit_code != 0
    assert not calls


def test_retrieve_citation_output_file(calls, tmp_path):
    output = tmp_path / "nested" / "citation.bib"

    res = runner.invoke(app, ["retrieve-citation", "1234", "--output", str(output)])

    assert res.exit_code == 0, res.output
    assert output.read_text() == "@dataset{fake}"
    assert res.stdout == ""


def test_retrieve_citation_rejects_a_style_zenodo_does_not_know(
    monkeypatch, make_recording_session
):
    session = make_recording_session()
    monkeypatch.setattr(zenodo_module, "build_session", lambda: session)

    res = runner.invoke(
        app,
        ["retrieve-citation", "1234", "--format", "citation", "--style", "mla"],
    )

    assert res.exit_code == 1
    # `mla` is a style we have verified Zenodo rejects, so we name the ID which
    # works instead of spending the request to be told
    assert "modern-language-association" in res.stderr
    assert not session.calls


@pytest.fixture
def failing_session(monkeypatch, make_recording_session, make_response):
    """Make Zenodo answer the next request with a failure of our choosing"""

    def factory(status_code, json_body=None):
        session = make_recording_session(
            [make_response(status_code=status_code, json_body=json_body)]
        )
        monkeypatch.setattr(zenodo_module, "build_session", lambda: session)

        return session

    return factory


def test_http_error_is_reported_without_a_traceback(failing_session, no_token_in_env):
    failing_session(404, json_body={"message": "No record found"})

    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 1
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "No record found" in res.stderr


def test_no_access_names_where_the_token_came_from(failing_session, no_token_in_env):
    failing_session(403)

    res = runner.invoke(app, ["retrieve-citation", "1234", "--token", "a-token"])

    assert res.exit_code == 1
    assert "No access to this record with the token from" in res.stderr
    # Where the token came from, never the token itself
    assert "a-token" not in res.stderr


def test_missing_token_is_reported_without_a_traceback(
    failing_session, no_token_in_env, tmp_path, monkeypatch
):
    # Somewhere with no `.env` file to find
    monkeypatch.chdir(tmp_path)
    failing_session(403)

    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 1
    # With no token at all, the useful message is which variables to set,
    # which `MissingTokenError` gives us
    assert "ZENODO_TOKEN" in res.stderr
    assert "No access to this record with the token from" not in res.stderr


@pytest.fixture
def captured_logging_setup(monkeypatch):
    """
    Capture how logging was set up, without touching the global logger

    Verbosity is a front-end onto the logging level, so what this checks is the
    level the flags resolve to.
    """
    captured = {}

    def fake_setup_logging(enable, logging_config=None, logging_level=None):
        captured.update(
            enable=enable, logging_config=logging_config, logging_level=logging_level
        )

    monkeypatch.setattr(cli_app_module, "setup_logging", fake_setup_logging)

    return captured


def test_no_verbosity_flags_leaves_the_level_alone(calls, captured_logging_setup):
    """
    With no flags we name no level, so the default stays where it is defined

    `get_default_config` applies `INFO` itself. Passing it explicitly here would
    also work, but it would stop `setup_logging` telling a level somebody chose
    apart from the one it fell back to.
    """
    res = runner.invoke(app, ["retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert captured_logging_setup["logging_level"] is None
    assert captured_logging_setup["enable"]


@pytest.mark.parametrize(
    "args, exp_level",
    (
        pytest.param(["-v"], "DEBUG", id="-v-adds-diagnostics"),
        pytest.param(["-vv"], "TRACE", id="-vv-adds-everything"),
        pytest.param(["-vvvv"], "TRACE", id="more-than-there-is-is-not-an-error"),
        pytest.param(["-q"], "WARNING", id="-q-leaves-warnings"),
        pytest.param(["-qq"], "ERROR", id="-qq-leaves-errors"),
        pytest.param(["-qqqq"], "CRITICAL", id="less-than-there-is-is-not-an-error"),
        pytest.param(["--verbose"], "DEBUG", id="long-form"),
        pytest.param(["--quiet"], "WARNING", id="long-form-quiet"),
    ),
)
def test_verbosity_sets_the_level(calls, captured_logging_setup, args, exp_level):
    res = runner.invoke(app, [*args, "retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert captured_logging_setup["logging_level"] == exp_level
    assert captured_logging_setup["enable"]


def test_logging_level_still_names_a_level(calls, captured_logging_setup):
    """`--logging-level` is kept for naming a level outright"""
    res = runner.invoke(
        app, ["--logging-level", "WARNING", "retrieve-citation", "1234"]
    )

    assert res.exit_code == 0, res.output
    assert captured_logging_setup["logging_level"] == "WARNING"


def test_no_logging_beats_verbosity(calls, captured_logging_setup):
    res = runner.invoke(app, ["-v", "--no-logging", "retrieve-citation", "1234"])

    assert res.exit_code == 0, res.output
    assert not captured_logging_setup["enable"]


def test_verbose_and_quiet_together_are_refused(calls, captured_logging_setup):
    res = runner.invoke(app, ["-v", "-q", "retrieve-citation", "1234"])

    assert res.exit_code == 1
    assert "cannot be combined" in res.stderr
    # Refused before anything else happens, including the request
    assert not calls
    assert not captured_logging_setup


def test_verbosity_with_an_explicit_level_is_refused(calls, captured_logging_setup):
    """
    Two ways of saying the same thing, so we ask which one was meant

    Picking one silently would mean a flag the user typed did nothing.
    """
    res = runner.invoke(
        app, ["-v", "--logging-level", "TRACE", "retrieve-citation", "1234"]
    )

    assert res.exit_code == 1
    assert "ambiguous" in res.stderr
    assert not calls
    assert not captured_logging_setup


def test_logging_config_from_a_file(
    monkeypatch, make_recording_session, make_response, tmp_path
):
    """
    `--logging-config` hands a whole loguru configuration over

    This goes through `loguru-config`, and is the reason it is in the test
    dependencies: the flag was broken and nothing noticed.

    The session is replaced rather than the client's method, so the real
    `get_citation` runs and logs what it did — which is what has to land in the
    file for this to have proved anything.
    """
    session = make_recording_session([make_response(text="@dataset{fake}")])
    monkeypatch.setattr(zenodo_module, "build_session", lambda: session)

    destination = tmp_path / "out.log"
    config = tmp_path / "logging.json"
    config.write_text(
        json.dumps(
            {
                "handlers": [
                    {
                        "sink": str(destination),
                        "level": "INFO",
                        "format": "{level} | {message}",
                    }
                ]
            }
        )
    )

    try:
        res = runner.invoke(
            app, ["--logging-config", str(config), "retrieve-citation", "1234"]
        )

        assert res.exit_code == 0, res.output

    finally:
        # `logger.configure` replaced every handler process-wide, and removing
        # them also closes the file we are about to read
        logger.remove()
        logger.disable("openscm_zenodo")

    assert "INFO | Retrieved record '1234' as bibtex" in destination.read_text()
