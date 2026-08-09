"""
Tests of the read paths: records, drafts, metadata and citations
"""

from __future__ import annotations

import inspect

import pytest

from openscm_zenodo.exceptions import (
    DraftMetadataEditsNotFoundError,
    DraftRecordDraftMetadataEditsError,
    OpenSCMZenodoWarning,
    PublishedRecordDraftError,
    RecordNotFoundError,
    UnknownCitationStyleError,
    ZenodoHTTPError,
)
from openscm_zenodo.metadata import Metadata
from openscm_zenodo.zenodo import (
    CITATION_FORMAT_ACCEPT,
    INVENIORDM_JSON_ACCEPT,
    CitationFormat,
    Record,
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

EDITED_METADATA_DRAFT_BODY = {
    "id": int(RECORD_ID),
    # Both, which is the shape only an edited metadata draft has
    "is_draft": True,
    "is_published": True,
    "metadata": {"title": "A correction", "version": "v1.0.0"},
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

    assert client.get_published(RECORD_ID) == Record.from_json(RECORD_BODY)

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

    assert client.get_draft(RECORD_ID) == Record.from_json(DRAFT_BODY)

    (call,) = session.calls
    assert call["url"] == DRAFT_URL
    assert call["headers"]["Accept"] == INVENIORDM_JSON_ACCEPT
    assert call["headers"]["Authorization"] == "Bearer fake-token"


def test_get_draft_of_a_published_record(make_recording_session, make_response):
    """
    A published record is not a draft, and never comes back from `get_draft`

    Zenodo answers with a bare `404`, which does not distinguish this from
    a record which is not there at all.
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=DRAFT_URL),
            # Our look at why, which finds the published record
            make_response(json_body=RECORD_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(PublishedRecordDraftError, match="is published"):
        client.get_draft(RECORD_ID)


def test_get_draft_never_returns_metadata_edits(make_recording_session, make_response):
    """
    A published record which *is* mid-edit is refused too

    Zenodo serves the edits from the same endpoint as a draft record, so a
    `200` here is not enough: the document has to be looked at. This is the case
    which would otherwise hand back a published record's pending metadata under
    the name of a draft.
    """
    session = make_recording_session(
        [make_response(json_body=EDITED_METADATA_DRAFT_BODY)]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(PublishedRecordDraftError, match="get_edited_metadata_draft"):
        client.get_draft(RECORD_ID)


def test_get_edited_metadata_draft(make_recording_session, make_response):
    """
    Reading a published record's pending edits does not start any
    """
    session = make_recording_session(
        [
            # `is_draft`, which finds the published record
            make_response(json_body=RECORD_BODY),
            make_response(json_body=EDITED_METADATA_DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    edits = client.get_edited_metadata_draft(RECORD_ID)

    assert edits == Record.from_json(EDITED_METADATA_DRAFT_BODY)
    # A read, not a create
    assert session.calls[-1]["method"] == "GET"


def test_get_edited_metadata_draft_when_there_are_none(
    make_recording_session, make_response
):
    session = make_recording_session(
        [
            make_response(json_body=RECORD_BODY),
            make_response(status_code=404, url=DRAFT_URL),
            # Our look at why, which finds the published record
            make_response(json_body=RECORD_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(
        DraftMetadataEditsNotFoundError, match="create_or_get_edited_metadata_draft"
    ):
        client.get_edited_metadata_draft(RECORD_ID)


def test_get_edited_metadata_draft_of_a_draft(make_recording_session, make_response):
    """
    A record which was never published has no separate metadata to read
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(DraftRecordDraftMetadataEditsError, match="already a draft"):
        client.get_edited_metadata_draft(RECORD_ID)


def test_get_draft_of_a_record_which_is_not_there(
    make_recording_session, make_response
):
    """
    The same `404` from a record which does not exist says *that* instead
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=DRAFT_URL),
            make_response(status_code=404, url=RECORD_URL),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotFoundError):
        client.get_draft(RECORD_ID)


def test_create_or_get_edited_metadata_draft(make_recording_session, make_response):
    """
    Starting metadata edits is a `POST`, which Zenodo answers with any existing one

    It is create-or-get on Zenodo's side,
    so calling it again is not a way to end up with two drafts.
    """
    session = make_recording_session(
        [
            # `is_draft`, which finds the published record
            make_response(json_body=RECORD_BODY),
            make_response(json_body=EDITED_METADATA_DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    draft = client.create_or_get_edited_metadata_draft(RECORD_ID)

    assert draft == Record.from_json(EDITED_METADATA_DRAFT_BODY)
    assert draft.is_edited_metadata_draft
    # An edited metadata draft is not a draft record, it belongs to a published one
    assert not draft.is_draft

    call = session.calls[-1]
    assert call["method"] == "POST"
    assert call["url"] == DRAFT_URL
    assert call["headers"]["Accept"] == INVENIORDM_JSON_ACCEPT


def test_create_or_get_edited_metadata_draft_of_a_draft(
    make_recording_session, make_response
):
    """
    A record which was never published is already a draft, so this does not apply
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(
        DraftRecordDraftMetadataEditsError,
        match="already a draft",
    ):
        client.create_or_get_edited_metadata_draft(RECORD_ID)


def test_has_edited_metadata_draft(make_recording_session, make_response):
    session = make_recording_session(
        [
            make_response(json_body=RECORD_BODY),
            make_response(json_body=EDITED_METADATA_DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.has_edited_metadata_draft(RECORD_ID) is True

    call = session.calls[-1]
    assert call["method"] == "GET"
    assert call["url"] == DRAFT_URL


def test_has_edited_metadata_draft_when_there_are_none(
    make_recording_session, make_response
):
    """
    A published record nobody has started editing has no pending changes
    """
    session = make_recording_session(
        [
            make_response(json_body=RECORD_BODY),
            make_response(status_code=404, url=DRAFT_URL),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.has_edited_metadata_draft(RECORD_ID) is False


def test_has_edited_metadata_draft_of_a_draft(make_recording_session, make_response):
    """
    The question does not apply to a record which was never published
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    with pytest.raises(DraftRecordDraftMetadataEditsError):
        client.has_edited_metadata_draft(RECORD_ID)


def test_a_published_record_never_reports_pending_edits(published, no_token_in_env):
    """
    Zenodo does not say, on the published endpoint, that edits are under way

    This is why `has_edited_metadata_draft` is a request of its own rather than
    something read off a record we already have. If Zenodo ever starts saying,
    this test is what notices.
    """
    client = ZenodoClient(session=published)

    assert client.get_published(RECORD_ID).is_edited_metadata_draft is False


def test_get_metadata_published(published, no_token_in_env):
    """
    Metadata comes back unwrapped, i.e. as `update_metadata` takes it
    """
    client = ZenodoClient(session=published)

    assert client.get_metadata(RECORD_ID) == Metadata.from_json(RECORD_BODY["metadata"])

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

    assert client.get_metadata(RECORD_ID) == Metadata.from_json(DRAFT_BODY["metadata"])

    assert [call["url"] for call in session.calls] == [RECORD_URL, DRAFT_URL]


def test_get_record_published(published, no_token_in_env):
    """
    A published record is found on the first request
    """
    client = ZenodoClient(session=published)

    assert client.get_record(RECORD_ID) == Record.from_json(RECORD_BODY)

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

    assert client.get_record(RECORD_ID) == Record.from_json(DRAFT_BODY)

    assert [call["url"] for call in session.calls] == [RECORD_URL, DRAFT_URL]


def test_get_record_prefers_the_published_record(make_recording_session, make_response):
    """
    A published record which also has a draft answers with the published record

    A published record can have a draft of its own,
    holding metadata changes which have not been published yet.
    We look for the published record first, so that is what comes back,
    and the draft endpoint is never asked:
    the published record is what the record says to everyone else,
    and `get_draft` is how to read the pending changes.
    """
    session = make_recording_session(
        [
            make_response(json_body=RECORD_BODY),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.get_record(RECORD_ID) == Record.from_json(RECORD_BODY)

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


def test_get_record_draft_does_not_log_an_error(
    make_recording_session, make_response, log_records
):
    """
    Finding a draft is a success, so nothing is logged as an error

    Getting there means asking for the published record and being told there is
    none, which is the ordinary path for a draft rather than a problem. Logging
    it as an `ERROR` made a working call look broken. The miss is still logged,
    at `DEBUG`, because it is worth seeing when working out what happened.
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    client.get_record(RECORD_ID)

    assert not [record for record in log_records if record[0] == "ERROR"]
    assert [
        message
        for level, message in log_records
        if level == "DEBUG" and "404" in message
    ]
    # What a user watching this sees is one line, and it is true: the record was
    # a draft, and nothing claims otherwise on the way there
    assert [message for level, message in log_records if level == "INFO"] == [
        f"Retrieved draft record {RECORD_ID!r}"
    ]


def test_is_draft_does_not_log_an_error(
    make_recording_session, make_response, log_records
):
    """
    Asking whether a record is a draft does not log an error to answer `True`

    This is the path every download from a draft goes through, see
    `test_get_record_draft_does_not_log_an_error`.
    """
    session = make_recording_session(
        [
            make_response(status_code=404, url=RECORD_URL),
            make_response(json_body=DRAFT_BODY),
        ]
    )
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    assert client.is_draft(RECORD_ID)

    assert not [record for record in log_records if record[0] == "ERROR"]


def test_a_failure_nobody_expected_is_still_logged_as_an_error(
    make_recording_session, make_response, log_records, no_token_in_env
):
    """
    Keeping expected misses quiet does not quieten anything else
    """
    session = make_recording_session(
        [make_response(status_code=500, url=RECORD_URL)],
    )
    client = ZenodoClient(session=session)

    with pytest.raises(ZenodoHTTPError):
        client.get_record(RECORD_ID)

    assert [
        message
        for level, message in log_records
        if level == "ERROR" and "500" in message
    ]


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

    assert retrieve_metadata(RECORD_ID) == Metadata.from_json(RECORD_BODY["metadata"])


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
    make_recording_session, make_response, no_token_in_env
):
    """
    Asking for a style with a format which has no styles says so
    """
    session = make_recording_session([make_response(text="@dataset{...}")])
    client = ZenodoClient(session=session)

    with pytest.warns(OpenSCMZenodoWarning, match="Ignoring style"):
        client.get_citation(RECORD_ID, fmt=CitationFormat.bibtex, style="ieee")

    (call,) = session.calls
    assert call["params"] is None


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
    make_recording_session, make_response, no_token_in_env
):
    """
    A style we do not know about is sent anyway, with a warning

    Zenodo accepts more CSL styles than we enumerate,
    so refusing them would be us getting in the way.
    """
    session = make_recording_session([make_response(text="A citation")])
    client = ZenodoClient(session=session)

    with pytest.warns(OpenSCMZenodoWarning, match="not checked the citation style"):
        client.get_citation(
            RECORD_ID, fmt=CitationFormat.citation, style="some-journal-style"
        )

    (call,) = session.calls
    assert call["params"]["style"] == "some-journal-style"


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


def test_a_record_can_be_passed_wherever_an_id_can(
    make_recording_session, make_response
):
    """
    A record which has just been handed back can be passed straight on
    """
    session = make_recording_session([make_response(json_body=RECORD_BODY)] * 2)
    client = ZenodoClient(token="fake-token", session=session)  # noqa: S106

    record = client.get_published(RECORD_ID)

    assert client.get_published(record) == record
    assert session.calls[-1]["url"] == RECORD_URL


def test_every_public_method_which_takes_a_record_takes_a_record_object():
    """
    The coercion is applied uniformly, so a new method cannot forget it

    Everything goes into a URL path, where a `Record` would stringify to its
    repr rather than its ID, so a missed coercion is a silent wrong request.
    """
    missed = []
    for name, method in inspect.getmembers(ZenodoClient, inspect.isfunction):
        if name.startswith("_"):
            continue

        parameters = inspect.signature(method).parameters
        if "record_id" not in parameters:
            continue

        if parameters["record_id"].annotation != "RecordIDLike":
            missed.append(name)

    assert missed == []
