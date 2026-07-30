"""
Configuration for the doctests in `src`

The fixtures the tests use live in `tests/conftest.py`.
This file sits alongside the code, rather than with the tests,
because pytest only loads a `conftest.py` for the directory it lives in
and the directories below it,
and the doctests it configures are in `src`.
"""

from __future__ import annotations

import doctest

import pytest
from _pytest.doctest import DoctestItem

ZENODO_API_OPTION = "--zenodo-api-doctests"
"""Command-line option which turns on the doctests which hit the Zenodo API"""

ZENODO_API_FLAG = doctest.register_optionflag("ZENODO_API")
"""
Doctest directive which marks an example as one which hits the Zenodo API

Marking one example is enough:
the doctest it belongs to is skipped as a whole
unless the tests are run with `--zenodo-api-doctests`,
because hitting Zenodo is slow and needs a network connection.

Registering the flag here is what makes the directive legal:
without this, doctest's parser rejects `+ZENODO_API` as an invalid option
and collecting `src` fails outright.
So this module has to be loaded before the doctests are parsed,
which sitting above them in the tree guarantees.
"""


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add our command-line options"""
    parser.addoption(
        ZENODO_API_OPTION,
        action="store_true",
        default=False,
        help=(
            "Run the doctests which hit the Zenodo API. "
            "These are skipped by default because they are slow "
            "and need a network connection."
        ),
    )


def _hits_zenodo_api(item: DoctestItem) -> bool:
    """Is this a doctest which hits the Zenodo API?"""
    return any(
        example.options.get(ZENODO_API_FLAG, False) for example in item.dtest.examples
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip the doctests which hit the Zenodo API, unless we asked for them"""
    if config.getoption(ZENODO_API_OPTION):
        return

    skip = pytest.mark.skip(
        reason=f"doctest hits the Zenodo API, run it with `{ZENODO_API_OPTION}`"
    )
    for item in items:
        if isinstance(item, DoctestItem) and _hits_zenodo_api(item):
            item.add_marker(skip)
