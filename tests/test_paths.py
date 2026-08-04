"""
Tests of what happens to a local path on the way to Zenodo

Zenodo has no directories, so `out/2024/data.nc` lands as `data.nc`. Everything
here is about making that visible rather than surprising.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openscm_zenodo.exceptions import DuplicateFileKeyError, OpenSCMZenodoWarning
from openscm_zenodo.zenodo import (
    ZenodoClient,
    get_upload_filenames,
    warn_about_stripped_paths,
)

RECORD_ID = "1234"


@pytest.fixture
def client_and_session(no_token_in_env, make_recording_session):
    """A client whose session hands back whatever the test scripts"""

    def factory(responses=None):
        session = make_recording_session(responses)

        return ZenodoClient(token="a-token", session=session), session  # noqa: S106

    return factory


@pytest.mark.parametrize(
    "paths",
    (
        pytest.param([Path("a.nc")], id="bare-name"),
        pytest.param([Path("./a.nc")], id="explicitly-here"),
        pytest.param([Path("a.nc"), Path("b.nc")], id="several-bare-names"),
    ),
)
def test_no_warning_when_nothing_is_lost(paths, recwarn):
    warn_about_stripped_paths(paths)

    assert [w for w in recwarn if issubclass(w.category, OpenSCMZenodoWarning)] == []


def test_warns_naming_the_path_and_what_it_becomes():
    with pytest.warns(OpenSCMZenodoWarning) as caught:
        warn_about_stripped_paths([Path("out/2024/data.nc"), Path("flat.nc")])

    (warning,) = caught
    message = str(warning.message)

    assert "out/2024/data.nc will be uploaded as data.nc" in message
    # The file which loses nothing is not mentioned
    assert "flat.nc will be uploaded" not in message
    # Both ways out are in the message, so the fix is discoverable from it
    assert "warn_path_stripped=False" in message
    assert "upload_files_as_zip" in message


def test_get_upload_filenames():
    assert get_upload_filenames([Path("out/a.nc"), Path("b.nc")]) == {
        "a.nc": Path("out/a.nc"),
        "b.nc": Path("b.nc"),
    }


def test_colliding_basenames_are_an_error():
    """
    Two paths with one name have no right answer, so we refuse rather than warn

    Before this, the second silently replaced the first.
    """
    with pytest.raises(DuplicateFileKeyError) as exc_info:
        get_upload_filenames([Path("2024/data.nc"), Path("2025/data.nc")])

    message = str(exc_info.value)
    assert "2024/data.nc" in message
    assert "2025/data.nc" in message
    assert "upload_files_as_zip" in message


@pytest.mark.parametrize("method", ("upload_files", "mirror_files"))
def test_collisions_are_refused_before_any_request(
    method, client_and_session, tmp_path
):
    """
    Nothing is sent, so there is no half-done state to clean up
    """
    paths = []
    for year in ("2024", "2025"):
        path = tmp_path / year / "data.nc"
        path.parent.mkdir()
        path.write_text(year)
        paths.append(path)

    client, session = client_and_session()

    with pytest.raises(DuplicateFileKeyError):
        getattr(client, method)(RECORD_ID, paths, progress=False)

    assert session.calls == []


def successful_upload_responses(make_response, contents, *, filename="data.nc"):
    """
    Script a listing of an empty draft, then one successful upload
    """
    md5 = hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5
    files_url = f"https://zenodo.org/api/records/{RECORD_ID}/draft/files"

    return [
        # The listing the diff starts from
        make_response(json_body={"entries": []}),
        # Initialise, content, commit
        make_response(),
        make_response(),
        make_response(
            json_body={
                "key": filename,
                "size": len(contents),
                "checksum": f"md5:{md5}",
                "status": "completed",
                "links": {"content": f"{files_url}/{filename}/content"},
            }
        ),
    ]


@pytest.mark.parametrize("method", ("upload_files", "mirror_files"))
@pytest.mark.parametrize("warn_path_stripped", (True, False))
def test_warn_path_stripped(  # noqa: PLR0913
    method, warn_path_stripped, client_and_session, make_response, tmp_path, recwarn
):
    contents = b"some data"
    path = tmp_path / "data.nc"
    path.write_bytes(contents)

    client, _ = client_and_session(successful_upload_responses(make_response, contents))

    getattr(client, method)(
        RECORD_ID, [path], progress=False, warn_path_stripped=warn_path_stripped
    )

    ours = [w for w in recwarn if issubclass(w.category, OpenSCMZenodoWarning)]
    assert len(ours) == (1 if warn_path_stripped else 0)


def test_the_warning_is_raised_once_for_the_whole_batch(
    client_and_session, make_response, tmp_path, recwarn
):
    """
    Not once per file from inside a worker thread

    A warning raised in a thread is attributed to that thread's stack, which is
    ours, so it would point at our code rather than at the caller.
    """
    contents = b"some data"
    paths = []
    for name in ("a.nc", "b.nc"):
        path = tmp_path / name
        path.write_bytes(contents)
        paths.append(path)

    responses = successful_upload_responses(make_response, contents, filename="a.nc")
    responses.extend(
        successful_upload_responses(make_response, contents, filename="b.nc")[1:]
    )
    client, _ = client_and_session(responses)

    client.upload_files(RECORD_ID, paths, n_threads=1, progress=False)

    ours = [w for w in recwarn if issubclass(w.category, OpenSCMZenodoWarning)]
    assert len(ours) == 1
    assert "a.nc" in str(ours[0].message)
    assert "b.nc" in str(ours[0].message)
    assert ours[0].filename == __file__
