"""
Checksum handling

Zenodo reports every file's checksum.
We use this to verify that uploads happened correctly.
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

    Enable `DEBUG` logging
    (see [`setup_logging`][openscm_zenodo.logging.setup_logging])
    to see the timing of every hash.
    Hashes which take longer than `slow_threshold_s` are reported at `INFO` level.
    so they can be reported without small file reporting making the logs very noisy.

    Parameters
    ----------
    path
        File to hash

    chunk_size
        Number of bytes to read at a time
        (avoids failures on files that don't fit in memory).

    slow_threshold_s
        Number of seconds beyond which we report the timing at `INFO` log level
        (rather than `DEBUG`).

    Returns
    -------
    :
        MD5 checksum of `path`, as a hex string
    """
    size = path.stat().st_size
    logger.debug(f"Computing MD5 for {path} ({size} bytes)")

    start = time.perf_counter()

    hasher = hashlib.md5()  # noqa: S324 # Zenodo's checksums are MD5, not our choice
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            hasher.update(chunk)

    elapsed = time.perf_counter() - start

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
        Checksum as Zenodo reports it

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
