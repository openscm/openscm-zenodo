"""
Integration tests of publishing, against the Zenodo sandbox

Publishing cannot be undone, so unlike the other integration tests
these leave a record behind rather than cleaning up after themselves.
That is fine on the sandbox, but it is why they are kept few.
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    PublishedRecordDraftError,
    RecordNotWritableError,
)
from openscm_zenodo.zenodo import FilesMode, create_or_get_new_version

pytestmark = pytest.mark.zenodo_token


def test_publish(sandbox_client, draft_record_id, tmp_path):
    """
    A draft with files and metadata can be published
    """
    path = tmp_path / "published.txt"
    path.write_text("published contents\n")
    sandbox_client.upload_file(draft_record_id, path, progress=False)

    published_id = sandbox_client.publish(draft_record_id)

    assert published_id == draft_record_id

    record = sandbox_client._request(f"/api/records/{published_id}").json()
    assert record["id"] == int(published_id)
    # The files are readable without asking for the draft
    assert list(sandbox_client.list_files(published_id)) == ["published.txt"]


def test_create_new_version_publish(sandbox_client, draft_record_id, tmp_path):
    """
    The whole path, end to end: publish, new version, files, publish

    This starts its own chain rather than versioning a long-lived sandbox
    record. Versioning a shared one leaves a file behind on every run, and the
    legacy end-to-end test versions the same chain and counts the files on it.
    """
    first = tmp_path / "first.txt"
    first.write_text("the first version\n")
    sandbox_client.upload_file(draft_record_id, first, progress=False)
    first_id = sandbox_client.publish(draft_record_id)

    path = tmp_path / "release.txt"
    path.write_text("a released file\n")

    new_version_id = create_or_get_new_version(
        first_id,
        sandbox_client,
        files=[path],
        files_mode=FilesMode.mirror,
        publish=True,
        progress=False,
    )

    assert new_version_id != first_id
    assert list(sandbox_client.list_files(new_version_id)) == ["release.txt"]
    # A published version becomes the latest
    assert sandbox_client.get_latest_version_id(first_id) == new_version_id


def test_editing_a_published_record_in_place(
    sandbox_client, draft_record_id, build_metadata, tmp_path
):
    """
    A published record's metadata can be corrected, but only deliberately

    `update_metadata` refuses a published record which has no draft, because
    publishing that draft changes what a public record says under the same ID
    and DOI. Taking the draft is how you say that is what you meant.
    This is the live version of the decision made in Part 13.3.
    """
    path = tmp_path / "corrected.txt"
    path.write_text("a record needs a file to be publishable\n")
    sandbox_client.upload_file(draft_record_id, path, progress=False)

    sandbox_client.update_metadata(draft_record_id, build_metadata())
    published_id = sandbox_client.publish(draft_record_id)

    assert not sandbox_client.is_draft(published_id)
    assert not sandbox_client.has_edited_metadata_draft(published_id)

    with pytest.raises(
        RecordNotWritableError, match="create_or_get_edited_metadata_draft"
    ):
        sandbox_client.update_metadata(published_id, build_metadata("Corrected"))

    edits = sandbox_client.create_or_get_edited_metadata_draft(published_id)
    assert edits.is_edited_metadata_draft
    assert sandbox_client.has_edited_metadata_draft(published_id)
    # The record is still published: pending edits do not make it a draft
    assert not sandbox_client.is_draft(published_id)

    sandbox_client.update_metadata(published_id, build_metadata("Corrected"))

    # The published record still says the old thing until the draft is published
    assert sandbox_client.get_metadata(published_id).title != "Corrected"
    assert (
        sandbox_client.get_edited_metadata_draft(published_id).metadata.title
        == "Corrected"
    )
    # And `get_draft` refuses it, because a published record is not a draft
    with pytest.raises(PublishedRecordDraftError):
        sandbox_client.get_draft(published_id)

    assert sandbox_client.publish(published_id) == published_id

    assert sandbox_client.get_metadata(published_id).title == "Corrected"
