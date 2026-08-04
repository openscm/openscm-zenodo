"""
Tests of `openscm_zenodo.zipping`

Zipping is how a directory structure survives a system which has no
directories, so what matters is that the structure comes back and that the
same files always produce the same archive.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from openscm_zenodo.zipping import (
    ZIP_TIMESTAMP_DEFAULT,
    DeterministicZipValues,
    get_base_dir,
    zip_files,
)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A small directory tree, and the paths in it"""
    monkeypatch.chdir(tmp_path)

    paths = []
    for name, contents in (
        ("out/2024/data.nc", b"the 2024 data"),
        ("out/2025/data.nc", b"the 2025 data"),
        ("out/notes.txt", b"some notes"),
    ):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        paths.append(path)

    return paths


def names_in(archive_path):
    """Get the names inside an archive"""
    with zipfile.ZipFile(archive_path) as archive:
        return sorted(archive.namelist())


def test_zip_files_keeps_the_structure(tree, tmp_path):
    dest = zip_files(tree, Path("archive.zip"), progress=False)

    assert names_in(dest) == ["2024/data.nc", "2025/data.nc", "notes.txt"]


def test_zip_files_round_trips_the_contents(tree, tmp_path):
    dest = zip_files(tree, Path("archive.zip"), progress=False)

    with zipfile.ZipFile(dest) as archive:
        assert archive.read("2024/data.nc") == b"the 2024 data"
        assert archive.read("2025/data.nc") == b"the 2025 data"


def test_zip_files_base_dir(tree):
    """
    A higher `base_dir` keeps more of the structure
    """
    dest = zip_files(tree, Path("archive.zip"), base_dir=Path(), progress=False)

    assert names_in(dest) == ["out/2024/data.nc", "out/2025/data.nc", "out/notes.txt"]


def test_zip_files_refuses_a_path_outside_base_dir(tree, tmp_path):
    outside = Path("elsewhere.nc")
    outside.write_bytes(b"x")

    with pytest.raises(ValueError, match="is not inside"):
        zip_files(
            [*tree, outside], Path("archive.zip"), base_dir=Path("out"), progress=False
        )


def test_the_same_name_in_two_directories_is_fine_in_an_archive(tree):
    """
    This is the case zipping exists for

    `out/2024/data.nc` and `out/2025/data.nc` cannot both be uploaded as
    `data.nc`, which is a `DuplicateFileKeyError`; inside an archive they keep
    their directories and sit happily side by side.
    """
    dest = zip_files(tree, Path("archive.zip"), progress=False)

    assert "2024/data.nc" in names_in(dest)
    assert "2025/data.nc" in names_in(dest)


def test_one_file_named_twice_is_stored_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("data.nc")
    path.write_bytes(b"x")

    dest = zip_files([path, Path("./data.nc")], Path("archive.zip"), progress=False)

    assert names_in(dest) == ["data.nc"]


def test_zipping_is_deterministic(tree):
    """
    The same files give a byte-identical archive

    Without this, every re-run would upload the whole archive again: the upload
    skips files whose name and checksum already match, and a stock archive
    records each file's modification time, so its checksum changes every time.
    """
    first = zip_files(tree, Path("first.zip"), progress=False).read_bytes()

    for path in tree:
        path.touch()  # a new modification time, same contents

    second = zip_files(tree, Path("second.zip"), progress=False).read_bytes()

    assert first == second


def test_zipping_is_not_deterministic_when_asked_not_to_be(tree):
    """
    The escape hatch really does record the mtimes
    """
    dest = zip_files(tree, Path("kept.zip"), deterministic=False, progress=False)

    with zipfile.ZipFile(dest) as archive:
        assert {info.date_time for info in archive.infolist()} != {
            ZIP_TIMESTAMP_DEFAULT
        }


def test_zipping_pins_the_timestamp(tree):
    dest = zip_files(tree, Path("archive.zip"), progress=False)

    with zipfile.ZipFile(dest) as archive:
        assert {info.date_time for info in archive.infolist()} == {
            ZIP_TIMESTAMP_DEFAULT
        }


def test_zipping_with_values_of_your_own(tree):
    """
    The pinned values can be chosen, which is the point of them being a class
    """
    pinned = DeterministicZipValues(timestamp=(2001, 2, 3, 4, 5, 6), permissions=0o755)

    dest = zip_files(tree, Path("archive.zip"), deterministic=pinned, progress=False)

    with zipfile.ZipFile(dest) as archive:
        infos = archive.infolist()

    assert {info.date_time for info in infos} == {(2001, 2, 3, 4, 5, 6)}
    assert {info.external_attr >> 16 for info in infos} == {0o755}


def test_zipping_with_values_of_your_own_is_still_deterministic(tree):
    """
    Anything but `False` sorts the members, so a re-run gives the same bytes
    """
    pinned = DeterministicZipValues(timestamp=(2001, 2, 3, 4, 5, 6))

    first = zip_files(tree, Path("a.zip"), deterministic=pinned, progress=False)
    for path in tree:
        path.touch()

    second = zip_files(tree, Path("b.zip"), deterministic=pinned, progress=False)

    assert first.read_bytes() == second.read_bytes()


def test_zip_files_makes_the_destination_directory(tree):
    dest = zip_files(tree, Path("somewhere/new/archive.zip"), progress=False)

    assert dest.exists()


@pytest.mark.parametrize(
    "paths, exp",
    (
        pytest.param(["out/2024/a.nc", "out/2025/b.nc"], "out", id="two-directories"),
        pytest.param(["out/a.nc", "out/b.nc"], "out", id="one-directory"),
        pytest.param(["out/2024/a.nc"], "out/2024", id="one-file"),
        pytest.param(["a.nc", "b.nc"], ".", id="working-directory"),
    ),
)
def test_get_base_dir(paths, exp, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert get_base_dir([Path(path) for path in paths]) == Path(exp)


def test_get_base_dir_of_absolute_paths_is_absolute(tmp_path):
    """
    Only relative paths are reported relative, because only they are relative
    to the working directory in the first place
    """
    paths = [tmp_path / "out" / "2024" / "a.nc", tmp_path / "out" / "2025" / "b.nc"]

    assert get_base_dir(paths) == tmp_path / "out"


def test_get_base_dir_of_paths_above_the_working_directory(tmp_path, monkeypatch):
    """
    A relative path which climbs out is still reported the way it was given
    """
    (tmp_path / "here").mkdir()
    monkeypatch.chdir(tmp_path / "here")

    assert get_base_dir([Path("../out/a.nc"), Path("../out/b.nc")]) == Path("../out")


def test_get_base_dir_needs_something_to_do():
    with pytest.raises(ValueError, match="No paths"):
        get_base_dir([])
