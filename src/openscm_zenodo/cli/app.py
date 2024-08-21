"""
CLI app
"""

# # Do not use this here, it breaks typer's annotations
# from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from typing_extensions import TypeAlias

import openscm_zenodo
from openscm_zenodo.logging import setup_logging
from openscm_zenodo.zenodo import ZenodoDomain, ZenodoInteractor, create_new_version

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
    str,
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
    token: TOKEN_TYPE,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
) -> None:
    """
    Retrieve metadata
    """
    zenoodo_interactor = ZenodoInteractor(
        token=token,
        zenodo_domain=zenodo_domain,
    )

    metadata = zenoodo_interactor.get_metadata(deposition_id)

    print(json.dumps(metadata, indent=2, sort_keys=True))


@app.command(name="update-metadata")
def update_metadata_command(
    deposition_id: DEPOSITION_ID_TYPE,
    metadata_file: METADATA_FILE_TYPE,
    token: TOKEN_TYPE,
    zenodo_domain: ZENODO_DOMAIN_TYPE = ZenodoDomain.production,
) -> None:
    """
    Update metadata
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

    zenoodo_interactor.update_metadata(deposition_id, metadata=metadata)


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
) -> None:
    """
    Create a new version of a recod
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
    )

    print(new_deposit_id)


# @app.command(name="get-bucket-id")
# def get_bucket_id_command(
#     deposit_id: DEPOSIT_ID_TYPE,
#     token: TOKEN_TYPE,
#     zenodo_url: ZENODO_URL_TYPE = ZenodoDomain.production,
# ) -> None:
#     """
#     Get the bucket associated with a given Zenodo deposit
#     """
#     bucket_id = get_bucket_id(
#         deposition_id=deposit_id, zenodo_url=zenodo_url, token=token
#     )
#
#     typer.echo(bucket_id)
#
#
# @app.command(name="upload-file")
# def upload_file_command(
#     file_to_upload: Annotated[
#         Path,
#         typer.Option(
#             exists=True,
#             dir_okay=False,
#             readable=True,
#             resolve_path=True,
#             help="File to upload.",
#         ),
#     ],
#     bucket_id: BUCKET_ID_TYPE,
#     token: TOKEN_TYPE,
#     dir_to_strip: Annotated[
#         Optional[Path],
#         typer.Option(
#             help=(
#                 "If supplied, "
#                 "this directory is removed from file paths before uploading."
#             ),
#             exists=True,
#             file_okay=False,
#         ),
#     ] = None,
#     zenodo_url: ZENODO_URL_TYPE = ZenodoDomain.production,
# ) -> None:
#     """
#     Upload a file to Zenodo
#     """
#     upload_file(
#         filepath=file_to_upload,
#         bucket=bucket_id,
#         root_dir=dir_to_strip,
#         zenodo_url=zenodo_url.value,
#         token=token,
#     )
