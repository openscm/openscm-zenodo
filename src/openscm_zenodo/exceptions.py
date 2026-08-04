"""
Exceptions raised by `openscm_zenodo`

All exceptions raised deliberately by this package
derive from [`ZenodoError`][openscm_zenodo.exceptions.ZenodoError],
so `except ZenodoError` catches everything we raise on purpose.
"""

from __future__ import annotations

import inspect
import json
import warnings
from collections.abc import Collection
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openscm_zenodo.logging import mask_token

if TYPE_CHECKING:
    import requests

_CLIENT_PATH = "openscm_zenodo.zenodo.ZenodoClient"
"""
Full path to the client

Error messages name the method to reach for next, in full, so that it can be
pasted into an import. Building those names from one place here means the
several messages which point at the same method cannot disagree about where it
lives, and a rename shows up as one edit rather than a hunt through strings.
"""

NEW_VERSION_PATH = f"{_CLIENT_PATH}.create_or_get_new_version"
"""Full path to the method which releases a change as a new version"""

EDITED_METADATA_DRAFT_PATH = f"{_CLIENT_PATH}.create_or_get_edited_metadata_draft"
"""Full path to the method which starts metadata edits on a published record"""

READ_METADATA_EDITS_PATH = f"{_CLIENT_PATH}.get_edited_metadata_draft"
"""Full path to the method which reads metadata edits on a published record"""

UPDATE_METADATA_PATH = f"{_CLIENT_PATH}.update_metadata"
"""Full path to the method which writes metadata"""

ACCESS_PATH = "openscm_zenodo.zenodo.Access"
"""Full path to the class which describes who may see a record"""

ZIP_UPLOAD_PATH = f"{_CLIENT_PATH}.upload_files_as_zip"
"""Full path to the method which uploads files as one archive"""


class ZenodoError(Exception):
    """
    Base class for all errors raised by `openscm_zenodo`
    """


class OpenSCMZenodoWarning(UserWarning):
    """
    Base class for all warnings raised by `openscm_zenodo`

    These go through
    [`warnings.warn`](https://docs.python.org/3/library/warnings.html)
    rather than through our logger, because the logger is disabled until a
    caller turns it on (see `openscm_zenodo/__init__.py`) and these are things
    a caller needs to hear whether or not they have configured any logging —
    typically that Zenodo has quietly ignored something they asked for.

    Being a category of its own means they can be silenced or turned into errors
    as a group:

    ```python
    import warnings

    from openscm_zenodo.exceptions import OpenSCMZenodoWarning

    warnings.simplefilter("error", OpenSCMZenodoWarning)
    ```
    """


def _stacklevel_outside_this_package() -> int:
    """
    Work out the `stacklevel` which points at the first frame outside this package

    Counting frames by hand does not work: the number depends on how many of our
    own functions happen to sit between the public method and
    [`warn_openscm_zenodo`][openscm_zenodo.exceptions.warn_openscm_zenodo],
    so it is wrong again the moment a helper is added or removed, and it would
    have to be a parameter on every public method to let a caller correct it.
    The question we actually want answered is "which line of *theirs* led to
    this?", and that is one the stack can answer for itself.

    This is a well-trodden path rather than a clever idea: pandas keeps a
    `find_stack_level` of its own for exactly this, and Python 3.12 added
    `warnings.warn(..., skip_file_prefixes=...)` which does it in the standard
    library. **Replace this with `skip_file_prefixes` when the supported Python
    floor reaches 3.12**; we cannot use it while we support 3.10.

    Returns
    -------
    :
        `stacklevel` for
        [`warnings.warn`](https://docs.python.org/3/library/warnings.html),
        pointing at the nearest frame which is not one of ours
    """
    # `warnings.warn(stacklevel=1)` is `warn_openscm_zenodo` itself, `2` is its caller,
    # which is where we start looking.
    level = 2

    frame = inspect.currentframe()
    for _ in range(2):  # this function, then `warn_openscm_zenodo`
        if frame is None:  # pragma: no cover - only without frame support
            return level

        frame = frame.f_back

    package_dir = str(Path(__file__).parent)
    while frame is not None and frame.f_code.co_filename.startswith(package_dir):
        frame = frame.f_back
        level += 1

    return level


def warn_openscm_zenodo(message: str) -> None:
    """
    Warn about something a caller needs to hear, whatever their logging setup

    The warning is attributed to the nearest line which is not ours, however
    many of our own frames it is raised beneath, see
    `_stacklevel_outside_this_package`.

    Parameters
    ----------
    message
        What to say
    """
    warnings.warn(
        message, OpenSCMZenodoWarning, stacklevel=_stacklevel_outside_this_package()
    )


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


class FileTransferFailedError(ZenodoError):
    """
    Raised when Zenodo accepts a file's content and then reports that it failed
    """

    def __init__(self, filename: str, *, record_id: str, errors: str) -> None:
        """
        Initialise

        Parameters
        ----------
        filename
            Name of the file whose transfer failed

        record_id
            ID of the record it was going to

        errors
            What Zenodo said about the failure
        """
        self.filename = filename
        self.record_id = record_id
        self.errors = errors

        msg = (
            f"Zenodo accepted the upload of {filename!r} to record {record_id!r} "
            f"and then reported that it failed: {errors}. "
            "The response was a 200, so this is Zenodo's file storage rather "
            "than the request. It is usually transient, so can justifiably be retried."
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


class RecordNotWritableError(ZenodoError):
    """
    Raised when a record cannot be written to the way that was asked for
    """

    def __init__(
        self,
        record_id: str,
        *,
        zenodo_domain: str,
        what: str = "files",
    ) -> None:
        """
        Initialise

        Parameters
        ----------
        record_id
            ID of the record which cannot be written to

        zenodo_domain
            The Zenodo domain the record is on

        what
            What we were trying to write, `"files"` or `"metadata"`.
        """
        self.record_id = record_id
        self.zenodo_domain = zenodo_domain
        self.what = what

        msg = f"Record {record_id!r} on {zenodo_domain} is published, "
        if what == "metadata":
            msg += (
                "so it has no draft to write metadata to. "
                "To correct a published record's metadata in place "
                "(same ID, same DOI), start the edits first with "
                f"`{EDITED_METADATA_DRAFT_PATH}`, then update and publish those. "
                "To release the change as a new version instead, "
                f"use `{NEW_VERSION_PATH}`."
            )

        else:
            msg += (
                f"so its {what} cannot be changed. "
                "Zenodo locks them when a record is published. "
                f"To release a change, create a new version with `{NEW_VERSION_PATH}`."
            )

        super().__init__(msg)


class DuplicateFileKeyError(ZenodoError):
    """
    Raised when several local paths would land under one name

    Zenodo has no directories, so a file is identified by its name alone.
    """

    def __init__(self, filename: str, *, paths: Collection[Path]) -> None:
        """
        Initialise

        Parameters
        ----------
        filename
            Name the paths collide under

        paths
            The colliding paths
        """
        self.filename = filename
        self.paths = tuple(paths)

        listed = ", ".join(repr(str(path)) for path in self.paths)

        super().__init__(
            f"{listed} would all be uploaded as {filename!r}, "
            "because Zenodo has no directories and identifies a file by its "
            "name alone, so only one of them would survive. "
            "Rename them, upload them to different records, or use "
            f"`{ZIP_UPLOAD_PATH}` to keep them apart in one archive."
        )


class AccessNotPermittedError(ZenodoError):
    """
    Raised when Zenodo will not let this account set the access which was asked for
    """

    def __init__(self, record_id: str, *, zenodo_domain: str, reported: str) -> None:
        """
        Initialise

        Parameters
        ----------
        record_id
            ID of the record whose access we were setting

        zenodo_domain
            The Zenodo domain the record is on

        reported
            What Zenodo said, which is the authority on why it refused
        """
        self.record_id = record_id
        self.zenodo_domain = zenodo_domain
        self.reported = reported

        super().__init__(
            f"Zenodo refused the access asked for on record {record_id!r} "
            f"on {zenodo_domain}: {reported}. "
            "As far as we understand, "
            "Zenodo treats records themselves as public, and only an admin can "
            f"change that, so `{ACCESS_PATH}.record` is not yours to set. "
            f"Files are: use `{ACCESS_PATH}.files` and `{ACCESS_PATH}.embargo` "
            "to keep those private."
        )


class DraftRecordDraftMetadataEditsError(ZenodoError):
    """
    Raised when the user tries to access draft metadata edits on a draft record

    This doesn't make sense on a draft record: just edit the record directly.
    The idea of a metadata-only edit only applies to published records.
    """

    def __init__(self, record_id: str, *, zenodo_domain: str) -> None:
        """
        Initialise

        Parameters
        ----------
        record_id
            ID of the record which is not published

        zenodo_domain
            The Zenodo domain the record is on
        """
        self.record_id = record_id
        self.zenodo_domain = zenodo_domain

        msg = (
            f"Record {record_id!r} on {zenodo_domain} has not been published, "
            "so it is already a draft and does not have a separate draft of its "
            "metadata. Edit it directly with "
            f"`{UPDATE_METADATA_PATH}`."
        )

        super().__init__(msg)


class PublishedRecordDraftError(ZenodoError):
    """
    Raised when the draft of a published record is asked for

    A published record is not a draft. It can have draft metadata edits, which
    are a different thing and have their own methods; the message points at
    them, because asking for a published record's draft is usually a sign of
    wanting those.
    """

    def __init__(self, record_id: str, *, zenodo_domain: str) -> None:
        """
        Initialise

        Parameters
        ----------
        record_id
            ID of the record which is published

        zenodo_domain
            The Zenodo domain the record is on
        """
        self.record_id = record_id
        self.zenodo_domain = zenodo_domain

        msg = (
            f"Record {record_id!r} on {zenodo_domain} is published, "
            "so it is not a draft. "
            "To read its unpublished metadata edits, if it has any, use "
            f"`{READ_METADATA_EDITS_PATH}`; "
            f"to start some, use `{EDITED_METADATA_DRAFT_PATH}`. "
            f"To make a new version of it, use `{NEW_VERSION_PATH}`."
        )

        super().__init__(msg)


class DraftMetadataEditsNotFoundError(ZenodoError):
    """
    Raised when a published record has no draft metadata edits to read
    """

    def __init__(self, record_id: str, *, zenodo_domain: str) -> None:
        """
        Initialise

        Parameters
        ----------
        record_id
            ID of the record which has no draft

        zenodo_domain
            The Zenodo domain the record is on
        """
        self.record_id = record_id
        self.zenodo_domain = zenodo_domain

        msg = (
            f"Record {record_id!r} on {zenodo_domain} is published "
            "and has no unpublished metadata edits, so there is no draft to read. "
            f"To start editing its metadata, use `{EDITED_METADATA_DRAFT_PATH}`."
        )

        super().__init__(msg)


class MetadataValidationError(ZenodoError, ValueError):
    """
    Raised when metadata is not something Zenodo will accept
    """

    def __init__(self, problems: Collection[str], *, description: str) -> None:
        """
        Initialise

        Parameters
        ----------
        problems
            The problems we found.

            Each should be a complete sentence,
            because they are listed verbatim in the message.

        description
            Description of what the metadata was going to be used for.

            This is injected into the message,
            so it should complete the sentence
            "This metadata cannot be used to ...".
        """
        self.problems = tuple(problems)
        self.description = description

        problems_formatted = "\n".join(f"- {problem}" for problem in self.problems)
        msg = (
            f"This metadata cannot be used to {description}. "
            f"Problems we found:\n{problems_formatted}"
        )

        super().__init__(msg)


class UnknownCitationStyleError(ZenodoError, ValueError):
    """
    Raised when a citation style is one we know Zenodo rejects
    """

    def __init__(
        self,
        style: str,
        *,
        suggestion: str | None,
        known_styles: Collection[str],
    ) -> None:
        """
        Initialise

        Parameters
        ----------
        style
            The style which was asked for

        suggestion
            The closest style ID Zenodo does accept, if we know of one

        known_styles
            Style IDs we have verified Zenodo accepts.
        """
        self.style = style
        self.suggestion = suggestion
        self.known_styles = tuple(known_styles)

        msg = f"Zenodo does not accept the citation style {style!r}. "
        if suggestion is not None:
            msg += f"Did you mean {suggestion!r}? "

        known_formatted = ", ".join(repr(known) for known in sorted(known_styles))
        msg += (
            f"Styles we have checked and know work: {known_formatted}. "
            "Zenodo accepts more CSL styles than we list, "
            "so other styles may be acceptable."
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
