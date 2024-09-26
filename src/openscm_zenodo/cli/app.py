"""
CLI app
"""

# # Do not use this here, it breaks typer's annotations
# from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional, Union

import typer
from loguru import logger
from typing_extensions import TypeAlias

import openscm_zenodo
from openscm_zenodo.logging import setup_logging
from openscm_zenodo.zenodo import (
    ZenodoDomain,
    ZenodoInteractor,
    create_new_version,
    get_reserved_doi,
)

app = typer.Typer()


DEPOSITION_ID_TYPE: TypeAlias = Annotated[
    str,
    typer.Argument(
        help=(
            "The ID of the deposition you wish to interact with. "
            "This ID is most easily extracted from the URL provided by Zenodo. "
            "It is just the digits at the end of that link. "
            "For example, if Zenodo URL is https://zenodo.org/records/10702583, "
            "then the deposition ID is 10702583."
        )
    ),
]

FILES_TO_UPLOAD_TYPE: TypeAlias = Annotated[
    Optional[list[Path]],
    typer.Argument(help="Files to upload to the Zenodo deposition"),
]

METADATA_FILE_TYPE: TypeAlias = Annotated[
    Optional[Path],
    typer.Option(
        exists=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help=(
            "Path to the `.json` file "
            "containing the metadata to use for this version. "
            "The `.json` file should have a single 'metadata' key, "
            "which points to a dictionary of key : value pairs."
            "For futher information about the required form, "
            "see the docstring of "
            "[`update_metadata`][openscm_zenodo.zenodo.ZenodoInteractor.update_metadata]. "  # noqa: E501
            "To get an example, see the docstring of "
            "[`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata]."
        ),
    ),
]

N_THREADS_TYPE: TypeAlias = Annotated[
    int, typer.Option(help="Number of threads to use for parallel processing")
]

TOKEN_TYPE: TypeAlias = Annotated[
    Union[str, None],
    typer.Option(
        envvar="ZENODO_TOKEN",
        help=(
            "Zenodo token to use for this interaction. "
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


def version_callback(version: Optional[bool]) -> None:
    """
    If requested, print the version string and exit
    """
    if version:
        print(f"openscm-zenodo {openscm_zenodo.__version__}")
        raise typer.Exit(code=0)


@app.callback()
def cli(
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            help="Print the version number and exit",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
    no_logging: Annotated[
        Optional[bool],
        typer.Option(
            "--no-logging",
            help="""Disable all logging.

If supplied, overrides '--logging-config'""",
        ),
    ] = None,
    logging_level: Annotated[
        Optional[str],
        typer.Option(
            help="""Logging level to use.

This is only applied if no other logging configuration flags are supplied."""
        ),
    ] = None,
    logging_config: Annotated[
        Optional[Path],
        typer.Option(
            help="""Path to the logging configuration file.

This will be loaded with [loguru-config](https://github.com/erezinman/loguru-config).
If supplied, this overrides any value provided with `--log-level`."""
        ),
    ] = None,
) -> None:
    """
    Entrypoint for the command-line interface
    """
    if no_logging:
        setup_logging(enable=False)

    else:
        setup_logging(
            enable=True, logging_config=logging_config, logging_level=logging_level
        )


@app.command(name="retrieve-metadata")
def retrieve_metadata_command(
    deposition_id: DEPOSITION_ID_TYPE,
    token: TOKEN_TYPE = None,
    user_controlled_only: Annotated[
        bool,
        typer.Option(
            help="""Only return metadata keys that the user can control.

If this is `True`, the metadata keys controlled by Zenodo (e.g. the DOI)
are removed from the returned metadata.
This flag is important to use
if you want to use the retrieved metadata
as the starting point for the next version of a deposit."""
        ),
    ] = False,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
) -> None:
    """
    Retrieve metadata
    """
    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    metadata = zenoodo_interactor.get_metadata(
        deposition_id, user_controlled_only=user_controlled_only
    )

    print(json.dumps(metadata, indent=2, sort_keys=True))


@app.command(name="retrieve-bibtex")
def retrieve_bibtex_command(
    deposition_id: DEPOSITION_ID_TYPE,
    token: TOKEN_TYPE = None,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
) -> None:
    """
    Retrieve bibtex entry
    """
    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    bibtex_entry = zenoodo_interactor.get_bibtex_entry(deposition_id)

    print(bibtex_entry)


@app.command(name="update-metadata")
def update_metadata_command(
    deposition_id: DEPOSITION_ID_TYPE,
    metadata_file: METADATA_FILE_TYPE,
    token: TOKEN_TYPE,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    reserve_doi: Annotated[
        bool,
        typer.Option(
            "--reserve-doi",
            help=(
                "Reserve a DOI while updating the metadata. "
                "This will overwrite any value in the metadata file supplied."
            ),
        ),
    ] = False,
) -> None:
    """
    Update metadata

    If the `--reserve-doi` flag is used,
    this prints the reserved DOI to stdout.
    """
    if metadata_file is None:
        msg = "A value must be provided for `--metadata-file`"

        raise ValueError(msg)

    with open(metadata_file) as fh:
        metadata = json.load(fh)

    if reserve_doi:
        if (
            "prereserve_doi" in metadata["metadata"]
            and not metadata["metadata"]["prereserve_doi"]
        ):
            logger.warning(
                f"The supplied metadata has {metadata['metadata']['prereserve_doi']=}, "
                "this will be overwritten by the `--reserve-doi` flag"
            )

        metadata["metadata"]["prereserve_doi"] = True

    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    update_metadata_response = zenoodo_interactor.update_metadata(
        deposition_id, metadata=metadata
    )

    if reserve_doi:
        print(get_reserved_doi(update_metadata_response))


@app.command(name="upload-files")
def upload_files_command(
    deposition_id: DEPOSITION_ID_TYPE,
    files_to_upload: FILES_TO_UPLOAD_TYPE,
    token: TOKEN_TYPE,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    n_threads: N_THREADS_TYPE = 4,
) -> None:
    """
    Upload files to a Zenodo deposition
    """
    if files_to_upload is None:
        msg = "You must supply some files to upload"
        raise ValueError(msg)

    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    zenoodo_interactor.upload_files(
        deposition_id, to_upload=files_to_upload, n_threads=n_threads
    )


@app.command(name="remove-files")
def remove_files_command(
    deposition_id: DEPOSITION_ID_TYPE,
    token: TOKEN_TYPE,
    files_to_remove: Annotated[
        Optional[list[Path]],
        typer.Argument(help="Files to remove from the Zenodo deposition"),
    ] = None,
    all: Annotated[bool, typer.Option("--all", help="Remove all files")] = False,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    # # Off until parallelism works
    # n_threads: N_THREADS_TYPE = 4,
) -> None:
    """
    Remove files from a Zenodo deposition
    """
    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    if all:
        zenoodo_interactor.remove_all_files(
            deposition_id,
            # n_threads=n_threads,
        )

    else:
        if not files_to_remove:
            msg = "If not using the `--all` flag, you must supply files to remove"
            print(msg)
            raise typer.Exit(1)

        zenoodo_interactor.remove_files(
            deposition_id,
            to_remove=files_to_remove,
            # n_threads=n_threads
        )


@app.command(name="create-new-version")
def create_new_version_command(  # noqa: PLR0913
    any_deposition_id: Annotated[
        str,
        typer.Argument(
            help=(
                "A deposition ID related to the record you wish to interact with. "
                "You can pick any published deposit/version. "
                "This ID is most easily extracted from the URL provided by Zenodo. "
                "It is just the digits at the end of that link. "
                "For example, if Zenodo URL is https://zenodo.org/records/10702583, "
                "then the deposit ID is 10702583."
            )
        ),
    ],
    token: TOKEN_TYPE,
    metadata_file: METADATA_FILE_TYPE = None,
    publish: Annotated[
        bool,
        typer.Option(
            "--publish",
            help=(
                "Publish the newly created version "
                "after creating it and uploading the files"
            ),
        ),
    ] = False,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
    files_to_upload: FILES_TO_UPLOAD_TYPE = None,
    n_threads: N_THREADS_TYPE = 4,
) -> None:
    """
    Create a new version of a record
    """
    if metadata_file is not None:
        with open(metadata_file) as fh:
            metadata = json.load(fh)

    else:
        metadata = None

    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    new_deposit_id = create_new_version(
        any_deposition_id=any_deposition_id,
        metadata=metadata,
        zenoodo_interactor=zenoodo_interactor,
        publish=publish,
        files_to_upload=files_to_upload,
        n_threads=n_threads,
    )

    print(new_deposit_id)
