"""
Checksums

Zenodo reports every file's checksum as `"md5:<hex>"`,
which is what lets us verify an upload,
skip re-uploading a file that has not changed (Part 3 of the rewrite)
and verify a download (Part 5).
This module is the one place that computes and compares them.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from loguru import logger

from openscm_zenodo.exceptions import ChecksumMismatchError


def get_file_md5(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
    slow_threshold_s: float = 3.0,
) -> str:
    """
    Get the MD5 checksum of a file

    The file is read in chunks, so this works on files
    which do not fit in memory.

    Hashing is CPU-bound.
    For large files it can be a non-trivial share of an upload or download,
    and it is easy to mistake for slow I/O,
    so we log how long it took.
    Enable `DEBUG` logging for `openscm_zenodo`
    (see [`setup_logging`][openscm_zenodo.logging.setup_logging])
    to see the timing of every hash;
    hashes which take longer than `slow_threshold_s` are reported at `INFO`,
    so the "why is this so slow?" case shows up
    without every small file being noisy.

    Parameters
    ----------
    path
        File to hash

    chunk_size
        Number of bytes to read at a time

    slow_threshold_s
        Number of seconds beyond which we report the timing at `INFO`

    Returns
    -------
    :
        MD5 checksum of `path`, as a hex string

    Examples
    --------
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as tmp_dir:
    ...     path = Path(tmp_dir) / "example.txt"
    ...     _ = path.write_text("Hello, Zenodo")
    ...     get_file_md5(path)
    'b77017ecd0f93c60f84d42dcf352368e'
    """
    size = path.stat().st_size
    logger.debug(f"Computing MD5 for {path} ({size} bytes)")

    start = time.perf_counter()

    hasher = hashlib.md5()  # noqa: S324 # Zenodo's checksums are MD5, not our choice
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            hasher.update(chunk)

    elapsed = time.perf_counter() - start

    # Guard against a zero elapsed time on tiny files
    rate_mb_s = (size / 1024**2) / elapsed if elapsed > 0 else float("inf")
    msg = f"Computed MD5 for {path} in {elapsed:.2f}s ({rate_mb_s:.1f} MB/s)"
    if elapsed > slow_threshold_s:
        logger.info(msg)

    else:
        logger.debug(msg)

    return hasher.hexdigest()


def get_md5_from_checksum(checksum: str) -> str:
    """
    Get the MD5 hex string out of a checksum reported by Zenodo

    Parameters
    ----------
    checksum
        Checksum as Zenodo reports it, i.e. `"md5:<hex>"`

    Returns
    -------
    :
        The MD5 hex string

    Raises
    ------
    ValueError
        `checksum` is not an MD5 checksum

    Examples
    --------
    >>> get_md5_from_checksum("md5:b77017ecd0f93c60f84d42dcf352368e")
    'b77017ecd0f93c60f84d42dcf352368e'
    """
    algorithm, _, value = checksum.partition(":")

    if algorithm != "md5" or not value:
        msg = (
            f"Expected a checksum of the form 'md5:<hex>', received {checksum!r}. "
            "If Zenodo has started reporting a different algorithm, "
            "this is a bug in openscm-zenodo, please report it."
        )

        raise ValueError(msg)

    return value


def assert_md5_matches(name: str, *, local_md5: str, remote_checksum: str) -> None:
    """
    Check that a file's local MD5 matches the checksum Zenodo reports for it

    Parameters
    ----------
    name
        Name of the file, used in the error message

    local_md5
        MD5 checksum we computed locally

    remote_checksum
        Checksum reported by Zenodo, i.e. `"md5:<hex>"`

    Raises
    ------
    ChecksumMismatchError
        The checksums do not match

    ValueError
        `remote_checksum` is not an MD5 checksum
    """
    remote_md5 = get_md5_from_checksum(remote_checksum)

    if remote_md5 != local_md5:
        raise ChecksumMismatchError(name, local_md5=local_md5, remote_md5=remote_md5)

    logger.debug(f"Checksum for {name!r} matches ({local_md5})")
