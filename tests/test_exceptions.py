"""
Tests of `openscm_zenodo.exceptions`

Zenodo's error bodies are not always the shape the docs suggest,
so the formatting has to survive whatever it is handed.
"""

from __future__ import annotations

import pytest

from openscm_zenodo.exceptions import (
    MissingTokenError,
    ZenodoError,
    ZenodoHTTPError,
    ZenodoWarning,
    format_error_body,
    format_field_error,
)
from openscm_zenodo.metadata import Metadata
from openscm_zenodo.zenodo import ZenodoClient


@pytest.mark.parametrize(
    "exp_subclass_of_zenodo_error", (MissingTokenError, ZenodoHTTPError)
)
def test_exceptions_derive_from_zenodo_error(exp_subclass_of_zenodo_error):
    assert issubclass(exp_subclass_of_zenodo_error, ZenodoError)


@pytest.mark.parametrize(
    "json_body, exp",
    (
        pytest.param(
            {"message": "Permission denied."},
            "Permission denied.",
            id="message-only",
        ),
        pytest.param(
            {
                "message": "A validation error occurred.",
                "errors": [
                    {"field": "metadata.title", "messages": ["Missing."]},
                    {"field": "metadata.creators", "messages": ["Also missing."]},
                ],
            },
            "\n".join(
                [
                    "A validation error occurred.",
                    "- metadata.title: Missing.",
                    "- metadata.creators: Also missing.",
                ]
            ),
            id="message-and-field-errors",
        ),
        pytest.param(
            {"errors": [{"field": "a", "messages": ["one", "two"]}]},
            "- a: one; two",
            id="several-messages-for-one-field",
        ),
        pytest.param(
            {"status": 400},
            '{"status": 400}',
            id="nothing-we-recognise-falls-back-to-the-raw-body",
        ),
        pytest.param(
            ["not", "a", "dict"],
            '["not", "a", "dict"]',
            id="body-is-not-a-dict",
        ),
        pytest.param(
            {"message": "Boom.", "errors": "not a list"},
            "Boom.\n- not a list",
            id="errors-is-not-a-list",
        ),
        pytest.param(
            {"message": "Boom.", "errors": {"field": "a", "messages": ["bad"]}},
            "Boom.\n- a: bad",
            id="errors-is-a-bare-dict",
        ),
    ),
)
def test_format_error_body(json_body, exp, make_response):
    assert format_error_body(make_response(json_body=json_body)) == exp


@pytest.mark.parametrize(
    "text, exp",
    (
        ("<html>Bad gateway</html>", "<html>Bad gateway</html>"),
        ("", "<no response body>"),
    ),
)
def test_format_error_body_not_json(text, exp, make_response):
    assert format_error_body(make_response(text=text)) == exp


def test_format_error_body_empty(make_response):
    assert format_error_body(make_response()) == "<no response body>"


@pytest.mark.parametrize(
    "error, exp",
    (
        pytest.param(
            {"field": "a", "messages": ["boom"]}, "a: boom", id="the-expected-shape"
        ),
        pytest.param("just a string", "just a string", id="not-a-dict"),
        pytest.param(
            {"field": "a", "messages": "boom"}, "a: boom", id="messages-not-a-list"
        ),
        pytest.param(
            {"messages": ["boom"]}, "<unknown field>: boom", id="no-field-given"
        ),
        pytest.param({"field": "a"}, "a: <no message>", id="no-messages-given"),
    ),
)
def test_format_field_error(error, exp):
    assert format_field_error(error) == exp


def test_zenodo_http_error_keeps_the_response(make_response):
    response = make_response(status_code=418, json_body={"message": "I am a teapot"})

    error = ZenodoHTTPError(response)

    assert error.response is response
    assert "418" in str(error)
    assert "I am a teapot" in str(error)


def test_zenodo_http_error_without_a_request(make_response):
    """
    We should still get a usable message if there is no request on the response
    """
    response = make_response(status_code=500)
    response.request = None

    assert "? https://zenodo.org/api/records/1234" in str(ZenodoHTTPError(response))


def test_warnings_point_at_the_caller(
    recwarn, make_recording_session, make_response, no_token_in_env
):
    """
    A warning names the line which led to it, not a line of ours

    The number of our own frames between a public method and `warn_zenodo` is
    ours to change, so it cannot be something a caller has to count. This is the
    test that adding or removing a helper does not move the warning.
    """
    updated = make_response(
        json_body={
            "id": 1234,
            "is_draft": True,
            "is_published": False,
            "parent": {"id": "1230"},
            "metadata": {},
        }
    )
    client = ZenodoClient(token="a-token", session=make_recording_session([updated]))  # noqa: S106

    client.update_metadata("1234", Metadata(resource_type="not-a-resource-type"))

    # One warning is raised before the request and one after, through different
    # numbers of our frames, and both have to land in the same place: here.
    ours = [w for w in recwarn if issubclass(w.category, ZenodoWarning)]
    assert len(ours) == 2
    assert [w.filename for w in ours] == [__file__, __file__]
