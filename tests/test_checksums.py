"""
Tests of `openscm_zenodo.checksums`
"""

from __future__ import annotations

import hashlib

import pytest

from openscm_zenodo.checksums import (
    assert_md5_matches,
    get_file_md5,
    get_md5_from_checksum,
)
from openscm_zenodo.exceptions import ChecksumMismatchError


@pytest.fixture
def a_file(tmp_path):
    """A file with known content, plus the MD5 of that content"""
    contents = b"Some contents, which are longer than one chunk" * 100

    res = tmp_path / "a-file.txt"
    res.write_bytes(contents)

    return res, hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5


def test_get_file_md5(a_file):
    path, exp = a_file

    assert get_file_md5(path) == exp


def test_get_file_md5_chunked(a_file):
    """
    The result does not depend on how the file is read

    Files are read in chunks so that we can hash files
    which do not fit in memory.
    """
    path, exp = a_file

    assert get_file_md5(path, chunk_size=7) == exp


def test_get_file_md5_empty_file(tmp_path):
    path = tmp_path / "empty.txt"
    path.touch()

    assert get_file_md5(path) == hashlib.md5(b"").hexdigest()  # noqa: S324 # Zenodo uses md5


@pytest.mark.parametrize(
    "checksum, exp",
    (
        ("md5:d41d8cd98f00b204e9800998ecf8427e", "d41d8cd98f00b204e9800998ecf8427e"),
        ("md5:abc", "abc"),
    ),
)
def test_get_md5_from_checksum(checksum, exp):
    assert get_md5_from_checksum(checksum) == exp


@pytest.mark.parametrize(
    "checksum",
    (
        pytest.param("sha256:abc", id="not-md5"),
        pytest.param("abc", id="no-algorithm"),
        pytest.param("md5:", id="no-value"),
        pytest.param("", id="empty"),
    ),
)
def test_get_md5_from_checksum_not_md5(checksum):
    with pytest.raises(ValueError, match="md5:<hex>"):
        get_md5_from_checksum(checksum)


def test_assert_md5_matches():
    assert_md5_matches("a-file.txt", local_md5="abc", remote_checksum="md5:abc")


def test_assert_md5_matches_mismatch():
    with pytest.raises(ChecksumMismatchError) as exc_info:
        assert_md5_matches("a-file.txt", local_md5="abc", remote_checksum="md5:def")

    msg = str(exc_info.value)
    assert "a-file.txt" in msg
    assert "abc" in msg
    assert "def" in msg
    assert exc_info.value.local_md5 == "abc"
    assert exc_info.value.remote_md5 == "def"


def test_get_file_md5_logs_timing(a_file, log_messages):
    """
    Timing is logged at `DEBUG`, so hashing can be ruled in or out as a bottleneck
    """
    path, _ = a_file

    get_file_md5(path)

    assert any(f"Computing MD5 for {path}" in message for message in log_messages)

    timings = [message for message in log_messages if "MB/s" in message]
    assert timings
    # Small files should not be noisy at coarser levels
    assert all(message.record["level"].name == "DEBUG" for message in timings)


def test_get_file_md5_logs_slow_hashing(a_file, log_messages):
    """
    A hash which takes a long time is reported at `INFO`

    Setting the threshold to zero makes every hash "slow",
    which is how we check the branch without waiting for seconds to pass.
    """
    path, _ = a_file

    get_file_md5(path, slow_threshold_s=0.0)

    timings = [message for message in log_messages if "MB/s" in message]
    assert timings
    assert all(message.record["level"].name == "INFO" for message in timings)
