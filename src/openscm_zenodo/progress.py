"""
Progress bars for file transfers
"""

from __future__ import annotations

import sys
from typing import Any

import tqdm
import tqdm.utils

TQDM_FILE_PROGRESS_KWARGS_DEFAULT: dict[str, Any] = dict(
    unit="B",
    unit_scale=True,
    unit_divisor=1024,
    leave=False,
    dynamic_ncols=True,
)
"""Default configuration for a per-file transfer progress bar"""

# Why is this a global constant, rather than the default value of the argument of the relevant function?
DESC_MAX_LENGTH = 30
"""Number of characters of a file's name to show as a bar's description"""


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
    tqdm.tqdm.write(message, end="", file=sys.stderr)


def get_file_progress_bar(
    *,
    desc: str,
    total: int | None,
    progress: bool = True,
    position: int | None = None,
    **kwargs: Any,
) -> tqdm.tqdm[Any]:
    """
    Get a progress bar for the transfer of a single file

    Parameters
    ----------
    desc
        Description of the transfer.

        [Ensure that information about truncation
        stays up to date here if we move DESC_MAX_LENGTH.]

    total
        Total number of bytes to be transferred.

        Pass `None` if the size is not known
        (which gives a bar with no completion percentage).

    progress
        Should a progress bar be shown?

        If `True` (the default), we let `tqdm` decide,
        which means the bar silences itself
        when `stderr` is not a terminal,
        keeping continuous integration logs and piped output clean.
        Pass `False` to force the bar off.

    position
        Line to display the bar on.

        Give each worker a stable slot when transferring in parallel,
        otherwise the bars jump around.

    **kwargs
        Passed to [`tqdm.tqdm`][tqdm.tqdm],
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
        # `disable=None` makes tqdm silence itself when stderr is not a terminal
        "disable": None if progress else True,
        "desc": desc[:DESC_MAX_LENGTH],
        "total": total,
        "position": position,
        **kwargs,
    }

    return tqdm.tqdm(**tqdm_kwargs)


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
