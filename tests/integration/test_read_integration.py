"""
Integration tests which only read from production Zenodo

These need no credentials, so they run everywhere,
including on pull requests from forks.
"""

from __future__ import annotations

from openscm_zenodo.zenodo import ZenodoClient

PUBLISHED_RECORD_ID = "4589756"
"""A published record which is not going anywhere, also our docstring example"""

MD5_HEX_LENGTH = 32


def test_list_files_published_record():
    """
    A published record's files can be listed without a token
    """
    with ZenodoClient() as client:
        files = client.list_files(PUBLISHED_RECORD_ID, draft=False)

    assert files
    for name, entry in files.items():
        assert entry.key == name
        assert entry.size > 0
        # The checksum parsing has to work against what production actually sends
        assert len(entry.md5) == MD5_HEX_LENGTH
