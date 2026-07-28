"""
Exceptions raised by `openscm_zenodo`

All exceptions raised deliberately by this package
derive from [`ZenodoError`][openscm_zenodo.exceptions.ZenodoError],
so `except ZenodoError` catches everything we raise on purpose.
"""

from __future__ import annotations

import json
from collections.abc import Collection
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

    def __init__(
        self,
        description: str,
        *,
        zenodo_domain: str,
        env_vars: tuple[str, ...] = (),
    ) -> None:
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

        env_vars
            Environment variables that were checked for a token,
            in the order they were checked.

            These are supplied by the caller rather than listed here
            so that the message cannot drift away from what we actually did.
        """
        self.description = description
        self.zenodo_domain = zenodo_domain
        self.env_vars = env_vars

        looked_in = ["no token was supplied"]
        looked_in.extend(f"${env_var} is not set" for env_var in env_vars)

        if len(looked_in) > 1:
            looked_in_formatted = f"{', '.join(looked_in[:-1])} and {looked_in[-1]}"

        else:
            looked_in_formatted = looked_in[0]

        msg = (
            f"A Zenodo token is required to {description}, "
            f"but no token could be resolved for {zenodo_domain}: "
            f"{looked_in_formatted}. "
            "For how to create a token, see the "
            "'Creating a personal access token' header of "
            "https://developers.zenodo.org/#authentication"
        )

        super().__init__(msg)


class ChecksumMismatchError(ZenodoError):
    """
    Raised when a transferred file's checksum does not match the one we expected

    The request itself succeeded but the bytes were wrong.
    """

    def __init__(self, name: str, *, local_md5: str, remote_md5: str) -> None:
        """
        Initialise

        Parameters
        ----------
        name
            Name of the file whose checksum did not match

        local_md5
            MD5 checksum of the local file

        remote_md5
            MD5 checksum reported by Zenodo
        """
        self.name = name
        self.local_md5 = local_md5
        self.remote_md5 = remote_md5

        msg = (
            f"The checksum of {name!r} does not match what we expect. "
            f"Locally we calculated {local_md5!r}, "
            f"Zenodo reports {remote_md5!r}. "
            "Most likely explanation: the transfer was corrupted."
        )

        super().__init__(msg)


class RecordNotFoundError(ZenodoError):
    """
    Raised when we cannot find a record which was asked for
    """

    def __init__(
        self, record_id: str, *, zenodo_domain: str, token_source: str | None
    ) -> None:
        """
        Initialise

        Parameters
        ----------
        record_id
            ID of the record we could not find

        zenodo_domain
            The Zenodo domain we looked on

        token_source
            Where the token we authenticated with came from, if we had one.

            `None` means we had no token.
        """
        self.record_id = record_id
        self.zenodo_domain = zenodo_domain
        self.token_source = token_source

        if token_source is not None:
            msg = (
                f"You asked for record {record_id!r} on {zenodo_domain}, "
                f"but we could not find it, even using {token_source}. "
                "Please check the record ID. "
                "Also check that the token is for the domain above: "
                "sandbox and production tokens are not interchangeable."
            )

        else:
            msg = (
                f"You asked for record {record_id!r} on {zenodo_domain}, "
                "but we could not find it, "
                "and we had no token to authenticate with. "
                "Drafts and restricted records are not visible without one, "
                "so if this record is either of those, "
                "supply a token which has access to it. "
                "Otherwise, please check the record ID."
            )

        super().__init__(msg)


class FileNotOnRecordError(ZenodoError):
    """
    Raised when a file which was asked for is not on the record
    """

    def __init__(
        self,
        filenames: str | Collection[str],
        *,
        record_id: str,
        available: Collection[str],
    ) -> None:
        """
        Initialise

        Parameters
        ----------
        filenames
            Name, or names, which are not there.

        record_id
            ID of the record which does not have them

        available
            Names of the files the record does have.

            These are listed in the message, because the usual cause
            is a typo or a name which changed between versions.
        """
        if isinstance(filenames, str):
            filenames = (filenames,)

        self.filenames = tuple(filenames)
        self.record_id = record_id
        self.available = tuple(available)

        if available:
            available_formatted = ", ".join(repr(name) for name in sorted(available))

        else:
            available_formatted = "nothing"

        missing_formatted = ", ".join(repr(name) for name in self.filenames)
        file_or_files = "file" if len(self.filenames) == 1 else "files"

        msg = (
            f"Record {record_id!r} has no {file_or_files} {missing_formatted}. "
            f"Available: {available_formatted}."
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
    # Zenodo sends a list, but if it ever sends something else
    # we would rather show it than silently drop it
    if not isinstance(errors, list):
        errors = [errors]

    lines.extend(f"- {format_field_error(error)}" for error in errors)

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
