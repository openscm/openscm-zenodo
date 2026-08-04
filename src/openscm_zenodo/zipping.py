"""
Zipping files, so that a directory structure survives being uploaded

Zenodo has no directories: a file is identified by its name alone, so
`out/2024/data.nc` and `out/2025/data.nc` cannot both be uploaded as they are.
Zenodo's own advice is to upload an archive, which its web interface then
displays the contents of. This builds that archive.
"""

from __future__ import annotations

import os.path
import zipfile
from collections.abc import Collection
from pathlib import Path

from attrs import define
from loguru import logger

from openscm_zenodo.progress import get_files_progress_bar

ZIP_TIMESTAMP_DEFAULT = (2026, 1, 1, 0, 0, 0)
"""
Timestamp given to every member of a deterministic archive

Any fixed timestamp does the job. Zip cannot represent anything before
1980-01-01, and a `date_time` with a zero month or day is not writeable at all,
so there is no "unset" to use instead.
"""

ZIP_PERMISSIONS_DEFAULT = 0o644
"""
Permissions given to every member of a deterministic archive

This value is owner read and write, everyone else read only,
i.e. what a data file usually extracts as.
Nothing is marked executable.
"""

ZIP_CREATE_SYSTEM_DEFAULT = 3
"""
Host system recorded for every member of a deterministic archive

3 is Unix. Zip records which platform wrote each member, and it is what decides
whether the permissions above are read back at all, so it has to be pinned
alongside them or the archive changes with the machine that built it.
"""


@define
class DeterministicZipValues:
    """
    The metadata pinned on every member of a deterministic archive

    Zip stores each member's modification time and permissions, so an archive
    built from the same files twice differs the second time. Pinning these is
    what makes zip-then-upload idempotent, see
    [`zip_files`][openscm_zenodo.zipping.zip_files].
    """

    timestamp: tuple[int, int, int, int, int, int] = ZIP_TIMESTAMP_DEFAULT
    """Modification time to record, as `(year, month, day, hour, minute, second)`"""

    permissions: int = ZIP_PERMISSIONS_DEFAULT
    """Unix permission bits to record"""

    create_system: int = ZIP_CREATE_SYSTEM_DEFAULT
    """Host system to record"""

    def apply_to(self, info: zipfile.ZipInfo) -> zipfile.ZipInfo:
        """
        Pin these values on an archive member

        Parameters
        ----------
        info
            Member to pin them on, modified in place

        Returns
        -------
        :
            `info`
        """
        info.date_time = self.timestamp
        info.create_system = self.create_system
        info.external_attr = self.permissions << 16

        return info


def get_base_dir(paths: Collection[Path]) -> Path:
    """
    Get the directory the paths in an archive should be relative to

    Parameters
    ----------
    paths
        Files which are going to be archived

    Returns
    -------
    :
        The deepest directory which contains all of `paths`, relative if every
        path was given relative, absolute otherwise

    Raises
    ------
    ValueError
        `paths` is empty, or the paths have nothing in common
        (on Windows, files on different drives)

    Examples
    --------
    >>> get_base_dir([Path("out/2024/a.nc"), Path("out/2025/b.nc")])
    PosixPath('out')
    """
    if not paths:
        msg = "No paths to archive"
        raise ValueError(msg)

    try:
        common = os.path.commonpath([path.absolute() for path in paths])

    except ValueError as exc:
        msg = f"The paths to archive have no common directory: {sorted(paths)}"
        raise ValueError(msg) from exc

    res = Path(common)
    # `commonpath` of one file, or of one file named several ways, is the file
    # itself, which is not a directory to be relative to
    if any(path.absolute() == res for path in paths):
        res = res.parent

    if any(path.is_absolute() for path in paths):
        return res

    # Every path was given relative, which means relative to the working
    # directory, so reporting the answer that way is what the caller wrote
    # rather than a guess. The fallback is for Windows, where a drive-relative
    # path like `D:out` is not absolute but need not be under the cwd either.
    try:
        return res.relative_to(Path.cwd())

    except ValueError:
        return res


def get_archive_names(
    paths: Collection[Path], base_dir: Path | None = None
) -> dict[Path, Path]:
    """
    Work out where each path goes inside an archive

    Parameters
    ----------
    paths
        Files which are going to be archived

    base_dir
        Directory the paths inside the archive are relative to.

        If not supplied, we use
        [`get_base_dir`][openscm_zenodo.zipping.get_base_dir].

    Returns
    -------
    :
        Where each path goes inside the archive

    Raises
    ------
    ValueError
        A path is not inside `base_dir`.

        Storing it would mean either an absolute path or one which climbs out
        of the archive with `..`, and neither is something to do quietly.

    Notes
    -----
    There is no collision to guard against here, unlike
    [`get_upload_filenames`][openscm_zenodo.zenodo.get_upload_filenames]:
    every path keeps its directories, so two different files under one
    `base_dir` always land in different places.

    It is also worth noting that one file named twice (which would be weird)
    is only stored once, so we can handle this oddity.

    Examples
    --------
    >>> names = get_archive_names([Path("out/2024/a.nc"), Path("out/2025/b.nc")])
    >>> sorted(str(name) for name in names.values())
    ['2024/a.nc', '2025/b.nc']
    """
    if base_dir is None:
        base_dir = get_base_dir(paths)

    res: dict[Path, Path] = {}

    for path in paths:
        try:
            res[path] = path.absolute().relative_to(base_dir.absolute())

        except ValueError as exc:
            msg = (
                f"{str(path)!r} is not inside {str(base_dir)!r}, "
                "so it has no place in the archive. "
                "Pass a `base_dir` which contains every path."
            )

            raise ValueError(msg) from exc

    return res


def zip_files(  # noqa: PLR0913
    paths: Collection[Path],
    dest: Path,
    *,
    base_dir: Path | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    deterministic: bool | DeterministicZipValues = True,
    progress: bool = True,
) -> Path:
    """
    Zip files, keeping the structure they have on disk

    Parameters
    ----------
    paths
        Files to archive

    dest
        Archive to write

    base_dir
        Directory the paths inside the archive are relative to.

        If not supplied, the deepest directory which contains all of `paths` is used.
        For example, `["out/2024/a.nc", "out/2025/b.nc"]`
        would be stored as `2024/a.nc` and `2025/b.nc`.

        Pass `base_dir` to keep a higher prefix.

    compression
        Compression to use, see
        [`zipfile`](https://docs.python.org/3/library/zipfile.html)

    deterministic
        Should the same files always produce the same archive?

        A stock archive records each file's modification time, so re-zipping
        the same files gives an archive with a different checksum every time.
        Uploads skip files whose name and checksum already match, so that would
        make every re-run upload the whole archive again. With this on, members
        are sorted and their metadata is pinned.

        Pass a
        [`DeterministicZipValues`][openscm_zenodo.zipping.DeterministicZipValues]
        to choose what they are pinned to, `False` to record the real ones.

    progress
        Should a progress bar be shown?

    Returns
    -------
    :
        `dest`

    Raises
    ------
    DuplicateFileKeyError
        Several paths would land in the same place inside the archive

    ValueError
        A path is not inside `base_dir`, or there is nothing to archive
    """
    names = get_archive_names(paths, base_dir=base_dir)

    pinned: DeterministicZipValues | None
    if deterministic is False:
        pinned = None

    elif deterministic is True:
        pinned = DeterministicZipValues()

    else:
        pinned = deterministic

    if pinned is None:
        ordered = list(names.items())

    else:
        ordered = sorted(names.items(), key=lambda item: str(item[1]))

    logger.info(f"Zipping {len(ordered)} file(s) into {dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    with (
        zipfile.ZipFile(dest, "w", compression=compression) as archive,
        get_files_progress_bar(
            desc=f"Zipping {dest.name}", total=len(ordered), progress=progress
        ) as bar,
    ):
        for path, name in ordered:
            if pinned is None:
                archive.write(path, arcname=name)

            else:
                info = pinned.apply_to(zipfile.ZipInfo(str(name)))
                info.compress_type = compression
                archive.writestr(info, path.read_bytes())

            bar.update(1)

    logger.info(f"Wrote {dest}")

    return dest
