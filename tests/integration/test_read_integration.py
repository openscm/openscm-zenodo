"""
Integration tests which only read from production Zenodo

These need no credentials, so they run everywhere,
including on pull requests from forks.
"""

from __future__ import annotations

import json

import pytest

from openscm_zenodo.exceptions import RecordNotFoundError, ZenodoHTTPError
from openscm_zenodo.zenodo import (
    CitationFormat,
    ZenodoClient,
)

PUBLISHED_RECORD_ID = "4589756"
"""A published record which is not going anywhere, also our docstring example"""

PUBLISHED_RECORD_PARENT_ID = "4589726"
"""The parent (all versions) ID of `PUBLISHED_RECORD_ID`"""

MD5_HEX_LENGTH = 32


@pytest.fixture(scope="module")
def client():
    """
    A client pointed at production, with no token

    The point of this file is that it needs no credentials,
    so we make sure it does not quietly use one that happens to be around.
    CI exports a *sandbox* token as `ZENODO_TOKEN`, which is not one
    these tests should be leaning on either way.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("ZENODO_TOKEN", raising=False)
        monkeypatch.delenv("ZENODO_SANDBOX_TOKEN", raising=False)

        with ZenodoClient() as client:
            yield client


def test_get_published(client):
    """
    A published record can be read without a token

    We ask for the native InvenioRDM serialisation,
    not Zenodo's legacy-compatible default,
    so `access` and `pids` have to actually be there.
    """
    record = client.get_published(PUBLISHED_RECORD_ID)

    assert str(record["id"]) == PUBLISHED_RECORD_ID
    assert record["is_published"]
    assert not record["is_draft"]
    assert record["pids"]["doi"]["identifier"] == "10.5281/zenodo.4589756"
    assert record["access"]["record"] == "public"
    assert record["parent"]["id"] == PUBLISHED_RECORD_PARENT_ID


def test_get_published_which_is_not_there(client):
    """
    An ID which is not a record surfaces as an error, not an empty answer
    """
    with pytest.raises(ZenodoHTTPError):
        client.get_published("0")


def test_get_record(client):
    """
    A published record can be had without knowing that it is published
    """
    assert client.get_record(PUBLISHED_RECORD_ID) == client.get_published(
        PUBLISHED_RECORD_ID
    )


def test_get_record_which_is_not_there(client):
    """
    An ID which resolves to neither a record nor a draft says which we looked on
    """
    with pytest.raises(RecordNotFoundError, match="no token"):
        client.get_record("0")


def test_get_metadata(client):
    """
    Metadata comes back in the InvenioRDM schema

    The legacy schema had `license` and `access_right` in here;
    the native one has `rights` and keeps access outside `metadata` entirely.
    """
    metadata = client.get_metadata(PUBLISHED_RECORD_ID)

    assert metadata["title"] == (
        "Reduced Complexity Model Intercomparison Project (RCMIP) protocol"
    )
    assert metadata["resource_type"]["id"] == "dataset"
    assert metadata["rights"][0]["id"] == "cc-by-sa-4.0"
    assert "access_right" not in metadata


def test_get_metadata_of_a_record_which_is_not_there(client):
    """
    A record we cannot find says which domain we looked on, and with what
    """
    with pytest.raises(RecordNotFoundError, match="no token"):
        client.get_metadata("0")


def test_get_parent_id(client):
    """
    The parent ID is the all-versions ID, which is a different record ID
    """
    parent_id = client.get_parent_id(PUBLISHED_RECORD_ID)

    assert parent_id == PUBLISHED_RECORD_PARENT_ID


def test_get_latest_version_id(client):
    """
    The latest version can be resolved from an older one
    """
    latest = client.get_latest_version_id(PUBLISHED_RECORD_ID)

    # This record's chain may grow, so all we can assert is that we got an ID
    # and that it resolves to a record whose parent is the one we started from.
    assert client.get_parent_id(latest) == PUBLISHED_RECORD_PARENT_ID


@pytest.mark.parametrize("fmt", [fmt for fmt in CitationFormat])
def test_get_citation_formats(client, fmt):
    """
    Every format we offer is one production Zenodo actually serves

    This is the regression test for the export path we used to use,
    which became a 404 without anything noticing.
    """
    citation = client.get_citation(PUBLISHED_RECORD_ID, fmt=fmt)

    assert citation.strip()
    assert PUBLISHED_RECORD_ID in citation


def test_get_citation_bibtex_is_the_default(client):
    """
    Asking for nothing in particular gets a BibTeX entry
    """
    citation = client.get_citation(PUBLISHED_RECORD_ID)

    assert citation.startswith("@dataset{")


@pytest.mark.parametrize("fmt", [CitationFormat.csl, CitationFormat.datacite_json])
def test_get_citation_json_formats_are_json(client, fmt):
    """
    The JSON formats really are JSON, i.e. the `Accept` header did something
    """
    assert json.loads(client.get_citation(PUBLISHED_RECORD_ID, fmt=fmt))


@pytest.mark.parametrize("style", ["apa", "ieee", "harvard-cite-them-right"])
def test_get_citation_styles(client, style):
    """
    The styles we claim to know are ones Zenodo accepts
    """
    citation = client.get_citation(
        PUBLISHED_RECORD_ID, fmt=CitationFormat.citation, style=style
    )

    assert "Nicholls" in citation


def test_get_citation_style_zenodo_does_not_know(client):
    """
    A style Zenodo does not know is a clean error, not a traceback

    We only fail up front on styles we have verified are rejected,
    so an unknown one gets sent and Zenodo has the final say.
    That answer has to stay readable.
    """
    with pytest.raises(ZenodoHTTPError, match="Citation string style not found"):
        client.get_citation(
            PUBLISHED_RECORD_ID,
            fmt=CitationFormat.citation,
            style="not-a-citation-style",
        )


def test_list_files_published_record(client):
    """
    A published record's files can be listed without a token
    """
    files = client.list_files(PUBLISHED_RECORD_ID)

    assert files
    for name, entry in files.items():
        assert entry.filename == name
        assert entry.size > 0
        # The checksum parsing has to work against what production actually sends
        assert len(entry.md5) == MD5_HEX_LENGTH
