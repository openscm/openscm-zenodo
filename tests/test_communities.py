"""
Tests of selecting a draft's community and submitting it for review
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    RecordNotFoundError,
    RecordNotWritableError,
    ZenodoError,
)
from openscm_zenodo.zenodo import Record, ZenodoClient

RECORD_ID = "1234"
RECORD_URL = f"https://zenodo.org/api/records/{RECORD_ID}"
DRAFT_URL = f"{RECORD_URL}/draft"
REVIEW_URL = f"{DRAFT_URL}/review"
COMMUNITY_SLUG = "a-community"
COMMUNITY_ID = "c9c4cee3-40bd-4a2f-8ef7-aba844b09320"
COMMUNITY_URL = f"https://zenodo.org/api/communities/{COMMUNITY_SLUG}"


def draft_body(is_published=False):
    return {
        "id": int(RECORD_ID),
        "is_draft": True,
        "is_published": is_published,
        "parent": {"id": "1230"},
        "metadata": {},
    }


def review_body(community_id=COMMUNITY_ID):
    return {
        "id": "a-request",
        "type": "community-submission",
        "status": "created",
        "receiver": {"community": community_id},
    }


@pytest.fixture
def client_and_session(no_token_in_env, make_recording_session):
    """A client whose session hands back whatever the test scripts"""

    def factory(responses):
        session = make_recording_session(responses)

        return ZenodoClient(token="a-token", session=session), session  # noqa: S106

    return factory


def test_get_community_id(client_and_session, make_response):
    client, session = client_and_session(
        [make_response(json_body={"id": COMMUNITY_ID, "slug": COMMUNITY_SLUG})]
    )

    assert client.get_community_id(COMMUNITY_SLUG) == COMMUNITY_ID
    assert session.calls[0]["url"] == COMMUNITY_URL


def test_get_draft_community(client_and_session, make_response):
    client, session = client_and_session([make_response(json_body=review_body())])

    assert client.get_draft_community(RECORD_ID) == COMMUNITY_ID
    assert session.calls[0]["url"] == REVIEW_URL


def test_get_draft_community_when_none_is_selected(client_and_session, make_response):
    client, _ = client_and_session(
        [
            make_response(status_code=404, url=REVIEW_URL),
            # Our check that the draft is there
            make_response(json_body=draft_body(), url=DRAFT_URL),
        ]
    )

    assert client.get_draft_community(RECORD_ID) is None


def test_get_draft_community_of_a_published_record(client_and_session, make_response):
    client, _ = client_and_session(
        [
            make_response(status_code=404, url=REVIEW_URL),
            make_response(status_code=404, url=DRAFT_URL),
            # Our look at why, which finds the published record
            make_response(json_body=draft_body(is_published=True), url=RECORD_URL),
        ]
    )

    with pytest.raises(RecordNotWritableError, match="inclusion request"):
        client.get_draft_community(RECORD_ID)


def test_get_draft_community_of_a_record_with_metadata_edits(
    client_and_session, make_response
):
    """
    Pending metadata edits are not a draft which can go to a community
    """
    client, _ = client_and_session(
        [
            make_response(status_code=404, url=REVIEW_URL),
            make_response(json_body=draft_body(is_published=True), url=DRAFT_URL),
        ]
    )

    with pytest.raises(RecordNotWritableError, match="inclusion request"):
        client.get_draft_community(RECORD_ID)


def test_get_draft_community_of_a_record_which_is_not_there(
    client_and_session, make_response
):
    client, _ = client_and_session(
        [
            make_response(status_code=404, url=REVIEW_URL),
            make_response(status_code=404, url=DRAFT_URL),
            make_response(status_code=404, url=RECORD_URL),
        ]
    )

    with pytest.raises(RecordNotFoundError):
        client.get_draft_community(RECORD_ID)


def test_set_draft_community(client_and_session, make_response):
    client, session = client_and_session(
        [
            make_response(json_body={"id": COMMUNITY_ID}),
            make_response(status_code=404, url=REVIEW_URL),
            make_response(json_body=draft_body(), url=DRAFT_URL),
            make_response(json_body=review_body(), method="PUT"),
        ]
    )

    assert client.set_draft_community(RECORD_ID, COMMUNITY_SLUG) == COMMUNITY_ID

    put = session.calls[-1]
    assert put["method"] == "PUT"
    assert put["url"] == REVIEW_URL
    assert put["json"] == {
        "receiver": {"community": COMMUNITY_ID},
        "type": "community-submission",
    }


def test_set_draft_community_replaces_another(client_and_session, make_response):
    client, session = client_and_session(
        [
            make_response(json_body={"id": COMMUNITY_ID}),
            make_response(json_body=review_body(community_id="another")),
            make_response(json_body=review_body(), method="PUT"),
        ]
    )

    client.set_draft_community(RECORD_ID, COMMUNITY_SLUG)

    assert session.calls[-1]["method"] == "PUT"


def test_set_draft_community_when_already_selected(client_and_session, make_response):
    """
    Nothing is sent, so re-running a script which got part way is safe
    """
    client, session = client_and_session(
        [
            make_response(json_body={"id": COMMUNITY_ID}),
            make_response(json_body=review_body()),
        ]
    )

    assert client.set_draft_community(RECORD_ID, COMMUNITY_SLUG) == COMMUNITY_ID
    assert [call["method"] for call in session.calls] == ["GET", "GET"]


def test_remove_draft_community(client_and_session, make_response):
    client, session = client_and_session(
        [
            make_response(json_body=review_body()),
            make_response(status_code=204, method="DELETE"),
        ]
    )

    client.remove_draft_community(RECORD_ID)

    delete = session.calls[-1]
    assert delete["method"] == "DELETE"
    assert delete["url"] == REVIEW_URL


def test_remove_draft_community_when_none_is_selected(
    client_and_session, make_response
):
    client, session = client_and_session(
        [
            make_response(status_code=404, url=REVIEW_URL),
            make_response(json_body=draft_body(), url=DRAFT_URL),
        ]
    )

    client.remove_draft_community(RECORD_ID)

    assert [call["method"] for call in session.calls] == ["GET", "GET"]


@pytest.mark.parametrize(
    "comment, exp_body",
    (
        pytest.param(None, {}, id="no-comment"),
        pytest.param(
            "<p>Hi</p>",
            {"payload": {"content": "<p>Hi</p>", "format": "html"}},
            id="comment",
        ),
    ),
)
def test_submit_draft_for_review(client_and_session, make_response, comment, exp_body):
    client, session = client_and_session(
        [
            make_response(json_body=review_body()),
            make_response(json_body={**review_body(), "status": "submitted"}),
        ]
    )

    res = client.submit_draft_for_review(RECORD_ID, comment=comment)

    assert res["status"] == "submitted"
    submit = session.calls[-1]
    assert submit["method"] == "POST"
    assert submit["url"] == f"{DRAFT_URL}/actions/submit-review"
    assert submit["json"] == exp_body


def test_submit_draft_for_review_without_a_community(client_and_session, make_response):
    """
    Nothing is submitted: there is nobody to submit to
    """
    client, session = client_and_session(
        [
            make_response(status_code=404, url=REVIEW_URL),
            make_response(json_body=draft_body(), url=DRAFT_URL),
        ]
    )

    with pytest.raises(ZenodoError, match="set_draft_community"):
        client.submit_draft_for_review(RECORD_ID)

    assert all(call["method"] == "GET" for call in session.calls)


@pytest.mark.parametrize(
    "parent, exp",
    (
        pytest.param({"id": "1"}, (), id="no-communities"),
        pytest.param(
            {"id": "1", "communities": {"ids": ["a", "b"], "default": "a"}},
            ("a", "b"),
            id="communities",
        ),
    ),
)
def test_record_community_ids(parent, exp):
    record = Record.from_json(
        {"id": 1, "is_draft": False, "is_published": True, "parent": parent}
    )

    assert record.community_ids == exp
