"""
Tests of `openscm_zenodo.zenodo.ZenodoClient` and its transport
"""

from __future__ import annotations

import pytest
import requests

from openscm_zenodo.exceptions import MissingTokenError, ZenodoError, ZenodoHTTPError
from openscm_zenodo.zenodo import (
    ZenodoClient,
    ZenodoDomain,
    build_session,
)


def test_token_hidden(no_token_in_env):
    client = ZenodoClient(token="special")  # noqa: S106

    assert "special" not in str(client)
    assert "***" in str(client)
    assert "special" not in repr(client)
    assert "***" in repr(client)


def test_session_repr_is_deterministic(no_token_in_env):
    """
    The session must not put a memory address in the `repr`

    If it did, no doctest could show a client.
    """
    client = ZenodoClient()

    assert "session=<Session>" in repr(client)
    assert "0x" not in repr(client)


def test_session_built_if_not_supplied(no_token_in_env):
    client = ZenodoClient()

    assert isinstance(client.session, requests.Session)
    assert client._owns_session


def test_supplied_session_is_used(no_token_in_env, make_recording_session):
    session = make_recording_session()

    client = ZenodoClient(session=session)

    assert client.session is session
    assert not client._owns_session


def test_owned_session_is_closed(no_token_in_env):
    with ZenodoClient() as client:
        session = client.session
        closed = []
        session.close = lambda: closed.append(True)

    assert closed == [True]


def test_supplied_session_is_not_closed(no_token_in_env, make_recording_session):
    session = make_recording_session()
    closed = []
    session.close = lambda: closed.append(True)

    with ZenodoClient(session=session):
        pass

    assert closed == []


def test_removing_the_session_blows_up(no_token_in_env, make_recording_session):
    """
    We do not quietly build a replacement for a session that was removed

    The session is set during initialisation,
    so `None` here means someone took it away on purpose.
    """
    client = ZenodoClient(session=make_recording_session())
    client.session = None

    with pytest.raises(ZenodoError, match="`session` is `None`"):
        client._request("/api/records/1234")


def test_token_resolved_at_initialisation(monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "from-the-environment")

    assert ZenodoClient().token == "from-the-environment"  # noqa: S105


def test_sandbox_token_resolved_at_initialisation(monkeypatch):
    monkeypatch.setenv("ZENODO_TOKEN", "prod-token")
    monkeypatch.setenv("ZENODO_SANDBOX_TOKEN", "sandbox-token")

    assert ZenodoClient(zenodo_domain=ZenodoDomain.sandbox).token == "sandbox-token"  # noqa: S105


def test_build_session_retry_configuration():
    session = build_session(max_retries=7, backoff_factor=0.5)

    for prefix in ("http://", "https://"):
        retries = session.get_adapter(f"{prefix}zenodo.org").max_retries

        assert retries.total == 7
        assert retries.backoff_factor == 0.5
        assert set(retries.status_forcelist) == {429, 500, 502, 503, 504}
        # All methods are retried, not just the idempotent ones
        assert retries.allowed_methods is None
        assert retries.respect_retry_after_header
        # We want the response back, so we can raise a `ZenodoHTTPError`
        assert not retries.raise_on_status


def test_request_uses_bearer_auth(no_token_in_env, make_recording_session):
    session = make_recording_session()
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client._request("/api/records/1234")

    (call,) = session.calls
    assert call["method"] == "GET"
    assert call["url"] == "https://zenodo.org/api/records/1234"
    assert call["headers"]["Authorization"] == "Bearer a-token"
    # The legacy query parameter must not be used
    assert "params" not in call


def test_request_does_not_mutate_the_session(no_token_in_env, make_recording_session):
    """
    An injected session's headers are never touched

    Authentication is per request,
    so a session which carries its own auth keeps working.
    """
    session = make_recording_session()
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    client._request("/api/records/1234")

    assert "Authorization" not in session.headers


def test_request_without_a_token_sends_no_auth_header(
    no_token_in_env, make_recording_session
):
    session = make_recording_session()
    client = ZenodoClient(session=session)

    client._request("/api/records/1234")

    (call,) = session.calls
    assert "Authorization" not in call["headers"]


def test_request_passes_headers_through(no_token_in_env, make_recording_session):
    session = make_recording_session()
    client = ZenodoClient(session=session)

    client._request("/api/records/1234", headers={"Accept": "application/x-bibtex"})

    (call,) = session.calls
    assert call["headers"]["Accept"] == "application/x-bibtex"


@pytest.mark.parametrize(
    "path, exp",
    (
        ("/api/records/1234", "https://zenodo.org/api/records/1234"),
        # Full URLs, e.g. the links in Zenodo's responses, are used as they are
        (
            "https://sandbox.zenodo.org/api/records/1",
            "https://sandbox.zenodo.org/api/records/1",
        ),
    ),
)
def test_request_url_building(no_token_in_env, path, exp, make_recording_session):
    session = make_recording_session()
    client = ZenodoClient(session=session)

    client._request(path)

    assert session.calls[0]["url"] == exp


def test_request_timeouts(no_token_in_env, make_recording_session):
    session = make_recording_session()
    client = ZenodoClient(session=session, timeout=3, timeout_upload=300)

    client._request("/api/records/1234")
    client._request("/api/records/1234", timeout=client.timeout_upload)

    assert [call["timeout"] for call in session.calls] == [3, 300]


def test_request_requires_auth_raises_before_the_request(
    no_token_in_env, make_recording_session
):
    session = make_recording_session()
    client = ZenodoClient(session=session)

    with pytest.raises(MissingTokenError, match="publish a record"):
        client._request(
            "/api/records/1234/draft/actions/publish",
            method="POST",
            requires_auth=True,
            description="publish a record",
        )

    # The point of `requires_auth` is that we never hit the network
    assert session.calls == []


def test_request_unauthorised_without_a_token(
    no_token_in_env, make_response, make_recording_session
):
    """
    A `401` with no token becomes an error which explains how to supply one
    """
    session = make_recording_session([make_response(status_code=401)])
    client = ZenodoClient(session=session)

    with pytest.raises(MissingTokenError) as exc_info:
        client._request("/api/records/1234")

    assert isinstance(exc_info.value.__cause__, ZenodoHTTPError)


def test_request_unauthorised_with_a_token(
    no_token_in_env, make_response, make_recording_session
):
    """
    A `401` with a token is a real HTTP error, not a missing token
    """
    session = make_recording_session(
        [make_response(status_code=403, json_body={"message": "Permission denied."})]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="Permission denied"):
        client._request("/api/records/1234")


def test_request_error_surfaces_field_errors(
    no_token_in_env, make_response, make_recording_session
):
    session = make_recording_session(
        [
            make_response(
                status_code=400,
                method="PUT",
                json_body={
                    "status": 400,
                    "message": "A validation error occurred.",
                    "errors": [
                        {
                            "field": "metadata.title",
                            "messages": ["Missing data for required field."],
                        }
                    ],
                },
            )
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError) as exc_info:
        client._request("/api/records/1234/draft", method="PUT")

    msg = str(exc_info.value)
    assert "PUT" in msg
    assert "400" in msg
    assert "A validation error occurred." in msg
    assert "metadata.title: Missing data for required field." in msg
    assert exc_info.value.response.status_code == 400


def test_request_error_with_a_non_json_body(
    no_token_in_env, make_response, make_recording_session
):
    session = make_recording_session(
        [make_response(status_code=502, text="<html>Bad gateway</html>")]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="Bad gateway"):
        client._request("/api/records/1234")


def test_request_error_with_an_empty_body(
    no_token_in_env, make_response, make_recording_session
):
    session = make_recording_session([make_response(status_code=500)])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="<no response body>"):
        client._request("/api/records/1234")


def test_request_error_masks_the_token(
    no_token_in_env, make_response, make_recording_session
):
    """
    A token which ends up in a URL by accident is masked in the error
    """
    session = make_recording_session(
        [
            make_response(
                status_code=400,
                url="https://zenodo.org/api/records/1234?access_token=a-token",
            )
        ]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError) as exc_info:
        client._request("/api/records/1234")

    assert "a-token" not in str(exc_info.value)
    assert "***" in str(exc_info.value)
