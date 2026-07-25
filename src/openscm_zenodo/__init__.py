"""
Python API and command-line tool for interacting with zenodo.
"""

import importlib.metadata

from loguru import logger

from openscm_zenodo.zenodo import (
    ZenodoClient,
    ZenodoDomain,
    ZenodoInteractor,
    build_session,
    create_new_version,
    get_reserved_doi,
    load_env_file,
    resolve_token,
    retrieve_bibtex_entry,
    retrieve_metadata,
)

logger.disable("openscm_zenodo")

__version__ = importlib.metadata.version("openscm_zenodo")

__all__ = [
    "ZenodoClient",
    "ZenodoDomain",
    "ZenodoInteractor",
    "build_session",
    "create_new_version",
    "get_reserved_doi",
    "load_env_file",
    "resolve_token",
    "retrieve_bibtex_entry",
    "retrieve_metadata",
]
