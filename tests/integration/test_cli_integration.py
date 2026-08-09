"""
Integration tests of the CLI

The unit tests in `tests/test_cli.py` check that flags become the right call.
What is left for real API calls is that the whole thing works end to end: bytes
out, bytes back, and a citation, driven the way a user drives it. In particular
the token is not passed in, so these also check that the CLI finds a sandbox
token in `ZENODO_SANDBOX_TOKEN` by itself.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import openscm_zenodo
from openscm_zenodo.cli import app
from openscm_zenodo.zenodo import ZenodoDomain

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    # New typer version, no mix_stderr argument
    runner = CliRunner()

SANDBOX = ["--zenodo-domain", ZenodoDomain.sandbox.value]

PUBLISHED_RECORD_ID = "4589756"
"""A published production record which is not going anywhere"""


@pytest.fixture
def local_files(in_a_working_directory):
    """Two local files to upload, under bare names so no path is stripped"""
    res = []
    for name, contents in (("a.txt", "contents of a\n"), ("b.txt", "contents of b\n")):
        path = Path(name)
        path.write_text(contents)
        res.append(path)

    return res


def test_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.exc_info
    assert result.stdout == f"openscm-zenodo {openscm_zenodo.__version__}\n"


@pytest.mark.zenodo_token
def test_upload_then_download(draft_record_id, local_files):
    upload = runner.invoke(
        app,
        [
            "upload-files",
            draft_record_id,
            *(str(path) for path in local_files),
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert upload.exit_code == 0, upload.output

    download = runner.invoke(
        app,
        [
            "download-files",
            draft_record_id,
            "--dest-dir",
            "downloaded",
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert download.exit_code == 0, download.output

    for path in local_files:
        assert (Path("downloaded") / path.name).read_text() == path.read_text()


@pytest.mark.zenodo_token
def test_download_a_subset(draft_record_id, local_files):
    upload = runner.invoke(
        app,
        [
            "upload-files",
            draft_record_id,
            *(str(path) for path in local_files),
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert upload.exit_code == 0, upload.output

    download = runner.invoke(
        app,
        [
            "download-files",
            draft_record_id,
            "a.txt",
            "--dest-dir",
            "downloaded",
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert download.exit_code == 0, download.output
    assert sorted(path.name for path in Path("downloaded").iterdir()) == ["a.txt"]


@pytest.mark.zenodo_token
def test_download_names_the_files_which_are_there(draft_record_id, local_files):
    upload = runner.invoke(
        app,
        [
            "upload-files",
            draft_record_id,
            *(str(path) for path in local_files),
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert upload.exit_code == 0, upload.output

    download = runner.invoke(
        app,
        ["download-files", draft_record_id, "typo.txt", "--no-progress", *SANDBOX],
    )

    assert download.exit_code == 1
    # A typo is the usual cause, so the names which are there are the fix
    assert "a.txt" in download.stderr


@pytest.mark.zenodo_token
def test_upload_files_mirror(draft_record_id, local_files):
    upload = runner.invoke(
        app,
        [
            "upload-files",
            draft_record_id,
            *(str(path) for path in local_files),
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert upload.exit_code == 0, upload.output

    mirror = runner.invoke(
        app,
        [
            "upload-files",
            draft_record_id,
            str(local_files[0]),
            "--mirror",
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert mirror.exit_code == 0, mirror.output

    listing = runner.invoke(
        app,
        [
            "download-files",
            draft_record_id,
            "--dest-dir",
            "downloaded",
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert listing.exit_code == 0, listing.output
    # `--mirror` deletes what is not in the files given
    assert sorted(path.name for path in Path("downloaded").iterdir()) == ["a.txt"]


@pytest.mark.zenodo_token
def test_upload_files_zip(draft_record_id, in_a_working_directory):
    nested = Path("out") / "2025"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("contents of a\n")

    upload = runner.invoke(
        app,
        [
            "upload-files",
            draft_record_id,
            str(nested / "a.txt"),
            "--zip",
            "bundle.zip",
            "--zip-base-dir",
            "out",
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert upload.exit_code == 0, upload.output

    download = runner.invoke(
        app,
        [
            "download-files",
            draft_record_id,
            "--dest-dir",
            "downloaded",
            "--no-progress",
            *SANDBOX,
        ],
    )

    assert download.exit_code == 0, download.output
    # The archive lands under the name we gave it, and the structure below
    # `--zip-base-dir` is what it preserves
    assert sorted(path.name for path in Path("downloaded").iterdir()) == ["bundle.zip"]

    with zipfile.ZipFile(Path("downloaded") / "bundle.zip") as archive:
        assert archive.namelist() == ["2025/a.txt"]


def test_retrieve_citation():
    """This reads a published production record, so it needs no token"""
    res = runner.invoke(app, ["retrieve-citation", PUBLISHED_RECORD_ID])

    assert res.exit_code == 0, res.output
    assert res.stdout.startswith("@dataset{")


def test_retrieve_citation_styled(tmp_path):
    output = tmp_path / "citation.txt"

    res = runner.invoke(
        app,
        [
            "retrieve-citation",
            PUBLISHED_RECORD_ID,
            "--format",
            "citation",
            "--style",
            "nature",
            "--output",
            str(output),
        ],
    )

    assert res.exit_code == 0, res.output
    assert "Nicholls" in output.read_text()
    assert res.stdout == ""
