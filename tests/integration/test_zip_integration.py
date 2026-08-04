"""
Integration tests of uploading an archive, against the Zenodo sandbox

The point of zipping is that a directory structure survives a system which has
no directories, so the test which matters is the round trip: upload it, download
it, unzip it, and look.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from openscm_zenodo.exceptions import DuplicateFileKeyError, OpenSCMZenodoWarning

pytestmark = pytest.mark.zenodo_token


@pytest.fixture
def tree(in_a_working_directory):
    """Files in two directories, sharing a name across them"""

    paths = []
    for name, contents in (
        ("out/2024/data.nc", b"the 2024 data"),
        ("out/2025/data.nc", b"the 2025 data"),
    ):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        paths.append(path)

    return paths


def test_upload_files_as_zip_round_trip(
    sandbox_client, draft_record_id, tree, tmp_path
):
    """
    The structure comes back, which is the whole reason to zip
    """
    entry = sandbox_client.upload_files_as_zip(
        draft_record_id, tree, zip_name="results.zip", progress=False
    )

    assert entry.filename == "results.zip"
    assert list(sandbox_client.list_files(draft_record_id)) == ["results.zip"]

    downloaded = tmp_path / "downloaded"
    (path,) = sandbox_client.download_files(draft_record_id, downloaded, progress=False)

    with zipfile.ZipFile(path) as archive:
        assert sorted(archive.namelist()) == ["2024/data.nc", "2025/data.nc"]
        assert archive.read("2024/data.nc") == b"the 2024 data"
        assert archive.read("2025/data.nc") == b"the 2025 data"


def test_uploading_the_same_archive_twice_uploads_nothing(
    sandbox_client, draft_record_id, tree
):
    """
    Determinism is what makes a re-run cheap

    A stock archive would have a new checksum every time, so the upload would
    have nothing to match against and would send the whole thing again.
    """
    first = sandbox_client.upload_files_as_zip(draft_record_id, tree, progress=False)

    for path in tree:
        path.touch()  # a new modification time, same contents

    sandbox_client.upload_files_as_zip(draft_record_id, tree, progress=False)

    after = sandbox_client.list_files(draft_record_id)["archive.zip"]
    # Same bytes, and Zenodo never saw a second version of them
    assert after.checksum == first.checksum


def test_the_same_name_in_two_directories_cannot_be_uploaded_flat(
    sandbox_client, draft_record_id, tree
):
    """
    The error which sends people to zipping, and nothing reaches Zenodo
    """
    with pytest.raises(DuplicateFileKeyError, match=r"data\.nc"):
        sandbox_client.upload_files(draft_record_id, tree, progress=False)

    assert sandbox_client.list_files(draft_record_id) == {}


def test_uploading_from_a_directory_warns_and_flattens(
    sandbox_client, draft_record_id, tree
):
    """
    Uploading without zipping loses the directories, and says so first
    """
    with pytest.warns(OpenSCMZenodoWarning, match=r"will be uploaded as data\.nc"):
        sandbox_client.upload_files(draft_record_id, tree[:1], progress=False)

    assert list(sandbox_client.list_files(draft_record_id)) == ["data.nc"]
