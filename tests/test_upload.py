"""
Tests of `openscm_zenodo.zenodo.ZenodoClient.upload_file`

The three-step init -> content -> commit flow is the thing to pin down here:
which requests go out, in which order, and what happens when one of them fails.
"""

from __future__ import annotations

import hashlib
import urllib.parse

import pytest
import requests

from openscm_zenodo.exceptions import (
    ChecksumMismatchError,
    MissingTokenError,
    ZenodoHTTPError,
)
from openscm_zenodo.zenodo import ZenodoClient, should_retry_upload

RECORD_ID = "1234"


@pytest.fixture
def to_upload(tmp_path):
    """A file to upload, plus the MD5 of its contents"""
    contents = b"Some contents to upload"

    res = tmp_path / "data.nc"
    res.write_bytes(contents)

    return res, hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5


def make_commit_response(make_response, md5, *, filename="data.nc"):
    """Build the response Zenodo gives when a file is committed"""
    return make_response(
        json_body={
            "key": filename,
            "size": 23,
            "checksum": f"md5:{md5}",
            "status": "completed",
        }
    )


@pytest.fixture
def upload_client(no_token_in_env, make_recording_session, make_response, to_upload):
    """A client whose session records requests and answers a successful upload"""
    _, md5 = to_upload

    session = make_recording_session(
        [
            # Initialise
            make_response(json_body={"entries": [{"key": "data.nc"}]}),
            # Content
            make_response(json_body={"key": "data.nc", "status": "pending"}),
            # Commit
            make_commit_response(make_response, md5),
        ]
    )

    return ZenodoClient(token="a-token", session=session), session  # noqa: S106


def test_upload_file_three_step_flow(upload_client, to_upload):
    client, session = upload_client
    path, md5 = to_upload

    entry = client.upload_file(RECORD_ID, path)

    assert [(call["method"], call["url"]) for call in session.calls] == [
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
    assert session.calls[0]["json"] == [{"key": "data.nc"}]
    # The content goes up as a stream, not as a body we have read into memory
    assert hasattr(session.calls[1]["data"], "read")
    assert session.calls[1]["headers"]["Content-Type"] == "application/octet-stream"
    assert entry.checksum == f"md5:{md5}"


def test_upload_file_uses_the_upload_timeout(upload_client, to_upload):
    """
    Only the content request gets the long timeout
    """
    client, session = upload_client
    path, _ = to_upload

    client.upload_file(RECORD_ID, path)

    assert [call["timeout"] for call in session.calls] == [
        client.timeout,
        client.timeout_upload,
        client.timeout,
    ]


def test_upload_file_strips_the_local_path(
    no_token_in_env, make_recording_session, make_response, tmp_path
):
    """
    Zenodo has no directories, so the file lands under its basename
    """
    path = tmp_path / "outputs" / "2024"
    path.mkdir(parents=True)
    path = path / "data.nc"
    path.write_bytes(b"x")

    md5 = hashlib.md5(b"x").hexdigest()  # noqa: S324 # Zenodo uses md5
    session = make_recording_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, md5),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client.upload_file(RECORD_ID, path)

    assert session.calls[0]["json"] == [{"key": "data.nc"}]


def test_upload_file_quotes_the_filename(
    no_token_in_env, make_recording_session, make_response, tmp_path
):
    """
    A name which is not URL safe still ends up hitting the right endpoint
    """
    filename = "a file & more.nc"
    path = tmp_path / filename
    path.write_bytes(b"x")

    md5 = hashlib.md5(b"x").hexdigest()  # noqa: S324 # Zenodo uses md5
    session = make_recording_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, md5, filename=filename),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client.upload_file(RECORD_ID, path)

    quoted = urllib.parse.quote(filename, safe="")
    assert session.calls[1]["url"].endswith(f"/draft/files/{quoted}/content")
    # The key itself is not quoted, only the URL
    assert session.calls[0]["json"] == [{"key": filename}]


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
    no_token_in_env, make_recording_session, make_response, to_upload
):
    """
    An existing file, or one left behind by a failed upload, is removed first

    Without this, a re-run fails on the initialise step
    and leaves the draft in the same broken state it started in.
    """
    path, md5 = to_upload

    session = make_recording_session(
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
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client.upload_file(RECORD_ID, path)

    assert [call["method"] for call in session.calls] == [
        "POST",
        "DELETE",
        "POST",
        "PUT",
        "POST",
    ]


def test_upload_file_does_not_swallow_other_initialise_failures(
    no_token_in_env, make_recording_session, make_response, to_upload
):
    path, _ = to_upload

    session = make_recording_session(
        [
            make_response(status_code=403, json_body={"message": "Permission denied"}),
            # The cleanup delete
            make_response(status_code=404),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="Permission denied"):
        client.upload_file(RECORD_ID, path, max_attempts=1)


def test_upload_file_checksum_mismatch(
    no_token_in_env, make_recording_session, make_response, to_upload
):
    path, _ = to_upload

    session = make_recording_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # The cleanup delete
            make_response(status_code=204),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ChecksumMismatchError):
        client.upload_file(RECORD_ID, path, max_attempts=1)


def test_upload_file_no_checksum_verification(
    no_token_in_env, make_recording_session, make_response, to_upload
):
    """
    With verification off, a mismatching checksum is not noticed
    """
    path, _ = to_upload

    session = make_recording_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    entry = client.upload_file(RECORD_ID, path, verify_checksum=False)

    assert entry.checksum == "md5:not-the-right-checksum"


def test_upload_file_retries_a_checksum_mismatch(
    no_token_in_env, make_recording_session, make_response, to_upload, log_messages
):
    """
    A corrupted transfer is tried again, from the initialise step

    Re-sending the content is not an option:
    a committed file's content cannot be replaced.
    """
    path, md5 = to_upload

    session = make_recording_session(
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
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    entry = client.upload_file(RECORD_ID, path, max_attempts=2)

    assert entry.checksum == f"md5:{md5}"
    assert len(session.calls) == 6
    assert any("Upload attempt 1 failed" in message for message in log_messages)


def test_upload_file_cleans_up_after_giving_up(
    no_token_in_env, make_recording_session, make_response, to_upload
):
    """
    A file which was initialised but never committed is removed

    A draft with a pending file cannot be published,
    so leaving one behind turns a failed upload into a broken draft.
    """
    path, _ = to_upload

    session = make_recording_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # The cleanup delete
            make_response(status_code=204),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ChecksumMismatchError):
        client.upload_file(RECORD_ID, path, max_attempts=1)

    last_call = session.calls[-1]
    assert last_call["method"] == "DELETE"
    assert last_call["url"].endswith("/draft/files/data.nc")


def test_upload_file_survives_a_failed_clean_up(
    no_token_in_env, make_recording_session, make_response, to_upload, log_messages
):
    """
    If we cannot clean up, we warn and raise the failure that actually matters
    """
    path, _ = to_upload

    session = make_recording_session(
        [
            make_response(),
            make_response(),
            make_commit_response(make_response, "not-the-right-checksum"),
            # The cleanup delete, which also fails
            make_response(status_code=500),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

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

    assert entry.key == "data.nc"
    assert entry.size == 23
    assert entry.status == "completed"
    assert entry.checksum == f"md5:{md5}"
    # The parsed form, which is what a caller comparing checksums wants
    assert entry.md5 == md5
    # Everything Zenodo sent is still reachable
    assert entry.raw["key"] == "data.nc"
    # The raw blob would make the repr unreadable, so it is not in there
    assert "raw=" not in repr(entry)


def test_delete_file(no_token_in_env, make_recording_session, make_response):
    session = make_recording_session([make_response(status_code=204)])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client.delete_file(RECORD_ID, "data.nc")

    (call,) = session.calls
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
            requests.exceptions.ConnectionError("boom"), True, id="connection-error"
        ),
        pytest.param(requests.exceptions.ReadTimeout("boom"), True, id="read-timeout"),
        pytest.param(ValueError("boom"), False, id="not-a-transfer-failure"),
    ),
)
def test_should_retry_upload(exc, exp):
    assert should_retry_upload(exc) is exp


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
def test_should_retry_upload_http_errors(make_response, status_code, exp):
    """
    Zenodo answered, so only try again if the answer suggests it is worth it
    """
    exc = ZenodoHTTPError(make_response(status_code=status_code))

    assert should_retry_upload(exc) is exp
