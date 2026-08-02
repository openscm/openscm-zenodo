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
    ZenodoInteractor,
    build_session,
    create_new_version_legacy,
    create_or_get_new_version,
    download_files,
    get_reserved_doi_legacy,
    load_env_file,
    resolve_token,
    retrieve_bibtex_entry,
    retrieve_citation,
    retrieve_metadata,
    retrieve_metadata_legacy,
)

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
    "ZenodoInteractor",
    "build_session",
    "create_new_version_legacy",
    "create_or_get_new_version",
    "download_files",
    "get_reserved_doi_legacy",
    "load_env_file",
    "resolve_token",
    "retrieve_bibtex_entry",
    "retrieve_citation",
    "retrieve_metadata",
    "retrieve_metadata_legacy",
]
