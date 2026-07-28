"""
Tests of creating new versions of a record
"""

from __future__ import annotations

import pytest

from openscm_zenodo.zenodo import (
    FilesMode,
    ZenodoClient,
    create_new_version,
)

RECORD_ID = "1234"
NEW_VERSION_ID = "5678"


@pytest.fixture
def client_and_session(no_token_in_env, make_recording_session):
    """A client whose session hands back whatever the test scripts"""

    def factory(responses):
        session = make_recording_session(responses)

        return ZenodoClient(token="a-token", session=session), session  # noqa: S106

    return factory


def test_new_version(client_and_session, make_response):
    client, session = client_and_session([make_response(json_body={"id": 5678})])

    new_version_id = client.new_version(RECORD_ID)

    assert new_version_id == NEW_VERSION_ID
    (call,) = session.calls
    assert call["method"] == "POST"
    assert call["url"] == f"https://zenodo.org/api/records/{RECORD_ID}/versions"


def test_new_version_returns_a_string(client_and_session, make_response):
    """
    Zenodo reports IDs as numbers, we always hand back strings
    """
    client, _ = client_and_session([make_response(json_body={"id": 5678})])

    assert client.new_version(RECORD_ID) == "5678"


def test_new_version_import_files(client_and_session, make_response):
    client, session = client_and_session(
        [
            # The new version
            make_response(json_body={"id": 5678}),
            # The listing `import_files` does first
            make_response(json_body={"entries": []}),
            # The import
            make_response(status_code=201),
        ]
    )

    client.new_version(RECORD_ID, import_files=True)

    assert session.calls[-1]["url"] == (
        f"https://zenodo.org/api/records/{NEW_VERSION_ID}/draft/actions/files-import"
    )


def test_import_files(client_and_session, make_response):
    client, session = client_and_session(
        [make_response(json_body={"entries": []}), make_response(status_code=201)]
    )

    assert client.import_files(RECORD_ID) is True
    assert [call["method"] for call in session.calls] == ["GET", "POST"]


def test_import_files_skips_a_draft_which_already_has_files(
    client_and_session, make_response
):
    """
    Zenodo only imports into an empty draft, so a re-run has to skip

    Without this, re-running a release which failed part way through
    dies on `400 Please remove all files first.`
    """
    client, session = client_and_session(
        [
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
            )
        ]
    )

    assert client.import_files(RECORD_ID) is False
    # Only the listing, no import
    assert [call["method"] for call in session.calls] == ["GET"]


def test_update_metadata(client_and_session, make_response):
    client, session = client_and_session([make_response(json_body={"id": 1234})])

    client.update_metadata(RECORD_ID, {"title": "A new title"})

    (call,) = session.calls
    assert call["method"] == "PUT"
    assert call["url"] == f"https://zenodo.org/api/records/{RECORD_ID}/draft"
    # The metadata is nested, so settings outside it are left alone
    assert call["json"] == {"metadata": {"title": "A new title"}}


def test_publish(client_and_session, make_response):
    client, session = client_and_session([make_response(json_body={"id": 1234})])

    assert client.publish(RECORD_ID) == RECORD_ID

    (call,) = session.calls
    assert call["method"] == "POST"
    assert call["url"] == (
        f"https://zenodo.org/api/records/{RECORD_ID}/draft/actions/publish"
    )


def test_get_latest_version_id(client_and_session, make_response):
    client, session = client_and_session(
        [
            make_response(
                json_body={
                    "id": 1234,
                    "links": {"latest": "https://zenodo.org/api/records/9999"},
                }
            ),
            make_response(json_body={"id": 9999}),
        ]
    )

    assert client.get_latest_version_id(RECORD_ID) == "9999"
    # The latest link is followed as given, rather than being rebuilt
    assert session.calls[1]["url"] == "https://zenodo.org/api/records/9999"


class RecordingClient:
    """A stand-in for the client, which records what `create_new_version` asks of it"""

    def __init__(self):
        self.calls = []

    def new_version(self, record_id, *, import_files=False):
        self.calls.append(("new_version", record_id))

        return NEW_VERSION_ID

    def import_files(self, record_id):
        self.calls.append(("import_files", record_id))

        return True

    def update_metadata(self, record_id, metadata):
        self.calls.append(("update_metadata", metadata))

        return {}

    def upload_files(self, record_id, paths, **kwargs):
        self.calls.append(("upload_files", sorted(p.name for p in paths)))

        return {}

    def mirror_files(self, record_id, paths, **kwargs):
        self.calls.append(("mirror_files", sorted(p.name for p in paths)))

        return {}

    def publish(self, record_id):
        self.calls.append(("publish", record_id))

        return record_id


@pytest.fixture
def recording_client():
    return RecordingClient()


@pytest.fixture
def paths(tmp_path):
    res = []
    for name in ("a.txt", "b.txt"):
        path = tmp_path / name
        path.write_text(name)
        res.append(path)

    return res


def test_create_new_version_start_fresh(recording_client, paths):
    """
    Nothing is carried over, the draft gets exactly what is passed
    """
    new_version_id = create_new_version(RECORD_ID, recording_client, files=paths)

    assert new_version_id == NEW_VERSION_ID
    assert recording_client.calls == [
        ("new_version", RECORD_ID),
        ("upload_files", ["a.txt", "b.txt"]),
    ]


def test_create_new_version_inherit(recording_client, paths):
    create_new_version(
        RECORD_ID, recording_client, files=paths, files_mode=FilesMode.inherit
    )

    assert recording_client.calls == [
        ("new_version", RECORD_ID),
        ("import_files", NEW_VERSION_ID),
        ("upload_files", ["a.txt", "b.txt"]),
    ]


def test_create_new_version_mirror(recording_client, paths):
    create_new_version(
        RECORD_ID, recording_client, files=paths, files_mode=FilesMode.mirror
    )

    assert recording_client.calls == [
        ("new_version", RECORD_ID),
        ("import_files", NEW_VERSION_ID),
        ("mirror_files", ["a.txt", "b.txt"]),
    ]


def test_create_new_version_mirror_needs_files(recording_client):
    """
    Mirroring deletes, so it may not happen by omission
    """
    with pytest.raises(ValueError, match="`files` must be supplied"):
        create_new_version(RECORD_ID, recording_client, files_mode=FilesMode.mirror)

    assert recording_client.calls == []


def test_create_new_version_mirror_empty_is_allowed(recording_client):
    """
    Asking for a version with no files is fine, as long as you say so
    """
    create_new_version(
        RECORD_ID, recording_client, files=[], files_mode=FilesMode.mirror
    )

    assert recording_client.calls == [
        ("new_version", RECORD_ID),
        ("import_files", NEW_VERSION_ID),
        ("mirror_files", []),
    ]


def test_create_new_version_no_files(recording_client):
    create_new_version(RECORD_ID, recording_client)

    assert recording_client.calls == [("new_version", RECORD_ID)]


def test_create_new_version_metadata(recording_client):
    create_new_version(RECORD_ID, recording_client, metadata={"title": "Hello"})

    assert recording_client.calls == [
        ("new_version", RECORD_ID),
        ("update_metadata", {"title": "Hello"}),
    ]


def test_create_new_version_publish(recording_client, paths):
    create_new_version(RECORD_ID, recording_client, files=paths, publish=True)

    assert recording_client.calls[-1] == ("publish", NEW_VERSION_ID)


def test_create_new_version_does_not_publish_by_default(recording_client, paths):
    create_new_version(RECORD_ID, recording_client, files=paths)

    assert not [call for call in recording_client.calls if call[0] == "publish"]
