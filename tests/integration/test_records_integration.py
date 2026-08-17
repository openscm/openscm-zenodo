"""
Integration tests of creating records, access and DOI reservation, against the sandbox

Nothing here publishes, so every record these make is deleted again.
The publish-side story is in `test_publish_integration.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from openscm_zenodo.exceptions import (
    AccessNotPermittedError,
    OpenSCMZenodoWarning,
    RecordNotFoundError,
    ZenodoHTTPError,
)
from openscm_zenodo.metadata import Metadata
from openscm_zenodo.zenodo import (
    INVENIORDM_JSON_ACCEPT,
    Access,
    Embargo,
    was_field_sent,
)

pytestmark = pytest.mark.zenodo_token

HTTP_NOT_FOUND = 404
HTTP_FORBIDDEN = 403


def test_create_record(sandbox_client):
    """
    A record can be made from nothing, and comes back unpublished and empty
    """
    created = sandbox_client.create_record()

    try:
        assert created.is_draft
        assert not created.is_edited_metadata_draft
        # Zenodo mints two IDs: the record's, and its parent's
        assert created.record_id
        assert created.parent_id
        assert created.parent_id != created.record_id
        # Nothing is set until we set it, including the DOI
        assert created.metadata.title is None
        assert created.doi is None
        assert created.access.record == "public"

        # It is really there, and it is the same record
        assert sandbox_client.get_draft(created).record_id == created.record_id

    finally:
        sandbox_client.delete_draft(created.record_id)


def test_delete_draft(sandbox_client, in_a_working_directory):
    """
    An unpublished record, files and all, can be taken back off Zenodo
    """
    created = sandbox_client.create_record()
    path = Path("regrettable.txt")
    path.write_text("this should not have been uploaded\n")
    sandbox_client.upload_file(created, path, progress=False)

    sandbox_client.delete_draft(created)

    with pytest.raises(RecordNotFoundError):
        sandbox_client.get_record(created.record_id)


def test_delete_draft_of_a_record_which_is_not_there(sandbox_client):
    """
    A record which is not there is said to be missing, not deleted quietly
    """
    with pytest.raises(RecordNotFoundError):
        sandbox_client.delete_draft("0")


def test_a_new_record_is_invisible_to_everyone_else(sandbox_client, build_metadata):
    """
    An unpublished record is nobody else's business, whatever its access says

    This is why `create_record` takes no arguments: there is no window in which
    a new record's files are exposed, so nothing has to be set at creation. An
    unauthenticated request gets `404` on the published endpoints and `403` on
    the draft ones, with `access.files` left at Zenodo's default of "public".
    """
    created = sandbox_client.create_record()

    try:
        sandbox_client.update_metadata(created, build_metadata())
        assert created.access.files == "public"

        base = f"{sandbox_client.zenodo_domain_url}/api/records/{created.record_id}"
        no_token = {
            suffix: requests.get(f"{base}{suffix}", timeout=30).status_code
            for suffix in ("", "/files", "/draft", "/draft/files")
        }

        assert no_token == {
            "": HTTP_NOT_FOUND,
            "/files": HTTP_NOT_FOUND,
            "/draft": HTTP_FORBIDDEN,
            "/draft/files": HTTP_FORBIDDEN,
        }

    finally:
        sandbox_client.delete_draft(created.record_id)


def test_update_access_to_restricted_files(sandbox_client, draft_record_id):
    """
    Files can be kept private, with or without an embargo
    """
    updated = sandbox_client.update_access(
        draft_record_id,
        Access(
            files="restricted",
            embargo=Embargo(active=True, until="2030-01-01", reason="Testing the API"),
        ),
    )

    assert updated.access.record == "public"
    assert updated.access.files == "restricted"
    assert updated.access.embargo.active
    assert updated.access.embargo.until == "2030-01-01"
    assert updated.access.status == "embargoed"


def test_a_restricted_record_is_refused(sandbox_client, draft_record_id):
    """
    Restricted *records* are not reachable through the API

    Zenodo refuses `access.record="restricted"` for an ordinary account, even
    on a record it created moments ago, so this is about the field rather than
    ownership. We send it anyway rather than blocking it ourselves, and turn the
    refusal into something which says what to do instead. This pins Zenodo's
    behaviour, so we notice if it changes.
    """
    with pytest.raises(AccessNotPermittedError, match="only an admin"):
        sandbox_client.update_access(
            draft_record_id, Access(record="restricted", files="restricted")
        )


def test_files_cannot_be_disabled_and_zenodo_does_not_say_so_loudly(sandbox_client):
    """
    Zenodo reports some refusals in the response body rather than refusing

    A metadata-only record is asked for with `files.enabled: false`. Zenodo
    answers `201`, leaves files enabled, and mentions it only in `errors`.
    This is why `update_access` warns about the fields it sent which turn up
    there, and why we offer no parameter for this one.
    """
    response = sandbox_client._request(
        "/api/records",
        method="POST",
        requires_auth=True,
        headers={"Accept": INVENIORDM_JSON_ACCEPT},
        json={"files": {"enabled": False}},
    )
    body = response.json()

    try:
        assert body["files"]["enabled"] is True

        refusals = [
            error for error in body["errors"] if error["field"] == "files.enabled"
        ]
        assert "permissions" in refusals[0]["messages"][0]

        # And the rule which decides whether we warn agrees it was ours to hear
        assert was_field_sent({"files": {"enabled": False}}, "files.enabled")

    finally:
        sandbox_client.delete_draft(str(body["id"]))


def test_update_access_keeps_the_metadata(sandbox_client, draft_record_id):
    """
    The thing this method exists to get right

    An access-only `PUT` leaves the record with no metadata at all, so
    `update_access` reads the record and sends its metadata back. If that ever
    stops happening, this test loses a title.
    """
    metadata = Metadata(
        title="Access test, please ignore",
        resource_type="dataset",
        version="v9.9.9",
    )
    sandbox_client.update_metadata(draft_record_id, metadata)

    updated = sandbox_client.update_access(
        draft_record_id,
        Access(files="restricted", embargo=Embargo(active=True, until="2030-01-01")),
    )

    assert updated.access.files == "restricted"
    assert updated.access.embargo.active

    # And, the point of the test
    assert updated.metadata.title == "Access test, please ignore"
    assert updated.metadata.version == "v9.9.9"

    # Not just in the response we were handed
    read_back = sandbox_client.get_draft(draft_record_id)
    assert read_back.metadata.title == "Access test, please ignore"
    assert read_back.access.files == "restricted"


def test_update_access_can_lift_an_embargo(sandbox_client, draft_record_id):
    """
    An embargo can be taken off again, not only put on

    This is why `Embargo.to_json` always sends `active`.
    """
    sandbox_client.update_access(
        draft_record_id,
        Access(files="restricted", embargo=Embargo(active=True, until="2030-01-01")),
    )

    lifted = sandbox_client.update_access(draft_record_id, Access())

    assert lifted.access.files == "public"
    assert not lifted.access.embargo.active


def test_update_access_of_a_record_which_is_not_there(sandbox_client):
    with pytest.raises(RecordNotFoundError):
        sandbox_client.update_access("0", Access())


def test_reserve_or_get_doi(sandbox_client, draft_record_id):
    """
    A DOI can be reserved before publishing, and reserving twice is safe

    Zenodo itself refuses the second reservation
    (`400 A PID already exists for type doi`), so "reserve or get" is doing
    real work here: this is the test that a re-run of a release script does not
    fall over.
    """
    reserved = sandbox_client.reserve_or_get_doi(draft_record_id)

    assert reserved.startswith("10.")
    assert draft_record_id in reserved

    assert sandbox_client.reserve_or_get_doi(draft_record_id) == reserved

    # And it is on the record itself
    assert sandbox_client.get_draft(draft_record_id).doi == reserved


def test_a_reserved_doi_survives_a_metadata_update(
    sandbox_client, draft_record_id, build_metadata
):
    """
    Reserving early is only useful if the DOI is still there later

    The documented order is create, reserve, write the DOI into the metadata or
    files, publish, so a metadata update must not lose it.
    """
    reserved = sandbox_client.reserve_or_get_doi(draft_record_id)

    updated = sandbox_client.update_metadata(draft_record_id, build_metadata())

    assert updated.doi == reserved


def test_reserve_or_get_doi_for_a_record_which_is_not_there(sandbox_client):
    with pytest.raises(RecordNotFoundError):
        sandbox_client.reserve_or_get_doi("0")


def test_update_metadata_warns_about_unknown_vocabulary_before_zenodo_refuses_it(
    sandbox_client, draft_record_id
):
    """
    Our vocabulary check and Zenodo's agree, at least about resource types

    We warn rather than refuse, because Zenodo's vocabularies are longer than
    ours. Here it does refuse, which is the useful half of the check: the
    warning names the field before the request goes out, and Zenodo's own
    answer arrives right behind it.
    """
    metadata = Metadata(title="Vocabulary test", resource_type="not-a-resource-type")

    with (
        pytest.warns(OpenSCMZenodoWarning, match="vocabulary"),
        pytest.raises(ZenodoHTTPError, match="Invalid value not-a-resource-type"),
    ):
        sandbox_client.update_metadata(draft_record_id, metadata)
