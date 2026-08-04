"""
Integration tests of uploading, against the Zenodo sandbox

The premise of the rewrite is that Zenodo's API drifted out from under us
and nothing noticed, so every endpoint we call gets a test
that has hit the real thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openscm_zenodo.checksums import get_file_md5
from openscm_zenodo.exceptions import OpenSCMZenodoWarning, ZenodoHTTPError

pytestmark = pytest.mark.zenodo_token


def get_remote_files(client, record_id):
    """Get a map of filename -> checksum for a draft's files"""
    return {
        name: entry.checksum for name, entry in client.list_files(record_id).items()
    }


def test_upload_file(sandbox_client, draft_record_id, in_a_working_directory):
    """
    The init -> content -> commit flow works against the real API
    """
    path = Path("data.txt")
    path.write_text("Some contents for the integration test\n")

    entry = sandbox_client.upload_file(draft_record_id, path, progress=False)

    assert entry.filename == "data.txt"
    assert entry.status == "completed"
    assert entry.checksum == f"md5:{get_file_md5(path)}"

    assert get_remote_files(sandbox_client, draft_record_id) == {
        "data.txt": entry.checksum
    }


def test_upload_file_strips_the_local_path(
    sandbox_client, draft_record_id, in_a_working_directory
):
    """
    Zenodo has no directories, so a nested file lands under its basename

    Zenodo really does do this, which is what the warning is for.
    """
    path = Path("outputs") / "2024"
    path.mkdir(parents=True)
    path = path / "nested.txt"
    path.write_text("Nested\n")

    with pytest.warns(OpenSCMZenodoWarning, match=r"will be uploaded as nested\.txt"):
        sandbox_client.upload_file(draft_record_id, path, progress=False)

    assert list(get_remote_files(sandbox_client, draft_record_id)) == ["nested.txt"]


def test_upload_file_twice_replaces_it(
    sandbox_client, draft_record_id, in_a_working_directory
):
    """
    Uploading the same name again replaces what is there

    A committed file's content cannot be overwritten,
    so this only works because we delete and initialise again.
    """
    path = Path("data.txt")

    path.write_text("First\n")
    sandbox_client.upload_file(draft_record_id, path, progress=False)

    path.write_text("Second, which is longer\n")
    entry = sandbox_client.upload_file(draft_record_id, path, progress=False)

    assert entry.checksum == f"md5:{get_file_md5(path)}"
    assert get_remote_files(sandbox_client, draft_record_id) == {
        "data.txt": entry.checksum
    }


def test_delete_file(sandbox_client, draft_record_id, in_a_working_directory):
    path = Path("data.txt")
    path.write_text("To be deleted\n")

    sandbox_client.upload_file(draft_record_id, path, progress=False)
    sandbox_client.delete_file(draft_record_id, "data.txt")

    assert get_remote_files(sandbox_client, draft_record_id) == {}


def test_delete_file_that_is_not_there(sandbox_client, draft_record_id):
    with pytest.raises(ZenodoHTTPError):
        sandbox_client.delete_file(draft_record_id, "never-existed.txt")
