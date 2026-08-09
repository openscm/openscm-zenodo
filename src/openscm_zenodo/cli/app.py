"""
CLI app
"""

# # Do not use this here, it breaks typer's annotations
# from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, TypeAlias

import typer

import openscm_zenodo
from openscm_zenodo.exceptions import ZenodoError, ZenodoHTTPError
from openscm_zenodo.logging import get_level_from_verbosity, setup_logging
from openscm_zenodo.zenodo import (
    CITATION_LOCALE_DEFAULT,
    CITATION_STYLE_DEFAULT,
    CitationFormat,
    ZenodoClient,
    ZenodoDomain,
    load_env_file,
)

app = typer.Typer()

HTTP_NO_ACCESS = (401, 403)
"""Status codes which mean the token we sent does not open this record"""


RECORD_ID_TYPE: TypeAlias = Annotated[
    str,
    typer.Argument(
        help=(
            "The ID of the record you wish to interact with. "
            "This ID is most easily extracted from the URL provided by Zenodo. "
            "It is just the digits at the end of that link. "
            "For example, if Zenodo URL is https://zenodo.org/records/10702583, "
            "then the record ID is 10702583."
        )
    ),
]

FILES_TO_UPLOAD_TYPE: TypeAlias = Annotated[
    list[Path],
    typer.Argument(
        exists=True,
        dir_okay=False,
        readable=True,
        help=(
            "Files to upload. "
            "Zenodo has no directories, so each file lands under its own name "
            "and any local directories above it are lost, see `--zip`."
        ),
    ),
]

FILENAMES_TYPE: TypeAlias = Annotated[
    list[str] | None,
    typer.Argument(
        help=(
            "Names of the files to download, as they appear on Zenodo. "
            "If none are given, every file on the record is downloaded."
        )
    ),
]

N_THREADS_TYPE: TypeAlias = Annotated[
    int, typer.Option(help="Number of files to transfer at once")
]

PROGRESS_TYPE: TypeAlias = Annotated[
    bool,
    typer.Option(
        "--progress/--no-progress",
        help="""Show a progress bar per file.

Progress bars turn themselves off when stderr is not a terminal,
so this is only needed to silence them in a terminal.""",
    ),
]

TOKEN_TYPE: TypeAlias = Annotated[
    str | None,
    typer.Option(
        help=(
            "Zenodo token to use for this interaction. "
            "If not supplied, we use, in order of preference: "
            "the `ZENODO_SANDBOX_TOKEN` environment variable "
            "(only when using the sandbox domain), "
            "then the `ZENODO_TOKEN` environment variable, "
            "then any value found in a `.env` file (see `--env-file`). "
            "For more information about generating tokens, "
            "see the 'Creating a personal access token' header of "
            "https://developers.zenodo.org/#authentication."
        ),
    ),
]

ZENODO_DOMAIN_TYPE: TypeAlias = Annotated[
    ZenodoDomain,
    typer.Option(help=("The zenodo domain with which you want to interact.")),
]


def version_callback(version: bool | None) -> None:
    """
    If requested, print the version string and exit
    """
    if version:
        print(f"openscm-zenodo {openscm_zenodo.__version__}")
        raise typer.Exit(code=0)


@contextlib.contextmanager
def reported_cleanly(client: ZenodoClient) -> Iterator[None]:
    """
    Turn our own errors into a message and a non-zero exit code

    A traceback is the wrong way to tell somebody at a shell prompt that their
    token cannot see a record, so anything we raise deliberately is reported as
    a message on stderr instead. Anything else still raises, because a traceback
    is exactly what we want for a bug.

    Parameters
    ----------
    client
        Client the work is being done with, used to say which token was sent

    Yields
    ------
    :
        Nothing, this only handles errors
    """
    try:
        yield

    except ZenodoError as exc:
        # The library logs the failing response as it happens, but that is a log:
        # `--no-logging` turns it off and a logging config can raise the level
        # past it, so the user-facing message has to be ours. With logging on and
        # an HTTP failure the text therefore appears twice, once as an `ERROR`
        # record and once here. The prefix is what tells them apart — a message
        # which is always there beats a tidier common case.
        typer.secho(f"Error: {exc}", err=True, fg=typer.colors.RED)

        no_access = (
            isinstance(exc, ZenodoHTTPError)
            and exc.response.status_code in HTTP_NO_ACCESS
        )
        if no_access and client.token is not None:
            typer.secho(
                f"No access to this record with the token from "
                f"{client.token_source}. "
                "Drafts and restricted records are only visible to a token "
                "whose owner has access, and sandbox and production tokens are "
                "not interchangeable.",
                err=True,
                fg=typer.colors.RED,
            )

        raise typer.Exit(code=1) from exc


@app.callback()
def cli(  # noqa: PLR0913
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            help="Print the version number and exit",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="""Show more about what is going on. Repeat for more still.

Nothing shows the narrative of what happened, `-v` adds the requests and
decisions behind it, and `-vv` adds everything.""",
        ),
    ] = 0,
    quiet: Annotated[
        int,
        typer.Option(
            "--quiet",
            "-q",
            count=True,
            help="""Show less. Repeat for less still.

`-q` leaves warnings and errors, `-qq` only errors.
Use `--no-logging` for silence.""",
        ),
    ] = 0,
    no_logging: Annotated[
        bool | None,
        typer.Option(
            "--no-logging",
            help="""Disable all logging.

If supplied, overrides `--logging-config`""",
        ),
    ] = None,
    logging_level: Annotated[
        str | None,
        typer.Option(
            help="""Logging level to use.

For when you want to name a level rather than count `-v`s.
This is only applied if no other logging configuration flags are supplied."""
        ),
    ] = None,
    logging_config: Annotated[
        Path | None,
        typer.Option(
            help="""Path to the logging configuration file.

This will be loaded with [loguru-config](https://github.com/erezinman/loguru-config).
If supplied, this overrides any value provided with `--log-level`."""
        ),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            readable=True,
            help="""Path to a `.env` file from which to load environment variables.

If not supplied, we look for a `.env` file
in the current working directory and its parents.
Variables which are already set in the environment are not overridden.""",
        ),
    ] = None,
) -> None:
    """
    Entrypoint for the command-line interface
    """
    if verbose and quiet:
        typer.secho(
            "`-v` and `-q` cannot be combined: they move the same setting in "
            "opposite directions, so pick one.",
            err=True,
            fg=typer.colors.RED,
        )

        raise typer.Exit(code=1)

    if (verbose or quiet) and logging_level is not None:
        typer.secho(
            "`--logging-level` and `-v`/`-q` both set the level, "
            f"so supplying `--logging-level {logging_level}` alongside them is "
            "ambiguous. Use one or the other.",
            err=True,
            fg=typer.colors.RED,
        )

        raise typer.Exit(code=1)

    if verbose or quiet:
        # `-v`/`-q` are how a level is normally chosen. Only resolved when they
        # were actually given, so that `setup_logging` can still tell a level
        # somebody asked for from the default it applies itself — which is what
        # its "ignored if `logging_config` is supplied" warning depends on.
        logging_level = get_level_from_verbosity(verbose=verbose, quiet=quiet)

    if no_logging:
        setup_logging(enable=False)

    else:
        setup_logging(
            enable=True, logging_config=logging_config, logging_level=logging_level
        )

    load_env_file(env_file)


@app.command(name="upload-files")
def upload_files_command(  # noqa: PLR0913
    record_id: RECORD_ID_TYPE,
    files_to_upload: FILES_TO_UPLOAD_TYPE,
    token: TOKEN_TYPE = None,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    n_threads: N_THREADS_TYPE = 4,
    mirror: Annotated[
        bool,
        typer.Option(
            "--mirror",
            help="""Make the record contain exactly the files given.

**This deletes files on the Zenodo record.**
Anything on the record which is not in the files given is removed.
Without this, files are only added and updated, never deleted.""",
        ),
    ] = False,
    zip_name: Annotated[
        str | None,
        typer.Option(
            "--zip",
            metavar="NAME",
            help="""Upload the files as a single archive under this name.

Zenodo has no directories, so this is the way to keep the structure of what
you upload. Cannot be combined with `--mirror`.""",
        ),
    ] = None,
    zip_base_dir: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            file_okay=False,
            help="""Directory the paths inside the archive are relative to.

Only applies with `--zip`.
If not supplied, we use the deepest directory which contains every file.""",
        ),
    ] = None,
    warn_path_stripped: Annotated[
        bool,
        typer.Option(
            "--warn-path-stripped/--no-warn-path-stripped",
            help=(
                "Warn when a file's local directories are about to be lost. "
                "Nothing is stripped with `--zip`, so this does not apply there."
            ),
        ),
    ] = True,
    progress: PROGRESS_TYPE = True,
) -> None:
    """
    Upload files to a record's draft

    Files which are already on the draft with the same contents are left alone,
    so re-running this after a failure part way through only sends what is
    still missing.
    """
    if zip_name is not None and mirror:
        typer.secho(
            "`--zip` and `--mirror` cannot be combined: "
            "`--zip` uploads a single archive, so there is no set of files "
            "for `--mirror` to make the record match.",
            err=True,
            fg=typer.colors.RED,
        )

        raise typer.Exit(code=1)

    if zip_base_dir is not None and zip_name is None:
        typer.secho(
            "`--zip-base-dir` only applies with `--zip`.",
            err=True,
            fg=typer.colors.RED,
        )

        raise typer.Exit(code=1)

    with ZenodoClient(token=token, zenodo_domain=zenodo_domain) as client:
        with reported_cleanly(client):
            if zip_name is not None:
                client.upload_files_as_zip(
                    record_id,
                    files_to_upload,
                    zip_name=zip_name,
                    base_dir=zip_base_dir,
                    progress=progress,
                )

            elif mirror:
                client.mirror_files(
                    record_id,
                    files_to_upload,
                    n_threads=n_threads,
                    progress=progress,
                    warn_path_stripped=warn_path_stripped,
                )

            else:
                client.upload_files(
                    record_id,
                    files_to_upload,
                    n_threads=n_threads,
                    progress=progress,
                    warn_path_stripped=warn_path_stripped,
                )


@app.command(name="download-files")
def download_files_command(  # noqa: PLR0913
    record_id: RECORD_ID_TYPE,
    filenames: FILENAMES_TYPE = None,
    token: TOKEN_TYPE = None,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    dest_dir: Annotated[
        Path,
        typer.Option(
            file_okay=False,
            help="Directory to write the files into, created if it is not there",
        ),
    ] = Path(),
    n_threads: N_THREADS_TYPE = 4,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace existing local files whose contents differ",
        ),
    ] = False,
    verify_checksum: Annotated[
        bool,
        typer.Option(
            "--verify-checksum/--no-verify-checksum",
            help=(
                "Check that we received what Zenodo says it sent. "
                "The bytes are hashed as they arrive, so this is nearly free."
            ),
        ),
    ] = True,
    progress: PROGRESS_TYPE = True,
) -> None:
    """
    Download a record's files

    This works for a published record and for an unpublished draft; the record
    ID says which, so there is nothing to declare. Restricted and embargoed
    records need no special handling either: they need a token whose owner has
    access, which is the same `--token` as everything else.

    Files which are already where they are going, with the same contents, are
    not downloaded again.
    """
    with ZenodoClient(token=token, zenodo_domain=zenodo_domain) as client:
        with reported_cleanly(client):
            client.download_files(
                record_id,
                dest_dir,
                # An empty variadic argument is typer's way of saying "not given",
                # which here means every file on the record
                filenames=filenames if filenames else None,
                n_threads=n_threads,
                verify_checksum=verify_checksum,
                overwrite=overwrite,
                progress=progress,
            )


@app.command(name="retrieve-citation")
def retrieve_citation_command(  # noqa: PLR0913
    record_id: RECORD_ID_TYPE,
    token: TOKEN_TYPE = None,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    fmt: Annotated[
        CitationFormat,
        typer.Option(
            "--format",
            help="Format to get the citation in.",
        ),
    ] = CitationFormat.bibtex,
    style: Annotated[
        str,
        typer.Option(
            help=(
                "Citation style to render in. "
                "This only applies to `--format citation`, "
                "and is ignored, with a warning, for any other format. "
                "Zenodo wants full CSL style IDs, e.g. `chicago-author-date`."
            )
        ),
    ] = CITATION_STYLE_DEFAULT,
    locale: Annotated[
        str,
        typer.Option(
            help=(
                "Locale to render in. "
                "As with `--style`, this only applies to `--format citation`."
            )
        ),
    ] = CITATION_LOCALE_DEFAULT,
    output: Annotated[
        Path | None,
        typer.Option(
            dir_okay=False,
            help="File to write the citation to. If not supplied, we use stdout.",
        ),
    ] = None,
) -> None:
    """
    Retrieve a record's citation

    With no options, this prints the record's BibTeX entry.
    """
    with ZenodoClient(token=token, zenodo_domain=zenodo_domain) as client:
        with reported_cleanly(client):
            citation = client.get_citation(
                record_id, fmt=fmt, style=style, locale=locale
            )

    if output is None:
        print(citation)

    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(citation)
