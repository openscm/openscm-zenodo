"""
Tests of `openscm_zenodo.progress`
"""

from __future__ import annotations

import concurrent.futures
import io
import threading
import time

import pytest
import tqdm as tqdm_module

from openscm_zenodo.progress import (
    PositionAllocator,
    get_file_progress_bar,
    get_files_progress_bar,
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


def test_position_allocator():
    """
    Slots are handed out and given back, so `n_slots` lines are ever used
    """
    allocator = PositionAllocator(n_slots=2)

    with allocator.slot() as first, allocator.slot() as second:
        assert {first, second} == {1, 2}

    # Both are back, so the next two callers get them again
    with allocator.slot() as third:
        assert third in (1, 2)


def test_position_allocator_leaves_room_for_the_overall_bar():
    allocator = PositionAllocator(n_slots=3)

    with allocator.slot() as position:
        assert position == 1


def test_position_allocator_runs_out():
    """
    Running out of slots is a wiring bug, so it is loud rather than papered over

    It cannot happen when `n_slots` matches the number of workers,
    so quietly carrying on would only hide the mistake.
    """
    allocator = PositionAllocator(n_slots=1)

    with allocator.slot(), pytest.raises(RuntimeError, match="No progress bar slots"):
        with allocator.slot():
            pass


def test_position_allocator_gives_slots_back_after_a_failure():
    allocator = PositionAllocator(n_slots=1)

    with pytest.raises(ValueError, match="boom"), allocator.slot():
        msg = "boom"
        raise ValueError(msg)

    with allocator.slot() as position:
        assert position == 1


def test_position_allocator_is_thread_safe():
    """
    No two workers may hold the same slot at once
    """
    allocator = PositionAllocator(n_slots=4)
    seen_at_once = []
    held = set()
    lock = threading.Lock()

    def worker(_):
        with allocator.slot() as position:
            with lock:
                assert position not in held
                held.add(position)
                seen_at_once.append(len(held))

            time.sleep(0.001)

            with lock:
                held.discard(position)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(worker, range(40)))

    assert max(seen_at_once) > 1


def test_get_files_progress_bar(captured_kwargs):
    get_files_progress_bar(desc="Uploading", total=3)

    assert captured_kwargs["unit"] == "file"
    assert captured_kwargs["total"] == 3
    # The overall bar stays once the operation finishes,
    # unlike the per-file bars
    assert captured_kwargs["leave"]
    # Lines count downwards, so line zero is above the per-file bars
    assert captured_kwargs["position"] == 0


def test_progress_bar_layout():
    """
    The overall bar is above the per-file bars

    tqdm counts lines downwards, so a lower position is higher up the screen.
    """
    allocator = PositionAllocator(n_slots=3)

    with allocator.slot() as first, allocator.slot() as second:
        assert first > 0
        assert second > first


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
