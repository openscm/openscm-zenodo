"""
Integration tests of the CLI
"""

from __future__ import annotations

from typer.testing import CliRunner

import openscm_zenodo
from openscm_zenodo.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.exc_info
    assert result.stdout == f"openscm-zenodo {openscm_zenodo.__version__}\n"


def test_say_hi():
    result = runner.invoke(app, ["say-hi", "Tim"])

    assert result.exit_code == 0, result.exc_info
    assert result.stdout == "Hi Tim\n"
