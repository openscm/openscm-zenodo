"""
Fixtures for the tests which hit Zenodo for real
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import ZenodoHTTPError
from openscm_zenodo.metadata import Creator, Metadata, Subject
from openscm_zenodo.zenodo import ZenodoClient, ZenodoDomain, resolve_token

HTTP_NOT_FOUND = 404


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
def sandbox_client():
    """A client pointed at the Zenodo sandbox"""
    with ZenodoClient(zenodo_domain=ZenodoDomain.sandbox) as client:
        yield client


@pytest.fixture
def legacy_zenodo_token(monkeypatch):
    """
    Put the sandbox token in `ZENODO_TOKEN`, where the legacy code looks for it

    **TO BE DELETED** with the legacy tests it serves (plan Part 8):
    `test_flow.py` and `test_cli.py`, and their `_token_where_the_legacy_code_looks`
    fixtures.

    `ZenodoInteractor` is handed `os.environ["ZENODO_TOKEN"]` directly, and the
    legacy CLI declares `envvar="ZENODO_TOKEN"`, which typer resolves before any
    of our code runs. Neither can see a token which lives only in
    `ZENODO_SANDBOX_TOKEN`, which is where a sandbox token belongs. So the tests
    which still exercise them map one onto the other, the same way CI does
    today. This goes when the CLI is trimmed and the legacy paths go with it.
    """
    resolved = resolve_token(zenodo_domain=ZenodoDomain.sandbox)
    if resolved.token is None:  # pragma: no cover - the marker skips first
        pytest.skip("no Zenodo sandbox token")

    monkeypatch.setenv("ZENODO_TOKEN", resolved.token)

    return resolved.token


@pytest.fixture
def draft_record_id(sandbox_client):
    """
    An unpublished record to work on, deleted once the test is done

    Deleting still goes through `_request`: creating a record is public API,
    deleting one is not (yet).
    """
    record_id = sandbox_client.create_record().record_id
    sandbox_client.update_metadata(record_id, DRAFT_METADATA)

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
