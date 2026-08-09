"""
Python API and command-line tool for interacting with zenodo.
"""

import importlib.metadata

from loguru import logger

from openscm_zenodo.metadata import (
    Affiliation,
    Award,
    Contributor,
    Creator,
    Date,
    Funder,
    Funding,
    Identifier,
    Metadata,
    Organisation,
    Person,
    RelatedIdentifier,
    Right,
    Subject,
)
from openscm_zenodo.zenodo import (
    Access,
    CitationFormat,
    Embargo,
    FilesMode,
    Record,
    Version,
    ZenodoClient,
    ZenodoDomain,
    build_session,
    create_or_get_new_version,
    download_files,
    load_env_file,
    resolve_token,
    retrieve_citation,
    retrieve_metadata,
)
from openscm_zenodo.zipping import zip_files

logger.disable("openscm_zenodo")

__version__ = importlib.metadata.version("openscm_zenodo")

__all__ = [
    "Access",
    "Affiliation",
    "Award",
    "CitationFormat",
    "Contributor",
    "Creator",
    "Date",
    "Embargo",
    "FilesMode",
    "Funder",
    "Funding",
    "Identifier",
    "Metadata",
    "Organisation",
    "Person",
    "Record",
    "RelatedIdentifier",
    "Right",
    "Subject",
    "Version",
    "ZenodoClient",
    "ZenodoDomain",
    "build_session",
    "create_or_get_new_version",
    "download_files",
    "load_env_file",
    "resolve_token",
    "retrieve_citation",
    "retrieve_metadata",
    "zip_files",
]
