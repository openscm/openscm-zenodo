"""
Integration tests of uploading, against the Zenodo sandbox

The premise of the rewrite is that Zenodo's API drifted out from under us
and nothing noticed, so every endpoint we call gets a test
that has hit the real thing.
"""

from __future__ import annotations

import pytest

from openscm_zenodo.checksums import get_file_md5
from openscm_zenodo.exceptions import ZenodoHTTPError
from openscm_zenodo.zenodo import ZenodoClient, ZenodoDomain

pytestmark = pytest.mark.zenodo_token

DRAFT_METADATA = {
    "access": {"record": "public", "files": "public"},
    "files": {"enabled": True},
    "metadata": {
        "title": "openscm-zenodo integration test, please ignore",
        "publication_date": "2026-07-25",
        "resource_type": {"id": "dataset"},
        "creators": [
            {
                "person_or_org": {
                    "type": "organizational",
                    "name": "openscm-zenodo test suite",
                }
            }
        ],
    },
}
"""
Metadata for the draft the tests upload to

The full metadata story is Part 6 of the rewrite,
this is just enough for the sandbox to accept a draft.
"""


@pytest.fixture
def sandbox_client():
    """A client pointed at the Zenodo sandbox"""
    with ZenodoClient(zenodo_domain=ZenodoDomain.sandbox) as client:
        yield client


@pytest.fixture
def draft_record_id(sandbox_client):
    """
    A draft to upload to, deleted once the test is done

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

    sandbox_client._request(
        f"/api/records/{record_id}/draft",
        method="DELETE",
        requires_auth=True,
    )


def get_remote_files(client, record_id):
    """Get a map of filename -> checksum for a draft's files"""
    response = client._request(
        f"/api/records/{record_id}/draft/files", requires_auth=True
    )

    return {entry["key"]: entry["checksum"] for entry in response.json()["entries"]}


def test_upload_file(sandbox_client, draft_record_id, tmp_path):
    """
    The init -> content -> commit flow works against the real API
    """
    path = tmp_path / "data.txt"
    path.write_text("Some contents for the integration test\n")

    entry = sandbox_client.upload_file(draft_record_id, path, progress=False)

    assert entry.key == "data.txt"
    assert entry.status == "completed"
    assert entry.checksum == f"md5:{get_file_md5(path)}"

    assert get_remote_files(sandbox_client, draft_record_id) == {
        "data.txt": entry.checksum
    }


def test_upload_file_strips_the_local_path(sandbox_client, draft_record_id, tmp_path):
    """
    Zenodo has no directories, so a nested file lands under its basename
    """
    path = tmp_path / "outputs" / "2024"
    path.mkdir(parents=True)
    path = path / "nested.txt"
    path.write_text("Nested\n")

    sandbox_client.upload_file(draft_record_id, path, progress=False)

    assert list(get_remote_files(sandbox_client, draft_record_id)) == ["nested.txt"]


def test_upload_file_twice_replaces_it(sandbox_client, draft_record_id, tmp_path):
    """
    Uploading the same name again replaces what is there

    A committed file's content cannot be overwritten,
    so this only works because we delete and initialise again.
    """
    path = tmp_path / "data.txt"

    path.write_text("First\n")
    sandbox_client.upload_file(draft_record_id, path, progress=False)

    path.write_text("Second, which is longer\n")
    entry = sandbox_client.upload_file(draft_record_id, path, progress=False)

    assert entry.checksum == f"md5:{get_file_md5(path)}"
    assert get_remote_files(sandbox_client, draft_record_id) == {
        "data.txt": entry.checksum
    }


def test_delete_file(sandbox_client, draft_record_id, tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("To be deleted\n")

    sandbox_client.upload_file(draft_record_id, path, progress=False)
    sandbox_client.delete_file(draft_record_id, "data.txt")

    assert get_remote_files(sandbox_client, draft_record_id) == {}


def test_delete_file_that_is_not_there(sandbox_client, draft_record_id):
    with pytest.raises(ZenodoHTTPError):
        sandbox_client.delete_file(draft_record_id, "never-existed.txt")
