"""
Integration tests of the many-file methods, against the Zenodo sandbox

The one behaviour worth spending real API calls on
is the difference between `upload_files` and `mirror_files`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openscm_zenodo.checksums import get_file_md5

pytestmark = pytest.mark.zenodo_token


@pytest.fixture
def local_files(in_a_working_directory):
    """Two local files to upload"""
    res = []
    for name, contents in (("a.txt", "contents of a\n"), ("b.txt", "contents of b\n")):
        path = Path(name)
        path.write_text(contents)
        res.append(path)

    return res


def test_upload_files(sandbox_client, draft_record_id, local_files):
    files = sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=2, progress=False
    )

    assert sorted(files) == ["a.txt", "b.txt"]
    assert sorted(sandbox_client.list_files(draft_record_id)) == ["a.txt", "b.txt"]

    for path in local_files:
        assert files[path.name].md5 == get_file_md5(path)


def test_upload_files_is_idempotent(sandbox_client, draft_record_id, local_files):
    """
    Re-running with unchanged files uploads nothing the second time
    """
    first = sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )
    second = sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )

    # If anything had been re-uploaded, Zenodo would have given it a new version ID
    assert {name: entry.raw["version_id"] for name, entry in first.items()} == {
        name: entry.raw["version_id"] for name, entry in second.items()
    }


def test_upload_files_never_deletes(sandbox_client, draft_record_id, local_files):
    sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )
    sandbox_client.upload_files(
        draft_record_id, local_files[:1], n_threads=1, progress=False
    )

    assert sorted(sandbox_client.list_files(draft_record_id)) == ["a.txt", "b.txt"]


def test_mirror_files_deletes(sandbox_client, draft_record_id, local_files):
    """
    The single most important behavioural difference in the library
    """
    sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )

    files = sandbox_client.mirror_files(
        draft_record_id, local_files[:1], n_threads=1, progress=False
    )

    assert list(files) == ["a.txt"]
    assert list(sandbox_client.list_files(draft_record_id)) == ["a.txt"]


def test_mirror_files_uploads_changes(sandbox_client, draft_record_id, local_files):
    sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )

    changed = local_files[0]
    changed.write_text("completely different contents\n")

    files = sandbox_client.mirror_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )

    assert files["a.txt"].md5 == get_file_md5(changed)


def test_delete_files(sandbox_client, draft_record_id, local_files):
    sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=1, progress=False
    )

    sandbox_client.delete_files(draft_record_id, ["a.txt"], progress=False)

    assert list(sandbox_client.list_files(draft_record_id)) == ["b.txt"]


def test_delete_all_files(sandbox_client, draft_record_id, local_files):
    """
    Deleting in parallel was disabled in the old code as "weirdly flaky"

    The shared session with retries is what makes it reliable,
    so this deliberately runs with more than one thread.
    """
    sandbox_client.upload_files(
        draft_record_id, local_files, n_threads=2, progress=False
    )

    sandbox_client.delete_all_files(draft_record_id, progress=False)

    assert sandbox_client.list_files(draft_record_id) == {}


@pytest.mark.parametrize("n_threads", (1, 4))
def test_upload_files_in_parallel(
    sandbox_client, draft_record_id, in_a_working_directory, n_threads
):
    paths = []
    for i in range(4):
        path = Path(f"parallel-{i}.txt")
        path.write_text(f"contents of file {i}\n")
        paths.append(path)

    files = sandbox_client.upload_files(
        draft_record_id, paths, n_threads=n_threads, progress=False
    )

    assert sorted(files) == sorted(path.name for path in paths)
