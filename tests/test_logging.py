"""
Tests of `openscm_zenodo.logging`

The level policy is doctested on the functions themselves. What needs a test with
a filesystem is `logging_config`, i.e. handing over a whole loguru configuration
from disk, because that goes through `loguru-config` and is the part which broke
without anything noticing.
"""

from __future__ import annotations

import json

import pytest
from loguru import logger

from openscm_zenodo.logging import get_level_from_verbosity, setup_logging


@pytest.fixture
def restored_logging():
    """
    Put the global logger back after a test which configures it

    `setup_logging` calls `logger.configure`, which replaces every handler
    process-wide, so a test which uses it has to clean up after itself or the
    rest of the suite inherits its sinks.
    """
    yield

    logger.remove()
    logger.disable("openscm_zenodo")


@pytest.fixture
def config_file(tmp_path):
    """A loguru configuration on disk, writing to a file we can read back"""
    destination = tmp_path / "out.log"
    path = tmp_path / "logging.json"
    path.write_text(
        json.dumps(
            {
                "handlers": [
                    {
                        "sink": str(destination),
                        "level": "DEBUG",
                        "format": "{level} | {message}",
                    }
                ]
            }
        )
    )

    return path, destination


def test_setup_logging_from_a_file(config_file, restored_logging):
    """
    A configuration file is loaded and applied

    This is what `--logging-config` does.
    """
    path, destination = config_file

    setup_logging(enable=True, logging_config=path)
    logger.info("a message which belongs in the file")

    # Closes the sink, so what was written is on disk to be read
    logger.remove()

    assert "INFO | a message which belongs in the file" in destination.read_text()


def test_setup_logging_from_a_file_reaches_our_own_messages(
    config_file, restored_logging
):
    """
    Applying a config also enables our logger, which is disabled on import
    """
    path, destination = config_file

    setup_logging(enable=True, logging_config=path)
    # Something inside the package, so it is subject to enable/disable
    get_level_from_verbosity()
    logger.bind().debug("from the test, standing in for the package")

    logger.remove()

    assert destination.read_text()


def test_setup_logging_warns_when_a_level_is_also_given(
    config_file, restored_logging, tmp_path
):
    """
    A config file decides the levels, so naming one as well does nothing

    The CLI refuses this combination outright; the library says so and carries
    on, because a caller may be passing both through from somewhere else.
    """
    path, destination = config_file

    setup_logging(enable=True, logging_config=path, logging_level="WARNING")

    logger.remove()

    assert "`logging_level` is ignored" in destination.read_text()


def test_setup_logging_resolves_references_in_a_file(
    tmp_path, restored_logging, capsys
):
    """
    An `ext://` sink is resolved to the object it names, not treated as a path

    This is what the parsing step of loading a configuration is for. Without it
    the sink stays the string `"ext://sys.stderr"`, which loguru would take for a
    filename, so the messages would end up in a file with a very odd name
    instead of on the screen.
    """
    path = tmp_path / "logging.json"
    path.write_text(
        json.dumps(
            {
                "handlers": [
                    {
                        "sink": "ext://sys.stderr",
                        "level": "INFO",
                        "format": "{level} | {message}",
                    }
                ]
            }
        )
    )

    setup_logging(enable=True, logging_config=path)
    logger.info("a message which belongs on the screen")

    assert "INFO | a message which belongs on the screen" in capsys.readouterr().err
    assert not list(tmp_path.glob("ext:*"))


def test_setup_logging_disabled_does_nothing_else(config_file, restored_logging):
    """`enable=False` ignores every other argument, including a config file"""
    path, destination = config_file

    setup_logging(enable=False, logging_config=path)

    assert not destination.exists()
