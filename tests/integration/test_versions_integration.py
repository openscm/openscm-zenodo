"""
Integration tests of new versions, against the Zenodo sandbox

These all work on drafts, which can be deleted again.
The publishing path is in `tests/integration/test_publish_integration.py`,
because publishing cannot be undone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openscm_zenodo.checksums import get_file_md5
from openscm_zenodo.exceptions import ZenodoHTTPError
from openscm_zenodo.metadata import Metadata
from openscm_zenodo.zenodo import FilesMode, create_or_get_new_version

pytestmark = pytest.mark.zenodo_token

VERSIONED_RECORD_ID = "101709"
"""
A published sandbox record which already has several versions

This is the record the repository's test suite has always used,
and it is deliberately not the latest version of itself,
which is what lets us check that any version can be used as a starting point.
"""


@pytest.fixture
def new_version_draft(sandbox_client):
    """A new version of the versioned record, deleted once the test is done"""
    record_id = sandbox_client.create_or_get_new_version(VERSIONED_RECORD_ID)

    yield record_id

    sandbox_client._request(
        f"/api/records/{record_id}/draft", method="DELETE", requires_auth=True
    )


def test_new_version_starts_empty(sandbox_client, new_version_draft):
    """
    A new version has no files until they are asked for
    """
    assert sandbox_client.list_files(new_version_draft) == {}


def test_new_version_is_get_or_create(sandbox_client, new_version_draft):
    """
    A record has at most one unpublished next version

    This is what makes a release script safe to re-run,
    so it is worth pinning against Zenodo's actual build
    rather than trusting upstream InvenioRDM's behaviour.
    """
    again = sandbox_client.create_or_get_new_version(VERSIONED_RECORD_ID)

    assert again == new_version_draft


def test_new_version_from_a_non_latest_version(sandbox_client, new_version_draft):
    """
    Any published version can be the starting point, not just the latest

    The legacy API required the latest version's ID,
    which is why the old code looked it up first.
    """
    latest = sandbox_client.get_latest_version_id(VERSIONED_RECORD_ID)
    assert latest != VERSIONED_RECORD_ID

    from_latest = sandbox_client.create_or_get_new_version(latest)

    assert from_latest == new_version_draft


def test_import_files(sandbox_client, new_version_draft):
    previous = sandbox_client.list_files(
        sandbox_client.get_latest_version_id(VERSIONED_RECORD_ID)
    )
    assert previous

    assert sandbox_client.import_files(new_version_draft) is True

    assert sandbox_client.list_files(new_version_draft).keys() == previous.keys()


def test_import_files_skips_when_the_draft_has_files(sandbox_client, new_version_draft):
    """
    Zenodo will not import into a draft which already has files

    Skipping rather than failing is what lets a release script be re-run.
    """
    sandbox_client.import_files(new_version_draft)

    assert sandbox_client.import_files(new_version_draft) is False


def test_import_files_into_a_draft_with_files_would_fail(
    sandbox_client, new_version_draft, in_a_working_directory
):
    """
    Pin the behaviour we are working around, so we notice if Zenodo changes it
    """
    path = Path("in-the-way.txt")
    path.write_text("in the way\n")
    sandbox_client.upload_file(new_version_draft, path, progress=False)

    with pytest.raises(ZenodoHTTPError, match="remove all files first"):
        sandbox_client._request(
            f"/api/records/{new_version_draft}/draft/actions/files-import",
            method="POST",
            requires_auth=True,
        )


def test_update_metadata(sandbox_client, new_version_draft):
    title = "openscm-zenodo integration test, please ignore"

    sandbox_client.update_metadata(new_version_draft, Metadata(title=title))

    draft = sandbox_client._request(
        f"/api/records/{new_version_draft}/draft", requires_auth=True
    ).json()
    assert draft["metadata"]["title"] == title


def test_create_new_version_start_fresh(sandbox_client, in_a_working_directory):
    path = Path("fresh.txt")
    path.write_text("fresh contents\n")

    new_version_id = create_or_get_new_version(
        VERSIONED_RECORD_ID, sandbox_client, files=[path], progress=False
    )

    try:
        files = sandbox_client.list_files(new_version_id)

        # Nothing was carried over, only what we passed
        assert list(files) == ["fresh.txt"]
        assert files["fresh.txt"].md5 == get_file_md5(path)

    finally:
        sandbox_client._request(
            f"/api/records/{new_version_id}/draft",
            method="DELETE",
            requires_auth=True,
        )


def test_create_new_version_inherit(sandbox_client, in_a_working_directory):
    path = Path("extra.txt")
    path.write_text("an extra file\n")

    previous = sandbox_client.list_files(
        sandbox_client.get_latest_version_id(VERSIONED_RECORD_ID)
    )

    new_version_id = create_or_get_new_version(
        VERSIONED_RECORD_ID,
        sandbox_client,
        files=[path],
        files_mode=FilesMode.inherit,
        progress=False,
    )

    try:
        files = sandbox_client.list_files(new_version_id)

        assert set(files) == set(previous) | {"extra.txt"}

    finally:
        sandbox_client._request(
            f"/api/records/{new_version_id}/draft",
            method="DELETE",
            requires_auth=True,
        )


def test_create_new_version_mirror(sandbox_client, in_a_working_directory):
    """
    Inherited files which are not in the local set are deleted
    """
    path = Path("only-this.txt")
    path.write_text("only this one\n")

    new_version_id = create_or_get_new_version(
        VERSIONED_RECORD_ID,
        sandbox_client,
        files=[path],
        files_mode=FilesMode.mirror,
        progress=False,
    )

    try:
        assert list(sandbox_client.list_files(new_version_id)) == ["only-this.txt"]

    finally:
        sandbox_client._request(
            f"/api/records/{new_version_id}/draft",
            method="DELETE",
            requires_auth=True,
        )


def test_create_new_version_is_re_runnable(sandbox_client, in_a_working_directory):
    """
    Running the whole thing twice resumes the same draft rather than making two
    """
    path = Path("resumed.txt")
    path.write_text("resumed\n")

    first = create_or_get_new_version(
        VERSIONED_RECORD_ID,
        sandbox_client,
        files=[path],
        files_mode=FilesMode.inherit,
        progress=False,
    )

    try:
        second = create_or_get_new_version(
            VERSIONED_RECORD_ID,
            sandbox_client,
            files=[path],
            files_mode=FilesMode.inherit,
            progress=False,
        )

        assert second == first

    finally:
        sandbox_client._request(
            f"/api/records/{first}/draft", method="DELETE", requires_auth=True
        )
