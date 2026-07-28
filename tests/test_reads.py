"""
Tests of the read paths: records, drafts, metadata and citations
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    RecordNotFoundError,
    UnknownCitationStyleError,
    ZenodoHTTPError,
)
from openscm_zenodo.zenodo import (
    CITATION_FORMAT_ACCEPT,
    INVENIORDM_JSON_ACCEPT,
    CitationFormat,
    ZenodoClient,
    retrieve_citation,
    retrieve_metadata,
)

RECORD_ID = "1234"
RECORD_URL = f"https://zenodo.org/api/records/{RECORD_ID}"
DRAFT_URL = f"{RECORD_URL}/draft"

RECORD_BODY = {
    "id": int(RECORD_ID),
    "is_draft": False,
    "is_published": True,
    "metadata": {"title": "A record", "version": "v1.0.0"},
    "parent": {"id": "1230"},
    "pids": {"doi": {"identifier": "10.5281/zenodo.1234"}},
}

DRAFT_BODY = {
    "id": int(RECORD_ID),
    "is_draft": True,
    "is_published": False,
    "metadata": {"title": "A draft", "version": "v2.0.0"},
    "parent": {"id": "1230"},
}


@pytest.fixture
def published(make_recording_session, make_response):
    """A session which answers as if the record is published"""
    return make_recording_session([make_response(json_body=RECORD_BODY)])


def test_get_published(published, no_token_in_env):
    """
    A record is fetched in Zenodo's native InvenioRDM serialisation
    """
    client = ZenodoClient(session=published)

    assert client.get_published(RECORD_ID) == RECORD_BODY

    (call,) = published.calls
    assert call["method"] == "GET"
    assert call["url"] == RECORD_URL
    assert call["headers"]["Accept"] == INVENIORDM_JSON_ACCEPT


def test_get_draft(make_recording_session, make_response):
    """
    A draft is fetched from the draft endpoint, with the token we have
    """
    session = make_recording_session([make_response(json_body=DRAFT_BODY)])
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.get_draft(RECORD_ID) == DRAFT_BODY

    (call,) = session.calls
    assert call["url"] == DRAFT_URL
    assert call["headers"]["Accept"] == INVENIORDM_JSON_ACCEPT
    assert call["headers"]["Authorization"] == "Bearer fake-token"


def test_get_draft_which_is_not_there(make_recording_session, make_response):
    """
    A record with no draft surfaces as the 404 Zenodo sent

    Whether a record even has a draft is the question being asked here,
    so this is information rather than a failure of ours to find something.
    """
    session = make_recording_session([make_response(status_code=404, url=DRAFT_URL)])
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError):
        client.get_draft(RECORD_ID)


def test_get_metadata_published(published, no_token_in_env):
    """
    Metadata comes back unwrapped, i.e. as `update_metadata` takes it
    """
    client = ZenodoClient(session=published)

    assert client.get_metadata(RECORD_ID) == RECORD_BODY["metadata"]

    # The published record answers on the first request, so there is only one
    assert len(published.calls) == 1


def test_get_metadata_draft(make_recording_session, make_response):
    """
    An unpublished draft's metadata is found without being asked for specially
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.get_metadata(RECORD_ID) == DRAFT_BODY["metadata"]

    assert [call["url"] for call in session.calls] == [RECORD_URL, DRAFT_URL]


def test_get_record_published(published, no_token_in_env):
    """
    A published record is found on the first request
    """
    client = ZenodoClient(session=published)

    assert client.get_record(RECORD_ID) == RECORD_BODY

    assert [call["url"] for call in published.calls] == [RECORD_URL]


def test_get_record_draft(make_recording_session, make_response):
    """
    A record which is only a draft is found on the second

    The caller does not have to know which of the two they have,
    which is the point of this method.
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.get_record(RECORD_ID) == DRAFT_BODY

    assert [call["url"] for call in session.calls] == [RECORD_URL, DRAFT_URL]


def test_get_record_prefers_the_published_record(make_recording_session, make_response):
    """
    A published record which also has a draft answers with the published record

    A published record can have a draft of its own,
    holding metadata changes which have not been published yet.
    We look for the published record first, so that is what comes back,
    and the draft endpoint is never asked.
    Whether that is the right answer is Part 13.3's question,
    so this test is here to make a change of mind visible.
    """
    session = make_recording_session(
        [
            make_response(json_body=RECORD_BODY),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.get_record(RECORD_ID) == RECORD_BODY

    assert [call["url"] for call in session.calls] == [RECORD_URL]


def test_get_record_no_record(make_recording_session, make_response, no_token_in_env):
    """
    An ID which resolves to nothing is an error which says so

    Without a token there is no point looking for a draft,
    so we do not.
    """
    session = make_recording_session([make_response(status_code=404, url=RECORD_URL)])
    client = ZenodoClient(session=session)

    with pytest.raises(RecordNotFoundError):
        client.get_record(RECORD_ID)

    assert [call["url"] for call in session.calls] == [RECORD_URL]


def test_get_record_error_which_is_not_about_finding_it(
    make_recording_session, make_response, no_token_in_env
):
    """
    An error which is not "cannot see it" is not turned into "cannot find it"
    """
    session = make_recording_session(
        [make_response(status_code=500, url=RECORD_URL)],
    )
    client = ZenodoClient(session=session)

    with pytest.raises(ZenodoHTTPError):
        client.get_record(RECORD_ID)


def test_get_parent_id(published, no_token_in_env):
    """
    The parent ID is read from the record's own document
    """
    client = ZenodoClient(session=published)

    assert client.get_parent_id(RECORD_ID) == "1230"


def test_retrieve_metadata_helper(monkeypatch, published, no_token_in_env):
    """
    The one-shot helper builds a default client when it is not given one
    """
    monkeypatch.setattr(
        "openscm_zenodo.zenodo.ZenodoClient", lambda: ZenodoClient(session=published)
    )

    assert retrieve_metadata(RECORD_ID) == RECORD_BODY["metadata"]


@pytest.mark.parametrize("fmt", list(CitationFormat))
def test_get_citation_accept_headers(
    fmt, make_recording_session, make_response, no_token_in_env
):
    """
    Every format asks for itself through content negotiation on the record
    """
    session = make_recording_session([make_response(text="citation")])
    client = ZenodoClient(session=session)

    assert client.get_citation(RECORD_ID, fmt=fmt) == "citation"

    (call,) = session.calls
    assert call["url"] == RECORD_URL
    assert call["headers"]["Accept"] == CITATION_FORMAT_ACCEPT[fmt]


def test_get_citation_defaults_to_bibtex(
    make_recording_session, make_response, no_token_in_env
):
    """
    With no arguments, we get BibTeX
    """
    session = make_recording_session([make_response(text="@dataset{...}")])
    client = ZenodoClient(session=session)

    client.get_citation(RECORD_ID)

    (call,) = session.calls
    assert call["headers"]["Accept"] == CITATION_FORMAT_ACCEPT[CitationFormat.bibtex]
    assert call["params"] is None


def test_get_citation_style_and_locale_are_sent_for_citations(
    make_recording_session, make_response, no_token_in_env
):
    """
    Style and locale are sent, but only for the format which uses them
    """
    session = make_recording_session([make_response(text="A citation")])
    client = ZenodoClient(session=session)

    client.get_citation(
        RECORD_ID, fmt=CitationFormat.citation, style="ieee", locale="en-GB"
    )

    (call,) = session.calls
    assert call["params"] == {"style": "ieee", "locale": "en-GB"}


def test_get_citation_style_ignored_with_a_warning(
    make_recording_session, make_response, log_messages, no_token_in_env
):
    """
    Asking for a style with a format which has no styles says so
    """
    session = make_recording_session([make_response(text="@dataset{...}")])
    client = ZenodoClient(session=session)

    client.get_citation(RECORD_ID, fmt=CitationFormat.bibtex, style="ieee")

    (call,) = session.calls
    assert call["params"] is None
    assert any("Ignoring style" in message for message in log_messages)


def test_get_citation_style_zenodo_rejects(make_recording_session, no_token_in_env):
    """
    A style we know does not work fails before the request, with the one that does
    """
    session = make_recording_session()
    client = ZenodoClient(session=session)

    with pytest.raises(UnknownCitationStyleError, match="modern-language-association"):
        client.get_citation(RECORD_ID, fmt=CitationFormat.citation, style="mla")

    assert not session.calls


def test_get_citation_style_we_have_not_checked(
    make_recording_session, make_response, log_messages, no_token_in_env
):
    """
    A style we do not know about is sent anyway, with a warning

    Zenodo accepts more CSL styles than we enumerate,
    so refusing them would be us getting in the way.
    """
    session = make_recording_session([make_response(text="A citation")])
    client = ZenodoClient(session=session)

    client.get_citation(
        RECORD_ID, fmt=CitationFormat.citation, style="some-journal-style"
    )

    (call,) = session.calls
    assert call["params"]["style"] == "some-journal-style"
    assert any("we have not checked" in message.lower() for message in log_messages)


def test_get_citation_style_warning_can_be_turned_off(
    make_recording_session, make_response, log_messages, no_token_in_env
):
    """
    The warning about a style we do not know can be silenced

    Someone using a style they know works should not have to hear about it
    on every call.
    """
    session = make_recording_session([make_response(text="A citation")])
    client = ZenodoClient(session=session)

    client.get_citation(
        RECORD_ID,
        fmt=CitationFormat.citation,
        style="some-journal-style",
        warn_unknown_style=False,
    )

    (call,) = session.calls
    assert call["params"]["style"] == "some-journal-style"
    assert not any("we have not checked" in message.lower() for message in log_messages)


def test_get_citation_style_zenodo_rejects_still_raises_when_not_warning(
    make_recording_session, no_token_in_env
):
    """
    Turning the warning off does not turn the error off

    They are different things: one is "we have not checked this",
    the other is "we have checked this and it does not work".
    """
    session = make_recording_session()
    client = ZenodoClient(session=session)

    with pytest.raises(UnknownCitationStyleError):
        client.get_citation(
            RECORD_ID,
            fmt=CitationFormat.citation,
            style="mla",
            warn_unknown_style=False,
        )

    assert not session.calls


def test_retrieve_citation_helper(monkeypatch, make_recording_session, make_response):
    """
    The one-shot helper builds a default client when it is not given one
    """
    session = make_recording_session([make_response(text="@dataset{...}")])
    monkeypatch.setattr(
        "openscm_zenodo.zenodo.ZenodoClient", lambda: ZenodoClient(session=session)
    )

    assert retrieve_citation(RECORD_ID) == "@dataset{...}"
