"""
Tests of the rule that file writes only ever target a draft

Zenodo locks a published record's files, so it refuses these writes itself.
The point of the guard is where and how they are refused: at the front door,
before half a batch has gone out, with an error which names the way forward.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from openscm_zenodo import zenodo
from openscm_zenodo.exceptions import (
    RecordNotFoundError,
    RecordNotWritableError,
    ZenodoHTTPError,
)
from openscm_zenodo.zenodo import ZenodoClient

RECORD_ID = "1234"
RECORD_URL = f"https://zenodo.org/api/records/{RECORD_ID}"

FILE_WRITE_METHODS = {
    "upload_file": lambda client, path: client.upload_file(
        RECORD_ID, path, progress=False
    ),
    "upload_files": lambda client, path: client.upload_files(
        RECORD_ID, [path], progress=False
    ),
    "upload_files_as_zip": lambda client, path: client.upload_files_as_zip(
        RECORD_ID, [path], progress=False
    ),
    "mirror_files": lambda client, path: client.mirror_files(
        RECORD_ID, [path], progress=False
    ),
    "delete_file": lambda client, path: client.delete_file(RECORD_ID, path.name),
    "delete_files": lambda client, path: client.delete_files(
        RECORD_ID, [path.name], progress=False
    ),
    "delete_all_files": lambda client, path: client.delete_all_files(
        RECORD_ID, progress=False
    ),
    "inherit_files": lambda client, path: client.inherit_files(RECORD_ID),
}
"""
Every method which changes a record's files, and how to call it

Kept as a map rather than a list so each method is called the way a caller
would call it. `test_every_file_write_method_is_covered` checks it against the
source, so a new file-write method cannot quietly skip these tests.
"""

NOT_FILE_WRITES = {"delete_draft"}
"""
Methods named like a file write which are not one

`delete_draft` deletes the whole record, so `_assert_writable`'s error — which
says the files are locked and to make a new version — would be the wrong advice.
It guards with `get_draft` instead, and `tests/test_records.py` checks that it
refuses a published record.
"""


@pytest.fixture
def to_upload(tmp_path, monkeypatch):
    """A file to write, sitting in the working directory so no path is stripped"""
    monkeypatch.chdir(tmp_path)

    res = Path("data.nc")
    res.write_bytes(b"Some contents")

    return res


def get_methods_which_guard():
    """
    Get the public methods of `ZenodoClient` which check the record is writable

    Read out of the source rather than listed here, so this cannot drift.
    """
    tree = ast.parse(inspect.getsource(zenodo))
    (client,) = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ZenodoClient"
    ]

    res = set()
    for node in client.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue

        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_assert_writable"
            ):
                res.add(node.name)

    return res


def test_every_file_write_method_is_covered():
    """
    The methods tested here are exactly the methods which guard

    Adding a file-write method means adding it above, which means it is called
    against a published record like every other one.
    """
    assert get_methods_which_guard() == set(FILE_WRITE_METHODS)


def test_public_file_write_methods_are_guarded():
    """
    Anything named like a file write checks the record before writing

    The name-based rule is the part which catches a *new* method: guarding is
    easy to forget, and Zenodo refusing the write late is not the same as us
    refusing it early.
    """
    write_verbs = ("upload", "delete", "mirror", "inherit", "import")
    named_like_a_write = {
        name
        for name in dir(ZenodoClient)
        if not name.startswith("_") and name.startswith(write_verbs)
    }

    assert named_like_a_write - get_methods_which_guard() == NOT_FILE_WRITES


@pytest.mark.parametrize("method", sorted(FILE_WRITE_METHODS))
def test_file_writes_are_refused_against_a_published_record(
    method, no_token_in_env, make_recording_session, make_response, to_upload
):
    """
    Every file write refuses a published record, and none of them writes first
    """
    session = make_recording_session([make_response(json_body={"id": 1234})])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotWritableError) as exc_info:
        FILE_WRITE_METHODS[method](client, to_upload)

    message = str(exc_info.value)
    assert RECORD_ID in message
    assert "create_or_get_new_version" in message

    # It asked whether the record was published, and then stopped
    assert [(call["method"], call["url"]) for call in session.calls] == [
        ("GET", RECORD_URL)
    ]


def test_a_published_record_with_metadata_edits_is_refused_just_as_early(
    no_token_in_env, make_recording_session, make_response, to_upload
):
    """
    The shape which used to fail late

    A published record whose metadata edits have been started has a draft, so
    the file listing succeeds and `mirror_files` gets as far as working out
    which files to delete before Zenodo refuses the deletes with `403 Bucket is
    locked for modifications.`. The published record is still what we ask about
    first, so it never gets that far.
    """
    session = make_recording_session(
        [
            # The published record, which is what settles it
            make_response(json_body={"id": 1234, "is_draft": False}),
            # Its metadata edits, and their listing, which we never ask for
            make_response(json_body={"id": 1234, "is_draft": True}),
            make_response(json_body={"entries": []}),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotWritableError):
        client.mirror_files(RECORD_ID, [to_upload], progress=False)

    assert len(session.calls) == 1
    assert not [call for call in session.calls if call["method"] == "DELETE"]


def test_inherit_files_is_an_error_on_a_published_record(
    no_token_in_env, make_recording_session, make_response
):
    """
    Not the quiet "the draft already had files, so I left it alone"

    Nothing was carried over and nothing could be, which is a different answer
    from there having been nothing to do.
    """
    session = make_recording_session([make_response(json_body={"id": 1234})])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotWritableError):
        client.inherit_files(RECORD_ID)


def test_new_version_still_works_on_a_published_record(
    no_token_in_env, make_recording_session, make_response
):
    """
    The documented exception: it addresses a published record to make a new one
    """
    session = make_recording_session([make_response(json_body={"id": 5678})])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    assert client.create_or_get_new_version(RECORD_ID) == "5678"

    (call,) = session.calls
    assert call["url"] == f"{RECORD_URL}/versions"


@pytest.mark.parametrize("status_code", (403, 404))
def test_a_write_refused_after_the_guard_passed_reports_the_same_thing(
    no_token_in_env, make_recording_session, make_response, status_code
):
    """
    Somebody publishing the record mid-call is not a different kind of failure

    Zenodo answers a file write against a published record with a `404` when it
    has no draft and a `403` when it has one whose files are locked. Neither
    says the thing the caller needs to hear, and the caller cannot tell the
    race apart from the ordinary case anyway.
    """
    session = make_recording_session(
        [
            # The guard, which finds a draft
            make_response(status_code=404),
            make_response(json_body={"id": 1234, "is_draft": True}),
            # The delete, refused because the record was published in between
            make_response(status_code=status_code),
            # Working out why, which finds the record published
            make_response(json_body={"id": 1234}),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotWritableError, match="create_or_get_new_version"):
        client.delete_file(RECORD_ID, "data.nc")


def test_a_missing_file_is_still_a_missing_file(
    no_token_in_env, make_recording_session, make_response
):
    """
    A `404` from a draft which is still a draft is about the file, not the record

    Claiming the record was published would send somebody looking for a race
    which did not happen.
    """
    session = make_recording_session(
        [
            # The guard, which finds a draft
            make_response(status_code=404),
            make_response(json_body={"id": 1234, "is_draft": True}),
            # The delete, refused because there is no such file
            make_response(status_code=404, json_body={"message": "No such file"}),
            # Working out why, which finds the record is still a draft
            make_response(status_code=404),
            make_response(json_body={"id": 1234, "is_draft": True}),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="No such file"):
        client.delete_file(RECORD_ID, "data.nc")


def test_is_draft_asks_again_before_saying_a_record_is_missing(
    no_token_in_env, make_recording_session, make_response
):
    """
    A record published mid-call exists, even though both questions missed it

    Publishing removes the draft, so a record which is published between the
    two questions is not found by either. Without the second look at the
    published record we would say a public record cannot be found.
    """
    session = make_recording_session(
        [
            # Not published yet
            make_response(status_code=404),
            # ...and by now the draft is gone, because it was published
            make_response(status_code=404),
            # Which the second look finds
            make_response(json_body={"id": 1234}),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    assert client.is_draft(RECORD_ID) is False
    assert len(session.calls) == 3


def test_is_draft_still_reports_a_record_which_is_really_not_there(
    no_token_in_env, make_recording_session, make_response
):
    session = make_recording_session([make_response(status_code=404) for _ in range(3)])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotFoundError):
        client.is_draft(RECORD_ID)


def test_list_files_falls_back_to_the_published_listing(
    no_token_in_env, make_recording_session, make_response
):
    """
    A record published between the question and the listing still lists

    The files are the same files, they are just behind the other endpoint now.
    """
    session = make_recording_session(
        [
            # Not published, so a draft
            make_response(status_code=404),
            make_response(json_body={"id": 1234, "is_draft": True}),
            # ...whose file listing has gone, because it was published
            make_response(status_code=404),
            # The published record's listing, which has them
            make_response(
                json_body={
                    "entries": [
                        {
                            "key": "a.txt",
                            "size": 1,
                            "checksum": "md5:abc",
                            "status": "completed",
                            "links": {"content": "https://zenodo.org/a.txt"},
                        }
                    ]
                }
            ),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    files = client.list_files(RECORD_ID)

    assert list(files) == ["a.txt"]
    assert [call["url"] for call in session.calls][-2:] == [
        f"{RECORD_URL}/draft/files",
        f"{RECORD_URL}/files",
    ]


def test_list_files_does_not_swallow_other_failures(
    no_token_in_env, make_recording_session, make_response
):
    session = make_recording_session(
        [
            make_response(status_code=404),
            make_response(json_body={"id": 1234, "is_draft": True}),
            make_response(status_code=500, json_body={"message": "Boom"}),
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="Boom"):
        client.list_files(RECORD_ID)
