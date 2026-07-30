"""
Integration tests of reading drafts, against the Zenodo sandbox

Drafts are not visible without a token, so these need one.
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import RecordNotFoundError, ZenodoHTTPError
from openscm_zenodo.zenodo import CitationFormat


@pytest.mark.zenodo_token
def test_get_draft(sandbox_client, draft_record_id):
    """
    An unpublished draft can be read back
    """
    draft = sandbox_client.get_draft(draft_record_id)

    assert draft.record_id == draft_record_id
    assert draft.is_draft
    # A record which has never been published is a draft, not a draft *of* one
    assert not draft.is_edited_metadata_draft
    assert draft.metadata.title


@pytest.mark.zenodo_token
def test_get_metadata_of_a_draft(sandbox_client, draft_record_id):
    """
    A draft's metadata is found without having to say it is a draft
    """
    metadata = sandbox_client.get_metadata(draft_record_id)

    assert metadata == sandbox_client.get_draft(draft_record_id).metadata


@pytest.mark.zenodo_token
def test_get_parent_id_of_a_draft(sandbox_client, draft_record_id):
    """
    A draft has a parent from the moment it exists
    """
    parent_id = sandbox_client.get_parent_id(draft_record_id)

    assert parent_id
    assert parent_id != draft_record_id


@pytest.mark.zenodo_token
def test_get_published_of_a_draft(sandbox_client, draft_record_id):
    """
    An unpublished draft is not a record, and `get_published` says so

    This is the difference between `get_published` and `get_draft`:
    neither guesses, so a caller who needs one specifically can say which.
    """
    with pytest.raises(ZenodoHTTPError):
        sandbox_client.get_published(draft_record_id)


@pytest.mark.zenodo_token
def test_get_record_of_a_draft(sandbox_client, draft_record_id):
    """
    `get_record` finds a draft, which is the difference from `get_published`

    It falls back to the draft endpoint when there is no published record,
    so a caller who does not know which they have does not have to find out.
    """
    record = sandbox_client.get_record(draft_record_id)

    assert record == sandbox_client.get_draft(draft_record_id)
    assert record.is_draft


@pytest.mark.zenodo_token
def test_get_metadata_of_a_record_which_is_not_there(sandbox_client):
    """
    With a token, a record we cannot find names the token we looked with
    """
    with pytest.raises(RecordNotFoundError, match="sandbox"):
        sandbox_client.get_metadata("0")


@pytest.mark.zenodo_token
def test_get_citation_of_a_draft(sandbox_client, draft_record_id):
    """
    A draft has no citation to give

    Zenodo's export formats are for records, so this is a `404`.
    Worth pinning: it is the reason `retrieve-citation`
    is documented as something you do to a published record.
    """
    with pytest.raises(ZenodoHTTPError):
        sandbox_client.get_citation(draft_record_id, fmt=CitationFormat.bibtex)
