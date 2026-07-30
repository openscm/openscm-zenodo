"""
Integration tests of the metadata write path, against the Zenodo sandbox

Zenodo only checks metadata when a record is published,
so most of what is worth pinning here is what it accepts *without* complaint.
The tests which publish live in `test_publish_integration.py`,
because publishing cannot be undone.
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    DraftRecordDraftMetadataEditsError,
    MetadataValidationError,
    RecordNotFoundError,
    ZenodoHTTPError,
)
from openscm_zenodo.metadata import Metadata, Subject
from openscm_zenodo.zenodo import ZenodoClient

pytestmark = pytest.mark.zenodo_token

PUBLISHED_PRODUCTION_RECORD_ID = "4589756"
"""A published production record whose metadata we copy onto the sandbox"""


def test_update_metadata_round_trip(sandbox_client, draft_record_id, build_metadata):
    """
    What we send is what comes back

    This is the check that our idea of the InvenioRDM schema
    matches the one Zenodo is actually running.
    """
    metadata = build_metadata()

    updated = sandbox_client.update_metadata(draft_record_id, metadata)

    for got in (updated.metadata, sandbox_client.get_metadata(draft_record_id)):
        assert got.title == metadata.title
        assert got.resource_type == metadata.resource_type
        assert got.publication_date == metadata.publication_date
        assert got.publisher == metadata.publisher
        assert got.version == metadata.version
        assert got.subjects == (Subject(subject="climate"),)
        assert got.raw["additional_titles"][0]["title"] == "Another title"

        person, organisation = got.creators
        assert person.entity.family_name == "Nicholls"
        assert person.entity.identifiers[0].identifier == "0000-0002-4767-2723"
        assert person.affiliations[0].name == "Climate Resource"
        assert organisation.name == "openscm-zenodo test suite"


def test_metadata_from_a_published_record_can_be_applied_to_another(
    sandbox_client, draft_record_id
):
    """
    Metadata read off one record goes onto another without being cleaned up first

    Zenodo expands vocabulary entries on the way out (titles, descriptions,
    icons), so this is the test that `to_json` hands back the shape Zenodo wants
    rather than the shape it sent.
    """
    with ZenodoClient(token=None) as production:
        metadata = production.get_metadata(PUBLISHED_PRODUCTION_RECORD_ID)

    updated = sandbox_client.update_metadata(draft_record_id, metadata)

    assert updated.metadata.title == metadata.title
    assert updated.metadata.rights[0].id == "cc-by-sa-4.0"
    assert updated.metadata.creators[0].name == metadata.creators[0].name


def test_update_metadata_replaces_rather_than_merges(
    sandbox_client, draft_record_id, build_metadata
):
    """
    A second update is the new metadata, not the two of them combined
    """
    sandbox_client.update_metadata(draft_record_id, build_metadata())

    updated = sandbox_client.update_metadata(
        draft_record_id, Metadata(title="A second title")
    )

    assert updated.metadata.title == "A second title"
    assert updated.metadata.version is None


def test_update_metadata_leaves_access_alone(
    sandbox_client, draft_record_id, build_metadata
):
    """
    Settings outside the metadata are not collateral damage

    `PUT /draft` takes the whole record, so nesting the metadata under
    `metadata` is what keeps `access` where it was.
    """
    before = sandbox_client.get_draft(draft_record_id).access

    updated = sandbox_client.update_metadata(draft_record_id, build_metadata())

    assert updated.access == before


def test_publish_with_incomplete_metadata(sandbox_client, draft_record_id):
    """
    We refuse to publish metadata Zenodo would reject, before asking it to

    Zenodo's own answer is a `400` naming one field at a time,
    and it only comes at the step which cannot be undone.
    Nothing is published here, so this test leaves nothing behind.
    """
    sandbox_client.update_metadata(
        draft_record_id, Metadata(title="A title and no more")
    )

    with pytest.raises(MetadataValidationError) as exc_info:
        sandbox_client.publish(draft_record_id)

    problems = exc_info.value.problems
    for field in ("resource_type", "creators", "publication_date", "publisher"):
        assert any(field in problem for problem in problems)

    # Zenodo agrees, which is the point of validating in the first place.
    # It reports one round of failures at a time, which is the other point:
    # `publisher` is not in this list, it only surfaces once the rest are there.
    with pytest.raises(ZenodoHTTPError, match="Missing data for required field"):
        sandbox_client.publish(draft_record_id, validate=False)


def test_publisher_is_only_required_at_publish(
    sandbox_client, draft_record_id, tmp_path, build_metadata
):
    """
    Zenodo's last-round complaint is the one we would otherwise be caught by

    Metadata which is complete apart from `publisher` is accepted by every
    endpoint until the irreversible one, which is exactly why we check first.
    """
    path = tmp_path / "a-file.txt"
    path.write_text("a record needs a file to be publishable\n")
    sandbox_client.upload_file(draft_record_id, path, progress=False)

    without_publisher = build_metadata()
    without_publisher.publisher = None

    # The draft accepts it without a word
    sandbox_client.update_metadata(draft_record_id, without_publisher)

    with pytest.raises(ZenodoHTTPError, match="publisher"):
        sandbox_client.publish(draft_record_id, validate=False)

    (problem,) = without_publisher.find_problems()
    assert "publisher" in problem


def test_metadata_edits_do_not_apply_to_an_unpublished_draft(
    sandbox_client, draft_record_id
):
    """
    A record which was never published is already a draft

    There is nothing separate to edit, and both methods say so rather than
    doing something surprising.
    """
    for call in (
        sandbox_client.create_or_get_edited_metadata_draft,
        sandbox_client.get_edited_metadata_draft,
        sandbox_client.has_edited_metadata_draft,
    ):
        with pytest.raises(DraftRecordDraftMetadataEditsError):
            call(draft_record_id)


def test_update_metadata_of_a_record_which_is_not_there(sandbox_client, build_metadata):
    """
    A `404` from a record which does not exist is not reported as "published"
    """
    with pytest.raises(RecordNotFoundError):
        sandbox_client.update_metadata("0", build_metadata())
