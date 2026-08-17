"""
Tests of creating records, and of the parts of a record which are not its files or metadata
"""  # noqa: E501

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    DraftMetadataEditsNotFoundError,
    DraftRecordDraftMetadataEditsError,
    OpenSCMZenodoWarning,
    PublishedRecordDraftError,
    RecordNotFoundError,
    RecordNotWritableError,
    ZenodoError,
    ZenodoHTTPError,
)
from openscm_zenodo.metadata import Creator, Metadata
from openscm_zenodo.zenodo import (
    INVENIORDM_JSON_ACCEPT,
    Access,
    ZenodoClient,
)

RECORD_ID = "1234"
RECORD_URL = f"https://zenodo.org/api/records/{RECORD_ID}"
DRAFT_URL = f"{RECORD_URL}/draft"

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


def record_body(  # noqa: PLR0913
    record_id=RECORD_ID,
    metadata=None,
    access=None,
    pids=None,
    errors=None,
    is_published=False,
):
    """
    Build a record, as Zenodo's native serialisation describes one
    """
    res = {
        "id": int(record_id),
        "is_draft": not is_published,
        "is_published": is_published,
        "parent": {"id": "1230"},
        "metadata": COMPLETE_METADATA if metadata is None else metadata,
        "access": access
        if access is not None
        else {"record": "public", "files": "public"},
        "pids": {} if pids is None else pids,
    }

    if errors is not None:
        res["errors"] = errors

    return res


@pytest.fixture
def client_and_session(no_token_in_env, make_recording_session):
    """A client whose session hands back whatever the test scripts"""

    def factory(responses):
        session = make_recording_session(responses)

        return ZenodoClient(token="a-token", session=session), session  # noqa: S106

    return factory


@pytest.fixture
def metadata():
    """Metadata which is complete enough for Zenodo to publish"""
    return Metadata(
        title="A record",
        resource_type="dataset",
        creators=(Creator.organisation("Climate Resource"),),
        publication_date="2026-07-28",
        publisher="Zenodo",
    )


def test_create_record(client_and_session, make_response):
    client, session = client_and_session(
        [make_response(status_code=201, json_body=record_body())]
    )

    created = client.create_record()

    assert created.record_id == RECORD_ID
    assert created.is_draft
    assert created.parent_id == "1230"

    (call,) = session.calls
    assert call["method"] == "POST"
    assert call["url"] == "https://zenodo.org/api/records"
    assert call["headers"]["Accept"] == INVENIORDM_JSON_ACCEPT


def test_create_record_asks_for_nothing(client_and_session, make_response):
    """
    Creating a record sets nothing, so it sends nothing

    A new record is unpublished, and an unpublished record is invisible to
    anyone without access to it, so there is nothing which has to be right
    before `update_metadata`, `update_access` and `upload_files` get their turn.
    Keeping metadata and access out of here keeps one home for each.
    """
    client, session = client_and_session(
        [make_response(status_code=201, json_body=record_body())]
    )

    client.create_record()

    (call,) = session.calls
    assert call["json"] == {}


def test_create_record_says_nothing_about_metadata(
    client_and_session, make_response, recwarn
):
    """
    Creating a record sends no metadata, so it has nothing to say about any

    The vocabulary and silent-discard warnings belong to `update_metadata`,
    which is the one thing which sets metadata.
    """
    client, _ = client_and_session(
        [
            make_response(
                status_code=201,
                json_body=record_body(
                    metadata={},
                    errors=[
                        {
                            "field": "metadata.title",
                            "messages": ["Missing data for required field."],
                        }
                    ],
                ),
            )
        ]
    )

    client.create_record()

    assert [w for w in recwarn if issubclass(w.category, OpenSCMZenodoWarning)] == []


def test_delete_draft(client_and_session, make_response):
    client, session = client_and_session(
        [
            # The draft we are about to delete
            make_response(json_body=record_body()),
            make_response(status_code=204),
        ]
    )

    client.delete_draft(RECORD_ID)

    call = session.calls[-1]
    assert call["method"] == "DELETE"
    assert call["url"] == DRAFT_URL


def test_delete_draft_of_a_published_record(client_and_session, make_response):
    """
    Nothing is sent: a published record cannot be deleted, so we do not try
    """
    client, session = client_and_session(
        [
            make_response(status_code=404, url=DRAFT_URL),
            # Our look at why, which finds the published record
            make_response(json_body=record_body(is_published=True)),
        ]
    )

    with pytest.raises(PublishedRecordDraftError):
        client.delete_draft(RECORD_ID)

    assert [call["method"] for call in session.calls] == ["GET", "GET"]


def test_delete_draft_of_a_record_with_metadata_edits(
    client_and_session, make_response
):
    """
    Pending metadata edits are not a draft record, and are not deleted here

    The same endpoint serves both and would have answered this one, so refusing
    it is ours to do. It is also the case where obeying would have been the
    wrong kind of destructive: `DELETE` on these discards the edits and leaves
    the published record alone.
    """
    edits = record_body()
    # Both, which is the shape only an edited metadata draft has
    edits["is_published"] = True

    client, session = client_and_session([make_response(json_body=edits)])

    with pytest.raises(PublishedRecordDraftError, match="is published"):
        client.delete_draft(RECORD_ID)

    assert [call["method"] for call in session.calls] == ["GET"]


def test_delete_draft_of_a_record_which_is_not_there(client_and_session, make_response):
    client, session = client_and_session(
        [
            make_response(status_code=404, url=DRAFT_URL),
            make_response(status_code=404, url=RECORD_URL),
        ]
    )

    with pytest.raises(RecordNotFoundError):
        client.delete_draft(RECORD_ID)

    assert [call["method"] for call in session.calls] == ["GET", "GET"]


def test_discard_edited_metadata_draft(client_and_session, make_response):
    edits = record_body()
    edits["is_published"] = True

    client, session = client_and_session(
        [
            # `is_draft`, which finds the published record
            make_response(json_body=record_body(is_published=True)),
            make_response(json_body=edits),
            make_response(status_code=204),
        ]
    )

    client.discard_edited_metadata_draft(RECORD_ID)

    call = session.calls[-1]
    assert call["method"] == "DELETE"
    assert call["url"] == DRAFT_URL


def test_discard_edited_metadata_draft_of_a_draft(client_and_session, make_response):
    """
    An unpublished record has no separate edits, and is not deleted by asking

    Getting this wrong would destroy the record, which is why the guard is not
    left to Zenodo: the request the two cases send is identical.
    """
    client, session = client_and_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=record_body()),
        ]
    )

    with pytest.raises(DraftRecordDraftMetadataEditsError, match="already a draft"):
        client.discard_edited_metadata_draft(RECORD_ID)

    assert [call["method"] for call in session.calls] == ["GET", "GET"]


def test_discard_edited_metadata_draft_when_there_are_none(
    client_and_session, make_response
):
    client, session = client_and_session(
        [
            make_response(json_body=record_body(is_published=True)),
            make_response(status_code=404, url=DRAFT_URL),
            # Our look at why, which finds the published record
            make_response(json_body=record_body(is_published=True)),
        ]
    )

    with pytest.raises(DraftMetadataEditsNotFoundError):
        client.discard_edited_metadata_draft(RECORD_ID)

    assert [call["method"] for call in session.calls] == ["GET", "GET", "GET"]


def test_update_access_sends_the_metadata_back(client_and_session, make_response):
    """
    An access-only `PUT` wipes the metadata, so we always send it back

    Verified against the sandbox: Zenodo's `PUT` is a replace for `metadata`
    and a preserve for `access`, which is not symmetric. This is the regression
    test for that, and it is the reason `update_access` reads before it writes.
    """
    client, session = client_and_session(
        [
            make_response(json_body=record_body()),
            make_response(json_body=record_body()),
        ]
    )

    client.update_access(RECORD_ID, Access(files="restricted"))

    read, write = session.calls
    assert read["method"] == "GET"
    assert read["url"] == f"https://zenodo.org/api/records/{RECORD_ID}/draft"

    assert write["method"] == "PUT"
    assert write["url"] == f"https://zenodo.org/api/records/{RECORD_ID}/draft"
    assert write["json"]["metadata"] == COMPLETE_METADATA
    assert write["json"]["access"] == {
        "record": "public",
        "files": "restricted",
        "embargo": {"active": False},
    }


def test_update_access_sends_zenodos_own_metadata_back(
    client_and_session, make_response
):
    """
    What goes back is what Zenodo holds, not a round trip through `Metadata`

    The point of the call is that the metadata does not change, so anything our
    own parsing might do to it on the way through is a risk with no upside.
    """
    with_extras = {
        **COMPLETE_METADATA,
        "something_we_do_not_model": {"nested": ["values"]},
    }
    client, session = client_and_session(
        [
            make_response(json_body=record_body(metadata=with_extras)),
            make_response(json_body=record_body(metadata=with_extras)),
        ]
    )

    client.update_access(RECORD_ID, Access())

    _, write = session.calls
    assert write["json"]["metadata"] == with_extras


def test_update_access_of_a_published_record(client_and_session, make_response):
    client, _ = client_and_session(
        [
            # The read of the draft, which is not there
            make_response(status_code=404),
            # The check for a published record, which is
            make_response(json_body=record_body(is_published=True)),
        ]
    )

    with pytest.raises(RecordNotWritableError, match="access"):
        client.update_access(RECORD_ID, Access())


def test_update_access_of_a_record_which_is_not_there(
    client_and_session, make_response
):
    client, _ = client_and_session(
        [make_response(status_code=404), make_response(status_code=404)]
    )

    with pytest.raises(RecordNotFoundError):
        client.update_access(RECORD_ID, Access())


def test_update_access_warns_about_what_zenodo_ignored(
    client_and_session, make_response
):
    client, _ = client_and_session(
        [
            make_response(json_body=record_body()),
            make_response(
                json_body=record_body(
                    errors=[{"field": "access.record", "messages": ["No permission"]}]
                )
            ),
        ]
    )

    with pytest.warns(OpenSCMZenodoWarning, match="access.record"):
        client.update_access(RECORD_ID, Access())


DOI = "10.5072/zenodo.1234"
PIDS = {"doi": {"identifier": DOI, "provider": "datacite"}}


def test_reserve_or_get_doi(client_and_session, make_response):
    client, session = client_and_session(
        [
            # The record is asked first, and does not have one yet
            make_response(status_code=404),
            make_response(json_body=record_body()),
            # The reservation
            make_response(status_code=201, json_body=record_body(pids=PIDS)),
        ]
    )

    assert client.reserve_or_get_doi(RECORD_ID) == DOI

    reserve = session.calls[-1]
    assert reserve["method"] == "POST"
    assert reserve["url"] == (
        f"https://zenodo.org/api/records/{RECORD_ID}/draft/pids/doi"
    )


def test_reserve_or_get_doi_when_one_is_already_reserved(
    client_and_session, make_response
):
    """
    A record which already has a DOI hands it back, and nothing is reserved

    Zenodo refuses a second reservation with
    `400 A PID already exists for type doi`, so asking the record first is what
    makes a release script which failed part way through safe to re-run, in the
    same way `inherit_files` and `create_or_get_new_version` are.
    """
    client, session = client_and_session(
        [
            make_response(status_code=404),
            make_response(json_body=record_body(pids=PIDS)),
        ]
    )

    assert client.reserve_or_get_doi(RECORD_ID) == DOI

    assert [call["method"] for call in session.calls] == ["GET", "GET"]


def test_reserve_or_get_doi_of_a_published_record(client_and_session, make_response):
    """
    A published record has a DOI, so it is handed back rather than refused

    Reserving one is impossible and asking for one is not a mistake: the method
    says "or get", and the DOI is the answer to the question.
    """
    client, session = client_and_session(
        [make_response(json_body=record_body(is_published=True, pids=PIDS))]
    )

    assert client.reserve_or_get_doi(RECORD_ID) == DOI

    (call,) = session.calls
    assert call["method"] == "GET"


def test_reserve_or_get_doi_when_somebody_reserves_one_first(
    client_and_session, make_response
):
    """
    A `400` after our read means somebody got there in between, so we re-read
    """
    client, _ = client_and_session(
        [
            make_response(status_code=404),
            make_response(json_body=record_body()),
            make_response(
                status_code=400,
                json_body={
                    "errors": [
                        {
                            "field": "pids.doi",
                            "messages": ["A PID already exists for type doi"],
                        }
                    ]
                },
            ),
            make_response(json_body=record_body(pids=PIDS)),
        ]
    )

    assert client.reserve_or_get_doi(RECORD_ID) == DOI


def test_reserve_or_get_doi_reraises_a_400_which_is_not_about_an_existing_doi(
    client_and_session, make_response
):
    """
    We ask the record rather than reading Zenodo's complaint

    A `400` with no DOI on the draft afterwards was a different problem, and
    swallowing it would be pretending we had done something we had not.
    """
    client, _ = client_and_session(
        [
            make_response(status_code=404),
            make_response(json_body=record_body()),
            make_response(status_code=400, json_body={"message": "Something else"}),
            make_response(json_body=record_body()),
        ]
    )

    with pytest.raises(ZenodoHTTPError):
        client.reserve_or_get_doi(RECORD_ID)


def test_reserve_or_get_doi_on_a_record_which_is_not_there(
    client_and_session, make_response
):
    # Not published, no draft, and still not published when we look again
    client, _ = client_and_session([make_response(status_code=404)] * 3)

    with pytest.raises(RecordNotFoundError):
        client.reserve_or_get_doi(RECORD_ID)


def test_reserve_or_get_doi_without_a_doi_in_the_response(
    client_and_session, make_response
):
    """
    Zenodo said yes and then did not give us one, which we do not paper over
    """
    client, _ = client_and_session(
        [
            make_response(status_code=404),
            make_response(json_body=record_body()),
            make_response(status_code=201, json_body=record_body()),
        ]
    )

    with pytest.raises(ZenodoError, match="does not have one"):
        client.reserve_or_get_doi(RECORD_ID)


def test_access_to_json_round_trip():
    """
    What Zenodo sends us, we can send back
    """
    raw = {
        "record": "public",
        "files": "restricted",
        "embargo": {"active": True, "until": "2030-01-01", "reason": "Not yet"},
        "status": "embargoed",
    }

    as_json = Access.from_json(raw).to_json()

    assert as_json == {key: value for key, value in raw.items() if key != "status"}
