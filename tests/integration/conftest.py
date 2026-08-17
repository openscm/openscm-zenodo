"""
Fixtures for the tests which hit Zenodo for real
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import PublishedRecordDraftError
from openscm_zenodo.metadata import Creator, Metadata, Subject
from openscm_zenodo.zenodo import ZenodoClient, ZenodoDomain


@pytest.fixture
def build_metadata():
    """
    Get a factory for metadata which is complete enough for Zenodo to publish
    """

    def factory(title="openscm-zenodo integration test, please ignore"):
        return Metadata(
            title=title,
            resource_type="dataset",
            creators=(
                Creator.person(
                    "Nicholls",
                    "Zebedee",
                    orcid="0000-0002-4767-2723",
                    affiliations=["Climate Resource"],
                ),
                Creator.organisation("openscm-zenodo test suite"),
            ),
            publication_date="2026-07-28",
            publisher="Zenodo",
            description="Metadata written by the test suite",
            version="v1.2.3",
            subjects=(Subject(subject="climate"),),
            raw={
                "additional_titles": [
                    {
                        "title": "Another title",
                        "type": {"id": "alternative-title"},
                    }
                ]
            },
        )

    return factory


DRAFT_METADATA = Metadata(
    title="openscm-zenodo integration test, please ignore",
    publication_date="2026-07-26",
    resource_type="dataset",
    creators=(Creator.organisation("openscm-zenodo test suite"),),
    # Only required at publish time, not to create the record
    publisher="Zenodo",
)
"""
Metadata for the record the tests write to

This is just enough for the sandbox to accept a record and then publish it.
Zenodo validates metadata when a record is published, not when it is created,
so a record can be created with less than this.
"""


@pytest.fixture
def in_a_working_directory(tmp_path, monkeypatch):
    """
    Run a test in its own directory

    Files to upload can then be created under bare names, so uploading them
    strips no path and does not warn about it.
    """
    monkeypatch.chdir(tmp_path)

    return tmp_path


@pytest.fixture
def sandbox_client():
    """A client pointed at the Zenodo sandbox"""
    with ZenodoClient(zenodo_domain=ZenodoDomain.sandbox) as client:
        yield client


@pytest.fixture
def draft_record_id(sandbox_client):
    """An unpublished record to work on, deleted once the test is done"""
    record_id = sandbox_client.create_record().record_id
    sandbox_client.update_metadata(record_id, DRAFT_METADATA)

    yield record_id

    try:
        sandbox_client.delete_draft(record_id)

    except PublishedRecordDraftError:
        # A draft which the test published is no longer a draft, and a
        # published record cannot be deleted, so there is nothing to clean up
        pass
