"""
Tests of creating new versions of a record
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    MetadataValidationError,
    OpenSCMZenodoWarning,
    RecordNotFoundError,
    RecordNotWritableError,
)
from openscm_zenodo.metadata import Metadata
from openscm_zenodo.zenodo import (
    INVENIORDM_JSON_ACCEPT,
    FilesMode,
    ZenodoClient,
    create_or_get_new_version,
)

RECORD_ID = "1234"
NEW_VERSION_ID = "5678"

COMPLETE_METADATA = {
    "title": "A record",
    "resource_type": {"id": "dataset"},
    "creators": [
        {"person_or_org": {"type": "organizational", "name": "Climate Resource"}}
    ],
    "publication_date": "2026-07-28",
    "publisher": "Zenodo",
}
"""Metadata which is complete enough for Zenodo to publish"""


def draft_body(record_id=RECORD_ID, metadata=None):
    """
    Build a draft, as Zenodo's native serialisation describes one
    """
    return {
        "id": int(record_id),
        "is_draft": True,
        "is_published": False,
        "parent": {"id": "1230"},
        "metadata": COMPLETE_METADATA if metadata is None else metadata,
    }


@pytest.fixture
def client_and_session(no_token_in_env, make_recording_session):
    """A client whose session hands back whatever the test scripts"""

    def factory(responses):
        session = make_recording_session(responses)

        return ZenodoClient(token="a-token", session=session), session  # noqa: S106

    return factory


def test_create_or_get_new_version(client_and_session, make_response):
    client, session = client_and_session([make_response(json_body={"id": 5678})])

    new_version_id = client.create_or_get_new_version(RECORD_ID)

    assert new_version_id == NEW_VERSION_ID
    (call,) = session.calls
    assert call["method"] == "POST"
    assert call["url"] == f"https://zenodo.org/api/records/{RECORD_ID}/versions"


def test_create_or_get_new_version_returns_a_string(client_and_session, make_response):
    """
    Zenodo reports IDs as numbers, we always hand back strings
    """
    client, _ = client_and_session([make_response(json_body={"id": 5678})])

    assert client.create_or_get_new_version(RECORD_ID) == "5678"


def test_create_or_get_new_version_import_files(client_and_session, make_response):
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

    client.create_or_get_new_version(RECORD_ID, import_files=True)

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
    client, session = client_and_session([make_response(json_body=draft_body())])

    updated = client.update_metadata(RECORD_ID, Metadata(title="A new title"))

    (call,) = session.calls
    assert call["method"] == "PUT"
    assert call["url"] == f"https://zenodo.org/api/records/{RECORD_ID}/draft"
    # The metadata is nested, so settings outside it are left alone
    assert call["json"] == {"metadata": {"title": "A new title"}}
    # We ask for the native serialisation, so what comes back can be parsed
    assert call["headers"]["Accept"] == INVENIORDM_JSON_ACCEPT
    assert updated.record_id == RECORD_ID


def test_update_metadata_takes_a_metadata_object(client_and_session, make_response):
    """
    Metadata can be handed over as a `Metadata`, not only as JSON

    The display text Zenodo adds to vocabulary entries is dropped on the way
    out, so metadata read off one record can be applied to another as it is.
    """
    client, session = client_and_session([make_response(json_body=draft_body())])

    metadata = Metadata.from_json(
        {
            "title": "A new title",
            "resource_type": {"id": "dataset", "title": {"en": "Dataset"}},
        }
    )
    client.update_metadata(RECORD_ID, metadata)

    (call,) = session.calls
    assert call["json"] == {
        "metadata": {"title": "A new title", "resource_type": {"id": "dataset"}}
    }


def test_update_metadata_only_takes_metadata(client_and_session):
    """
    Metadata has to be a `Metadata` by the time it gets here

    Legacy-schema and malformed metadata are caught by `Metadata.from_json`,
    which is one place rather than every write method,
    and it keeps `Metadata | Mapping | Path` out of the client's signatures.
    """
    client, session = client_and_session([])

    with pytest.raises(AttributeError):
        client.update_metadata(RECORD_ID, {"title": "A new title"})

    assert session.calls == []


def test_update_metadata_published_record(client_and_session, make_response):
    """
    A published record with no draft is refused, with the way forward in the message

    Zenodo answers the `PUT` with a `404`, which does not say any of that.
    """
    client, _ = client_and_session(
        [
            # The `PUT`, which Zenodo refuses because there is no draft
            make_response(status_code=404, method="PUT"),
            # Our look at why, which finds the published record
            make_response(json_body={"id": 1234}),
        ]
    )

    with pytest.raises(
        RecordNotWritableError, match="create_or_get_edited_metadata_draft"
    ):
        client.update_metadata(RECORD_ID, Metadata(title="A new title"))


def test_update_metadata_record_which_is_not_there(client_and_session, make_response):
    """
    The same `404` from a record which does not exist says *that* instead
    """
    client, _ = client_and_session(
        [
            make_response(status_code=404, method="PUT"),
            make_response(status_code=404),
        ]
    )

    with pytest.raises(RecordNotFoundError):
        client.update_metadata(RECORD_ID, Metadata(title="A new title"))


def test_publish(client_and_session, make_response):
    client, session = client_and_session(
        [
            # The draft we validate before publishing
            make_response(json_body=draft_body()),
            make_response(json_body={"id": 1234}),
        ]
    )

    assert client.publish(RECORD_ID) == RECORD_ID

    call = session.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == (
        f"https://zenodo.org/api/records/{RECORD_ID}/draft/actions/publish"
    )


def test_publish_validates_first(client_and_session, make_response):
    """
    Metadata which Zenodo would reject at publish time is caught before we get there

    Publishing is the one step which cannot be undone,
    and it is also the only point at which Zenodo checks metadata,
    so this is the last chance to say something useful.
    """
    client, session = client_and_session(
        [make_response(json_body=draft_body(metadata={"title": "A title only"}))]
    )

    with pytest.raises(MetadataValidationError, match="publisher"):
        client.publish(RECORD_ID)

    # Nothing was published
    assert len(session.calls) == 1


def test_publish_without_validating(client_and_session, make_response):
    """
    Zenodo can be left to have the last word
    """
    client, session = client_and_session([make_response(json_body={"id": 1234})])

    assert client.publish(RECORD_ID, validate=False) == RECORD_ID

    (call,) = session.calls
    assert call["url"].endswith("/draft/actions/publish")


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
    """A stand-in for the client, recording what `create_or_get_new_version` asks"""

    def __init__(self):
        self.calls = []

    def create_or_get_new_version(self, record_id, *, import_files=False):
        self.calls.append(("create_or_get_new_version", record_id))

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
    new_version_id = create_or_get_new_version(RECORD_ID, recording_client, files=paths)

    assert new_version_id == NEW_VERSION_ID
    assert recording_client.calls == [
        ("create_or_get_new_version", RECORD_ID),
        ("upload_files", ["a.txt", "b.txt"]),
    ]


def test_create_new_version_inherit(recording_client, paths):
    create_or_get_new_version(
        RECORD_ID, recording_client, files=paths, files_mode=FilesMode.inherit
    )

    assert recording_client.calls == [
        ("create_or_get_new_version", RECORD_ID),
        ("import_files", NEW_VERSION_ID),
        ("upload_files", ["a.txt", "b.txt"]),
    ]


def test_create_new_version_mirror(recording_client, paths):
    create_or_get_new_version(
        RECORD_ID, recording_client, files=paths, files_mode=FilesMode.mirror
    )

    assert recording_client.calls == [
        ("create_or_get_new_version", RECORD_ID),
        ("import_files", NEW_VERSION_ID),
        ("mirror_files", ["a.txt", "b.txt"]),
    ]


def test_create_new_version_mirror_needs_files(recording_client):
    """
    Mirroring deletes, so it may not happen by omission
    """
    with pytest.raises(ValueError, match="`files` must be supplied"):
        create_or_get_new_version(
            RECORD_ID, recording_client, files_mode=FilesMode.mirror
        )

    assert recording_client.calls == []


def test_create_new_version_mirror_empty_is_allowed(recording_client):
    """
    Asking for a version with no files is fine, as long as you say so
    """
    create_or_get_new_version(
        RECORD_ID, recording_client, files=[], files_mode=FilesMode.mirror
    )

    assert recording_client.calls == [
        ("create_or_get_new_version", RECORD_ID),
        ("import_files", NEW_VERSION_ID),
        ("mirror_files", []),
    ]


def test_create_new_version_no_files(recording_client):
    create_or_get_new_version(RECORD_ID, recording_client)

    assert recording_client.calls == [("create_or_get_new_version", RECORD_ID)]


def test_create_new_version_metadata(recording_client):
    metadata = Metadata(title="Hello")
    create_or_get_new_version(RECORD_ID, recording_client, metadata=metadata)

    assert recording_client.calls == [
        ("create_or_get_new_version", RECORD_ID),
        ("update_metadata", metadata),
    ]


def test_create_new_version_publish(recording_client, paths):
    create_or_get_new_version(RECORD_ID, recording_client, files=paths, publish=True)

    assert recording_client.calls[-1] == ("publish", NEW_VERSION_ID)


def test_create_new_version_does_not_publish_by_default(recording_client, paths):
    create_or_get_new_version(RECORD_ID, recording_client, files=paths)

    assert not [call for call in recording_client.calls if call[0] == "publish"]


def test_update_metadata_warns_about_what_zenodo_discarded(
    client_and_session, make_response
):
    """
    A field Zenodo dropped is reported, whether or not any logging is configured

    Zenodo accepts a value it cannot parse with a `200` and stores nothing, so
    without this the field simply goes missing. The warning goes through
    `warnings.warn` rather than our logger precisely because the logger is off
    until a caller turns it on.
    """
    # Zenodo answers without the `version` we sent
    client, _ = client_and_session([make_response(json_body=draft_body())])

    with pytest.warns(OpenSCMZenodoWarning, match="Zenodo discarded 'version'"):
        client.update_metadata(
            RECORD_ID, Metadata.from_json({**COMPLETE_METADATA, "version": "v1"})
        )


def test_update_metadata_discarded_warning_can_be_silenced(
    client_and_session, make_response, recwarn
):
    client, _ = client_and_session([make_response(json_body=draft_body())])

    client.update_metadata(
        RECORD_ID,
        Metadata.from_json({**COMPLETE_METADATA, "version": "v1"}),
        warn_discarded=False,
    )

    assert [w for w in recwarn if issubclass(w.category, OpenSCMZenodoWarning)] == []


def test_update_metadata_warns_about_unknown_vocabulary(
    client_and_session, make_response
):
    client, _ = client_and_session([make_response(json_body=draft_body())])

    with pytest.warns(OpenSCMZenodoWarning, match="`resource_type` is 'pottery'"):
        client.update_metadata(
            RECORD_ID,
            Metadata.from_json(
                {**COMPLETE_METADATA, "resource_type": {"id": "pottery"}}
            ),
            # The response does not echo it back, which is a separate warning
            warn_discarded=False,
        )
