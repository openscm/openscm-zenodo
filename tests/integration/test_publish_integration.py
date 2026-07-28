"""
Integration tests of publishing, against the Zenodo sandbox

Publishing cannot be undone, so unlike the other integration tests
these leave a record behind rather than cleaning up after themselves.
That is fine on the sandbox, but it is why they are kept few.
"""

from __future__ import annotations

import pytest

from openscm_zenodo.zenodo import FilesMode, create_new_version

pytestmark = pytest.mark.zenodo_token

VERSIONED_RECORD_ID = "101709"
"""The sandbox record the repository's test suite has always versioned"""


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


def test_create_new_version_publish(sandbox_client, tmp_path):
    """
    The whole path, end to end: new version, files, publish
    """
    path = tmp_path / "release.txt"
    path.write_text("a released file\n")

    new_version_id = create_new_version(
        VERSIONED_RECORD_ID,
        sandbox_client,
        files=[path],
        files_mode=FilesMode.mirror,
        publish=True,
        progress=False,
    )

    assert list(sandbox_client.list_files(new_version_id)) == ["release.txt"]
    # A published version becomes the latest
    assert sandbox_client.get_latest_version_id(VERSIONED_RECORD_ID) == new_version_id
