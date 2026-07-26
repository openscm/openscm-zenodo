"""
Show what the progress bars look like, without touching Zenodo

This drives the real `ZenodoClient.upload_files` path.
The only thing faked is the session, which sleeps instead of
sending anything, so what you see is what an upload looks like.

Run it with:

    uv run python scripts/demo-progress-bars.py

Add `--with-logging` to also turn logging on,
which shows that log lines written while a transfer is in flight
go through `tqdm.write` and so do not break the bars.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

import requests

from openscm_zenodo.logging import setup_logging
from openscm_zenodo.zenodo import ZenodoClient

N_FILES = 8
"""Number of files to pretend to upload"""

N_THREADS = 3
"""Number of files to pretend to upload at once"""

FILE_SIZE = 5 * 1024**2
"""Size of each pretend file, in bytes"""

READ_SIZE = 128 * 1024
"""Number of bytes to pretend to send at a time"""

CHUNK_PAUSE_S = 0.05
"""How long to pretend each chunk takes, i.e. how fast the per-file bars move"""


class SleepySession(requests.Session):
    """A session which answers like Zenodo would, slowly, without any network"""

    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, bytes] = {}

    def request(  # type: ignore[override]
        self, method: str, url: str, **kwargs: object
    ) -> requests.models.Response:
        """Answer a request, taking a realistic amount of time about it"""
        path = url.split("/draft", 1)[-1]
        name = path.removeprefix("/files/").split("/")[0]

        if method == "PUT" and path.endswith("/content"):
            # Read in chunks so the per-file bar actually moves,
            # the same way `requests` would stream the real thing
            handle = kwargs["data"]
            contents = b""
            while chunk := handle.read(READ_SIZE):  # type: ignore[attr-defined]
                contents += chunk
                time.sleep(CHUNK_PAUSE_S)

            self.files[name] = contents
            body: object = {"key": name, "status": "pending"}

        elif method == "POST" and path.endswith("/commit"):
            contents = self.files[name]
            md5 = hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5
            body = {
                "key": name,
                "size": len(contents),
                "checksum": f"md5:{md5}",
                "status": "completed",
            }

        else:
            time.sleep(0.1)
            body = {"entries": []}

        response = requests.models.Response()
        response.status_code = 200
        response.url = url
        response._content = json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"

        return response


def main() -> None:
    """Run the demonstration"""
    with_logging = "--with-logging" in sys.argv
    setup_logging(enable=with_logging, logging_level="INFO")

    print(
        f"Pretending to upload {N_FILES} files of "
        f"{FILE_SIZE / 1024**2:.0f}MB each, {N_THREADS} at a time.\n"
        "\n"
        "  - the overall bar counting files is at the top, and stays\n"
        "  - each file in flight gets its own bar underneath,\n"
        "    which disappears as soon as that file finishes\n"
    )
    if not with_logging:
        print("Re-run with --with-logging to see log lines and bars coexisting.\n")

    with tempfile.TemporaryDirectory() as tmp_dir:
        paths = []
        for i in range(N_FILES):
            path = Path(tmp_dir) / f"some-dataset-file-{i:02d}.nc"
            path.write_bytes(bytes(FILE_SIZE))
            paths.append(path)

        client = ZenodoClient(token="not-a-real-token", session=SleepySession())  # noqa: S106

        client.upload_files("1234", paths, n_threads=N_THREADS)

    print("\nDone: only the overall bar is left behind.")


if __name__ == "__main__":
    main()
