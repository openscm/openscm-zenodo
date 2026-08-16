"""
Tests of `openscm_zenodo.zenodo.ZenodoClient.upload_file`

The three-step init -> content -> commit flow is the thing to pin down here:
which requests go out, in which order, and what happens when one of them fails.
"""

from __future__ import annotations

import hashlib
import urllib.parse
from pathlib import Path

import pytest
import requests

from openscm_zenodo.exceptions import (
    ChecksumMismatchError,
    FileTransferFailedError,
    MissingTokenError,
    OpenSCMZenodoWarning,
    ZenodoHTTPError,
)
from openscm_zenodo.zenodo import ZenodoClient, should_retry_transfer

RECORD_ID = "1234"


@pytest.fixture
def to_upload(tmp_path, monkeypatch):
    """
    A file to upload, plus the MD5 of its contents

    It sits in the working directory, so uploading it strips no path. What
    happens when one is stripped is `test_paths.py`'s subject.
    """
    monkeypatch.chdir(tmp_path)
    contents = b"Some contents to upload"

    res = Path("data.nc")
    res.write_bytes(contents)

    return res, hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5


def make_commit_response(make_response, md5, *, filename="data.nc"):
    """Build the response Zenodo gives when a file is committed"""
    quoted = urllib.parse.quote(filename, safe="")
    draft_files_url = f"https://zenodo.org/api/records/{RECORD_ID}/draft/files"

    return make_response(
        json_body={
            "key": filename,
            "size": 23,
            "checksum": f"md5:{md5}",
            "status": "completed",
            "links": {"content": f"{draft_files_url}/{quoted}/content"},
        }
    )


@pytest.fixture
def client_and_session(no_token_in_env, make_recording_session, draft_check_responses):
    """
    A client whose session answers the writable check, then whatever is scripted

    Every upload and delete asks whether the record is still a draft before it
    writes anything, so those answers go in front of the test's own responses.
    """

    def factory(responses=None):
        session = make_recording_session([*draft_check_responses(), *(responses or [])])

        return ZenodoClient(token="a-token", session=session), session  # noqa: S106

    return factory


@pytest.fixture
def upload_client(client_and_session, make_response, to_upload):
    """A client whose session records requests and answers a successful upload"""
    _, md5 = to_upload

    client, session = client_and_session(
        [
            # Initialise
            make_response(json_body={"entries": [{"key": "data.nc"}]}),
            # Content
            make_response(json_body={"key": "data.nc", "status": "pending"}),
            # Commit
            make_commit_response(make_response, md5),
        ]
    )

    return client, session


def test_upload_file_three_step_flow(upload_client, to_upload, write_calls):
    client, session = upload_client
    path, md5 = to_upload

    entry = client.upload_file(RECORD_ID, path)

    calls = write_calls(session)
    assert [(call["method"], call["url"]) for call in calls] == [
        ("POST", f"https://zenodo.org/api/records/{RECORD_ID}/draft/files"),
        (
            "PUT",
            f"https://zenodo.org/api/records/{RECORD_ID}/draft/files/data.nc/content",
        ),
        (
            "POST",
            f"https://zenodo.org/api/records/{RECORD_ID}/draft/files/data.nc/commit",
        ),
    ]

    # The key is sent as a list, which is what InvenioRDM expects
    assert calls[0]["json"] == [{"key": "data.nc"}]
    # The content goes up as a stream, not as a body we have read into memory
    assert hasattr(calls[1]["data"], "read")
    assert calls[1]["headers"]["Content-Type"] == "application/octet-stream"
    assert entry.checksum == f"md5:{md5}"


def test_upload_file_uses_the_upload_timeout(upload_client, to_upload, write_calls):
    """
    Only the content request gets the long timeout
    """
    client, session = upload_client
    path, _ = to_upload

    client.upload_file(RECORD_ID, path)

    assert [call["timeout"] for call in write_calls(session)] == [
        client.timeout,
        client.timeout_upload,
        client.timeout,
    ]


def test_upload_file_strips_the_local_path(
    client_and_session, make_response, tmp_path, write_calls
):
    """
    Zenodo has no directories, so the file lands under its basename, and says so
    """
    path = tmp_path / "outputs" / "2024"
    path.mkdir(parents=True)
    path = path / "data.nc"
    path.write_bytes(b"x")

    md5 = hashlib.md5(b"x").hexdigest()  # noqa: S324 # Zenodo uses md5
    client, session = client_and_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, md5),
        ]
    )

    with pytest.warns(OpenSCMZenodoWarning, match="will be uploaded as data.nc"):
        client.upload_file(RECORD_ID, path)

    assert write_calls(session)[0]["json"] == [{"key": "data.nc"}]


def test_upload_file_quotes_the_filename(
    client_and_session,
    make_response,
    tmp_path,
    monkeypatch,
    write_calls,
):
    """
    A name which is not URL safe still ends up hitting the right endpoint
    """
    monkeypatch.chdir(tmp_path)
    filename = "a file & more.nc"
    path = Path(filename)
    path.write_bytes(b"x")

    md5 = hashlib.md5(b"x").hexdigest()  # noqa: S324 # Zenodo uses md5
    client, session = client_and_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, md5, filename=filename),
        ]
    )

    client.upload_file(RECORD_ID, path)

    calls = write_calls(session)
    quoted = urllib.parse.quote(filename, safe="")
    assert calls[1]["url"].endswith(f"/draft/files/{quoted}/content")
    # The key itself is not quoted, only the URL
    assert calls[0]["json"] == [{"key": filename}]


def test_upload_file_requires_a_token(
    no_token_in_env, make_recording_session, to_upload
):
    path, _ = to_upload
    session = make_recording_session()
    client = ZenodoClient(session=session)

    with pytest.raises(MissingTokenError):
        client.upload_file(RECORD_ID, path)

    assert session.calls == []


def test_upload_file_replaces_a_file_which_is_already_there(
    client_and_session,
    make_response,
    to_upload,
    write_calls,
):
    """
    An existing file, or one left behind by a failed upload, is removed first

    Without this, a re-run fails on the initialise step
    and leaves the draft in the same broken state it started in.
    """
    path, md5 = to_upload

    client, session = client_and_session(
        [
            # Initialise, rejected because the key is already there
            make_response(status_code=400, json_body={"message": "already exists"}),
            # Delete
            make_response(status_code=204),
            # Initialise, second time
            make_response(),
            # Content
            make_response(),
            # Commit
            make_commit_response(make_response, md5),
        ]
    )

    client.upload_file(RECORD_ID, path)

    assert [call["method"] for call in write_calls(session)] == [
        "POST",
        "DELETE",
        "POST",
        "PUT",
        "POST",
    ]


def test_upload_file_does_not_swallow_other_initialise_failures(
    client_and_session,
    make_response,
    to_upload,
):
    path, _ = to_upload

    client, _ = client_and_session(
        [
            # Initialise, refused for a reason which is nothing to do with the
            # record being published
            make_response(status_code=403, json_body={"message": "Permission denied"}),
            # Working out whether it was: the record is still a draft, so the
            # `403` above is left to speak for itself
            make_response(status_code=404),
            make_response(json_body={"id": 1234, "is_draft": True}),
            # The cleanup delete
            make_response(status_code=404),
        ]
    )

    with pytest.raises(ZenodoHTTPError, match="Permission denied"):
        client.upload_file(RECORD_ID, path, max_attempts=1)


def test_upload_file_checksum_mismatch(client_and_session, make_response, to_upload):
    path, _ = to_upload

    client, _ = client_and_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # The cleanup delete
            make_response(status_code=204),
        ]
    )

    with pytest.raises(ChecksumMismatchError):
        client.upload_file(RECORD_ID, path, max_attempts=1)


def test_upload_file_no_checksum_verification(
    client_and_session,
    make_response,
    to_upload,
):
    """
    With verification off, a mismatching checksum is not noticed
    """
    path, _ = to_upload

    client, _ = client_and_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
        ]
    )

    entry = client.upload_file(RECORD_ID, path, verify_checksum=False)

    assert entry.checksum == "md5:not-the-right-checksum"


def test_upload_file_retries_a_checksum_mismatch(
    client_and_session,
    make_response,
    to_upload,
    log_messages,
    write_calls,
):
    """
    A corrupted transfer is tried again, from the initialise step

    Re-sending the content is not an option:
    a committed file's content cannot be replaced.
    """
    path, md5 = to_upload

    client, session = client_and_session(
        [
            # First attempt, which comes back corrupted
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # Second attempt, which works
            make_response(),
            make_response(),
            make_commit_response(make_response, md5),
        ]
    )

    entry = client.upload_file(RECORD_ID, path, max_attempts=2)

    assert entry.checksum == f"md5:{md5}"
    assert len(write_calls(session)) == 6
    assert any("Transfer attempt 1 failed" in message for message in log_messages)


def test_upload_file_cleans_up_after_giving_up(
    client_and_session,
    make_response,
    to_upload,
):
    """
    A file which was initialised but never committed is removed

    A draft with a pending file cannot be published,
    so leaving one behind turns a failed upload into a broken draft.
    """
    path, _ = to_upload

    client, session = client_and_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # The cleanup delete
            make_response(status_code=204),
        ]
    )

    with pytest.raises(ChecksumMismatchError):
        client.upload_file(RECORD_ID, path, max_attempts=1)

    last_call = session.calls[-1]
    assert last_call["method"] == "DELETE"
    assert last_call["url"].endswith("/draft/files/data.nc")


def test_upload_file_survives_a_failed_clean_up(
    client_and_session,
    make_response,
    to_upload,
    log_messages,
):
    """
    If we cannot clean up, we warn and raise the failure that actually matters
    """
    path, _ = to_upload

    client, _ = client_and_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # The cleanup delete, which also fails
            make_response(status_code=500),
        ]
    )

    with pytest.raises(ChecksumMismatchError):
        client.upload_file(RECORD_ID, path, max_attempts=1)

    assert any("Failed to clean up" in message for message in log_messages)


def test_upload_file_returns_a_file_entry(upload_client, to_upload):
    """
    The entry models the fields we care about and keeps the rest
    """
    client, _ = upload_client
    path, md5 = to_upload

    entry = client.upload_file(RECORD_ID, path)

    assert entry.filename == "data.nc"
    assert entry.size == 23
    assert entry.status == "completed"
    assert entry.checksum == f"md5:{md5}"
    # The parsed form, which is what a caller comparing checksums wants
    assert entry.md5 == md5
    # Everything Zenodo sent is still reachable
    assert entry.raw["key"] == "data.nc"
    # The raw blob would make the repr unreadable, so it is not in there
    assert "raw=" not in repr(entry)


def test_delete_file(
    no_token_in_env,
    make_recording_session,
    make_response,
    draft_check_responses,
    write_calls,
):
    session = make_recording_session(
        [*draft_check_responses(), make_response(status_code=204)]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client.delete_file(RECORD_ID, "data.nc")

    (call,) = write_calls(session)
    assert call["method"] == "DELETE"
    assert call["url"] == (
        f"https://zenodo.org/api/records/{RECORD_ID}/draft/files/data.nc"
    )


@pytest.mark.parametrize(
    "exc, exp",
    (
        pytest.param(
            ChecksumMismatchError("a", local_md5="b", remote_md5="c"),
            True,
            id="checksum-mismatch",
        ),
        pytest.param(
            FileTransferFailedError("a", record_id="1", errors="it failed"),
            True,
            id="transfer-failed",
        ),
        pytest.param(
            requests.exceptions.ConnectionError("boom"), True, id="connection-error"
        ),
        pytest.param(requests.exceptions.ReadTimeout("boom"), True, id="read-timeout"),
        pytest.param(ValueError("boom"), False, id="not-a-transfer-failure"),
    ),
)
def test_should_retry_transfer(exc, exp):
    assert should_retry_transfer(exc) is exp


@pytest.mark.parametrize(
    "status_code, exp",
    (
        (429, True),
        (500, True),
        (503, True),
        (400, False),
        (403, False),
        (404, False),
    ),
)
def test_should_retry_transfer_http_errors(make_response, status_code, exp):
    """
    Zenodo answered, so only try again if the answer suggests it is worth it
    """
    exc = ZenodoHTTPError(make_response(status_code=status_code))

    assert should_retry_transfer(exc) is exp


def test_upload_file_content_which_zenodo_accepts_and_then_drops(
    client_and_session,
    make_response,
    to_upload,
):
    """
    A `200` which says the transfer failed is a failure, and says so where it happens

    Zenodo reports a storage failure on the content upload as a `200` whose body
    carries an `errors` key, a `size` of zero and no checksum. Nothing about the
    response itself says anything is wrong, and the file then disappears from
    the draft, so without this check the upload dies two steps later on
    `Record 'X' has no file 'Y'` — which points at the wrong thing entirely.
    """
    path, _ = to_upload
    failed_content = {
        "key": "data.nc",
        "status": "completed",
        "size": 0,
        "checksum": None,
        "errors": "File upload transfer failed.",
    }

    client, session = client_and_session(
        [
            # Initialise, then the content Zenodo accepts and reports as failed
            make_response(),
            make_response(json_body=failed_content),
            # The clean-up delete, once we give up
            make_response(),
        ]
    )

    with pytest.raises(FileTransferFailedError, match="File upload transfer failed"):
        client.upload_file(RECORD_ID, path, max_attempts=1, progress=False)

    # It never got as far as committing
    assert not any(call["url"].endswith("/commit") for call in session.calls)


def test_upload_file_content_failure_is_retried(
    client_and_session,
    make_response,
    to_upload,
):
    """
    Zenodo's storage failures are transient, so a second attempt is worth making
    """
    path, md5 = to_upload

    client, _ = client_and_session(
        [
            # First attempt, which Zenodo accepts and then drops
            make_response(),
            make_response(json_body={"key": "data.nc", "errors": "it failed"}),
            # Second attempt, which works
            make_response(),
            make_response(),
            make_commit_response(make_response, md5),
        ]
    )

    entry = client.upload_file(RECORD_ID, path, max_attempts=2, progress=False)

    assert entry.checksum == f"md5:{md5}"
