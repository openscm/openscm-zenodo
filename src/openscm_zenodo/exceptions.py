"""
Exceptions raised by `openscm_zenodo`

All exceptions raised deliberately by this package
derive from [`ZenodoError`][openscm_zenodo.exceptions.ZenodoError],
so `except ZenodoError` catches everything we raise on purpose.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from openscm_zenodo.logging import mask_token

if TYPE_CHECKING:
    import requests


class ZenodoError(Exception):
    """
    Base class for all errors raised by `openscm_zenodo`
    """


class MissingTokenError(ZenodoError):
    """
    Raised when an interaction requires a token, but no token could be resolved

    The message spells out the full precedence chain
    used by [`resolve_token`][openscm_zenodo.zenodo.resolve_token],
    so the fix is discoverable from the error alone.
    """

    def __init__(self, description: str, *, zenodo_domain: str) -> None:
        """
        Initialise

        Parameters
        ----------
        description
            Description of the interaction that needed a token.

            This is injected into the message,
            so it should complete the sentence
            "A Zenodo token is required to ...".

        zenodo_domain
            The Zenodo domain that was being interacted with.
        """
        self.description = description
        self.zenodo_domain = zenodo_domain

        msg = "\n".join(
            [
                f"A Zenodo token is required to {description}, "
                "but no token could be resolved.",
                "Tokens are looked for in the following places, "
                "highest precedence first:",
                "1. the `token` argument to `ZenodoClient` "
                "(`--token` on the command-line)",
                "2. the `ZENODO_SANDBOX_TOKEN` environment variable "
                "(only used with https://sandbox.zenodo.org)",
                "3. the `ZENODO_TOKEN` environment variable",
                "4. a `.env` file (command-line only, see `--env-file`)",
                f"The domain being used is {zenodo_domain}.",
                "For how to create a token, see the "
                "'Creating a personal access token' header of "
                "https://developers.zenodo.org/#authentication",
            ]
        )

        super().__init__(msg)


class ZenodoHTTPError(ZenodoError):
    """
    Raised when Zenodo returns an unsuccessful HTTP status code

    The response is kept on the exception as `response`,
    so callers can inspect the status code and body themselves.
    """

    def __init__(
        self,
        response: requests.models.Response,
        *,
        token: str | None = None,
    ) -> None:
        """
        Initialise

        Parameters
        ----------
        response
            The unsuccessful response.

        token
            Token to mask in the message.

            This is only a safety net,
            in case the token ends up in the URL by accident.
        """
        self.response = response

        request = getattr(response, "request", None)
        method = "?" if request is None else str(request.method)
        url = mask_token(str(response.url), token=token)
        reason = response.reason if response.reason else "no reason given"

        msg = (
            f"{method} {url} "
            f"returned {response.status_code} ({reason}). "
            f"Response body: {format_error_body(response)}"
        )

        super().__init__(msg)


def format_error_body(response: requests.models.Response) -> str:
    """
    Format the body of an unsuccessful response as a human-readable string

    InvenioRDM reports validation failures as a `message`
    plus a list of `errors`, each with the `field` that failed
    and the `messages` explaining why.
    We surface both, rather than the raw JSON blob,
    because the field-level messages are the actionable part.

    Parameters
    ----------
    response
        Response whose body should be formatted

    Returns
    -------
    :
        Human-readable rendering of the response's body
    """
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip()

        return text if text else "<no response body>"

    if not isinstance(body, dict):
        return json.dumps(body, sort_keys=True)

    lines = []

    message = body.get("message")
    if message:
        lines.append(str(message))

    errors: Any = body.get("errors") or []
    if isinstance(errors, list):
        for error in errors:
            lines.append(f"- {format_field_error(error)}")

    if not lines:
        return json.dumps(body, sort_keys=True)

    return "\n".join(lines)


def format_field_error(error: Any) -> str:
    """
    Format a single entry from an InvenioRDM `errors` list

    Parameters
    ----------
    error
        Entry to format.

        Typically a `dict` with `field` and `messages` keys,
        but we do not rely on that.

    Returns
    -------
    :
        Human-readable rendering of `error`
    """
    if not isinstance(error, dict):
        return str(error)

    field = error.get("field", "<unknown field>")

    messages = error.get("messages", [])
    if isinstance(messages, list):
        messages_formatted = "; ".join(str(v) for v in messages)
    else:
        messages_formatted = str(messages)

    if not messages_formatted:
        messages_formatted = "<no message>"

    return f"{field}: {messages_formatted}"
