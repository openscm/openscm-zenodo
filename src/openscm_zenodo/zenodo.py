"""
Zenodo interactions handling
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import os.path
import urllib.parse
from collections.abc import Callable, Collection, Iterable, Mapping
from enum import Enum, auto
from pathlib import Path
from types import TracebackType
from typing import (
    Any,
    NewType,
    NoReturn,
    TypeAlias,
    TypeVar,
    cast,
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

from openscm_zenodo.checksums import (
    assert_md5_matches,
    get_file_md5,
    get_md5_from_checksum,
)
from openscm_zenodo.exceptions import (
    ChecksumMismatchError,
    DraftMetadataEditsNotFoundError,
    DraftRecordDraftMetadataEditsError,
    FileNotOnRecordError,
    FileTransferFailedError,
    MissingTokenError,
    PublishedRecordDraftError,
    RecordNotFoundError,
    RecordNotWritableError,
    UnknownCitationStyleError,
    ZenodoError,
    ZenodoHTTPError,
    warn_zenodo,
)
from openscm_zenodo.logging import mask_token
from openscm_zenodo.metadata import Metadata, find_discarded_metadata
from openscm_zenodo.progress import (
    PositionAllocator,
    get_file_progress_bar,
    get_files_progress_bar,
    get_progress_reading_wrapper,
)

_T = TypeVar("_T")
_R = TypeVar("_R")

_LOGGER = logging.getLogger(__name__)

HTTP_BAD_REQUEST = 400
"""HTTP status code Zenodo returns when it will not accept a request"""

HTTP_FORBIDDEN = 403
"""HTTP status code Zenodo returns when we may not have what we asked for"""

HTTP_NOT_FOUND = 404
"""HTTP status code Zenodo returns when there is nothing at a path"""

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


class FilesMode(str, Enum):
    """
    How a new version of a record should treat files

    The names line up one-for-one with what happens to the new draft's file list:
    nothing carried over, everything carried over, or made to match.
    """

    start_fresh = "start_fresh"
    """
    Start from an empty draft, then upload the files given

    Nothing is carried over from the previous version.
    """

    inherit = "inherit"
    """
    Carry the previous version's files over, then upload the files given on top

    Inherited files are not re-uploaded, Zenodo copies them across itself.
    """

    mirror = "mirror"
    """
    Carry the previous version's files over, then make the draft match exactly

    **This deletes files.** Inherited files which are not in the files given
    are removed, and only changed or new files are transferred.
    This is the efficient path for releasing a new version of a dataset
    where most files have not changed.
    """


class CitationFormat(str, Enum):
    """
    Formats in which Zenodo will export a record

    Zenodo serves these through content negotiation on the record itself,
    i.e. `GET /api/records/{id}` with an `Accept` header,
    see [`CITATION_FORMAT_ACCEPT`][openscm_zenodo.zenodo.CITATION_FORMAT_ACCEPT].

    The values here are the names a user types, e.g. `--format datacite-json`,
    not the mime types they translate to.
    The two are deliberately separate: the value is public API
    (it appears on the command line, in help text and in any config file),
    whereas the mime type is a transport detail
    which Zenodo could change without the format itself changing.
    """

    bibtex = "bibtex"
    """BibTeX entry"""

    csl = "csl"
    """Citation Style Language JSON"""

    datacite_json = "datacite-json"
    """DataCite JSON"""

    datacite_xml = "datacite-xml"
    """DataCite XML"""

    dublin_core = "dublin-core"
    """Dublin Core XML"""

    json_ld = "json-ld"
    """JSON-LD"""

    marcxml = "marcxml"
    """MARCXML"""

    dcat = "dcat"
    """DCAT XML"""

    citation = "citation"
    """
    A styled, human-readable citation string

    This is the only format which uses
    `style` and `locale`, see
    [`get_citation`][openscm_zenodo.zenodo.ZenodoClient.get_citation].
    """


CITATION_FORMAT_ACCEPT: dict[CitationFormat, str] = {
    CitationFormat.bibtex: "application/x-bibtex",
    CitationFormat.csl: "application/vnd.citationstyles.csl+json",
    CitationFormat.datacite_json: "application/vnd.datacite.datacite+json",
    CitationFormat.datacite_xml: "application/vnd.datacite.datacite+xml",
    CitationFormat.dublin_core: "application/x-dc+xml",
    CitationFormat.json_ld: "application/ld+json",
    CitationFormat.marcxml: "application/marcxml+xml",
    CitationFormat.dcat: "application/dcat+xml",
    CitationFormat.citation: "text/x-bibliography",
}
"""
The `Accept` header which gets each [`CitationFormat`][openscm_zenodo.zenodo.CitationFormat]

Every format is covered: the tests parametrise over
[`CitationFormat`][openscm_zenodo.zenodo.CitationFormat],
so a member added without an entry here fails the suite
rather than raising a `KeyError` in front of a user.
"""  # noqa: E501

INVENIORDM_JSON_ACCEPT = "application/vnd.inveniordm.v1+json"
"""
`Accept` header which gets the native InvenioRDM view of a record

Zenodo's default serialisation of a record is a legacy-compatible shape,
which hides `access` and `pids` and renders `files` as a list.
We can use this to ask for the native document instead
(because that is the shape the rest of the API speaks).
"""

CITATION_STYLE_DEFAULT = "apa"
"""
Citation style used if none is given

`Accept: text/x-bibliography` without a style is a
`400 Citation string style not found.`, so there has to be a default.
"""

CITATION_LOCALE_DEFAULT = "en-US"
"""Citation locale used if none is given"""

KNOWN_CITATION_STYLES: tuple[str, ...] = (
    "acm-sig-proceedings",
    "american-medical-association",
    "apa",
    "bibtex",
    "chicago-author-date",
    "harvard-cite-them-right",
    "ieee",
    "modern-language-association",
    "nature",
    "science",
)
"""
Citation style IDs we have verified Zenodo accepts

This is not the full list.
Zenodo accepts more CSL styles than we have checked,
so an unknown style is passed through with a warning
rather than being rejected.
"""

CITATION_STYLE_ALIASES: dict[str, str] = {
    "chicago-fullnote-bibliography": "chicago-author-date",
    "chicago-note-bibliography": "chicago-author-date",
    "harvard1": "harvard-cite-them-right",
    "mla": "modern-language-association",
    "vancouver": "american-medical-association",
}
"""
Style IDs we have verified Zenodo rejects, and the closest ID it accepts

Zenodo wants full CSL style filenames, not the short names its web UI shows,
so these are the obvious things to type which come back as a `400`.
We fail on them up front, with the working ID in the message,
rather than letting the request go out and fail.
"""


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


def _repr_token(token: str | None) -> str:
    """Get the `repr` to use for a token, i.e. never the token itself"""
    return "None" if token is None else "***"


@define
class ResolvedToken:
    """
    A token, and where it came from
    """

    token: str | None = field(repr=_repr_token)
    """The token, if one could be resolved"""

    source: str | None
    """
    Where the token came from, if one could be resolved

    This is a description, never the token itself,
    written so that it reads after "using",
    for example "the token from $ZENODO_TOKEN".
    """


def get_token_env_vars(
    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production,
) -> tuple[str, ...]:
    """
    Get the environment variables we read a token from, in order of precedence

    Parameters
    ----------
    zenodo_domain
        Zenodo domain that the token will be used with

    Returns
    -------
    :
        Environment variables to check, highest precedence first

    Examples
    --------
    >>> get_token_env_vars(ZenodoDomain.production)
    ('ZENODO_TOKEN',)

    Sandbox and production tokens are not interchangeable,
    so the sandbox variable is only in play when talking to the sandbox

    >>> get_token_env_vars(ZenodoDomain.sandbox)
    ('ZENODO_SANDBOX_TOKEN', 'ZENODO_TOKEN')
    """
    if get_zenodo_domain_url(zenodo_domain) == ZenodoDomain.sandbox.value:
        return (ZENODO_SANDBOX_TOKEN_ENV_VAR, ZENODO_TOKEN_ENV_VAR)

    return (ZENODO_TOKEN_ENV_VAR,)


def resolve_token(
    token: str | None = None,
    *,
    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production,
    env: Mapping[str, str] | None = None,
    required: bool = False,
    description: str = "interact with Zenodo",
) -> ResolvedToken:
    """
    Resolve the token to use for interacting with Zenodo

    This is the one place that knows where tokens come from,
    so the library, the command-line interface and the tests
    cannot disagree about where a token came from.
    In order of precedence, highest first, we use:

    1. `token`, if it is supplied
    1. `ZENODO_SANDBOX_TOKEN`, but only if `zenodo_domain` is the sandbox
    1. `ZENODO_TOKEN`

    A `.env` file is not read here, call
    [`load_env_file`][openscm_zenodo.zenodo.load_env_file]
    before this function if you need `.env` file support.

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

        The default, `False`, resolves to no token if there is none to find,
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
        The token and where it came from.

        Both are `None` if no token could be resolved
        and `required` is `False`.

    Raises
    ------
    MissingTokenError
        No token could be resolved and `required` is `True`

    Examples
    --------
    >>> resolve_token("supplied-directly", env={}).token
    'supplied-directly'
    >>> resolve_token("supplied-directly", env={}).source
    'the token you supplied'

    >>> resolve_token(env={"ZENODO_TOKEN": "from-the-environment"}).token
    'from-the-environment'
    >>> resolve_token(env={"ZENODO_TOKEN": "abc"}).source
    'the token from $ZENODO_TOKEN'

    Sandbox tokens are only used with the sandbox domain

    >>> env = {"ZENODO_SANDBOX_TOKEN": "sandbox-token", "ZENODO_TOKEN": "prod-token"}
    >>> resolve_token(env=env, zenodo_domain=ZenodoDomain.sandbox).token
    'sandbox-token'
    >>> resolve_token(env=env, zenodo_domain=ZenodoDomain.production).token
    'prod-token'

    If nothing resolves, there is no token and no source

    >>> resolve_token(env={}).token is None
    True
    >>> resolve_token(env={}).source is None
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
        resolved = ResolvedToken(token=token, source="the token you supplied")

    else:
        if env is None:
            env = os.environ

        for env_var in get_token_env_vars(zenodo_domain):
            env_token = env.get(env_var)
            if env_token:
                logger.debug(f"Using the token from ${env_var}")
                resolved = ResolvedToken(
                    token=env_token, source=f"the token from ${env_var}"
                )
                break

    if resolved is None:
        logger.debug("No Zenodo token could be resolved")

        if required:
            raise MissingTokenError(
                description,
                zenodo_domain=get_zenodo_domain_url(zenodo_domain),
                env_vars=get_token_env_vars(zenodo_domain),
            )

        resolved = ResolvedToken(token=None, source=None)

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


@define
class FileEntry:
    """
    A file which is on a record as Zenodo describes it
    """

    filename: str
    """
    Name of the file, as it appears on Zenodo

    Zenodo calls this the file's `key`;
    we call it `filename` throughout, as the rest of this package does.
    """

    size: int
    """Size of the file in bytes"""

    checksum: str
    """Checksum of the file, as Zenodo reports it, i.e. `"md5:<hex>"`"""

    status: str
    """
    Status of the file

    `"completed"` once the file has been committed,
    `"pending"` while it is still being uploaded.
    A draft which has a pending file on it cannot be published.
    """

    content_url: str
    """
    URL to download the file's content from

    This comes from Zenodo rather than being built by us,
    which is what saves downloads from having to work out
    whether the file sits behind the published or the draft endpoint.
    """

    raw: dict[str, Any] = field(repr=False)
    """
    Everything Zenodo sent about the file

    Zenodo reports more than we model here
    (timestamps, mime type, storage class, internal IDs and links).
    Rather than grow a field every time one of them turns out to be useful,
    they are kept here.
    """

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> FileEntry:
        """
        Initialise from Zenodo's description of a file

        Everything we model is pulled out here,
        so a response which is not shaped the way we expect
        fails at the point we read it,
        rather than much later when something reaches for the missing piece.

        Parameters
        ----------
        raw
            Zenodo's description of the file

        Returns
        -------
        :
            Initialised `FileEntry`

        Raises
        ------
        KeyError
            `raw` is not shaped the way Zenodo describes a file
        """
        return cls(
            filename=raw["key"],
            size=raw["size"],
            checksum=raw["checksum"],
            status=raw["status"],
            content_url=raw["links"]["content"],
            raw=raw,
        )

    @property
    def md5(self) -> str:
        """
        MD5 checksum of the file, as a hex string

        Returns
        -------
        :
            MD5 checksum of the file
        """
        return get_md5_from_checksum(self.checksum)


@define
class Embargo:
    """
    An embargo on a record, i.e. a date before which it is not public
    """

    active: bool = False
    """Is the embargo in force?"""

    until: str | None = None
    """Date the embargo lifts, as `YYYY-MM-DD`"""

    reason: str | None = None
    """Why the record is embargoed"""

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Embargo:
        """
        Initialise from Zenodo's description of an embargo

        Parameters
        ----------
        raw
            Zenodo's description of the embargo

        Returns
        -------
        :
            Initialised `Embargo`
        """
        return cls(
            active=bool(raw.get("active", False)),
            until=raw.get("until"),
            reason=raw.get("reason"),
        )


@define
class Access:
    """
    Who may see a record and its files

    Writing this is not supported yet — it lands with `create_record`,
    where a record's access has to be set at creation anyway.
    """

    record: str = "public"
    """Who may see the record itself, `"public"` or `"restricted"`"""

    files: str = "public"
    """Who may see its files, `"public"` or `"restricted"`"""

    embargo: Embargo = field(factory=Embargo)
    """The embargo, if there is one"""

    status: str | None = None
    """
    Zenodo's summary of the two, e.g. `"open"`, `"embargoed"`, `"restricted"`

    Zenodo derives this, so it is read-only.
    """

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Access:
        """
        Initialise from Zenodo's description of a record's access

        Parameters
        ----------
        raw
            Zenodo's description of the access

        Returns
        -------
        :
            Initialised `Access`
        """
        return cls(
            record=raw.get("record", "public"),
            files=raw.get("files", "public"),
            embargo=Embargo.from_json(raw.get("embargo", {})),
            status=raw.get("status"),
        )


@define
class Version:
    """
    Where a record sits in its chain of versions
    """

    index: int | None = None
    """
    Which version this is, counting from one

    `None` for a draft of a brand-new record, which is not a version of
    anything yet.
    """

    is_latest: bool | None = None
    """
    Is this the latest *published* version?

    `None` when Zenodo did not say.
    """

    is_latest_draft: bool | None = None
    """
    Is this the most recent version, published or not?

    `None` when Zenodo did not say.
    """

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Version:
        """
        Initialise from Zenodo's description of a record's versions

        Parameters
        ----------
        raw
            Zenodo's description of the versions

        Returns
        -------
        :
            Initialised `Version`
        """
        return cls(
            index=raw.get("index"),
            is_latest=raw.get("is_latest"),
            is_latest_draft=raw.get("is_latest_draft"),
        )


@define
class Record:
    """
    A record on Zenodo, published or still a draft

    Like [`FileEntry`][openscm_zenodo.zenodo.FileEntry],
    the parts we model are pulled out into typed fields
    and everything else Zenodo sent is kept in `raw`,
    so a response which is not shaped the way we expect
    fails at the point we read it.
    """

    record_id: RecordID
    """ID of this version of the record"""

    metadata: Metadata
    """The record's metadata"""

    is_draft: bool
    """
    Has this record never been published?

    This is the opposite of "is it published", so there is only one of the two.
    A published record which has an *edited metadata draft* is still published,
    so this is `False` for it; the draft document itself is marked by
    [`is_edited_metadata_draft`][openscm_zenodo.zenodo.Record.is_edited_metadata_draft].
    """

    is_edited_metadata_draft: bool
    """
    Is this document a published record's unpublished metadata changes?

    A published record's metadata can be corrected in place, by taking a draft
    of it, editing that and publishing it again under the same ID and DOI. This
    is `True` only for that draft, i.e. only for what
    [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft]
    hands back.

    A record read with
    [`get_published`][openscm_zenodo.zenodo.ZenodoClient.get_published]
    is always `False` here, **even when such a draft exists**.
    Asking whether a draft exists is a separate request,
    see
    [`has_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.has_edited_metadata_draft]
    or
    [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft]
    to ensure that an edited metadata draft exists then get it.
    """

    parent_id: ParentID
    """
    ID which refers to all versions of this record

    Resolving it (via e.g. Zenodo's web interface)
    gives whichever version is the latest at the time.
    """

    access: Access
    """Who may see the record and its files"""

    pids: dict[str, Any]
    """
    Persistent identifiers Zenodo has minted for the record

    For specific IDs, access the specific properties,
    e.g. [`doi`][openscm_zenodo.zenodo.Record.doi],
    rather than reaching in here.
    """

    version: Version
    """Where this record sits in its chain of versions"""

    raw: dict[str, Any] = field(repr=False)
    """
    Everything Zenodo sent about the record

    Timestamps, links, statistics, custom fields and the file listing
    all live here rather than each growing a field of its own.
    """

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Record:
        """
        Initialise from Zenodo's description of a record

        Parameters
        ----------
        raw
            Zenodo's description of the record.

            This must be the native InvenioRDM serialisation,
            i.e. what Zenodo sends for
            `Accept: application/vnd.inveniordm.v1+json`.

        Returns
        -------
        :
            Initialised `Record`

        Raises
        ------
        KeyError
            `raw` is not shaped the way Zenodo describes a record
        """
        is_published = raw["is_published"]

        return cls(
            record_id=RecordID(str(raw["id"])),
            metadata=Metadata.from_json(raw.get("metadata", {})),
            is_draft=not is_published,
            is_edited_metadata_draft=bool(raw["is_draft"] and is_published),
            parent_id=ParentID(str(raw["parent"]["id"])),
            access=Access.from_json(raw.get("access", {})),
            pids=raw.get("pids", {}),
            version=Version.from_json(raw.get("versions", {})),
            raw=raw,
        )

    @property
    def doi(self) -> str | None:
        """
        DOI of this version of the record

        Returns
        -------
        :
            The DOI, or `None` if one has not been minted or reserved yet.

            A draft only has one once it has been published
            or a DOI has been reserved for it.
        """
        doi = self.pids.get("doi", {}).get("identifier")

        return cast("str | None", doi)

    @property
    def parent_doi(self) -> str | None:
        """
        DOI which refers to all versions of the record

        This is the one to cite if the citation should not go stale:
        it resolves to whichever version is the latest at the time.

        Returns
        -------
        :
            The DOI, or `None` if one has not been minted yet
        """
        doi = (
            self.raw.get("parent", {}).get("pids", {}).get("doi", {}).get("identifier")
        )

        return cast("str | None", doi)

    @property
    def is_latest_version(self) -> bool | None:
        """
        Is this the latest published version of the record?

        Returns
        -------
        :
            Whether it is, or `None` if Zenodo did not say
            (which it does not for a draft of a brand-new record)
        """
        return self.version.is_latest


RecordIDLike: TypeAlias = "str | RecordID | Record"
"""
Anything we will take as "which record"

A [`Record`][openscm_zenodo.zenodo.Record] is accepted as well as its ID so
that a record which has just been handed back can be passed straight on,
rather than having to be unwrapped at every call site.
Note the asymmetry with metadata, which is only ever taken as a
[`Metadata`][openscm_zenodo.metadata.Metadata]: a `Record` *is* an ID plus more,
so nothing has to be guessed, whereas metadata given as a mapping or a path is
a different thing which has to be interpreted.
"""


def get_record_id(record_id: RecordIDLike) -> RecordID:
    """
    Get the record ID out of whatever we were handed

    Parameters
    ----------
    record_id
        The record, or its ID

    Returns
    -------
    :
        The record's ID

    Examples
    --------
    >>> get_record_id("4589756")
    '4589756'
    """
    if isinstance(record_id, Record):
        return record_id.record_id

    return RecordID(str(record_id))


@define
class FileDiff:
    """
    The difference between a set of local files and a record's files
    """

    to_upload: dict[Path, str]
    """
    Files which are not on the record, or are there with different contents

    Maps each file to its local MD5 checksum,
    which we have already calculated in order to make the comparison,
    so the upload does not have to calculate it again.
    """

    unchanged: tuple[Path, ...]
    """Files which are already on the record with the same contents"""

    to_delete: tuple[str, ...]
    """Names of files which are on the record but not in the local set"""

    remote: dict[str, FileEntry] = field(repr=False)
    """The record's files, as they were before any of this is acted on"""


def _run_in_parallel(
    func: Callable[[_T], _R],
    items: Collection[_T],
    *,
    n_threads: int,
    desc: str,
    progress: bool = True,
) -> list[_R]:
    """
    Apply `func` to every item, in parallel, showing progress as they complete

    Parameters
    ----------
    func
        Function to apply

    items
        Items to apply `func` to

    n_threads
        Number of threads to use

    desc
        Description of the operation, shown on the progress bar

    progress
        Should a progress bar be shown?

    Returns
    -------
    :
        The results, in the order of `items`

    Raises
    ------
    Exception
        Whatever `func` raised.

        Every item is attempted before we raise,
        so a single failure part way through does not abandon the rest,
        and the failure that is raised is the first one in `items` order.
    """
    with (
        get_files_progress_bar(desc=desc, total=len(items), progress=progress) as bar,
        concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor,
    ):
        futures = [executor.submit(func, item) for item in items]

        for _ in concurrent.futures.as_completed(futures):
            bar.update(1)

    # Now that everything has finished, surface the first failure, if there was one
    return [future.result() for future in futures]


def get_reported_errors(response: requests.models.Response) -> str | None:
    """
    Get the failure a successful-looking response is reporting, if it is reporting one

    Zenodo answers a failed file transfer with a `200` whose body carries an
    `errors` key, so a successful status code is not on its own proof that
    anything happened.

    Parameters
    ----------
    response
        Response to look at

    Returns
    -------
    :
        What Zenodo said went wrong, or `None` if it did not say anything.

        A body which is not JSON, or not an object, counts as not saying
        anything: this is a safety net rather than a parser, and a response we
        cannot read is not evidence of a failure.
    """
    try:
        body = response.json()

    except ValueError:
        return None

    if not isinstance(body, Mapping):
        return None

    errors = body.get("errors")

    return None if not errors else str(errors)


def should_retry_transfer(
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
    if isinstance(exc, ChecksumMismatchError | FileTransferFailedError):
        # Not HTTP failures: the request succeeded, but the bytes were wrong
        # or Zenodo's storage dropped them, so we need to retry.
        return True

    if isinstance(exc, ZenodoHTTPError):
        # Zenodo answered, so only try again if the answer suggests it is worth it
        # (e.g. retrying a rejected upload four more times helps no one).
        return exc.response.status_code in retry_status_forcelist

    # Only worth retrying connection errors.
    return isinstance(exc, requests.exceptions.RequestException)


def _build_transfer_retrying(max_attempts: int) -> Retrying:
    """
    Build the retry policy for transferring a file's content

    Uploads and downloads share this.
    Both uploads and downloads stream,
    so neither can be retried by the transport layer
    (see [`should_retry_transfer`][openscm_zenodo.zenodo.should_retry_transfer]).
    Instead, both start from the beginning when they are tried again.

    Parameters
    ----------
    max_attempts
        Maximum number of attempts before giving up

    Returns
    -------
    :
        The retry policy
    """
    return Retrying(
        retry=retry_if_exception(should_retry_transfer),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=1.0, max=60.0),
        before_sleep=_log_transfer_retry,
        reraise=True,
    )


def _log_transfer_retry(retry_state: RetryCallState) -> None:
    """
    Log that a transfer is about to be tried again

    Parameters
    ----------
    retry_state
        State of the retrying, as tenacity reports it
    """
    # tenacity only calls this when it is about to sleep and try again,
    # so there is always an outcome and a next action.
    # The `None` handling below is only here to satisfy the type checkers,
    # and deliberately does not claim a sleep time it does not have.
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None

    next_action = retry_state.next_action
    trying_again = (
        "Trying again."
        if next_action is None
        else f"Trying again in {next_action.sleep:.1f}s."
    )

    logger.warning(
        f"Transfer attempt {retry_state.attempt_number} failed with {exc!r}. "
        f"{trying_again}"
    )


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

    token_source: str | None = field(init=False, default=None)
    """
    Where [`token`][openscm_zenodo.zenodo.ZenodoClient.token] came from

    Worked out during initialisation, see
    [`resolve_token`][openscm_zenodo.zenodo.resolve_token].
    This is a description, never the token itself, so it is safe to show,
    and it is what lets errors say which token they used.
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
        resolved = resolve_token(self.token, zenodo_domain=self.zenodo_domain)
        self.token = resolved.token
        self.token_source = resolved.source

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
                description,
                zenodo_domain=self.zenodo_domain_url,
                env_vars=get_token_env_vars(self.zenodo_domain),
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
            raise MissingTokenError(
                description,
                zenodo_domain=self.zenodo_domain_url,
                env_vars=get_token_env_vars(self.zenodo_domain),
            )

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

    def delete_file(self, record_id: RecordIDLike, filename: str) -> None:
        """
        Delete a file from a record

        The record must be, by definition, a draft
        (you can't delete from published records).

        Parameters
        ----------
        record_id
            Record from which to delete the file

        filename
            Name of the file to delete, as it appears on Zenodo
        """
        record_id = get_record_id(record_id)
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

        This is the first step of an upload.

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

        def initialise_file() -> None:
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
                response = self._request(
                    self._get_draft_file_path(record_id, filename, "/content"),
                    method="PUT",
                    requires_auth=True,
                    data=get_progress_reading_wrapper(file_handle, progress_bar),
                    headers={"Content-Type": "application/octet-stream"},
                    timeout=self.timeout_upload,
                    description=f"upload {filename!r} to record {record_id!r}",
                )

        # Zenodo reports a storage failure here as a `200` with an `errors` key,
        # not as an unsuccessful status, so the response has to be read.
        errors = get_reported_errors(response)
        if errors is not None:
            raise FileTransferFailedError(
                filename, record_id=str(record_id), errors=errors
            )

    def _commit_file(self, record_id: str, filename: str) -> FileEntry:
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

        return FileEntry.from_json(cast(dict[str, Any], response.json()))

    def _upload_file_attempt(  # noqa: PLR0913
        self,
        record_id: str,
        path: Path,
        *,
        filename: str,
        local_md5: str | None,
        progress: bool,
        position: int | None,
    ) -> FileEntry:
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
                filename, local_md5=local_md5, remote_checksum=entry.checksum
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
        record_id: RecordIDLike,
        path: Path,
        *,
        verify_checksum: bool = True,
        local_md5: str | None = None,
        progress: bool = True,
        position: int | None = None,
        max_attempts: int = 5,
    ) -> FileEntry:
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
            Unless `local_md5` is supplied,
            this costs one extra read of the file, so it can be turned off,
            but a corrupted upload is worse than a slow one.

        local_md5
            MD5 checksum of `path`, if you have already calculated it.

            Only used when `verify_checksum` is `True`,
            in which case supplying it saves us reading the file again.

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
        record_id = get_record_id(record_id)
        filename = path.name
        logger.info(f"Uploading {path} as {filename!r} to record {record_id!r}")

        local_md5_to_check = None
        if verify_checksum:
            local_md5_to_check = (
                local_md5 if local_md5 is not None else get_file_md5(path)
            )

        retrying = _build_transfer_retrying(max_attempts)

        try:
            entry = retrying(
                self._upload_file_attempt,
                record_id,
                path,
                filename=filename,
                local_md5=local_md5_to_check,
                progress=progress,
                position=position,
            )

        except Exception:
            self._clean_up_failed_upload(record_id, filename)

            raise

        logger.info(f"Successfully uploaded {path} as {filename!r}")

        return entry

    def _list_files_at(
        self, record_id: RecordIDLike, *, draft: bool
    ) -> dict[str, FileEntry]:
        """
        List the files behind one of the two file endpoints

        Parameters
        ----------
        record_id
            ID of the record whose files to list

        draft
            Is the record a draft, rather thana published record?

        Returns
        -------
        :
            The files, keyed by their name on Zenodo
        """
        record_id = get_record_id(record_id)
        part = "/draft/files" if draft else "/files"
        response = self._request(
            f"/api/records/{record_id}{part}",
            # A published record's files can be public, a draft's never are
            requires_auth=draft,
            description=f"list the files on record {record_id!r}",
        )

        entries = cast(list[dict[str, Any]], response.json()["entries"])
        parsed = [FileEntry.from_json(entry) for entry in entries]

        return {entry.filename: entry for entry in parsed}

    def _can_see(self, path: str, *, requires_auth: bool, description: str) -> bool:
        """
        Is there something at this path which we are allowed to have?

        Parameters
        ----------
        path
            Path to look at

        requires_auth
            Does looking there require a token?

        description
            Description of what we are looking for, used in error messages

        Returns
        -------
        :
            `True` if there is something there and we may have it.

            `False` covers both "there is nothing there"
            and "there is, but not for you",
            because from out here those are the same thing.
        """
        try:
            self._request(path, requires_auth=requires_auth, description=description)

        except ZenodoHTTPError as exc:
            nothing_for_us = (HTTP_FORBIDDEN, HTTP_NOT_FOUND)
            if exc.response.status_code not in nothing_for_us:
                raise

            return False

        return True

    def is_draft(self, record_id: RecordIDLike) -> bool:
        """
        Is this record unpublished?

        Parameters
        ----------
        record_id
            ID of the record to ask about, or the record itself

        Returns
        -------
        :
            `True` if the record has never been published

        Raises
        ------
        RecordNotFoundError
            We could not find the record at all

        Notes
        -----
        On Zenodo, a published record can also have a draft of its own.
        That does not make it unpublished, and this still answers `False`:
        a published record's *metadata* can be corrected in place,
        by taking a draft of it, changing the metadata and publishing again
        (keeping the same ID and DOI),
        but the record itself is out there either way.
        Its *files* cannot be changed that way at all —
        Zenodo locks them when the record is published — which is why
        the file-writing methods can act on this answer alone.

        "Does it have unpublished metadata edits?" is a different question, and
        [`has_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.has_edited_metadata_draft]
        is the one which answers it.
        """
        record_id = get_record_id(record_id)
        description = f"work out whether record {record_id!r} is a draft"

        if self._can_see(
            f"/api/records/{record_id}",
            requires_auth=False,
            description=description,
        ):
            return False

        # It only makes sense to look for draft information
        # if we have a token (draft information can't be accessed otherwise).
        if self.token and self._can_see(
            f"/api/records/{record_id}/draft",
            requires_auth=True,
            description=description,
        ):
            return True

        raise RecordNotFoundError(
            str(record_id),
            zenodo_domain=self.zenodo_domain_url,
            token_source=self.token_source,
        )

    def list_files(self, record_id: RecordIDLike) -> dict[str, FileEntry]:
        """
        List the files on a record

        Parameters
        ----------
        record_id
            ID of the record whose files to list

        Returns
        -------
        :
            The record's files, keyed by their name on Zenodo
        """
        record_id = get_record_id(record_id)
        return self._list_files_at(record_id, draft=self.is_draft(record_id))

    def _diff_files(self, record_id: RecordIDLike, paths: Collection[Path]) -> FileDiff:
        """
        Work out what has to change for a record's files to match `paths`

        Zenodo reports each file's checksum in the listing,
        so we can do this without downloading anything.

        Parameters
        ----------
        record_id
            ID of the record whose files to compare against

        paths
            Local files to compare

        Returns
        -------
        :
            The difference between `paths` and the record's files
        """
        record_id = get_record_id(record_id)
        remote = self._list_files_at(record_id, draft=True)
        # Zenodo has no directories, so a local file's name is its name on Zenodo
        want = {path.name: path for path in paths}

        to_upload = {}
        unchanged = []
        for name, path in want.items():
            entry = remote.get(name)
            local_md5 = get_file_md5(path)

            if entry is not None and entry.md5 == local_md5:
                unchanged.append(path)

            else:
                to_upload[path] = local_md5

        return FileDiff(
            to_upload=to_upload,
            unchanged=tuple(unchanged),
            to_delete=tuple(name for name in remote if name not in want),
            remote=remote,
        )

    def _upload_diff(
        self,
        record_id: RecordIDLike,
        diff: FileDiff,
        *,
        n_threads: int,
        progress: bool,
        max_attempts: int,
    ) -> dict[str, FileEntry]:
        """
        Upload the files a diff says need uploading

        Parameters
        ----------
        record_id
            ID of the record whose draft to upload to

        diff
            Difference to act on

        n_threads
            Number of files to upload at once

        progress
            Should progress bars be shown?

        max_attempts
            Maximum number of times to try each upload before giving up

        Returns
        -------
        :
            The uploaded files' entries, keyed by their name on Zenodo
        """
        record_id = get_record_id(record_id)
        if not diff.to_upload:
            return {}

        positions = PositionAllocator(n_slots=n_threads)

        def upload_one(path: Path) -> FileEntry:
            with positions.slot() as position:
                return self.upload_file(
                    record_id,
                    path,
                    local_md5=diff.to_upload[path],
                    progress=progress,
                    position=position,
                    max_attempts=max_attempts,
                )

        entries = _run_in_parallel(
            upload_one,
            list(diff.to_upload),
            n_threads=n_threads,
            desc="Uploading",
            progress=progress,
        )

        return {entry.filename: entry for entry in entries}

    def upload_files(
        self,
        record_id: RecordIDLike,
        paths: Collection[Path],
        *,
        n_threads: int = 4,
        progress: bool = True,
        max_attempts: int = 5,
    ) -> dict[str, FileEntry]:
        """
        Add files to a record

        This never deletes anything.
        Files which are already on the draft with the same contents
        are left alone rather than being uploaded again,
        so re-running after a failure part way through is cheap.
        If you want the draft to end up containing exactly `paths`,
        use [`mirror_files`][openscm_zenodo.zenodo.ZenodoClient.mirror_files].

        Note that Zenodo has no directories:
        each file lands under its own name,
        whatever local directories it sits in.

        Parameters
        ----------
        record_id
            ID of the record whose draft to upload to

        paths
            Files to upload

        n_threads
            Number of files to upload at once

        progress
            Should progress bars be shown?

        max_attempts
            Maximum number of times to try each upload before giving up

        Returns
        -------
        :
            The draft's files once we are done, keyed by their name on Zenodo

        Notes
        -----
        There is no `verify_checksum` argument here, unlike
        [`upload_file`][openscm_zenodo.zenodo.ZenodoClient.upload_file].
        Working out what to upload requires each file's local checksum anyway,
        so checking it against Zenodo's reported checksum afterwards is free.
        Turning that off would remove a safety net and save nothing.
        """
        record_id = get_record_id(record_id)
        diff = self._diff_files(record_id, paths)

        logger.info(
            f"Uploading {len(diff.to_upload)} file(s) to record {record_id!r}, "
            f"leaving {len(diff.unchanged)} unchanged file(s) alone"
        )

        uploaded = self._upload_diff(
            record_id,
            diff,
            n_threads=n_threads,
            progress=progress,
            max_attempts=max_attempts,
        )

        return {**diff.remote, **uploaded}

    def mirror_files(
        self,
        record_id: RecordIDLike,
        paths: Collection[Path],
        *,
        n_threads: int = 4,
        progress: bool = True,
        max_attempts: int = 5,
    ) -> dict[str, FileEntry]:
        """
        Make a record contain exactly `paths`

        **This deletes files.**
        Anything on the draft which is not in `paths` is removed.
        Files which are already there with the same contents are left alone.
        If you only want to add files, use
        [`upload_files`][openscm_zenodo.zenodo.ZenodoClient.upload_files].

        Parameters
        ----------
        record_id
            ID of the record whose draft to mirror `paths` onto

        paths
            Files the draft should end up containing

        n_threads
            Number of files to upload at once.

            This does not apply to the deletes, which Zenodo can only do
            one at a time, see
            [`delete_files`][openscm_zenodo.zenodo.ZenodoClient.delete_files].

        progress
            Should progress bars be shown?

        max_attempts
            Maximum number of times to try each upload before giving up

        Returns
        -------
        :
            The draft's files once we are done, keyed by their name on Zenodo
        """
        record_id = get_record_id(record_id)
        diff = self._diff_files(record_id, paths)

        logger.info(
            f"Mirroring {len(paths)} file(s) onto record {record_id!r}: "
            f"uploading {len(diff.to_upload)}, "
            f"deleting {len(diff.to_delete)}, "
            f"leaving {len(diff.unchanged)} unchanged"
        )

        # Delete before uploading, so that renaming a large file
        # does not need room for both copies at once
        if diff.to_delete:
            self.delete_files(record_id, diff.to_delete, progress=progress)

        uploaded = self._upload_diff(
            record_id,
            diff,
            n_threads=n_threads,
            progress=progress,
            max_attempts=max_attempts,
        )

        kept = {
            name: entry
            for name, entry in diff.remote.items()
            if name not in diff.to_delete
        }

        return {**kept, **uploaded}

    def delete_files(
        self,
        record_id: RecordIDLike,
        filenames: Collection[str],
        *,
        progress: bool = True,
    ) -> None:
        """
        Delete files from a record, by name

        Files are deleted one at a time, deliberately.
        Deleting in parallel does not work:
        each delete updates the draft's list of files,
        concurrent deletes race with each other,
        and Zenodo rejects the ones which lose
        with `400 Not a valid value`, leaving those files in place.
        Uploading in parallel is fine, see
        [`upload_files`][openscm_zenodo.zenodo.ZenodoClient.upload_files].

        Parameters
        ----------
        record_id
            ID of the record whose draft to delete from

        filenames
            Names of the files to delete, as they appear on Zenodo

        progress
            Should a progress bar be shown?
        """
        record_id = get_record_id(record_id)
        if not filenames:
            return

        logger.info(f"Deleting {len(filenames)} file(s) from record {record_id!r}")

        with get_files_progress_bar(
            desc="Deleting", total=len(filenames), progress=progress
        ) as progress_bar:
            for filename in filenames:
                self.delete_file(record_id, filename)
                progress_bar.update(1)

    def delete_all_files(
        self, record_id: RecordIDLike, *, progress: bool = True
    ) -> None:
        """
        Delete every file from a record's draft

        Parameters
        ----------
        record_id
            ID of the record whose draft to empty

        progress
            Should a progress bar be shown?
        """
        record_id = get_record_id(record_id)
        self.delete_files(
            record_id,
            tuple(self._list_files_at(record_id, draft=True)),
            progress=progress,
        )

    def _stream_to_disk(
        self,
        entry: FileEntry,
        target: Path,
        *,
        verify_checksum: bool,
        progress: bool,
        position: int | None,
    ) -> None:
        """
        Stream a file's content to disk, one attempt

        The content is written to a temporary file next to `target`
        and only moved into place once it has arrived in full
        and been checked, so a download which is interrupted or corrupted
        never leaves anything behind under the name callers will look for.

        Parameters
        ----------
        entry
            File to download

        target
            Where to write the file

        verify_checksum
            Should we check that we received what Zenodo says it sent?

        progress
            Should a progress bar be shown?

        position
            Line to display the progress bar on

        Raises
        ------
        ChecksumMismatchError
            `verify_checksum` is `True` and what arrived is not what was sent
        """
        partial = target.with_name(f"{target.name}.part")
        # MD5 because that is what Zenodo reports, not because we chose it
        hasher = hashlib.md5()  # noqa: S324

        try:
            response = self._request(
                entry.content_url,
                stream=True,
                timeout=self.timeout_upload,
                description=f"download {entry.filename!r}",
            )

            with (
                response,
                get_file_progress_bar(
                    desc=entry.filename,
                    total=entry.size,
                    progress=progress,
                    position=position,
                ) as progress_bar,
                open(partial, "wb") as fh,
            ):
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    fh.write(chunk)
                    hasher.update(chunk)
                    progress_bar.update(len(chunk))

            if verify_checksum:
                assert_md5_matches(
                    entry.filename,
                    local_md5=hasher.hexdigest(),
                    remote_checksum=entry.checksum,
                )

        except BaseException:
            # Whatever went wrong, do not leave half a file lying around
            partial.unlink(missing_ok=True)

            raise

        # Only now do we know the file is complete and correct,
        # so only now does it get the name callers will look for
        partial.replace(target)

    def _download_entry(  # noqa: PLR0913
        self,
        entry: FileEntry,
        dest: Path,
        *,
        verify_checksum: bool = True,
        overwrite: bool = False,
        progress: bool = True,
        position: int | None = None,
        max_attempts: int = 5,
    ) -> Path:
        """
        Download one file which we already have Zenodo's description of

        Parameters
        ----------
        entry
            File to download

        dest
            Where to write the file.

            A directory means "write it in here, under its own name".

        verify_checksum
            Should we check that we received what Zenodo says it sent?

        overwrite
            Should an existing file with different contents be replaced?

        progress
            Should a progress bar be shown?

        position
            Line to display the progress bar on

        max_attempts
            Maximum number of times to try before giving up

        Returns
        -------
        :
            Path the file was written to

        Raises
        ------
        FileExistsError
            `dest` already holds a different file and `overwrite` is `False`

        ChecksumMismatchError
            `verify_checksum` is `True` and the download was corrupted
            on every attempt
        """
        target = dest / entry.filename if dest.is_dir() else dest

        if target.exists():
            if get_file_md5(target) == entry.md5:
                logger.info(f"{target} is already up to date, not downloading it again")

                return target

            if not overwrite:
                msg = (
                    f"{target} already exists and its contents are different "
                    f"from {entry.filename!r} on Zenodo. "
                    "Pass `overwrite=True` to replace it."
                )

                raise FileExistsError(msg)

        logger.info(f"Downloading {entry.filename!r} to {target}")

        retrying = _build_transfer_retrying(max_attempts)
        retrying(
            self._stream_to_disk,
            entry,
            target,
            verify_checksum=verify_checksum,
            progress=progress,
            position=position,
        )

        return target

    def download_file(  # noqa: PLR0913
        self,
        record_id: RecordIDLike,
        filename: str,
        dest: Path,
        *,
        verify_checksum: bool = True,
        overwrite: bool = False,
        progress: bool = True,
        max_attempts: int = 5,
    ) -> Path:
        """
        Download one of a record's files

        A record you have access to downloads.
        A record you do not have access to returns a `403`.

        Parameters
        ----------
        record_id
            ID of the record to download from

        filename
            Name of the file to download, as it appears on Zenodo

            Zenodo calls this the file's `key`,
            but we use `filename` throughout
            (see [`FileEntry`][openscm_zenodo.zenodo.FileEntry]).

        dest
            Where to write the file.

            A directory means "write it in here, under its own name".

        verify_checksum
            Should we check that we received what Zenodo says it sent?

            The checksum is calculated as the bytes arrive,
            so this costs almost nothing.

        overwrite
            Should an existing file with different contents be replaced?

            A file which is already there with the same contents
            is left alone either way, and not downloaded again.

        progress
            Should a progress bar be shown?

        max_attempts
            Maximum number of times to try before giving up

        Returns
        -------
        :
            Path the file was written to

        Raises
        ------
        FileNotOnRecordError
            The record has no file called `filename`
        """
        record_id = get_record_id(record_id)
        files = self.list_files(record_id)
        if filename not in files:
            raise FileNotOnRecordError(
                filename, record_id=str(record_id), available=files
            )

        return self._download_entry(
            files[filename],
            dest,
            verify_checksum=verify_checksum,
            overwrite=overwrite,
            progress=progress,
            max_attempts=max_attempts,
        )

    def download_files(  # noqa: PLR0913
        self,
        record_id: RecordIDLike,
        dest: Path | Mapping[str, Path],
        *,
        filenames: Collection[str] | None = None,
        n_threads: int = 4,
        verify_checksum: bool = True,
        overwrite: bool = False,
        progress: bool = True,
        max_attempts: int = 5,
    ) -> list[Path]:
        """
        Download a record's files

        Files which are already where they are going, with the same contents,
        are not downloaded again, so re-running after a failure part way through
        only fetches what is still missing.

        Parameters
        ----------
        record_id
            ID of the record to download from

        dest
            Where to write the files.

            A directory means "write them all in here, under their own names",
            and it is created if it is not there already.
            A mapping of filename to path says exactly where each file goes,
            for callers who want that control;
            it also says which files to download,
            so `filenames` may not be given as well.

        filenames
            Names of the files to download, as they appear on Zenodo.

            If not supplied, every file on the record is downloaded.
            May not be given when `dest` is a mapping,
            which already says which files are wanted.

        n_threads
            Number of files to download at once

        verify_checksum
            Should we check that we received what Zenodo says it sent?

        overwrite
            Should existing files with different contents be replaced?

        progress
            Should progress bars be shown?

        max_attempts
            Maximum number of times to try each download before giving up

        Returns
        -------
        :
            Paths the files were written to, in the order they were requested

        Raises
        ------
        FileNotOnRecordError
            The record has no file with one of the names asked for

        ValueError
            Both `dest` and `filenames` say which files are wanted,
            and they cannot both decide
        """
        record_id = get_record_id(record_id)
        dest_per_file = None if isinstance(dest, Path) else dict(dest)

        if dest_per_file is not None and filenames is not None:
            msg = (
                "`dest` is a mapping, which already says which files to download, "
                "so `filenames` may not be given as well. "
                f"Received {filenames=}."
            )

            raise ValueError(msg)

        files = self.list_files(record_id)

        wanted = tuple(dest_per_file) if dest_per_file is not None else filenames
        if wanted is None:
            to_download = list(files.values())

        else:
            missing = [name for name in wanted if name not in files]
            if missing:
                raise FileNotOnRecordError(
                    missing, record_id=str(record_id), available=files
                )

            to_download = [files[name] for name in wanted]

        if not to_download:
            return []

        logger.info(f"Downloading {len(to_download)} file(s) from {record_id!r}")

        if dest_per_file is None:
            cast(Path, dest).mkdir(parents=True, exist_ok=True)

        positions = PositionAllocator(n_slots=n_threads)

        def download_one(entry: FileEntry) -> Path:
            if dest_per_file is not None:
                entry_dest = dest_per_file[entry.filename]
                entry_dest.parent.mkdir(parents=True, exist_ok=True)

            else:
                entry_dest = cast(Path, dest)

            with positions.slot() as position:
                return self._download_entry(
                    entry,
                    entry_dest,
                    verify_checksum=verify_checksum,
                    overwrite=overwrite,
                    progress=progress,
                    position=position,
                    max_attempts=max_attempts,
                )

        return _run_in_parallel(
            download_one,
            to_download,
            n_threads=n_threads,
            desc="Downloading",
            progress=progress,
        )

    def get_published(self, record_id: RecordIDLike) -> Record:
        """
        Get a published record

        Parameters
        ----------
        record_id
            ID of the record to get

        Returns
        -------
        :
            The record

        Raises
        ------
        ZenodoHTTPError
            There is no published record with this ID,
            or it is restricted and our token does not have access to it.

            Use [`get_draft`][openscm_zenodo.zenodo.ZenodoClient.get_draft]
            for a record which has not been published yet
            and use a token if the record is private.

        Examples
        --------
        >>> record = ZenodoClient().get_published("4589756")
        >>> record.metadata.title
        'Reduced Complexity Model Intercomparison Project (RCMIP) protocol'
        >>> record.doi
        '10.5281/zenodo.4589756'
        """
        record_id = get_record_id(record_id)
        logger.info(f"Retrieving published record {record_id!r}")

        response = self._request(
            f"/api/records/{record_id}",
            headers={"Accept": INVENIORDM_JSON_ACCEPT},
            description=f"get published record {record_id!r}",
        )

        return Record.from_json(response.json())

    def _get_draft_document(self, record_id: str | RecordID) -> Record:
        """
        Get whatever Zenodo serves from a record's draft endpoint

        The endpoint serves two different things — an unpublished record, and a
        published record's draft metadata edits — and callers care which. This
        is the shared request; processing the returned record
        is the caller's job.

        Parameters
        ----------
        record_id
            ID of the record whose draft endpoint to read

        Returns
        -------
        :
            Whatever was there

        Raises
        ------
        ZenodoHTTPError
            There was nothing there (a `404`), or we may not see it
        """
        response = self._request(
            f"/api/records/{record_id}/draft",
            requires_auth=True,
            headers={"Accept": INVENIORDM_JSON_ACCEPT},
            description=f"hit the draft endpoint of record {record_id!r}",
        )

        return Record.from_json(response.json())

    def get_draft(self, record_id: RecordIDLike) -> Record:
        """
        Get an unpublished record

        A record which has never been published *is* a draft, and this is how to
        read it. **A published record never comes back from here**, whatever
        state it is in: the draft metadata edits a published record can have are
        a different thing, and
        [`get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.get_edited_metadata_draft]
        is what reads those. Zenodo serves both from one endpoint, which is
        exactly why this method does not.

        Parameters
        ----------
        record_id
            ID of the record to get, or the record itself

        Returns
        -------
        :
            The draft

        Raises
        ------
        PublishedRecordDraftError
            The record is published, so it is not a draft

        RecordNotFoundError
            There is no record with this ID at all

        ZenodoHTTPError
            Anything else, e.g. we may not see this record
        """
        record_id = get_record_id(record_id)
        logger.info(f"Retrieving draft record {record_id!r}")

        try:
            draft = self._get_draft_document(record_id)

        except ZenodoHTTPError as exc:
            if exc.response.status_code != HTTP_NOT_FOUND:
                raise

            if self._published_record_exists(record_id):
                raise PublishedRecordDraftError(
                    str(record_id), zenodo_domain=self.zenodo_domain_url
                ) from exc

            raise RecordNotFoundError(
                str(record_id),
                zenodo_domain=self.zenodo_domain_url,
                token_source=self.token_source,
            ) from exc

        if draft.is_edited_metadata_draft:
            # The endpoint answered, but with the other thing it serves.
            raise PublishedRecordDraftError(
                str(record_id), zenodo_domain=self.zenodo_domain_url
            )

        return draft

    def get_edited_metadata_draft(self, record_id: RecordIDLike) -> Record:
        """
        Read a published record's unpublished metadata edits

        This only reads. Use
        [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft]
        to start editing, which is a deliberate act and so is not something this
        does as a side effect of being asked to look.

        Parameters
        ----------
        record_id
            ID of the published record to read, or the record itself

        Returns
        -------
        :
            The draft metadata edits

        Raises
        ------
        DraftRecordDraftMetadataEditsError
            The record has never been published, so it *is* a draft and does not
            have a separate draft of its metadata

        DraftMetadataEditsNotFoundError
            The record is published but nobody has started editing its metadata

        RecordNotFoundError
            There is no record with this ID at all
        """
        record_id = get_record_id(record_id)
        if self.is_draft(record_id):
            raise DraftRecordDraftMetadataEditsError(
                str(record_id), zenodo_domain=self.zenodo_domain_url
            )

        logger.info(f"Retrieving the metadata edits on record {record_id!r}")

        try:
            return self._get_draft_document(record_id)

        except ZenodoHTTPError as exc:
            if exc.response.status_code != HTTP_NOT_FOUND:
                raise

            raise self._explain_missing_draft(record_id) from exc

    def create_or_get_edited_metadata_draft(self, record_id: RecordIDLike) -> Record:
        """
        Start editing a published record's metadata, or get the edits already going

        This is how a published record's metadata is corrected in place: the
        draft keeps the record's ID and DOI, so editing it and publishing it
        again changes what the public record says without releasing a new
        version. **Starting the edits is the opt-in to that**, which is why
        [`update_metadata`][openscm_zenodo.zenodo.ZenodoClient.update_metadata]
        refuses a published record which does not already have them under way.

        A published record's *files* cannot be changed this way, whatever the
        draft says: Zenodo locks them at publication, and
        [`create_or_get_new_version`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_new_version]
        is the way to change those.

        This is create-or-get, as the name says, so it is safe to call
        repeatedly: Zenodo returns the draft which already exists rather than
        making a second one.

        Parameters
        ----------
        record_id
            ID of the published record to edit, or the record itself

        Returns
        -------
        :
            The draft.

            Its `record_id` is the one which was asked for — an edited metadata
            draft keeps the record's ID — and its `is_edited_metadata_draft` is
            `True`.

        Raises
        ------
        RecordNotPublishedError
            The record has never been published, so it *is* a draft
            and there is nothing separate to edit

        RecordNotFoundError
            There is no record with this ID at all

        ZenodoHTTPError
            Anything else, e.g. we may not edit this record
        """
        record_id = get_record_id(record_id)
        if self.is_draft(record_id):
            raise DraftRecordDraftMetadataEditsError(
                str(record_id), zenodo_domain=self.zenodo_domain_url
            )

        logger.info(f"Getting (or starting) metadata edits on record {record_id!r}")

        response = self._request(
            f"/api/records/{record_id}/draft",
            method="POST",
            requires_auth=True,
            headers={"Accept": INVENIORDM_JSON_ACCEPT},
            description=f"start metadata edits on record {record_id!r}",
        )

        return Record.from_json(response.json())

    def has_edited_metadata_draft(self, record_id: RecordIDLike) -> bool:
        """
        Find out whether this published record has unpublished metadata edits

        This is the second of two questions.
        [`is_draft`][openscm_zenodo.zenodo.ZenodoClient.is_draft] answers
        "has this record never been published?"; this one answers "are there
        edits waiting to go out?", and only a published record can have those.

        Parameters
        ----------
        record_id
            ID of the published record to ask about, or the record itself

        Returns
        -------
        :
            `True` if the record has an edited metadata draft

        Raises
        ------
        RecordNotPublishedError
            The record has never been published, so it *is* a draft
            and the question does not apply to it

        MissingTokenError
            We have no token.

            Drafts are not visible without one,
            so without a token there is no answer to give.

        Notes
        -----
        This costs a request of its own, and it has to.
        `GET /api/records/{id}` reports `is_draft` as `False` for a published
        record whether or not one of these drafts exists, so the answer cannot
        be read off a record we already have.
        """
        record_id = get_record_id(record_id)
        if self.is_draft(record_id):
            raise DraftRecordDraftMetadataEditsError(
                str(record_id), zenodo_domain=self.zenodo_domain_url
            )

        return self._can_see(
            f"/api/records/{record_id}/draft",
            requires_auth=True,
            description=(
                f"work out whether record {record_id!r} has unpublished metadata edits"
            ),
        )

    def get_record(self, record_id: RecordIDLike) -> Record:
        """
        Get a record, whether it is published or still a draft

        This is the one to reach for if you do not already know which you have.
        If you do, [`get_published`][openscm_zenodo.zenodo.ZenodoClient.get_published]
        and [`get_draft`][openscm_zenodo.zenodo.ZenodoClient.get_draft]
        each hit one endpoint and say so at the call site.
        The record we return tells you which you got, through its `is_draft`.

        The published record wins if there is one.
        A published record can also have unpublished metadata edits;
        to read those, use
        [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft].

        Parameters
        ----------
        record_id
            ID of the record to get, or the record itself

        Returns
        -------
        :
            The record

        Raises
        ------
        RecordNotFoundError
            We could not find the record at all

        Notes
        -----
        We work out which of the two we are looking at by asking for the
        published record and falling back to the draft, rather than by calling
        [`is_draft`][openscm_zenodo.zenodo.ZenodoClient.is_draft] first.
        The answer is the same — `is_draft` decides the same way — but asking it
        first would mean fetching `/api/records/{id}` to find out, then fetching
        it again to get the record. This way a published record costs one
        request and a draft costs two.

        Examples
        --------
        >>> record = ZenodoClient().get_record("4589756")
        >>> record.is_draft
        False
        """
        record_id = get_record_id(record_id)
        for getter, needs_token in (
            (self.get_published, False),
            (self.get_draft, True),
        ):
            if needs_token and not self.token:
                break

            try:
                return getter(record_id)

            except ZenodoHTTPError as exc:
                nothing_for_us = (HTTP_FORBIDDEN, HTTP_NOT_FOUND)
                if exc.response.status_code not in nothing_for_us:
                    raise

        raise RecordNotFoundError(
            str(record_id),
            zenodo_domain=self.zenodo_domain_url,
            token_source=self.token_source,
        )

    def get_metadata(self, record_id: RecordIDLike) -> Metadata:
        """
        Get a record's metadata

        This works with both published records and drafts,
        because it goes through
        [`get_record`][openscm_zenodo.zenodo.ZenodoClient.get_record].

        A published record can also have unpublished metadata edits.
        **We return the published metadata in that case**, not the pending
        changes, because the published record is what `get_record` returns.
        [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft]
        is how to see the pending changes.

        Parameters
        ----------
        record_id
            ID of the record whose metadata to get, or the record itself

        Returns
        -------
        :
            The record's metadata.

        Raises
        ------
        RecordNotFoundError
            We could not find the record at all

        Examples
        --------
        >>> metadata = ZenodoClient().get_metadata("4589756")
        >>> metadata.title
        'Reduced Complexity Model Intercomparison Project (RCMIP) protocol'
        >>> metadata.rights[0].id
        'cc-by-sa-4.0'
        >>> metadata.creators[0].name
        'Zebedee Nicholls'
        """
        record_id = get_record_id(record_id)
        logger.info(f"Retrieving the metadata of record {record_id!r}")

        return self.get_record(record_id).metadata

    def get_parent_id(self, record_id: RecordIDLike) -> ParentID:
        """
        Get the ID which refers to all versions of a record

        Zenodo calls this the record's parent.
        Resolving it gives whichever version is the latest at the time.

        Parameters
        ----------
        record_id
            ID of any version of the record

        Returns
        -------
        :
            ID of the record's parent

        Raises
        ------
        RecordNotFoundError
            We could not find the record at all

        Examples
        --------
        >>> ZenodoClient().get_parent_id("4589756")
        '4589726'
        """
        record_id = get_record_id(record_id)
        return self.get_record(record_id).parent_id

    def _get_citation_params(
        self,
        fmt: CitationFormat,
        *,
        style: str,
        locale: str,
        warn_unknown_style: bool,
    ) -> dict[str, str] | None:
        """
        Work out the query parameters to send with a citation request

        Parameters
        ----------
        fmt
            Format the citation was asked for in

        style
            Citation style that was asked for

        locale
            Locale that was asked for

        warn_unknown_style
            Should we warn about a style we have not checked?

        Returns
        -------
        :
            Parameters to send, or `None` if there are none to send

        Raises
        ------
        UnknownCitationStyleError
            `style` is one we have verified Zenodo rejects
        """
        if fmt is not CitationFormat.citation:
            asked_for = {
                "style": (style, CITATION_STYLE_DEFAULT),
                "locale": (locale, CITATION_LOCALE_DEFAULT),
            }
            supplied = [
                name for name, (given, default) in asked_for.items() if given != default
            ]
            if supplied:
                warn_zenodo(
                    f"Ignoring {' and '.join(supplied)}: "
                    f"they only apply to {CitationFormat.citation.value!r}, "
                    f"not {fmt.value!r}",
                    # `get_citation` -> `_get_citation_params` -> here
                    stacklevel=4,
                )

            return None

        if style in CITATION_STYLE_ALIASES:
            raise UnknownCitationStyleError(
                style,
                suggestion=CITATION_STYLE_ALIASES[style],
                known_styles=KNOWN_CITATION_STYLES,
            )

        if warn_unknown_style and style not in KNOWN_CITATION_STYLES:
            warn_zenodo(
                f"We have not checked the citation style {style!r}. "
                "Zenodo accepts more CSL styles than we know about, "
                "so we are sending it anyway. "
                "If Zenodo does not know it either, "
                "the request comes back as a 400.",
                # `get_citation` -> `_get_citation_params` -> here
                stacklevel=4,
            )

        return {"style": style, "locale": locale}

    def get_citation(
        self,
        record_id: RecordIDLike,
        *,
        fmt: CitationFormat = CitationFormat.bibtex,
        style: str = CITATION_STYLE_DEFAULT,
        locale: str = CITATION_LOCALE_DEFAULT,
        warn_unknown_style: bool = True,
    ) -> str:
        """
        Get a record's citation

        Parameters
        ----------
        record_id
            ID of the record to cite

        fmt
            Format to get the citation in.

        style
            Citation style to render in.

            This only applies to
            [`CitationFormat.citation`][openscm_zenodo.zenodo.CitationFormat],
            and is ignored, with a warning, for any other format.
            Zenodo wants full CSL style IDs,
            see [`KNOWN_CITATION_STYLES`][openscm_zenodo.zenodo.KNOWN_CITATION_STYLES].

        locale
            Locale to render in.

            As with `style`, this only applies to
            [`CitationFormat.citation`][openscm_zenodo.zenodo.CitationFormat].

        warn_unknown_style
            Should we warn about a style we have not checked?

            We only know a subset of the CSL styles Zenodo accepts, see
            [`KNOWN_CITATION_STYLES`][openscm_zenodo.zenodo.KNOWN_CITATION_STYLES],
            so a style we do not know is sent with a warning rather than
            refused. Set this to `False` if you are using a style
            you know works and do not want to hear about it every time.

        Returns
        -------
        :
            The record's citation, as Zenodo renders it

        Raises
        ------
        UnknownCitationStyleError
            `style` is one we have verified Zenodo rejects

        Examples
        --------
        >>> client = ZenodoClient()
        >>> bibtex = client.get_citation("4589756")
        >>> print(bibtex.splitlines()[0])
        @dataset{zebedee_nicholls_2021_4589756,

        >>> citation = client.get_citation("4589756", fmt=CitationFormat.citation)
        >>> # Zenodo sends this as one long line, we wrap it here
        >>> import textwrap
        >>> print(textwrap.fill(citation, width=70))
        Zebedee Nicholls& Jared Lewis. (2021). Reduced Complexity Model
        Intercomparison Project (RCMIP) protocol (Version v5.1.0) [Dataset].
        Zenodo. https://doi.org/10.5281/zenodo.4589756
        """
        record_id = get_record_id(record_id)
        logger.info(f"Retrieving the {fmt.value} citation of record {record_id!r}")

        response = self._request(
            f"/api/records/{record_id}",
            headers={"Accept": CITATION_FORMAT_ACCEPT[fmt]},
            params=self._get_citation_params(
                fmt,
                style=style,
                locale=locale,
                warn_unknown_style=warn_unknown_style,
            ),
            description=f"get the {fmt.value} citation of record {record_id!r}",
        )

        return response.text

    def get_latest_version_id(self, record_id: RecordIDLike) -> RecordID:
        """
        Get the ID of the latest version of a record

        Parameters
        ----------
        record_id
            ID of any published version of the record

        Returns
        -------
        :
            ID of the latest version
        """
        record_id = get_record_id(record_id)
        record = self._request(
            f"/api/records/{record_id}",
            description=f"get record {record_id!r}",
        ).json()

        latest = self._request(
            record["links"]["latest"],
            description=f"get the latest version of record {record_id!r}",
        ).json()

        return RecordID(str(latest["id"]))

    def create_or_get_new_version(
        self, record_id: RecordIDLike, *, import_files: bool = False
    ) -> RecordID:
        """
        Create a new version of a published record, or get the one already going

        The new version is a draft with **no files**.
        Pass `import_files=True`, or call
        [`import_files`][openscm_zenodo.zenodo.ZenodoClient.import_files],
        to carry the previous version's files over.

        This is create-or-get, which the name is meant to make plain.
        A record can have at most one unpublished next version,
        so calling this again returns the draft that already exists
        rather than creating a second one.
        That is what makes a release script safe to re-run
        after it has failed part way through.

        Any published version of the record can be used,
        it does not have to be the latest.

        Parameters
        ----------
        record_id
            ID of any published version of the record, or the record itself

        import_files
            Should the previous version's files be carried over?

        Returns
        -------
        :
            ID of the new version
        """
        record_id = get_record_id(record_id)
        logger.info(f"Creating a new version of record {record_id!r}")

        response = self._request(
            f"/api/records/{record_id}/versions",
            method="POST",
            requires_auth=True,
            description=f"create a new version of record {record_id!r}",
        )
        new_version_id = RecordID(str(response.json()["id"]))

        logger.info(f"The new version of record {record_id!r} is {new_version_id!r}")

        if import_files:
            self.import_files(new_version_id)

        return new_version_id

    def import_files(self, record_id: RecordIDLike) -> bool:
        """
        Carry the previous version's files over to a record's draft

        Zenodo copies the files across itself,
        so nothing is uploaded and no storage is duplicated.

        Parameters
        ----------
        record_id
            ID of the record whose draft to import into

        Returns
        -------
        :
            Whether the files were imported.

            This is `False` if the draft already has files on it,
            in which case there is nothing to do:
            Zenodo only allows importing into an empty draft
            (`400 Please remove all files first.`).
            Skipping rather than failing is what makes a release script
            safe to re-run after it has failed part way through.
        """
        record_id = get_record_id(record_id)
        already_there = self._list_files_at(record_id, draft=True)
        if already_there:
            logger.info(
                f"Not importing files into record {record_id!r}, "
                f"it already has {len(already_there)} file(s)"
            )

            return False

        logger.info(f"Importing the previous version's files into {record_id!r}")
        self._request(
            f"/api/records/{record_id}/draft/actions/files-import",
            method="POST",
            requires_auth=True,
            description=f"import files into record {record_id!r}",
        )

        return True

    def update_metadata(
        self,
        record_id: RecordIDLike,
        metadata: Metadata,
        *,
        warn_unknown_vocabulary: bool = True,
        warn_discarded: bool = True,
        validate: bool = False,
    ) -> Record:
        """
        Update the metadata of a record

        This is either editing a draft's metadata, or editing the pending
        changes to a published record's metadata. For a published record, the
        edits have to be started first, with
        [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft];
        we do not do that on your behalf, because publishing those edits changes
        what a public record says under the same ID and DOI.
        [`create_or_get_new_version`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_new_version]
        is how to release the change as a new version instead.

        Parameters
        ----------
        record_id
            ID of the record whose draft to update, or the record itself

        metadata
            Metadata to apply.

            Build it with [`Metadata`][openscm_zenodo.metadata.Metadata], or
            load it from a file with
            [`Metadata.from_file`][openscm_zenodo.metadata.Metadata.from_file].

            This is the draft's metadata, not the whole draft, and it replaces
            what is there rather than being merged into it.
            Settings which live outside the metadata, such as `access`,
            are left alone. Writing *those* is not supported yet; it lands with
            `create_record`, which has to set them at creation anyway.

        warn_unknown_vocabulary
            Should we warn about vocabulary values we do not recognise?

            Zenodo's vocabularies are longer than ours and they change,
            so this is a warning rather than a refusal —
            and this flag is here for when you know better than we do.
            See
            [`find_unknown_vocabulary_values`][openscm_zenodo.metadata.Metadata.find_unknown_vocabulary_values].

        warn_discarded
            Should we warn about fields Zenodo silently discarded?

            Zenodo does not refuse a value it cannot parse, it drops it: a
            malformed `publication_date` or an over-long `version` comes back
            as `null` with a `200`. We compare what came back against what we
            sent and say so, because otherwise the field simply goes missing.

        validate
            Should we check the metadata is complete before sending it?

            Off by default: Zenodo accepts an incomplete draft, and filling one
            in over several calls is a normal thing to do.
            [`publish`][openscm_zenodo.zenodo.ZenodoClient.publish]
            is where completeness has to be right, and it checks by default.

        Returns
        -------
        :
            The updated draft

        Raises
        ------
        RecordNotWritableError
            The record is published and not metadata edits have been started

        RecordNotFoundError
            There is no record with this ID

        MetadataValidationError
            `validate` is on and the metadata is not complete
        """
        record_id = get_record_id(record_id)

        if validate:
            metadata.validate(description=f"update record {record_id!r}")

        if warn_unknown_vocabulary:
            unknown = metadata.find_unknown_vocabulary_values()
            if unknown:
                warn_zenodo(
                    # The prose goes first and the reports last: each one ends
                    # with a vocabulary in full, so anything after them is a
                    # long way down the message.
                    "Some of this metadata uses vocabulary values we do not "
                    "know of. Zenodo's vocabularies are longer than the lists "
                    "we keep and they change, so this may well be fine; pass "
                    "`warn_unknown_vocabulary=False` to silence this.\n"
                    + "\n".join(f"- {value}" for value in unknown)
                )

        logger.info(f"Updating the metadata of record {record_id!r}")
        metadata_json = metadata.to_json()
        logger.debug(f"New metadata: {metadata_json}")

        try:
            response = self._request(
                f"/api/records/{record_id}/draft",
                method="PUT",
                requires_auth=True,
                headers={"Accept": INVENIORDM_JSON_ACCEPT},
                json={"metadata": metadata_json},
                description=f"update the metadata of record {record_id!r}",
            )

        except ZenodoHTTPError as exc:
            if exc.response.status_code != HTTP_NOT_FOUND:
                raise

            if self._published_record_exists(record_id):
                raise RecordNotWritableError(
                    str(record_id),
                    zenodo_domain=self.zenodo_domain_url,
                    what="metadata",
                ) from exc

            raise RecordNotFoundError(
                str(record_id),
                zenodo_domain=self.zenodo_domain_url,
                token_source=self.token_source,
            ) from exc

        updated = Record.from_json(response.json())

        if warn_discarded:
            # Warned about here rather than in a helper, so that the warning
            # points at whoever called us rather than at a line of ours.
            discarded = find_discarded_metadata(
                sent=metadata_json, got=updated.metadata
            )
            if discarded:
                warn_zenodo(
                    f"Zenodo discarded "
                    f"{', '.join(repr(key) for key in discarded)} "
                    f"from the metadata of record {record_id!r}. "
                    "It accepts values it cannot parse and then stores nothing, "
                    "so the usual cause is a value it could not read "
                    "(a `publication_date` which is not a date, "
                    "a `version` longer than about 190 characters). "
                    "Pass `warn_discarded=False` to silence this."
                )

        return updated

    def _published_record_exists(self, record_id: str | RecordID) -> bool:
        """
        Is there a published record with this ID?

        Used on error paths, to tell "this record is published"
        apart from "there is no such record".

        Parameters
        ----------
        record_id
            ID of the record to look for

        Returns
        -------
        :
            `True` if there is one
        """
        return self._can_see(
            f"/api/records/{record_id}",
            requires_auth=False,
            description=f"work out if a published record {record_id!r} exists",
        )

    def _explain_missing_draft(self, record_id: str | RecordID) -> ZenodoError:
        """
        Work out why there was no draft to read, and build the error to raise

        A `404` from the draft endpoint has two causes which need different
        answers: the record is published and its metadata edits were never
        started, or there is no such record at all.

        Parameters
        ----------
        record_id
            ID of the record whose draft we wanted

        Returns
        -------
        :
            The error to raise
        """
        if self._published_record_exists(record_id):
            return DraftMetadataEditsNotFoundError(
                str(record_id), zenodo_domain=self.zenodo_domain_url
            )

        return RecordNotFoundError(
            str(record_id),
            zenodo_domain=self.zenodo_domain_url,
            token_source=self.token_source,
        )

    def publish(self, record_id: RecordIDLike, *, validate: bool = True) -> RecordID:
        """
        Publish a record's draft

        **This cannot be undone.**
        A published record cannot be deleted,
        and its files can no longer be changed;
        changing files after this means creating a new version, see
        [`create_or_get_new_version`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_new_version].
        Changing metadata after this is possible, see
        [`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft]
        and [`update_metadata`][openscm_zenodo.zenodo.ZenodoClient.update_metadata].

        Parameters
        ----------
        record_id
            ID of the record whose draft to publish, or the record itself

        validate
            Should we check the draft's metadata before publishing it?

            Zenodo only validates metadata at publish time, so this is the last
            point at which a missing field can be caught, and it is the reason
            this defaults to `True`. It costs one extra request, and it reports
            every problem at once rather than the first one Zenodo trips over.

            Turn it off if you would rather let Zenodo have the last word,
            for instance if it accepts something we think it should not.

        Returns
        -------
        :
            ID of the published record

        Raises
        ------
        MetadataValidationError
            `validate` is on and the draft's metadata is not complete enough
            for Zenodo to publish it
        """
        record_id = get_record_id(record_id)

        if validate:
            # Both an unpublished record and a published record's metadata edits
            # get published from here, so this reads the endpoint directly
            # rather than through either of the methods which insist on one.
            self._get_draft_document(record_id).metadata.validate(
                description=f"publish record {record_id!r}"
            )

        logger.info(f"Publishing record {record_id!r}")

        response = self._request(
            f"/api/records/{record_id}/draft/actions/publish",
            method="POST",
            requires_auth=True,
            description=f"publish record {record_id!r}",
        )
        published_id = RecordID(str(response.json()["id"]))

        logger.info(f"Successfully published record {published_id!r}")

        return published_id


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
        To create a new version from the all records ID that references all
        versions, use
        [`create_or_get_new_version`][openscm_zenodo.zenodo.create_or_get_new_version].
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
    record_id: RecordIDLike,
    client: ZenodoClient | None = None,
) -> Metadata:
    """
    Retrieve a record's metadata, in one call

    Parameters
    ----------
    record_id
        ID of the record whose metadata to retrieve

    client
        Client to interact with Zenodo with.

        If not supplied, we build a default
        [`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient],
        which picks up a token from the environment if there is one.

    Returns
    -------
    :
        The record's metadata, see
        [`get_metadata`][openscm_zenodo.zenodo.ZenodoClient.get_metadata]

    Examples
    --------
    >>> metadata = retrieve_metadata("4589756")
    >>> metadata.version
    'v5.1.0'
    """
    if client is None:
        client = ZenodoClient()

    return client.get_metadata(record_id)


def retrieve_citation(  # noqa: PLR0913
    record_id: RecordIDLike,
    client: ZenodoClient | None = None,
    *,
    fmt: CitationFormat = CitationFormat.bibtex,
    style: str = CITATION_STYLE_DEFAULT,
    locale: str = CITATION_LOCALE_DEFAULT,
    warn_unknown_style: bool = True,
) -> str:
    r"""
    Retrieve a record's citation, in one call

    Parameters
    ----------
    record_id
        ID of the record to cite

    client
        Client to interact with Zenodo with.

        If not supplied, we build a default
        [`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient],
        which picks up a token from the environment if there is one.

    fmt
        Format to get the citation in

    style
        Citation style to render in, for
        [`CitationFormat.citation`][openscm_zenodo.zenodo.CitationFormat] only

    locale
        Locale to render in, for
        [`CitationFormat.citation`][openscm_zenodo.zenodo.CitationFormat] only

    warn_unknown_style
        Should we warn about a style we have not checked?

    Returns
    -------
    :
        The record's citation, see
        [`get_citation`][openscm_zenodo.zenodo.ZenodoClient.get_citation]

    Examples
    --------
    >>> res = retrieve_citation("4589756")
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
    if client is None:
        client = ZenodoClient()

    return client.get_citation(
        record_id,
        fmt=fmt,
        style=style,
        locale=locale,
        warn_unknown_style=warn_unknown_style,
    )


def retrieve_metadata_legacy(
    deposition_id: str,
    zenodo_interactor: ZenodoInteractor | None = None,
) -> dict[str, dict[str, str]]:
    r"""
    Retrieve metadata associated with a given deposition ID, using the legacy API

    This is the pre-InvenioRDM implementation,
    so the metadata comes back in the legacy schema.
    It is kept only so that the command-line interface keeps working
    while the rewrite lands, and goes when the CLI is trimmed (Part 8).
    Use [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata] instead.

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
    >>> res_raw = retrieve_metadata_legacy("4589756")
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


def download_files(  # noqa: PLR0913
    record_id: RecordIDLike,
    dest: Path | Mapping[str, Path],
    client: ZenodoClient | None = None,
    *,
    filenames: Collection[str] | None = None,
    n_threads: int = 4,
    verify_checksum: bool = True,
    overwrite: bool = False,
    progress: bool = True,
) -> list[Path]:
    """
    Download a record's files, in one call

    Parameters
    ----------
    record_id
        ID of the record to download from

    dest
        Where to write the files.

        A directory means "write them all in here, under their own names".
        A mapping of filename to path says exactly where each file goes,
        and also says which files to download, see
        [`download_files`][openscm_zenodo.zenodo.ZenodoClient.download_files].

    client
        Client to interact with Zenodo with.

        If not supplied, we build a default
        [`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient],
        which picks up a token from the environment if there is one.

    filenames
        Names of the files to download.

        If not supplied, every file on the record is downloaded.

    n_threads
        Number of files to download at once

    verify_checksum
        Should we check that we received what Zenodo says it sent?

    overwrite
        Should existing files with different contents be replaced?

    progress
        Should progress bars be shown?

    Returns
    -------
    :
        Paths the files were written to
    """
    if client is None:
        client = ZenodoClient()

    return client.download_files(
        record_id,
        dest,
        filenames=filenames,
        n_threads=n_threads,
        verify_checksum=verify_checksum,
        overwrite=overwrite,
        progress=progress,
    )


def create_or_get_new_version(  # noqa: PLR0913
    record_id: RecordIDLike,
    client: ZenodoClient | None = None,
    *,
    metadata: Metadata | None = None,
    files: Collection[Path] | None = None,
    files_mode: FilesMode = FilesMode.start_fresh,
    publish: bool = False,
    n_threads: int = 4,
    progress: bool = True,
) -> RecordID:
    """
    Create a new version of a record, in one call

    This is the high-level path for releasing a new version of a dataset:
    create the version, set its metadata, get its files into the state you want,
    and optionally publish it.

    It is safe to re-run.
    Creating a new version is get-or-create, and the file methods
    skip anything which is already there with the same contents,
    so a run which failed part way through picks up where it left off
    rather than creating a second draft.

    Parameters
    ----------
    record_id
        ID of any published version of the record to create a new version of

    client
        Client to interact with Zenodo with.

        If not supplied, we build a default
        [`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient].

    metadata
        Metadata to apply to the new version.

        If not supplied, the new version keeps
        the metadata it inherited from the previous version.

    files
        Files the new version should have.

        How these combine with the previous version's files
        is decided by `files_mode`.

    files_mode
        How the new version should treat files, see
        [`FilesMode`][openscm_zenodo.zenodo.FilesMode]

    publish
        Should the new version be published once it is ready?

        **Publishing cannot be undone.**

    n_threads
        Number of files to upload at once

    progress
        Should progress bars be shown?

    Returns
    -------
    :
        ID of the new version

    Raises
    ------
    ValueError
        `files_mode` is [`FilesMode.mirror`][openscm_zenodo.zenodo.FilesMode]
        but no `files` were given.

        Mirroring deletes whatever is not in `files`,
        so we do not let that happen by omission.
        Pass `files=[]` if you really do want the new version to have no files.
    """
    if files_mode is FilesMode.mirror and files is None:
        msg = (
            "`files` must be supplied when `files_mode` is `FilesMode.mirror`, "
            "because mirroring deletes any file which is not in `files`. "
            "Pass `files=[]` if you want the new version to have no files."
        )

        raise ValueError(msg)

    if client is None:
        client = ZenodoClient()

    new_version_id = client.create_or_get_new_version(record_id)

    if metadata is not None:
        client.update_metadata(new_version_id, metadata)

    if files_mode in (FilesMode.inherit, FilesMode.mirror):
        client.import_files(new_version_id)

    if files_mode is FilesMode.mirror:
        # `files` cannot be `None` here, that is rejected above
        client.mirror_files(
            new_version_id,
            cast(Collection[Path], files),
            n_threads=n_threads,
            progress=progress,
        )

    elif files:
        client.upload_files(
            new_version_id, files, n_threads=n_threads, progress=progress
        )

    if publish:
        client.publish(new_version_id)

    return new_version_id


def create_new_version_legacy(  # noqa: PLR0913
    any_deposition_id: str,
    zenodo_interactor: ZenodoInteractor,
    metadata: MetadataType | None = None,
    publish: bool = False,
    files_to_upload: list[Path] | None = None,
    n_threads: int = 4,
) -> str:
    """
    Create a new version of a given record, using the legacy API

    This is the pre-InvenioRDM implementation.
    It is kept only so that the command-line interface keeps working
    while the rewrite lands, and goes when the CLI is trimmed (Part 8).
    Use
    [`create_or_get_new_version`][openscm_zenodo.zenodo.create_or_get_new_version]
    instead.

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
