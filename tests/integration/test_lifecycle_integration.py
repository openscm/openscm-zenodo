"""
The library end to end, against the Zenodo sandbox

One record is taken from nothing to published, versioned once for each
[`FilesMode`][openscm_zenodo.zenodo.FilesMode], then downloaded again.
Everything here is covered piece by piece elsewhere; the point of this file is
the joins between the pieces.

Like `test_publish_integration.py`, this publishes, so the records it makes are
left on the sandbox rather than cleaned up. That is why there is one test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openscm_zenodo.checksums import get_file_md5
from openscm_zenodo.zenodo import FilesMode, create_or_get_new_version


@pytest.mark.zenodo_token
def test_full_lifecycle(
    sandbox_client, build_metadata, in_a_working_directory, tmp_path
):
    """
    Create, describe, reserve a DOI, upload, publish, version, download
    """
    record = sandbox_client.create_record()
    assert record.is_draft

    sandbox_client.update_metadata(record, build_metadata("Lifecycle, v1"))

    doi = sandbox_client.reserve_or_get_doi(record)
    assert doi.startswith("10.")

    first = Path("first.txt")
    first.write_text("the first file\n")
    second = Path("second.txt")
    second.write_text("the second file\n")

    sandbox_client.upload_files(record, [first, second], n_threads=2, progress=False)

    on_zenodo = sandbox_client.list_files(record)
    assert sorted(on_zenodo) == ["first.txt", "second.txt"]
    for path in (first, second):
        assert on_zenodo[path.name].md5 == get_file_md5(path)

    v1 = sandbox_client.publish(record)

    assert not sandbox_client.is_draft(v1)
    # Publishing keeps the DOI which was reserved before there was anything to
    # publish, which is the whole reason for reserving one
    assert sandbox_client.get_record(v1).doi == doi

    # A new version starts from nothing and gets exactly what it is given
    third = Path("third.txt")
    third.write_text("the third file\n")
    v2 = create_or_get_new_version(
        v1,
        sandbox_client,
        metadata=build_metadata("Lifecycle, v2"),
        files=[third],
        files_mode=FilesMode.start_fresh,
        publish=True,
        progress=False,
    )

    assert v2 != v1
    assert sorted(sandbox_client.list_files(v2)) == ["third.txt"]

    # `inherit` carries the previous version's files over and adds to them
    fourth = Path("fourth.txt")
    fourth.write_text("the fourth file\n")
    v3 = create_or_get_new_version(
        v1,
        sandbox_client,
        metadata=build_metadata("Lifecycle, v3"),
        files=[fourth],
        files_mode=FilesMode.inherit,
        publish=True,
        progress=False,
    )

    assert sorted(sandbox_client.list_files(v3)) == ["fourth.txt", "third.txt"]

    # `mirror` inherits, then makes the result match what it was given,
    # so it is the mode which deletes
    v4 = create_or_get_new_version(
        v1,
        sandbox_client,
        metadata=build_metadata("Lifecycle, v4"),
        files=[fourth],
        files_mode=FilesMode.mirror,
        publish=True,
        progress=False,
    )

    assert sorted(sandbox_client.list_files(v4)) == ["fourth.txt"]
    assert sandbox_client.get_latest_version_id(v1) == v4
    # Every version shares the parent, and each has its own DOI
    assert sandbox_client.get_parent_id(v4) == sandbox_client.get_parent_id(v1)
    assert sandbox_client.get_record(v4).doi != doi

    # And the files come back, byte for byte
    dest = tmp_path / "downloaded"
    written = sandbox_client.download_files(v4, dest, n_threads=1, progress=False)

    assert [path.name for path in written] == ["fourth.txt"]
    assert get_file_md5(dest / "fourth.txt") == get_file_md5(fourth)
