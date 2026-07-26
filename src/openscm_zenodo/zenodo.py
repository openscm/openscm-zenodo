"""
Zenodo interactions handling
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import os.path
import urllib.parse
from collections.abc import Collection, Iterable, Mapping
from enum import Enum, auto
from pathlib import Path
from types import TracebackType
from typing import (
    Any,
    Literal,
    NewType,
    NoReturn,
    TypeAlias,
    cast,
    overload,
)

import requests
import tqdm
import tqdm.utils
from attrs import define, field
from dotenv import find_dotenv, load_dotenv
from loguru import logger
from requests.adapters import HTTPAdapter
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from urllib3.util.retry import Retry

from openscm_zenodo.checksums import assert_md5_matches, get_file_md5
from openscm_zenodo.exceptions import (
    ChecksumMismatchError,
    MissingTokenError,
    ZenodoError,
    ZenodoHTTPError,
)
from openscm_zenodo.logging import mask_token
from openscm_zenodo.progress import (
    get_file_progress_bar,
    get_progress_reading_wrapper,
)

_LOGGER = logging.getLogger(__name__)

HTTP_BAD_REQUEST = 400
"""HTTP status code Zenodo returns when it will not accept a request"""

TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT = dict(
    unit="B",
    unit_scale=True,
    unit_divisor=1024,
)
"""Default configuration for upload progress bar"""


class ZenodoDomain(str, Enum):
    """
    Supported zenodo URLs
    """

    production = "https://zenodo.org"
    sandbox = "https://sandbox.zenodo.org"


class RestAction(Enum):
    """
    Known rest actions
    """

    get = auto()
    """Get request"""

    post = auto()
    """
    Post request

    This should only add new data, it should not modify existing data.
    """

    put = auto()
    """
    Put request

    This modifies existing data.
    """

    delete = auto()
    """Delete request"""


MetadataType: TypeAlias = dict[str, dict[str, str]]

RecordID = NewType("RecordID", str)
"""
The ID of a single version of a record

This is a plain `str` at runtime.
It exists so that type checkers can tell the difference
between the ID of one version of a record
and the ID of the record's parent
(i.e. [`ParentID`][openscm_zenodo.zenodo.ParentID]).
Zenodo accepts both in the same places
and will happily return a wrong-but-successful answer if you mix them up.
"""

ParentID = NewType("ParentID", str)
"""
The ID which refers to all versions of a record

See [`RecordID`][openscm_zenodo.zenodo.RecordID] for why this is its own type.
"""

ZENODO_TOKEN_ENV_VAR = "ZENODO_TOKEN"  # noqa: S105 # this is a name, not a token
"""Environment variable from which we read a Zenodo token"""

ZENODO_SANDBOX_TOKEN_ENV_VAR = "ZENODO_SANDBOX_TOKEN"  # noqa: S105 # a name too
"""
Environment variable from which we read a Zenodo sandbox token

Sandbox and production tokens are not interchangeable,
so this is only used when interacting with
[`ZenodoDomain.sandbox`][openscm_zenodo.zenodo.ZenodoDomain].
"""


def get_zenodo_domain_url(zenodo_domain: str | ZenodoDomain) -> str:
    """
    Get the URL of a Zenodo domain

    Parameters
    ----------
    zenodo_domain
        Zenodo domain of interest

    Returns
    -------
    :
        URL of `zenodo_domain`, without any trailing slash

    Examples
    --------
    >>> get_zenodo_domain_url(ZenodoDomain.sandbox)
    'https://sandbox.zenodo.org'
    >>> get_zenodo_domain_url("https://zenodo.org/")
    'https://zenodo.org'
    """
    if isinstance(zenodo_domain, ZenodoDomain):
        return zenodo_domain.value

    return zenodo_domain.rstrip("/")


@overload
def resolve_token(
    token: str | None = ...,
    *,
    zenodo_domain: str | ZenodoDomain = ...,
    env: Mapping[str, str] | None = ...,
    required: Literal[False] = False,
    description: str = ...,
) -> str | None: ...


@overload
def resolve_token(
    token: str | None = ...,
    *,
    zenodo_domain: str | ZenodoDomain = ...,
    env: Mapping[str, str] | None = ...,
    required: Literal[True],
    description: str = ...,
) -> str: ...


def resolve_token(
    token: str | None = None,
    *,
    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production,
    env: Mapping[str, str] | None = None,
    required: bool = False,
    description: str = "interact with Zenodo",
) -> str | None:
    """
    Resolve the token to use for interacting with Zenodo

    This is the one place that knows where tokens come from,
    so the library, the command-line interface and the tests
    cannot disagree about where a token came from.
    In order of precedence, highest first, we use:

    1. `token`, if it is supplied
    1. `ZENODO_SANDBOX_TOKEN`, but only if `zenodo_domain` is the sandbox
    1. `ZENODO_TOKEN`

    A `.env` file is not read here.

    Parameters
    ----------
    token
        Token supplied by the caller.

        An empty string is treated the same way as no token at all.

    zenodo_domain
        Zenodo domain that the token will be used with.

        Sandbox and production tokens are not interchangeable,
        so this determines whether `ZENODO_SANDBOX_TOKEN` is considered.

    env
        Environment in which to look for tokens.

        If not supplied, we use
        [`os.environ`][os.environ].

    required
        Does the interaction we are resolving a token for require one?

        The default, `False`, returns `None` if no token can be resolved,
        because unauthenticated reads of public records are supported.
        Pass `True` for interactions which cannot work without a token,
        so that they fail before any request goes out.

    description
        Description of the interaction that needs a token.

        Only used when `required` is `True`.
        This is injected into the error message,
        so it should complete the sentence
        "A Zenodo token is required to ...".

    Returns
    -------
    :
        The resolved token.

        This is `None` if no token could be resolved and `required` is `False`.

    Raises
    ------
    MissingTokenError
        No token could be resolved and `required` is `True`

    Examples
    --------
    >>> resolve_token("supplied-directly", env={})
    'supplied-directly'

    >>> resolve_token(env={"ZENODO_TOKEN": "from-the-environment"})
    'from-the-environment'

    Sandbox tokens are only used with the sandbox domain

    >>> env = {"ZENODO_SANDBOX_TOKEN": "sandbox-token", "ZENODO_TOKEN": "prod-token"}
    >>> resolve_token(env=env, zenodo_domain=ZenodoDomain.sandbox)
    'sandbox-token'
    >>> resolve_token(env=env, zenodo_domain=ZenodoDomain.production)
    'prod-token'

    If nothing resolves, we return `None`

    >>> resolve_token(env={}) is None
    True

    unless the interaction requires a token

    >>> resolve_token(env={}, required=True)  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    ...
    openscm_zenodo.exceptions.MissingTokenError
    """
    resolved = None

    if token:
        logger.debug("Using the token supplied by the caller")
        resolved = token

    else:
        if env is None:
            env = os.environ

        if get_zenodo_domain_url(zenodo_domain) == ZenodoDomain.sandbox.value:
            sandbox_token = env.get(ZENODO_SANDBOX_TOKEN_ENV_VAR)
            if sandbox_token:
                logger.debug(f"Using the token from ${ZENODO_SANDBOX_TOKEN_ENV_VAR}")
                resolved = sandbox_token

        if resolved is None:
            zenodo_token = env.get(ZENODO_TOKEN_ENV_VAR)
            if zenodo_token:
                logger.debug(f"Using the token from ${ZENODO_TOKEN_ENV_VAR}")
                resolved = zenodo_token

    if resolved is None:
        logger.debug("No Zenodo token could be resolved")

        if required:
            raise MissingTokenError(
                description, zenodo_domain=get_zenodo_domain_url(zenodo_domain)
            )

    return resolved


def load_env_file(
    env_file: Path | None = None,
    *,
    override: bool = False,
) -> Path | None:
    """
    Load environment variables from a `.env` file

    This is deliberately never called on import.
    The command-line interface calls it before running a command,
    and users who want the same behaviour in a script or notebook
    can call it themselves.

    Parameters
    ----------
    env_file
        The `.env` file to load.

        If not supplied, we search for one,
        starting from the current working directory
        and walking up the directory tree.

    override
        Should the values in `env_file` override variables
        which are already set in the environment?

        The default, `False`,
        is what allows a `.env` file to be the lowest-precedence source of a token
        (see [`resolve_token`][openscm_zenodo.zenodo.resolve_token]).

    Returns
    -------
    :
        The file that was loaded, or `None` if no file was found

    Raises
    ------
    FileNotFoundError
        `env_file` was supplied, but does not exist
    """
    if env_file is None:
        found = find_dotenv(usecwd=True)
        if not found:
            logger.debug("No `.env` file found")

            return None

        env_file = Path(found)

    elif not env_file.exists():
        msg = f"The supplied env file does not exist: {env_file}"

        raise FileNotFoundError(msg)

    logger.debug(f"Loading environment variables from {env_file}")
    load_dotenv(env_file, override=override)

    return env_file


def build_session(
    *,
    max_retries: int = 5,
    backoff_factor: float = 1.0,
    retry_status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    """
    Build a session for interacting with Zenodo

    This is the only place where our retry policy is expressed.
    If you want different behaviour,
    build a session here (or however you like)
    and pass it to [`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient],
    e.g.

    ```python
    client = ZenodoClient(session=build_session(max_retries=10, backoff_factor=2.0))
    ```

    Parameters
    ----------
    max_retries
        Maximum number of times to retry a request before giving up

    backoff_factor
        Backoff factor to apply between retries.

        Zenodo's `Retry-After` header is respected where it is supplied,
        this only applies when it is not.

    retry_status_forcelist
        HTTP status codes which trigger a retry

    Returns
    -------
    :
        Session with our retry policy mounted for both HTTP and HTTPS

    Notes
    -----
    We retry on all HTTP methods, not just the idempotent ones.
    The failure we actually see from Zenodo is rate limiting (`429`),
    which is safe to retry on any method
    because the request was rejected before it was processed.
    The trade-off is that a `500` from a `POST`
    is also retried, even though Zenodo may have processed it;
    in exchange, transient failures during long uploads do not kill the run.

    Retries do not apply to the streaming upload of a file's content.
    That request cannot be replayed by the transport layer,
    so it is retried a level up.
    """
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=list(retry_status_forcelist),
        # Retry all methods, see the note in the docstring
        allowed_methods=None,
        respect_retry_after_header=True,
        # Hand the last response back to us,
        # so we can raise an error that includes Zenodo's explanation
        # rather than urllib3's.
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry)

    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


def should_retry_upload(
    exc: BaseException,
    *,
    retry_status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> bool:
    """
    Decide whether an upload which raised `exc` should be tried again

    Parameters
    ----------
    exc
        Exception raised by the upload attempt

    retry_status_forcelist
        HTTP status codes which are worth trying again

    Returns
    -------
    :
        `True` if the upload is worth trying again
    """
    if isinstance(exc, ChecksumMismatchError):
        # Not an HTTP failure: the request succeeded but the bytes were wrong
        # so we need to retry.
        return True

    if isinstance(exc, ZenodoHTTPError):
        # Zenodo answered, so only try again if the answer suggests it is worth it
        # (e.g. retrying a rejected upload four more times helps no one).
        return exc.response.status_code in retry_status_forcelist

    # Please clarify what this means: it can be retried,
    # but only if the exception is a request exception?
    # If it isn't, the retry has to be handled elsewhere
    # so that the file handle is replayed correctly?
    # Connection errors, read timeouts and the like.
    # The session's retries cannot cover the content request,
    # because its body is a file handle which cannot be replayed.
    return isinstance(exc, requests.exceptions.RequestException)


def _log_upload_retry(retry_state: RetryCallState) -> None:
    """
    Log that an upload is about to be tried again

    Parameters
    ----------
    retry_state
        State of the retrying, as tenacity reports it
    """
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
    sleep = (
        retry_state.next_action.sleep if retry_state.next_action is not None else 0.0
    )

    logger.warning(
        f"Upload attempt {retry_state.attempt_number} failed with {exc!r}. "
        # Does it make sense to report trying again in 0.0s?
        # Doesn't next_action being None indicate that there will be no retry?
        f"Trying again in {sleep:.1f}s."
    )


def _repr_token(token: str | None) -> str:
    """Get the `repr` to use for a token, i.e. never the token itself"""
    return "None" if token is None else "***"


def _repr_session(session: requests.Session | None) -> str:
    """
    Get the `repr` to use for a session

    The default `repr` of a session includes its memory address,
    which is noise and makes any doctest that shows a client unrunnable.
    """
    return "None" if session is None else f"<{type(session).__qualname__}>"


@define
class ZenodoClient:
    """
    Client for interacting with Zenodo's InvenioRDM API

    Examples
    --------
    Neither the token nor the session leaks noise into the `repr`

    >>> client = ZenodoClient(token="a-real-token")
    >>> "a-real-token" in repr(client)
    False
    >>> "token=***" in repr(client)
    True
    >>> "session=<Session>" in repr(client)
    True
    """

    token: str | None = field(default=None, repr=_repr_token)
    """
    Token to use for authenticating interactions with the Zenodo domain

    If not supplied, we resolve one with
    [`resolve_token`][openscm_zenodo.zenodo.resolve_token],
    which may also come up empty.
    Reads of public records work without a token,
    everything else raises
    [`MissingTokenError`][openscm_zenodo.exceptions.MissingTokenError]
    when it is used.
    """

    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production
    """Zenodo domain to interact with"""

    timeout: int = 10
    """Timeout to apply to requests calls"""

    timeout_upload: int = 60 * 60
    """Timeout to apply to uploads"""

    session: requests.Session | None = field(default=None, repr=_repr_session)
    """
    Session to use for interacting with Zenodo

    If not supplied, we build one with
    [`build_session`][openscm_zenodo.zenodo.build_session].
    Supply your own if you need different transport behaviour.

    We never modify a session that was handed to us.
    In particular, authentication is applied per request,
    not written onto the session's headers.
    """

    _owns_session: bool = field(init=False, default=False)
    """
    Did we build [`session`][openscm_zenodo.zenodo.ZenodoClient.session] ourselves?

    This governs whether
    [`close`][openscm_zenodo.zenodo.ZenodoClient.close] closes it.
    Closing a session we were handed is a bug, not a courtesy.
    """

    def __attrs_post_init__(self) -> None:
        """
        Finish initialisation

        We resolve the token and build a session, if we were not given one.
        """
        self.token = resolve_token(self.token, zenodo_domain=self.zenodo_domain)

        if self.session is None:
            self.session = build_session()
            self._owns_session = True

    def __enter__(self) -> ZenodoClient:
        """
        Enter a context block

        Returns
        -------
        :
            The client itself
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """
        Exit a context block, closing the session if we own it
        """
        self.close()

    @property
    def zenodo_domain_url(self) -> str:
        """
        The URL of the Zenodo domain we are interacting with

        Returns
        -------
        :
            URL of the Zenodo domain we are interacting with
        """
        return get_zenodo_domain_url(self.zenodo_domain)

    def close(self) -> None:
        """
        Close the session, if we built it

        A session that was supplied by the caller is left alone,
        because its lifecycle belongs to whoever created it.
        """
        if self._owns_session and self.session is not None:
            self.session.close()

    def _get_url(self, path: str) -> str:
        """
        Get the URL to hit

        Parameters
        ----------
        path
            The post-domain part of the URL to hit,
            for example "/api/records/1858949".

            A full URL is returned unaltered,
            so links from Zenodo's responses can be passed straight in.

        Returns
        -------
        :
            URL to hit
        """
        if path.startswith(("http://", "https://")):
            return path

        return f"{self.zenodo_domain_url}{path}"

    def _handle_error_response(
        self, response: requests.models.Response, *, description: str
    ) -> NoReturn:
        """
        Raise the most helpful error we can for an unsuccessful response

        Parameters
        ----------
        response
            The unsuccessful response

        description
            Description of the interaction, used in
            [`MissingTokenError`][openscm_zenodo.exceptions.MissingTokenError]

        Raises
        ------
        MissingTokenError
            Zenodo refused the request and we have no token to authenticate with

        ZenodoHTTPError
            Any other unsuccessful response
        """
        error = ZenodoHTTPError(response, token=self.token)
        logger.error(str(error))

        unauthorised = (401, 403)
        if response.status_code in unauthorised and not self.token:
            raise MissingTokenError(
                description, zenodo_domain=self.zenodo_domain_url
            ) from error

        raise error

    def _request(  # noqa: PLR0913
        self,
        path: str,
        *,
        method: str = "GET",
        requires_auth: bool = False,
        description: str | None = None,
        timeout: int | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> requests.models.Response:
        """
        Make a request to Zenodo

        Parameters
        ----------
        path
            The post-domain part of the URL to hit, or a full URL.

            See [`_get_url`][openscm_zenodo.zenodo.ZenodoClient._get_url].

        method
            HTTP method to use

        requires_auth
            Does this interaction require a token?

            If `True` and no token was resolved,
            we raise before the request goes out.

        description
            Description of the interaction, used in error messages.

            If not supplied, we build one from `method` and the URL.

        timeout
            Timeout to apply to this request.

            If not supplied, we use
            [`timeout`][openscm_zenodo.zenodo.ZenodoClient.timeout].
            Uploads and downloads pass
            [`timeout_upload`][openscm_zenodo.zenodo.ZenodoClient.timeout_upload].

        headers
            Headers to send with the request.

            The `Authorization` header is added by us, per request,
            so it does not need to be included here.

        **kwargs
            Passed to
            [`requests.Session.request`][requests.sessions.Session.request]

        Returns
        -------
        :
            The response from Zenodo

        Raises
        ------
        MissingTokenError
            A token is required, but none could be resolved

        ZenodoHTTPError
            Zenodo returned an unsuccessful status code
        """
        url = self._get_url(path)
        if description is None:
            description = f"send a {method} request to {url}"

        request_headers = dict(headers) if headers is not None else {}
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"

        elif requires_auth:
            raise MissingTokenError(description, zenodo_domain=self.zenodo_domain_url)

        if self.session is None:
            msg = (
                "`session` is `None`. "
                "It is set during initialisation, "
                "so this can only happen if it was removed afterwards. "
                "Assign a `requests.Session` to `session`, "
                "or create a new client."
            )

            raise ZenodoError(msg)

        # Mask just in case the token ended up in the URL by accident
        logger.debug(f"Sending {method} request to {mask_token(url, token=self.token)}")

        response = self.session.request(
            method,
            url,
            headers=request_headers,
            timeout=self.timeout if timeout is None else timeout,
            **kwargs,
        )

        if not response.ok:
            self._handle_error_response(response, description=description)

        return response

    def _get_draft_file_path(
        self, record_id: str, filename: str, suffix: str = ""
    ) -> str:
        """
        Get the path of one of a draft's files

        Parameters
        ----------
        record_id
            ID of the record whose draft the file belongs to

        filename
            Name of the file, as it appears on Zenodo

        suffix
            Suffix to append, for example "/content" or "/commit"

        Returns
        -------
        :
            Path of the file
        """
        # `filename` comes from the local file system,
        # so it can contain characters which are not URL safe.
        quoted = urllib.parse.quote(filename, safe="")

        return f"/api/records/{record_id}/draft/files/{quoted}{suffix}"

    def delete_file(self, record_id: str | RecordID, filename: str) -> None:
        """
        Delete a file from a record

        The record must be, by definition a draft,
        you can't delete from published records.

        Parameters
        ----------
        record_id
            Record from which to delete the file

        filename
            Name of the file to delete, as it appears on Zenodo
        """
        logger.info(f"Deleting {filename!r} from {record_id!r}")
        self._request(
            self._get_draft_file_path(record_id, filename),
            method="DELETE",
            requires_auth=True,
            description=f"delete {filename!r} from {record_id!r}",
        )

    def _initialise_file(self, record_id: str, filename: str) -> None:
        """
        Initialise a file on a record (by definition a draft)

        This is the the first step of an upload.

        If `filename` is already on the draft, we delete it and start again.
        That covers both re-uploading a file which has changed
        and picking up after a previous upload
        which died between initialising and committing.

        Parameters
        ----------
        record_id
            Record on which to initialise the file

        filename
            Name of the file, as it will appear on Zenodo
        """
        path = f"/api/records/{record_id}/draft/files"
        description = f"initialise {filename!r} on record {record_id!r}"

        def initialise_file():
            self._request(
                path,
                method="POST",
                requires_auth=True,
                json=[{"key": filename}],
                description=description,
            )

        try:
            initialise_file()

        except ZenodoHTTPError as exc:
            if exc.response.status_code != HTTP_BAD_REQUEST:
                raise

            logger.debug(
                f"Could not initialise {filename!r} on record {record_id!r}, "
                "assuming it is already there. "
                "Deleting it and initialising again."
            )
            self.delete_file(record_id, filename)
            initialise_file()

    def _upload_file_content(
        self,
        record_id: str,
        path: Path,
        *,
        filename: str,
        progress: bool = True,
        position: int | None = None,
    ) -> None:
        """
        Upload a file's content, the second step of an upload

        Parameters
        ----------
        record_id
            ID of the record to upload to

        path
            File to upload

        filename
            Name of the file, as it will appear on Zenodo

        progress
            Should a progress bar be shown?

        position
            Line to display the progress bar on
        """
        with get_file_progress_bar(
            desc=filename,
            total=path.stat().st_size,
            progress=progress,
            position=position,
        ) as progress_bar:
            with open(path, "rb") as file_handle:
                self._request(
                    self._get_draft_file_path(record_id, filename, "/content"),
                    method="PUT",
                    requires_auth=True,
                    data=get_progress_reading_wrapper(file_handle, progress_bar),
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=self.timeout_upload,
                    description=(f"upload {filename!r} to record {record_id!r}"),
                )

    def _commit_file(self, record_id: str, filename: str) -> dict[str, Any]:
        """
        Commit a file, the third and final step of an upload

        Parameters
        ----------
        record_id
            ID of the record the file belongs to

        filename
            Name of the file, as it appears on Zenodo

        Returns
        -------
        :
            The file's entry.

            Among other things, this reports the checksum
            which Zenodo calculated for the bytes it received.
        """
        response = self._request(
            self._get_draft_file_path(record_id, filename, "/commit"),
            method="POST",
            requires_auth=True,
            description=f"commit {filename!r} to record {record_id!r}",
        )

        return cast(dict[str, Any], response.json())

    def _upload_file_attempt(  # noqa: PLR0913
        self,
        record_id: str,
        path: Path,
        *,
        filename: str,
        # Is there a reason to not make this required
        # i.e. not always check against the local checksum?
        local_md5: str | None,
        progress: bool,
        position: int | None,
        # Can we introduce a more helpful return type
        # rather than the loose dict[str, Any] we currently have?
    ) -> dict[str, Any]:
        """
        Make one attempt at uploading a file

        This is the unit that is retried,
        so it starts by initialising the file from scratch:
        the content of a committed file cannot be replaced
        and a partly-uploaded file cannot be resumed.

        Parameters
        ----------
        record_id
            ID of the record to upload to

        path
            File to upload

        filename
            Name of the file, as it will appear on Zenodo

        local_md5
            MD5 checksum of `path`.

            If supplied, we check it against the checksum
            Zenodo reports once the file is committed.

        progress
            Should a progress bar be shown?

        position
            Line to display the progress bar on

        Returns
        -------
        :
            The file's entry on the draft

        Raises
        ------
        ChecksumMismatchError
            `local_md5` was supplied and does not match the checksum
            reported by Zenodo
        """
        self._initialise_file(record_id, filename)
        self._upload_file_content(
            record_id, path, filename=filename, progress=progress, position=position
        )
        entry = self._commit_file(record_id, filename)

        if local_md5 is not None:
            assert_md5_matches(
                filename, local_md5=local_md5, remote_checksum=entry["checksum"]
            )

        return entry

    def _clean_up_failed_upload(self, record_id: str, filename: str) -> None:
        """
        Remove what is left of an upload which failed

        A draft which has a file that was initialised but never committed
        cannot be published,
        so leaving one behind turns a failed upload
        into a draft that is broken in a way that is hard to diagnose.

        Parameters
        ----------
        record_id
            ID of the record to clean up

        filename
            Name of the file whose upload failed
        """
        try:
            self.delete_file(record_id, filename)

        except ZenodoError as exc:
            logger.warning(
                f"Failed to clean up {filename!r} "
                f"on the record {record_id!r} after a failed upload: {exc}. "
                "The draft may not be publishable until this file is removed."
            )

    def upload_file(  # noqa: PLR0913
        self,
        record_id: str | RecordID,
        path: Path,
        *,
        verify_checksum: bool = True,
        progress: bool = True,
        position: int | None = None,
        max_attempts: int = 5,
        # As above, can we introduce a better type here?
    ) -> dict[str, Any]:
        """
        Upload a file to a record's draft

        InvenioRDM uploads are a three-step, explicitly-committed flow:
        the file is initialised on the draft,
        its content is streamed up,
        then it is committed.
        This does all three.

        The record must be a draft
        (it is not possible to upload a file to a published record).

        Note that Zenodo has no directories:
        the file lands under `path.name`,
        whatever local directories it sits in.

        Parameters
        ----------
        record_id
            ID of the record whose draft to upload to

        path
            File to upload

        verify_checksum
            Should we check that Zenodo received the bytes we sent?

            We compare the file's local MD5 checksum
            against the checksum Zenodo reports when the file is committed.
            This costs one extra read of the file, so it can be turned off,
            but a corrupted upload is worse than a slow one.

        progress
            Should a progress bar be shown?

        position
            Line to display the progress bar on.

            Give each worker a stable slot when uploading in parallel.

        max_attempts
            Maximum number of times to try the upload before giving up.

            Any retries mounted on `self.session` cannot help here:
            the content request streams a file handle which cannot be replayed,
            and a checksum mismatch is not an HTTP failure at all.
            So the whole upload is retried instead,
            re-reading the file and resetting the progress bar each time.

        Returns
        -------
        :
            The file's entry on the draft, as Zenodo reports it once committed

        Raises
        ------
        ChecksumMismatchError
            `verify_checksum` is `True` and the upload was corrupted
            on every attempt

        ZenodoHTTPError
            Zenodo rejected the upload
        """
        filename = path.name
        logger.info(
            f"Uploading {path} as {filename!r} to the draft of record {record_id!r}"
        )

        local_md5 = get_file_md5(path) if verify_checksum else None

        retrying = Retrying(
            retry=retry_if_exception(should_retry_upload),
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=1.0, max=60.0),
            before_sleep=_log_upload_retry,
            reraise=True,
        )

        try:
            entry = retrying(
                self._upload_file_attempt,
                record_id,
                path,
                filename=filename,
                local_md5=local_md5,
                progress=progress,
                position=position,
            )

        except Exception:
            self._clean_up_failed_upload(record_id, filename)

            raise

        logger.info(f"Successfully uploaded {path} as {filename!r}")

        return entry


@define
class ZenodoInteractor:
    """
    Class for interacting with Zenodo
    """

    token: str | None = field(default=None, repr=lambda value: "***")
    """Token to use for authenticating interactions with the Zenodo domain"""

    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production
    """Zenodo domain to interact with"""

    timeout: int = 10
    """Timeout to apply to requests calls"""

    timeout_upload: int = 60 * 60
    """Timeout to apply to uploads"""

    def create_new_version_from_latest(
        self,
        latest_deposition_id: str,
    ) -> requests.models.Response:
        """
        Create a new version of a record from the latest deposition ID

        Parameters
        ----------
        latest_deposition_id
            The ID of the latest deposition.

            This is the ID of the latest version from a collection of records.
            For example, if there is v1.0.0, v2.0.0 and v3.0.0 on Zenodo,
            this should be the deposition ID of v3.0.0.

        Returns
        -------
        :
            The new version's record from Zenodo

        Notes
        -----
        From https://developers.zenodo.org/#new-version

        ...
        - The id used to create this new version has to be the id of the latest version.
          It is not possible to use the global id that references all the versions.

        We replicate this logic here.
        To create a new version from the all records ID that references all versions,
        use [`create_new_version`][openscm_zenodo.zenodo.create_new_version].
        """
        logger.info(f"Creating a new version from {latest_deposition_id=!r}")

        try:
            create_new_version_response = self.get_response(
                post_domain_part=f"/api/deposit/depositions/{latest_deposition_id}/actions/newversion",
                rest_action=RestAction.post,
            )
            logger.info(
                "Successfully created new version. "
                "The new version's deposition id is "
                f"{create_new_version_response.json()['id']!r}"
            )

        except requests.exceptions.HTTPError as exc:
            exc_response_json = exc.response.json()
            if (
                exc_response_json["errors"][0]["messages"][0]
                == "Please remove all files first."
            ):
                # TODO: consider just not raising an error in this case
                msg = (
                    "You must remove all the files in the current draft version "
                    "before you can call the 'create a new version' "
                    "API again without error. "
                    "Having said that, this error means that you already have a draft, "
                    "hence you probably don't need to call the "
                    "'create a new version' API in the first place."
                )

                raise AssertionError(msg) from exc

            raise

        # I am pretty sure the below text from https://developers.zenodo.org/#new-version
        # is wrong because the above appears to return the new version's record.
        #
        # Text I think is wrong from https://developers.zenodo.org/#new-version:
        #
        # - The response body of this action
        #   is NOT the new version deposit, but the original resource.
        #   The new version deposition can be accessed through the "latest_draft"
        #   under "links" in the response body.

        return create_new_version_response

    def delete_deposition(self, deposition_id: str) -> None:
        """
        Delete a deposition

        Note that this only works on draft depositions.

        Parameters
        ----------
        deposition_id
            Deposition ID to delete
        """
        logger.info(f"Deleting {deposition_id=!r}")
        self.get_response(
            f"/api/deposit/depositions/{deposition_id}",
            rest_action=RestAction.delete,
        )
        logger.info(f"Successfully deleted {deposition_id=!r}")

    def get_bibtex_entry(
        self,
        deposition_id: str,
    ) -> str:
        """
        Get the bibtex entry for a given deposition ID

        Parameters
        ----------
        deposition_id
            The ID of the deposition

        Returns
        -------
        :
            Bibtex entry for `deposition_id`.
        """
        logger.info(f"Retrieving bibtex entry for {deposition_id=!r}")
        response = self.get_response(f"/records/{deposition_id}/export/bibtex")

        bibtex_entry = response.text

        return bibtex_entry

    def get_bucket_url(self, deposition_id: str) -> str:
        """
        Get the bucket URL for a given deposition ID

        Parameters
        ----------
        deposition_id
            Deposition ID for which to get the bucket URL

        Returns
        -------
        :
            Bucket URL for `deposition_id`
        """
        logger.info(f"Retrieving bucket URL for {deposition_id=!r}")

        deposit_id_response = self.get_response(
            post_domain_part=f"/api/deposit/depositions/{deposition_id}",
        )

        bucket_url = str(deposit_id_response.json()["links"]["bucket"])
        logger.info(f"Successfully retrieved {bucket_url=!r} for {deposition_id=!r}")

        return bucket_url

    def get_concept_id(self, any_deposition_id: str) -> str:
        """
        Get the concept ID for a deposition

        The concept ID is the ID that is associated with all versions of a record.

        Parameters
        ----------
        any_deposition_id
            Any deposition ID in the concept

        Returns
        -------
        :
            Concept ID
        """
        concept_id = str(
            self.get_record(record_id=any_deposition_id).json()["conceptrecid"]
        )

        return concept_id

    def get_deposition(
        self,
        deposition_id: str,
    ) -> requests.models.Response:
        """
        Get a deposition from Zenodo

        Parameters
        ----------
        deposition_id
            The ID of the deposition

        Returns
        -------
        :
            The Zenodo deposition
        """
        logger.info(f"Retrieving deposition {deposition_id!r}")
        response = self.get_response(f"/api/deposit/depositions/{deposition_id}")

        return response

    def get_draft_deposition_id(self, latest_deposition_id: str) -> str:
        """
        Get the deposition ID for a draft

        If no draft exists, it is created from the latest deposition ID.
        Otherwise, the existing draft is returned.

        Parameters
        ----------
        latest_deposition_id
            ID of the latest deposition

        Returns
        -------
        :
            ID of the draft deposition
        """
        draft_deposition_id: None | str = None
        try:
            draft_deposition_id = self.create_new_version_from_latest(
                latest_deposition_id=latest_deposition_id
            ).json()["id"]

        except AssertionError:
            concept_id_record = self.get_record(record_id=latest_deposition_id).json()[
                "conceptrecid"
            ]

            drafts = self.get_response(
                post_domain_part="/api/deposit/depositions",
                rest_action=RestAction.get,
                params={"status": "draft"},
            ).json()
            for draft in drafts:
                if draft["conceptrecid"] == concept_id_record:
                    draft_deposition_id = draft["record_id"]
                    break

        if draft_deposition_id is None:
            msg = "Should have created a new draft or found an existing draft"
            raise AssertionError(msg)

        return draft_deposition_id

    def get_latest_deposition_id(
        self,
        any_deposition_id: str,
    ) -> str:
        """
        Get the latest deposition ID from any deposition ID which is part of the record

        For example, we can take the deposition ID
        from the first version of a record which was published
        and always be given back the deposition ID of the latest record in the series.

        Parameters
        ----------
        any_deposition_id
            Any deposition ID which belongs to the series/record of interest.

            This can be obtained from the URL of any deposit in the series.
            For example, if the Zenodo URL is
            https://sandbox.zenodo.org/records/101709,
            then you can pass in "101709" as `any_deposition_id`.

        Returns
        -------
        :
            ID of the latest deposition in the series/record
        """
        logger.info(
            "Retrieving the ID of the latest deposition in the series "
            f"which includes deposition ID {any_deposition_id!r}"
        )
        record = self.get_record(record_id=any_deposition_id)
        record_json = record.json()

        record_latest = requests.get(
            record_json["links"]["latest"], timeout=self.timeout
        )

        latest_deposition_id = str(record_latest.json()["id"])
        logger.info(
            f"For deposition ID {any_deposition_id!r}, "
            "the ID of the latest deposition in the series is "
            f"{latest_deposition_id!r}"
        )

        return latest_deposition_id

    def get_metadata(
        self,
        deposition_id: str,
        user_controlled_only: bool = False,
    ) -> MetadataType:
        """
        Get the metadata for a given deposition ID

        Parameters
        ----------
        deposition_id
            The ID of the deposition

        user_controlled_only
            Only return metadata keys that the user can control.

            If this is `True`, the metadata keys controlled by Zenodo
            (e.g. the DOI)
            are removed from the returned metadata.
            This flag is important to use
            if you want to use the retrieved metadata
            as the starting point for the next version of a deposit.

        Returns
        -------
        :
            Metadata, in a form which could be used directly with the Zenodo API

            For an example, see the docstring of
            [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata].
        """
        logger.info(f"Retrieving metadata for {deposition_id=!r}")
        if self.token:
            deposition = self.get_deposition(deposition_id)

        else:
            deposition = self.get_record(deposition_id)

        metadata = {"metadata": deposition.json()["metadata"]}

        if user_controlled_only:
            for k in [
                "doi",
                "imprint_publisher",
                "prereserve_doi",
                "publication_date",
                "relations",
            ]:
                if k in metadata["metadata"]:
                    metadata["metadata"].pop(k)

        return metadata

    def get_record(
        self,
        record_id: str,
    ) -> requests.models.Response:
        """
        Get a record from Zenodo

        Parameters
        ----------
        record_id
            The ID of the record

        Returns
        -------
        :
            The Zenodo record
        """
        logger.info(f"Retrieving record {record_id!r}")
        response = self.get_response(f"/api/records/{record_id}")

        return response

    def get_response(
        self,
        post_domain_part: str,
        rest_action: RestAction = RestAction.get,
        params: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> requests.models.Response:
        """
        Get a response from Zenodo

        Parameters
        ----------
        post_domain_part
            The post-domain part of the URL to hit.

            In other words, the API to hit.
            For example, "/api/deposit/depositions/1858949"

        params
            Headers to use as part of the request.

            The authentication token is automatically added
            before passing to the relevant requests action
            so you don't need to included that in `params`.

        **kwargs
            Passed to the relevant requests action.

        Returns
        -------
        :
            Response from the URL that was hit
        """
        if isinstance(self.zenodo_domain, ZenodoDomain):
            zenodo_domain = self.zenodo_domain.value

        else:
            zenodo_domain = self.zenodo_domain

        if params is None:
            params = {}

        if self.token:
            params["access_token"] = self.token

        url_to_hit = f"{zenodo_domain}{post_domain_part}"
        # Mask just in case the user put the token in the URL by accident
        logger.debug(
            f"Sending {rest_action} request to "
            f"{mask_token(url_to_hit, token=self.token)}"
        )

        requests_kwargs = dict(
            params=params,
            **kwargs,
        )
        if rest_action == RestAction.get:
            response = requests.get(url_to_hit, **requests_kwargs, timeout=self.timeout)

        elif rest_action == RestAction.post:
            response = requests.post(
                url_to_hit, **requests_kwargs, timeout=self.timeout
            )

        elif rest_action == RestAction.put:
            response = requests.put(url_to_hit, **requests_kwargs, timeout=self.timeout)

        elif rest_action == RestAction.delete:
            response = requests.delete(
                url_to_hit, **requests_kwargs, timeout=self.timeout
            )

        else:
            raise NotImplementedError(rest_action)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            print(response.json())
            raise

        return response

    def publish(self, deposition_id: str) -> requests.models.Response:
        """
        Publish a deposition

        Note that this only works on draft depositions.

        Parameters
        ----------
        deposition_id
            Deposition ID to publish

        Returns
        -------
        :
            Response from the publish request
        """
        logger.info(f"Publishing {deposition_id=!r}")
        response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/actions/publish",
            rest_action=RestAction.post,
        )
        logger.info(f"Successfully published {deposition_id=!r}")

        return response

    def remove_all_files(
        self,
        deposition_id: str,
        # # Off until parallelism works
        # n_threads: int = 4
    ) -> tuple[requests.models.Response, ...]:
        """
        Remove all the files currently associated with a given deposition

        Parameters
        ----------
        deposition_id
            Deposition ID from which to remove all files

        Returns
        -------
        :
            The response(s) from the file removal request(s)
        """
        logger.info(f"Removing all files from {deposition_id=!r}")
        files_response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/files",
        )

        file_ids_to_remove = [v["id"] for v in files_response.json()]

        return self.remove_files_by_id(
            deposition_id=deposition_id,
            file_ids_to_remove=file_ids_to_remove,
            # n_threads=n_threads,
        )

    def remove_file_id(
        self,
        deposition_id: str,
        to_remove_id: str,
    ) -> requests.models.Response:
        """
        Remove a file from a deposition, using its ID

        Parameters
        ----------
        deposition_id
            ID of the deposition to alter

        to_remove_id
            ID of the file to remove

        Returns
        -------
        :
            The response from the file removal request
        """
        response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/files/{to_remove_id}",
            rest_action=RestAction.delete,
        )

        return response

    def remove_files(
        self,
        deposition_id: str,
        to_remove: Collection[Path],
        # # Off until parallelism works
        # n_threads: int = 4,
    ) -> tuple[requests.models.Response, ...]:
        """
        Remove file(s) from a deposition

        Parameters
        ----------
        deposition_id
            ID of the deposition to alter

        to_remove
            File(s) to remove

        Returns
        -------
        :
            The response(s) from the file removal request(s)
        """
        logger.info(
            f"Removing {len(to_remove)} {'files' if len(to_remove) > 1 else 'file'} "
            f"from {deposition_id=!r}"
        )
        filenames_to_delete = set(f.name for f in to_remove)

        files_response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/files",
        )
        file_ids_to_remove = [
            v["id"]
            for v in files_response.json()
            if v["filename"] in filenames_to_delete
        ]

        return self.remove_files_by_id(
            file_ids_to_remove=file_ids_to_remove,
            deposition_id=deposition_id,
        )

    def remove_files_by_id(
        self,
        deposition_id: str,
        file_ids_to_remove: Iterable[str],
        # Off until parallelism works
        # n_threads: int = 4,
    ) -> tuple[requests.models.Response, ...]:
        """
        Remove file(s) from a deposition, using their IDs

        Parameters
        ----------
        deposition_id
            ID of the deposition to alter

        file_ids_to_remove
            ID of file(s) to remove

        Returns
        -------
        :
            The response(s) from the file removal request(s)
        """
        # Wanted to do this in parallel, but weirdly flaky
        responses = tuple(
            [
                self.remove_file_id(deposition_id=deposition_id, to_remove_id=file_id)
                for file_id in tqdm.tqdm(file_ids_to_remove, desc="Files to remove")
            ]
        )
        # with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:
        #     futures = [
        #         executor.submit(
        #             self.remove_file_id,
        #             to_remove_id=file_id,
        #             deposition_id=deposition_id,
        #         )
        #         for file_id in tqdm.tqdm(
        #             file_ids_to_remove, desc="Submitting files to queue"
        #         )
        #     ]
        #
        #     responses = tuple(
        #         [
        #             future.result()
        #             for future in tqdm.tqdm(
        #                 concurrent.futures.as_completed(futures),
        #                 desc="Files to remove",
        #                 total=len(futures),
        #             )
        #         ]
        #     )

        return responses

    def upload_file_to_bucket_url(
        self,
        to_upload: Path,
        bucket_url: str,
        tqdm_kwargs: dict[str, Any] | None = None,
    ) -> requests.models.Response:
        """
        Upload a file to a bucket URL

        This is a relatively low-level function,
        which requires you to have already determined
        the bucket URL to upload to yourself.

        Note that zenodo does not allow you to upload folders.
        As noted in [this response](https://support.zenodo.org/help/en-gb/1-upload-deposit/74-can-i-upload-folders-directories):

        > Instead, you can create a ZIP archive and upload it,
        > in which case Zenodo will display the file structure inside the ZIP.

        Parameters
        ----------
        to_upload
            File to upload

        bucket_url
            The bucket URL to use for the upload

        tqdm_kwargs
            Keyword arguments to use with our progress bar.

            If not supplied, we use
            [`TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT`][openscm_zenodo.zenodo.TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT].

        Returns
        -------
        :
            The response from the file upload request
        """
        if tqdm_kwargs is None:
            tqdm_kwargs = TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT

        upload_url = f"{bucket_url}/{to_upload.name}"

        logger.info(f"Uploading {to_upload} to {upload_url=!r}")

        file_size = os.stat(to_upload).st_size
        with tqdm.tqdm(total=file_size, **tqdm_kwargs) as tqdm_bar:
            with open(to_upload, "rb") as file_handle:
                wrapped_file = tqdm.utils.CallbackIOWrapper(
                    tqdm_bar.update, file_handle, "read"
                )
                response = requests.put(
                    upload_url,
                    data=wrapped_file,
                    params={"access_token": self.token},
                    timeout=self.timeout_upload,
                )

        response.raise_for_status()
        logger.info(f"Successfully uploaded {to_upload}")
        return response

    def upload_files(
        self,
        deposition_id: str,
        to_upload: Collection[Path],
        tqdm_kwargs: dict[str, Any] | None = None,
        n_threads: int = 4,
    ) -> tuple[requests.models.Response, ...]:
        """
        Upload file(s) to a deposition

        Note that zenodo does not allow you to upload folders.
        As noted in [this response](https://support.zenodo.org/help/en-gb/1-upload-deposit/74-can-i-upload-folders-directories):

        > Instead, you can create a ZIP archive and upload it,
        > in which case Zenodo will display the file structure inside the ZIP.

        Parameters
        ----------
        deposition_id
            ID of the deposition to upload to

        to_upload
            File(s) to upload

        tqdm_kwargs
            Keyword arguments to use with our progress bar.

            Passed to
            [`upload_file_to_bucket_url`][openscm_zenodo.zenodo.ZenodoInteractor.upload_file_to_bucket_url].

        n_threads
            Number of threads to use for the uploads.

        Returns
        -------
        :
            The response(s) from the file upload request(s)
        """
        logger.info(
            f"Uploading {len(to_upload)} {'files' if len(to_upload) > 1 else 'file'} "
            f"to {deposition_id=!r}"
        )
        bucket_url = self.get_bucket_url(deposition_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [
                executor.submit(
                    self.upload_file_to_bucket_url,
                    to_upload=file,
                    bucket_url=bucket_url,
                    tqdm_kwargs=tqdm_kwargs,
                )
                for file in tqdm.tqdm(to_upload, desc="Submitting files to queue")
            ]

            responses = tuple(
                [
                    future.result()
                    for future in tqdm.tqdm(
                        concurrent.futures.as_completed(futures),
                        desc="Files to upload",
                        total=len(futures),
                    )
                ]
            )

        return responses

    def update_metadata(
        self, deposition_id: str, metadata: MetadataType
    ) -> requests.models.Response:
        """
        Update the metadata for a given deposition

        Parameters
        ----------
        deposition_id
            Deposition ID of which to update the metadata

        metadata
            Metadata to apply to the deposition

            For the complete list of supported key : value pairs supported by Zenodo,
            see [https://developers.zenodo.org/#representation]().
            You do not need to provide values for all the metadata keys,
            only the ones relevant to you.

            For an example, see the docstring of
            [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata].

        Returns
        -------
        :
            Response to the metadata update request.
        """
        logger.info(f"Updating metadata for {deposition_id=!r}")
        logger.debug(f"New metadata: {metadata}")

        update_metadata_response = self.get_response(
            post_domain_part=f"/api/deposit/depositions/{deposition_id}",
            rest_action=RestAction.put,
            data=json.dumps(metadata),
            headers={"Content-Type": "application/json"},
        )

        return update_metadata_response


def retrieve_metadata(
    deposition_id: str,
    zenodo_interactor: ZenodoInteractor | None = None,
) -> dict[str, dict[str, str]]:
    r"""
    Retrieve metadata associated with a given deposition ID

    Parameters
    ----------
    deposition_id
        The ID of the deposition

    zenodo_interactor
        Object to use to interact with Zenodo.

        If not supplied, we use a default interactor with no authentication.

    Returns
    -------
    :
        Metadata, in a form which could be used directly with the Zenodo API.

    Examples
    --------
    >>> import json
    >>> res_raw = retrieve_metadata("4589756")
    >>> res_json = json.dumps(res_raw, indent=2, sort_keys=True)
    >>> print(res_json)
    {
      "metadata": {
        "access_right": "open",
        "creators": [
          {
            "affiliation": "Australian-German Climate & Energy College, University of Melbourne",
            "name": "Zebedee Nicholls",
            "orcid": "0000-0002-4767-2723"
          },
          {
            "affiliation": "Australian-German Climate & Energy College, University of Melbourne",
            "name": "Jared Lewis",
            "orcid": "0000-0002-8155-8924"
          }
        ],
        "description": "Reduced Complexity Model Intercomparison Project (RCMIP) protocol. The protocol defines all of RCMIP's experiments as well as RCMIP's submission template. If used, please also cite Nicholls et al., GMD 2020 (https://doi.org/10.5194/gmd-13-5175-2020).",
        "doi": "10.5281/zenodo.4589756",
        "keywords": [
          "rcmip",
          "protocol",
          "climate",
          "reduced-complexity",
          "model",
          "models",
          "intercomparison",
          "comparison"
        ],
        "language": "eng",
        "license": {
          "id": "cc-by-sa-4.0"
        },
        "publication_date": "2021-03-09",
        "relations": {
          "version": [
            {
              "index": 1,
              "is_last": true,
              "parent": {
                "pid_type": "recid",
                "pid_value": "4589726"
              }
            }
          ]
        },
        "resource_type": {
          "title": "Dataset",
          "type": "dataset"
        },
        "title": "Reduced Complexity Model Intercomparison Project (RCMIP) protocol",
        "version": "v5.1.0"
      }
    }
    """  # noqa: E501
    if zenodo_interactor is None:
        zenodo_interactor = ZenodoInteractor()

    return zenodo_interactor.get_metadata(deposition_id)


def retrieve_bibtex_entry(
    deposition_id: str,
    zenodo_interactor: ZenodoInteractor | None = None,
) -> str:
    r"""
    Retrieve the bibtext entry associated with a given deposition ID

    Parameters
    ----------
    deposition_id
        The ID of the deposition

    zenodo_interactor
        Object to use to interact with Zenodo.

        If not supplied, we use a default interactor with no authentication.

    Returns
    -------
    :
        Bibtex entry for deposition ID `deposition_id`.

    Examples
    --------
    >>> res = retrieve_bibtex_entry("4589756")
    >>> # There are trailing newlines in the Zenodo response.
    >>> # We strip them here
    >>> res_disp = "\n".join([v.rstrip() for v in res.splitlines()])
    >>> print(res_disp)
    @dataset{zebedee_nicholls_2021_4589756,
      author       = {Zebedee Nicholls and
                      Jared Lewis},
      title        = {Reduced Complexity Model Intercomparison Project
                       (RCMIP) protocol
                      },
      month        = mar,
      year         = 2021,
      publisher    = {Zenodo},
      version      = {v5.1.0},
      doi          = {10.5281/zenodo.4589756},
      url          = {https://doi.org/10.5281/zenodo.4589756},
    }
    """
    if zenodo_interactor is None:
        zenodo_interactor = ZenodoInteractor()

    return zenodo_interactor.get_bibtex_entry(deposition_id)


def create_new_version(  # noqa: PLR0913
    any_deposition_id: str,
    zenodo_interactor: ZenodoInteractor,
    metadata: MetadataType | None = None,
    publish: bool = False,
    files_to_upload: list[Path] | None = None,
    n_threads: int = 4,
) -> str:
    """
    Create a new version of a given record

    This starts from the ID of any deposition in the record/series.

    Parameters
    ----------
    any_deposition_id
        Any deposition ID which belongs to the series/record of interest.

        This can be obtained from the URL of any deposit in the series.
        For example, if the Zenodo URL is
        https://sandbox.zenodo.org/records/101709,
        then you can pass in "101709" as `any_deposition_id`.

    zenodo_interactor
        Object to use to interact with Zenodo

    metadata
        Path to the file that contains the metadata to apply to the new version.

        If not supplied, the metadata from the previous version will not be updated.

        For futher information about the required form,
        see the docstring of
        [`update_metadata`][openscm_zenodo.zenodo.ZenodoInteractor.update_metadata].
        To get an example, see the docstring of
        [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata].

    publish
        Should we publish the newly created version once we have uploaded the files?

    files_to_upload
        If supplied, the files to upload to the newly created version.

    n_threads
        If `files_to_upload` is supplied,
        the number of threads to use for parallel uploads.

    Returns
    -------
    :
        Deposition ID of the new version
    """
    latest_deposition_id = zenodo_interactor.get_latest_deposition_id(
        any_deposition_id=any_deposition_id,
    )

    new_deposition_id = zenodo_interactor.create_new_version_from_latest(
        latest_deposition_id=latest_deposition_id
    ).json()["id"]

    if metadata is not None:
        zenodo_interactor.update_metadata(
            deposition_id=new_deposition_id,
            metadata=metadata,
        )

    if files_to_upload is not None:
        zenodo_interactor.upload_files(
            deposition_id=new_deposition_id,
            to_upload=files_to_upload,
            n_threads=n_threads,
        )

    if publish:
        zenodo_interactor.publish(new_deposition_id)

    return str(new_deposition_id)


def get_reserved_doi(zenodo_record_response: requests.models.Response) -> str:
    """
    Get the reserved DOI from a Zenodo record response

    We think that this works
    with basically any response related to retrieving a record from Zenodo,
    because it basically just looks at the metadata field.
    However, it may not support all responses.
    You have been warned.

    Parameters
    ----------
    zenodo_record_response
        The Zenodo response for a record, from which to get the reserved DOI.

    Returns
    -------
    :
        The record's reserved DOI
    """
    return str(zenodo_record_response.json()["metadata"]["prereserve_doi"]["doi"])
