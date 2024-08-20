"""
Command-line tool for uploading to zenodo
"""

import importlib.metadata

from loguru import logger

logger.disable("openscm_zenodo")

__version__ = importlib.metadata.version("openscm_zenodo")
