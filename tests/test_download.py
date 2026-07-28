"""
Tests of downloading files
"""

from __future__ import annotations

import hashlib

import pytest

from openscm_zenodo.exceptions import ChecksumMismatchError, FileNotOnRecordError
from openscm_zenodo.zenodo import ZenodoClient, download_files

RECORD_ID = "1234"
RECORD_URL = f"https://zenodo.org/api/records/{RECORD_ID}"
FILES_URL = f"{RECORD_URL}/files"


def md5_of(contents):
    """Get the MD5 of some contents"""
    return hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5


class FakeRecord:
    """
    A stand-in for a published Zenodo record which has files to download
    """

    def __init__(self, make_response, files, *, corrupt=False):
        self.make_response = make_response
        # name -> contents
        self.files = dict(files)
        self.corrupt = corrupt
        self.calls = []

    def entry(self, name):
        """Build Zenodo's description of one file"""
        contents = self.files[name]

        return {
            "key": name,
            "size": len(contents),
            "checksum": f"md5:{md5_of(contents)}",
            "status": "completed",
            "links": {"content": f"{FILES_URL}/{name}/content"},
        }

    def request(self, method, url, **kwargs):
        """Answer a request the way Zenodo would"""
        self.calls.append((method, url))

        if url == RECORD_URL:
            # A published record, so `is_draft` is answered right away
            return self.make_response(json_body={"id": int(RECORD_ID)})

        if url == FILES_URL:
            return self.make_response(
                json_body={"entries": [self.entry(name) for name in self.files]}
            )

        if url.endswith("/content"):
            name = url[len(f"{FILES_URL}/") : -len("/content")]
            contents = self.files[name]
            if self.corrupt:
                contents = contents + b" and some rubbish"

            return self.make_response(content=contents)

        msg = f"Unexpected request: {method} {url}"
        raise AssertionError(msg)


@pytest.fixture
def fake_record(make_response, make_recording_session, no_token_in_env):
    """A client whose session is answered by a `FakeRecord`"""

    def factory(files, *, corrupt=False):
        record = FakeRecord(make_response, files, corrupt=corrupt)
        session = make_recording_session()
        session.request = record.request

        return ZenodoClient(session=session), record

    return factory


@pytest.fixture
def two_files():
    return {"a.txt": b"contents of a", "b.txt": b"contents of b, which is longer"}


def test_download_file(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    written = client.download_file(RECORD_ID, "a.txt", tmp_path, progress=False)

    assert written == tmp_path / "a.txt"
    assert written.read_bytes() == two_files["a.txt"]


def test_download_file_to_a_named_path(fake_record, two_files, tmp_path):
    """
    A destination which is not a directory is used as the file name
    """
    client, _ = fake_record(two_files)
    dest = tmp_path / "somewhere-else.txt"

    written = client.download_file(RECORD_ID, "a.txt", dest, progress=False)

    assert written == dest
    assert dest.read_bytes() == two_files["a.txt"]


def test_download_file_not_on_the_record(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    with pytest.raises(FileNotOnRecordError) as exc_info:
        client.download_file(RECORD_ID, "nope.txt", tmp_path, progress=False)

    msg = str(exc_info.value)
    assert "nope.txt" in msg
    # The available names are listed, because a typo is the usual cause
    assert "'a.txt'" in msg
    assert "'b.txt'" in msg


def test_download_file_leaves_nothing_behind_on_failure(
    fake_record, two_files, tmp_path
):
    """
    A download which never succeeds does not leave a truncated file in place
    """
    client, _ = fake_record(two_files, corrupt=True)

    with pytest.raises(ChecksumMismatchError):
        client.download_file(
            RECORD_ID, "a.txt", tmp_path, progress=False, max_attempts=1
        )

    # Nothing at all is left behind: not the file callers would look for,
    # and not the temporary one it was being written to either
    assert list(tmp_path.iterdir()) == []


def test_download_file_no_checksum_verification(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files, corrupt=True)

    written = client.download_file(
        RECORD_ID, "a.txt", tmp_path, progress=False, verify_checksum=False
    )

    assert written.read_bytes() == two_files["a.txt"] + b" and some rubbish"


def test_download_file_skips_what_is_already_there(fake_record, two_files, tmp_path):
    """
    A file which is already there with the same contents is not fetched again
    """
    client, record = fake_record(two_files)
    (tmp_path / "a.txt").write_bytes(two_files["a.txt"])

    client.download_file(RECORD_ID, "a.txt", tmp_path, progress=False)

    assert not [url for _, url in record.calls if url.endswith("/content")]


def test_download_file_refuses_to_clobber(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)
    (tmp_path / "a.txt").write_bytes(b"something else entirely")

    with pytest.raises(FileExistsError, match="overwrite=True"):
        client.download_file(RECORD_ID, "a.txt", tmp_path, progress=False)

    assert (tmp_path / "a.txt").read_bytes() == b"something else entirely"


def test_download_file_overwrite(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)
    (tmp_path / "a.txt").write_bytes(b"something else entirely")

    client.download_file(RECORD_ID, "a.txt", tmp_path, progress=False, overwrite=True)

    assert (tmp_path / "a.txt").read_bytes() == two_files["a.txt"]


def test_download_files(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    written = client.download_files(
        RECORD_ID, tmp_path / "out", n_threads=1, progress=False
    )

    assert sorted(p.name for p in written) == ["a.txt", "b.txt"]
    for name, contents in two_files.items():
        assert (tmp_path / "out" / name).read_bytes() == contents


def test_download_files_creates_the_destination(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)
    dest = tmp_path / "deeply" / "nested"

    client.download_files(RECORD_ID, dest, n_threads=1, progress=False)

    assert dest.is_dir()


def test_download_files_subset(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    written = client.download_files(
        RECORD_ID, tmp_path, filenames=["b.txt"], n_threads=1, progress=False
    )

    assert [p.name for p in written] == ["b.txt"]
    assert not (tmp_path / "a.txt").exists()


def test_download_files_unknown_name(fake_record, two_files, tmp_path):
    client, record = fake_record(two_files)

    with pytest.raises(FileNotOnRecordError, match="nope"):
        client.download_files(
            RECORD_ID, tmp_path, filenames=["a.txt", "nope.txt"], progress=False
        )

    # Nothing is downloaded, rather than downloading what we can and then failing
    assert not [url for _, url in record.calls if url.endswith("/content")]


def test_download_files_in_parallel(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    written = client.download_files(RECORD_ID, tmp_path, n_threads=2, progress=False)

    assert len(written) == 2


def test_download_files_is_idempotent(fake_record, two_files, tmp_path):
    client, record = fake_record(two_files)

    client.download_files(RECORD_ID, tmp_path, n_threads=1, progress=False)
    before = len([url for _, url in record.calls if url.endswith("/content")])

    client.download_files(RECORD_ID, tmp_path, n_threads=1, progress=False)
    after = len([url for _, url in record.calls if url.endswith("/content")])

    assert before == 2
    # The second run fetched nothing
    assert after == before


def test_download_files_no_files(fake_record, tmp_path):
    client, _ = fake_record({})

    assert client.download_files(RECORD_ID, tmp_path, progress=False) == []


def test_download_files_helper(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    written = download_files(RECORD_ID, tmp_path, client, n_threads=1, progress=False)

    assert sorted(p.name for p in written) == ["a.txt", "b.txt"]


def test_download_files_to_a_mapping_of_destinations(fake_record, two_files, tmp_path):
    """
    A mapping says exactly where each file goes, and which files are wanted
    """
    client, _ = fake_record(two_files)
    dest = {
        "a.txt": tmp_path / "renamed-a.txt",
        "b.txt": tmp_path / "nested" / "b.txt",
    }

    written = client.download_files(RECORD_ID, dest, n_threads=1, progress=False)

    assert written == [dest["a.txt"], dest["b.txt"]]
    assert dest["a.txt"].read_bytes() == two_files["a.txt"]
    # Directories on the way are created
    assert dest["b.txt"].read_bytes() == two_files["b.txt"]


def test_download_files_mapping_selects_which_files(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    written = client.download_files(
        RECORD_ID, {"b.txt": tmp_path / "b.txt"}, n_threads=1, progress=False
    )

    assert [p.name for p in written] == ["b.txt"]
    assert not (tmp_path / "a.txt").exists()


def test_download_files_mapping_and_filenames_conflict(
    fake_record, two_files, tmp_path
):
    """
    A mapping already says which files are wanted, so `filenames` cannot too
    """
    client, record = fake_record(two_files)

    with pytest.raises(ValueError, match="`filenames` may not be given"):
        client.download_files(
            RECORD_ID,
            {"a.txt": tmp_path / "a.txt"},
            filenames=["b.txt"],
            progress=False,
        )

    assert record.calls == []


def test_download_files_mapping_unknown_name(fake_record, two_files, tmp_path):
    client, _ = fake_record(two_files)

    with pytest.raises(FileNotOnRecordError, match="nope"):
        client.download_files(
            RECORD_ID, {"nope.txt": tmp_path / "nope.txt"}, progress=False
        )


def test_file_not_on_record_error_lists_every_missing_name(
    fake_record, two_files, tmp_path
):
    """
    Asking for several names which are not there names all of them
    """
    client, _ = fake_record(two_files)

    with pytest.raises(FileNotOnRecordError) as exc_info:
        client.download_files(
            RECORD_ID, tmp_path, filenames=["nope.txt", "also-nope.txt"], progress=False
        )

    msg = str(exc_info.value)
    assert "'nope.txt'" in msg
    assert "'also-nope.txt'" in msg
    assert "has no files" in msg
    assert exc_info.value.filenames == ("nope.txt", "also-nope.txt")
