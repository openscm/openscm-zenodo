"""
Fixtures for the tests which hit Zenodo for real
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import ZenodoHTTPError
from openscm_zenodo.zenodo import ZenodoClient, ZenodoDomain

HTTP_NOT_FOUND = 404

DRAFT_METADATA = {
    "access": {"record": "public", "files": "public"},
    "files": {"enabled": True},
    "metadata": {
        "title": "openscm-zenodo integration test, please ignore",
        "publication_date": "2026-07-26",
        "resource_type": {"id": "dataset"},
        "creators": [
            {
                "person_or_org": {
                    "type": "organizational",
                    "name": "openscm-zenodo test suite",
                }
            }
        ],
        # Only required at publish time, not to create the draft
        "publisher": "Zenodo",
    },
}
"""
Metadata for the draft the tests upload to

The full metadata story is Part 6 of the rewrite,
this is just enough for the sandbox to accept a draft and then publish it.
Zenodo validates metadata when a record is published, not when it is drafted,
so a draft can be created with less than this.
"""


@pytest.fixture
def sandbox_client():
    """A client pointed at the Zenodo sandbox"""
    with ZenodoClient(zenodo_domain=ZenodoDomain.sandbox) as client:
        yield client


@pytest.fixture
def draft_record_id(sandbox_client):
    """
    A draft to work on, deleted once the test is done

    This creates the draft through `_request` because `create_record`
    does not exist yet; swap it over when it lands.
    """
    response = sandbox_client._request(
        "/api/records",
        method="POST",
        requires_auth=True,
        json=DRAFT_METADATA,
    )
    record_id = str(response.json()["id"])

    yield record_id

    try:
        sandbox_client._request(
            f"/api/records/{record_id}/draft",
            method="DELETE",
            requires_auth=True,
        )

    except ZenodoHTTPError as exc:
        # A draft which was published is no longer a draft, so there is
        # nothing left to clean up. Anything else is worth knowing about.
        if exc.response.status_code != HTTP_NOT_FOUND:
            raise
