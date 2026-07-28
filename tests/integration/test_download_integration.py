"""
Integration tests of downloading, against Zenodo
"""

from __future__ import annotations

import pytest

from openscm_zenodo.checksums import get_file_md5
from openscm_zenodo.exceptions import FileNotOnRecordError, RecordNotFoundError
from openscm_zenodo.zenodo import ZenodoClient, download_files

PUBLISHED_RECORD_ID = "4589756"
"""A published production record which is not going anywhere"""

VERSIONED_RECORD_ID = "101709"
"""A published sandbox record"""

NONEXISTENT_RECORD_ID = "999999999999"
"""An ID which no record has"""


def test_download_file_from_a_published_record(tmp_path):
    """
    Downloading a public file needs no token
    """
    with ZenodoClient() as client:
        files = client.list_files(PUBLISHED_RECORD_ID)
        name = min(files, key=lambda key: files[key].size)

        written = client.download_file(
            PUBLISHED_RECORD_ID, name, tmp_path, progress=False
        )

    assert written == tmp_path / name
    # Verified on the way in, so this is belt and braces against a silent pass
    assert get_file_md5(written) == files[name].md5


def test_download_file_not_on_the_record(tmp_path):
    with ZenodoClient() as client:
        with pytest.raises(FileNotOnRecordError, match="definitely-not-here"):
            client.download_file(
                PUBLISHED_RECORD_ID, "definitely-not-here.txt", tmp_path, progress=False
            )


def test_record_which_does_not_exist_without_a_token(no_token_in_env):
    """
    A record we cannot find is reported plainly, and says a token might help
    """
    with ZenodoClient() as client:
        with pytest.raises(RecordNotFoundError) as exc_info:
            client.list_files(NONEXISTENT_RECORD_ID)

    msg = str(exc_info.value)
    assert NONEXISTENT_RECORD_ID in msg
    assert "supply a token" in msg


@pytest.mark.zenodo_token
def test_record_which_does_not_exist_with_a_token(sandbox_client):
    """
    With a token, we name which one we used and which domain we used it on

    That pairing is what gives away a production token used against the sandbox,
    which is the usual reason a record which is definitely there is not found.
    """
    with pytest.raises(RecordNotFoundError) as exc_info:
        sandbox_client.list_files(NONEXISTENT_RECORD_ID)

    msg = str(exc_info.value)
    assert "even using the token from $ZENODO" in msg
    assert "https://sandbox.zenodo.org" in msg
    assert exc_info.value.token_source == sandbox_client.token_source


@pytest.mark.zenodo_token
def test_is_draft_against_real_records(sandbox_client, draft_record_id):
    """
    A real draft and a real published record are told apart
    """
    assert sandbox_client.is_draft(draft_record_id) is True
    assert sandbox_client.is_draft(VERSIONED_RECORD_ID) is False


def test_download_files_helper_from_a_published_record(tmp_path):
    """
    The one-shot helper, against a real record
    """
    written = download_files(PUBLISHED_RECORD_ID, tmp_path, n_threads=2, progress=False)

    assert written
    assert sorted(p.name for p in written) == sorted(p.name for p in tmp_path.iterdir())


@pytest.mark.zenodo_token
def test_download_files_from_a_draft(sandbox_client, draft_record_id, tmp_path):
    """
    A draft's files download too, and the caller does not say it is a draft

    The record ID already decides that, so `list_files` works it out.
    """
    source = tmp_path / "source"
    source.mkdir()
    contents = {"a.txt": "contents of a\n", "b.txt": "contents of b\n"}
    for name, text in contents.items():
        (source / name).write_text(text)

    sandbox_client.upload_files(
        draft_record_id, list(source.iterdir()), n_threads=2, progress=False
    )

    dest = tmp_path / "dest"
    written = sandbox_client.download_files(
        draft_record_id, dest, n_threads=2, progress=False
    )

    assert sorted(p.name for p in written) == ["a.txt", "b.txt"]
    for name, text in contents.items():
        assert (dest / name).read_text() == text


@pytest.mark.zenodo_token
def test_download_files_round_trip_is_idempotent(
    sandbox_client, draft_record_id, tmp_path
):
    """
    Downloading twice into the same place fetches nothing the second time
    """
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("contents of a\n")
    sandbox_client.upload_files(
        draft_record_id, list(source.iterdir()), n_threads=1, progress=False
    )

    dest = tmp_path / "dest"
    sandbox_client.download_files(draft_record_id, dest, n_threads=1, progress=False)
    first = (dest / "a.txt").stat().st_mtime_ns

    sandbox_client.download_files(draft_record_id, dest, n_threads=1, progress=False)

    # Untouched, so it was not fetched again
    assert (dest / "a.txt").stat().st_mtime_ns == first


@pytest.mark.zenodo_token
def test_download_files_subset_of_a_draft(sandbox_client, draft_record_id, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.txt", "b.txt"):
        (source / name).write_text(f"contents of {name}\n")

    sandbox_client.upload_files(
        draft_record_id, list(source.iterdir()), n_threads=1, progress=False
    )

    dest = tmp_path / "dest"
    written = sandbox_client.download_files(
        draft_record_id, dest, filenames=["a.txt"], n_threads=1, progress=False
    )

    assert [p.name for p in written] == ["a.txt"]
    assert not (dest / "b.txt").exists()
