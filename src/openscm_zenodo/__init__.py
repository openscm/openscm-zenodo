"""
Command-line tool for uploading to zenodo
"""

import importlib.metadata

from loguru import logger

from openscm_zenodo.zenodo import (
    ZenodoDomain,
    ZenodoInteractor,
    create_new_version,
    get_reserved_doi,
    retrieve_bibtex_entry,
    retrieve_metadata,
)

logger.disable("openscm_zenodo")

__version__ = importlib.metadata.version("openscm_zenodo")

__all__ = [
    "ZenodoDomain",
    "ZenodoInteractor",
    "create_new_version",
    "get_reserved_doi",
    "retrieve_bibtex_entry",
    "retrieve_metadata",
]
