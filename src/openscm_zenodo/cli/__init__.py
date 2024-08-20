"""
Command-line interface
"""

# # Do not use this here, it breaks typer's annotations
# from __future__ import annotations

from openscm_zenodo.cli.app import app

__all__ = ["app"]
