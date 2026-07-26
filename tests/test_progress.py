"""
Tests of `openscm_zenodo.progress`
"""

from __future__ import annotations

import io

import pytest
import tqdm as tqdm_module

from openscm_zenodo.progress import (
    get_file_progress_bar,
    get_progress_reading_wrapper,
)


@pytest.fixture
def captured_kwargs(monkeypatch):
    """Capture the arguments we hand to tqdm, without making a bar"""
    res = {}

    def fake_tqdm(**kwargs):
        res.update(kwargs)

    monkeypatch.setattr(tqdm_module, "tqdm", fake_tqdm)

    return res


def test_get_file_progress_bar_defaults(captured_kwargs):
    get_file_progress_bar(desc="data.nc", total=100)

    assert captured_kwargs["desc"] == "data.nc"
    assert captured_kwargs["total"] == 100
    assert captured_kwargs["unit"] == "B"
    assert captured_kwargs["unit_scale"]
    # The bar is erased when the file completes, so bars do not pile up
    assert not captured_kwargs["leave"]
    # `None` lets tqdm silence itself when stderr is not a terminal,
    # which keeps CI logs and piped output clean
    assert captured_kwargs["disable"] is None


def test_get_file_progress_bar_no_progress(captured_kwargs):
    get_file_progress_bar(desc="data.nc", total=100, progress=False)

    assert captured_kwargs["disable"] is True


def test_get_file_progress_bar_position(captured_kwargs):
    get_file_progress_bar(desc="data.nc", total=100, position=3)

    assert captured_kwargs["position"] == 3


def test_get_file_progress_bar_truncates_the_description(captured_kwargs):
    desc = "a-very-long-file-name-" * 10

    get_file_progress_bar(desc=desc, total=1, desc_max_length=12)

    assert captured_kwargs["desc"] == desc[:12]


def test_get_file_progress_bar_kwargs_win(captured_kwargs):
    get_file_progress_bar(desc="data.nc", total=1, leave=True)

    assert captured_kwargs["leave"] is True


def test_get_file_progress_bar_unknown_total(captured_kwargs):
    """
    A transfer of unknown size gets a bar without a percentage, not no bar
    """
    get_file_progress_bar(desc="data.nc", total=None)

    assert captured_kwargs["total"] is None


def test_get_progress_reading_wrapper():
    contents = b"0123456789"

    # Force the bar on: under pytest stderr is not a terminal,
    # so it would otherwise silence itself and never count anything
    with get_file_progress_bar(
        desc="data.nc", total=len(contents), disable=False
    ) as progress_bar:
        wrapped = get_progress_reading_wrapper(io.BytesIO(contents), progress_bar)

        assert wrapped.read(4) == b"0123"
        assert progress_bar.n == 4

        assert wrapped.read() == b"456789"
        assert progress_bar.n == len(contents)


def test_get_progress_reading_wrapper_proxies():
    """
    `requests` needs to be able to work out the content's length

    If it cannot, it falls back to chunked transfer encoding,
    so the wrapper has to proxy everything it does not handle itself.
    """
    contents = b"0123456789"

    with get_file_progress_bar(
        desc="data.nc", total=len(contents), disable=False
    ) as progress_bar:
        wrapped = get_progress_reading_wrapper(io.BytesIO(contents), progress_bar)

        assert wrapped.seekable()
        assert wrapped.tell() == 0
