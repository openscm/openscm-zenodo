"""
Progress bars for file transfers
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import tqdm
import tqdm.auto
import tqdm.utils
from attrs import define, field

TQDM_FILE_PROGRESS_KWARGS_DEFAULT: dict[str, Any] = dict(
    unit="B",
    unit_scale=True,
    unit_divisor=1024,
    leave=False,
    dynamic_ncols=True,
)
"""Default configuration for a per-file transfer progress bar"""


def tqdm_write_sink(message: str) -> None:
    """
    Write a log message without breaking any progress bars

    Parameters
    ----------
    message
        Message to write
    """
    # `tqdm.write` defaults to stdout.
    # We want stderr, both because that is where logs belong
    # and because that is where the bars are,
    # so this is the stream whose bars need clearing.
    # end="" ensures that we don't have new lines appearing
    # where we don't want them.
    tqdm.auto.tqdm.write(message, end="", file=sys.stderr)


def get_file_progress_bar(
    *,
    desc: str,
    total: int | None,
    progress: bool = True,
    position: int | None = None,
    desc_max_length: int = 30,
    **kwargs: Any,
) -> tqdm.tqdm[Any]:
    """
    Get a progress bar for the transfer of a single file

    Parameters
    ----------
    desc
        Description of the transfer.

        Good practice is to use the file's name as it appears on Zenodo,
        so it is obvious which bar tracks which file.

        `desc` is truncated to `desc_max_length` characters.

    total
        Total number of bytes to be transferred.

        Pass `None` if the size is not known
        (which gives a bar with no completion percentage).

    progress
        Should a progress bar be shown?

        If `True` (the default), we let `tqdm` decide:
        a bar in a terminal, a widget in a notebook,
        and silence when `stderr` is neither,
        which keeps continuous integration logs and piped output clean.
        Pass `False` to force the bar off.

    position
        Line to display the bar on.

        Give each worker a stable slot when transferring in parallel,
        otherwise the bars jump around.
        Lines count downwards and line zero is taken by the overall files bar,
        so these start at one, see
        [`PositionAllocator`][openscm_zenodo.progress.PositionAllocator].

    desc_max_length
        Number of characters of `desc` to show

    **kwargs
        Passed to [`tqdm`](https://tqdm.github.io/docs/tqdm/),
        overriding
        [`TQDM_FILE_PROGRESS_KWARGS_DEFAULT`][openscm_zenodo.progress.TQDM_FILE_PROGRESS_KWARGS_DEFAULT].

    Returns
    -------
    :
        The progress bar.

        Use it as a context manager,
        so the bar is torn down whether the transfer succeeds or raises.
    """
    tqdm_kwargs: dict[str, Any] = {
        **TQDM_FILE_PROGRESS_KWARGS_DEFAULT,
        # `disable=None` hands the decision to tqdm, see this module's docstring
        "disable": None if progress else True,
        "desc": desc[:desc_max_length],
        "total": total,
        "position": position,
        **kwargs,
    }

    return tqdm.auto.tqdm(**tqdm_kwargs)


def get_progress_reading_wrapper(file_handle: Any, progress_bar: tqdm.tqdm[Any]) -> Any:
    """
    Wrap a file handle so that reading from it advances a progress bar

    Parameters
    ----------
    file_handle
        File handle to wrap

    progress_bar
        Progress bar to advance as `file_handle` is read

    Returns
    -------
    :
        The wrapped file handle.
    """
    return tqdm.utils.CallbackIOWrapper(progress_bar.update, file_handle, "read")


def get_files_progress_bar(
    *,
    desc: str,
    total: int,
    progress: bool = True,
    position: int = 0,
    **kwargs: Any,
) -> tqdm.tqdm[Any]:
    """
    Get a progress bar which counts files, rather than bytes

    This is the overall bar for an operation on many files.
    It sits at the top, with the per-file bars underneath it, see
    [`get_file_progress_bar`][openscm_zenodo.progress.get_file_progress_bar].

    Unlike the per-file bars, this one stays on screen when it finishes.
    The per-file bars are noise once their file is done,
    but "uploaded 40 of 40 files" is worth keeping.

    Parameters
    ----------
    desc
        Description of the operation

    total
        Total number of files

    progress
        Should a progress bar be shown?

        As with the per-file bars, `True` lets `tqdm` decide,
        so this is silent when `stderr` is neither a terminal nor a notebook.

    position
        Line to display the bar on.

        Lines count downwards, so the default, `0`,
        puts this bar above the per-file bars.

    **kwargs
        Passed to [`tqdm`](https://tqdm.github.io/docs/tqdm/)

    Returns
    -------
    :
        The progress bar
    """
    tqdm_kwargs: dict[str, Any] = {
        "unit": "file",
        "leave": True,
        "dynamic_ncols": True,
        "disable": None if progress else True,
        "desc": desc,
        "total": total,
        "position": position,
        **kwargs,
    }

    return tqdm.auto.tqdm(**tqdm_kwargs)


@define
class PositionAllocator:
    """
    Hands out progress bar lines to parallel workers

    Without this, every worker's bar draws on the same line
    and they overwrite each other.
    Slots are recycled as files complete,
    so `n_slots` lines are used no matter how many files there are.
    """

    n_slots: int
    """Number of slots to hand out, i.e. the number of workers"""

    first_slot: int = 1
    """
    Line of the first slot

    Lines count downwards, so the default leaves line zero
    for the overall files bar, which sits above the per-file bars.
    """

    _free: list[int] = field(init=False, factory=list)
    """Slots which are not currently in use"""

    _lock: threading.Lock = field(init=False, factory=threading.Lock, repr=False)
    """Guards `_free`, which workers take from and give back to"""

    def __attrs_post_init__(self) -> None:
        """
        Finish initialisation

        Every slot starts out free.
        """
        self._free.extend(range(self.first_slot, self.first_slot + self.n_slots))

    @contextmanager
    def slot(self) -> Iterator[int]:
        """
        Take a slot for as long as the context is open, then give it back

        Yields
        ------
        :
            The line to draw on

        Raises
        ------
        RuntimeError
            There are no slots left.

            This cannot happen if `n_slots` matches the number of workers,
            so it means the two have been wired up incorrectly.
        """
        with self._lock:
            if not self._free:
                msg = (
                    f"No progress bar slots left. "
                    f"{self.n_slots} slot(s) were created, "
                    "and all of them are in use, "
                    "which means more workers are running than there are slots. "
                    "`n_slots` must match the number of workers."
                )

                raise RuntimeError(msg)

            position = self._free.pop(0)

        try:
            yield position

        finally:
            with self._lock:
                self._free.append(position)
