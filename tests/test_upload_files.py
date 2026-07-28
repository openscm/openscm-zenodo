"""
Tests of the file-writing methods which act on many files at once

The behaviour that matters most here is the difference between the two:
`upload_files` never deletes, `mirror_files` does.
"""

from __future__ import annotations

import hashlib

import pytest

from openscm_zenodo.exceptions import RecordNotFoundError, ZenodoHTTPError
from openscm_zenodo.zenodo import ZenodoClient, ZenodoDomain

RECORD_ID = "1234"
DRAFT_FILES_URL = f"https://zenodo.org/api/records/{RECORD_ID}/draft/files"


def md5_of(contents):
    """Get the MD5 of some contents, the way Zenodo reports it"""
    return hashlib.md5(contents).hexdigest()  # noqa: S324 # Zenodo uses md5


@pytest.fixture
def local_files(tmp_path):
    """Two local files, keyed by name, with their contents"""
    res = {}
    for name, contents in (("a.txt", b"contents of a"), ("b.txt", b"contents of b")):
        path = tmp_path / name
        path.write_bytes(contents)
        res[name] = path

    return res


class FakeZenodo:
    """
    A stand-in for a Zenodo draft, which answers the requests we make of it

    Working out what to upload and what to delete
    is a conversation across several endpoints,
    so this keeps the state that conversation depends on
    rather than every test having to script a list of responses.
    """

    def __init__(self, make_response, files=None):
        self.make_response = make_response
        # name -> contents
        self.files = dict(files) if files is not None else {}
        self.pending = {}
        self.calls = []

    def entry(self, name):
        """Build Zenodo's description of one file"""
        contents = self.files[name]

        return {
            "key": name,
            "size": len(contents),
            "checksum": f"md5:{md5_of(contents)}",
            "status": "completed",
            "links": {"content": f"{DRAFT_FILES_URL}/{name}/content"},
        }

    def request(self, method, url, **kwargs):  # noqa: PLR0911
        """Answer a request the way Zenodo would"""
        self.calls.append((method, url))

        prefix = f"https://zenodo.org/api/records/{RECORD_ID}"
        if not url.startswith(f"{prefix}/draft"):
            # This fake is an unpublished draft,
            # so the published endpoints have nothing at them
            return self.make_response(
                status_code=404, json_body={"message": "Not found."}
            )

        path = url[len(f"{prefix}/draft") :]

        if method == "GET" and path == "":
            # An unpublished draft, which is what `is_draft` asks about
            return self.make_response(
                json_body={
                    "id": int(RECORD_ID),
                    "is_draft": True,
                    "is_published": False,
                }
            )

        if method == "GET" and path == "/files":
            return self.make_response(
                json_body={"entries": [self.entry(name) for name in self.files]}
            )

        if method == "POST" and path == "/files":
            for entry in kwargs["json"]:
                if entry["key"] in self.files:
                    return self.make_response(
                        status_code=400, json_body={"message": "already exists"}
                    )
                self.pending[entry["key"]] = b""

            return self.make_response(json_body={"entries": kwargs["json"]})

        name = path.removeprefix("/files/").split("/")[0]

        if method == "PUT" and path.endswith("/content"):
            self.pending[name] = kwargs["data"].read()

            return self.make_response(json_body={"key": name, "status": "pending"})

        if method == "POST" and path.endswith("/commit"):
            self.files[name] = self.pending.pop(name)

            return self.make_response(json_body=self.entry(name))

        if method == "DELETE":
            self.files.pop(name, None)
            self.pending.pop(name, None)

            return self.make_response(status_code=204)

        msg = f"Unexpected request: {method} {url}"
        raise AssertionError(msg)


@pytest.fixture
def fake_zenodo(make_response, make_recording_session):
    """A client whose session is answered by a `FakeZenodo`"""

    def factory(files=None):
        zenodo = FakeZenodo(make_response, files=files)
        session = make_recording_session()
        session.request = zenodo.request

        client = ZenodoClient(token="a-token", session=session)  # noqa: S106

        return client, zenodo

    return factory


def test_list_files(fake_zenodo):
    client, _ = fake_zenodo({"a.txt": b"contents of a"})

    files = client.list_files(RECORD_ID)

    assert list(files) == ["a.txt"]
    assert files["a.txt"].md5 == md5_of(b"contents of a")
    assert files["a.txt"].size == len(b"contents of a")


def test_is_draft(fake_zenodo):
    """
    No published record but a draft we can see means it is a draft
    """
    client, zenodo = fake_zenodo()

    assert client.is_draft(RECORD_ID) is True

    assert [url for _, url in zenodo.calls] == [
        f"https://zenodo.org/api/records/{RECORD_ID}",
        f"https://zenodo.org/api/records/{RECORD_ID}/draft",
    ]


def test_is_draft_published(no_token_in_env, make_recording_session, make_response):
    """
    Finding the published record answers the question on its own

    A published record may also have a draft of its own,
    for correcting its metadata, but its files are locked either way,
    so there is no reason to go looking for one.
    """
    session = make_recording_session([make_response(json_body={"id": 1234})])
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    assert client.is_draft(RECORD_ID) is False
    assert len(session.calls) == 1


@pytest.mark.parametrize("status_code", (403, 404))
def test_is_draft_record_not_found_with_a_token(
    no_token_in_env, make_recording_session, make_response, status_code
):
    """
    Nothing published and no draft we can see means we say we cannot find it
    """
    session = make_recording_session(
        [make_response(status_code=status_code) for _ in range(2)]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(RecordNotFoundError) as exc_info:
        client.is_draft(RECORD_ID)

    msg = str(exc_info.value)
    assert RECORD_ID in msg
    assert "even using the token you supplied" in msg
    assert exc_info.value.token_source == "the token you supplied"  # noqa: S105 # a description, not a token


def test_is_draft_record_not_found_without_a_token(
    no_token_in_env, make_recording_session, make_response
):
    """
    Without a token the record may simply not be visible, so we say so

    Only published records can be seen without one,
    so there is no point looking for a draft, and we do not.
    """
    session = make_recording_session([make_response(status_code=404)])
    client = ZenodoClient(session=session)

    with pytest.raises(RecordNotFoundError) as exc_info:
        client.is_draft(RECORD_ID)

    msg = str(exc_info.value)
    assert RECORD_ID in msg
    assert "no token" in msg
    assert "supply a token" in msg
    assert exc_info.value.token_source is None
    # No point asking about a draft we could not see anyway
    assert len(session.calls) == 1


def test_is_draft_record_not_found_names_the_token_and_the_domain(
    no_token_in_env, make_recording_session, make_response, monkeypatch
):
    """
    The message pairs which token we used with which domain we used it on

    A production environment variable against the sandbox, or the other way
    round, is the usual reason a record which is definitely there is not found,
    and seeing the two side by side is what gives that away.
    """
    monkeypatch.setenv("ZENODO_TOKEN", "a-production-token")

    session = make_recording_session([make_response(status_code=404) for _ in range(2)])
    client = ZenodoClient(zenodo_domain=ZenodoDomain.sandbox, session=session)

    with pytest.raises(RecordNotFoundError) as exc_info:
        client.is_draft(RECORD_ID)

    msg = str(exc_info.value)
    assert "the token from $ZENODO_TOKEN" in msg
    assert "https://sandbox.zenodo.org" in msg
    # The token itself is never in there
    assert "a-production-token" not in msg


def test_token_source_is_on_the_client(no_token_in_env, monkeypatch):
    """
    Which token was used is worth being able to look at, and safe to show
    """
    monkeypatch.setenv("ZENODO_SANDBOX_TOKEN", "a-sandbox-token")

    client = ZenodoClient(zenodo_domain=ZenodoDomain.sandbox)

    assert client.token_source == "the token from $ZENODO_SANDBOX_TOKEN"  # noqa: S105 # a description, not a token
    assert "a-sandbox-token" not in repr(client)
    assert "$ZENODO_SANDBOX_TOKEN" in repr(client)


def test_is_draft_does_not_swallow_other_failures(
    no_token_in_env, make_recording_session, make_response
):
    session = make_recording_session(
        [make_response(status_code=500, json_body={"message": "Boom"})]
    )
    client = ZenodoClient(token="a-token", session=session)  # noqa: S106

    with pytest.raises(ZenodoHTTPError, match="Boom"):
        client.is_draft(RECORD_ID)


def test_list_files_asks_whether_the_record_is_a_draft(fake_zenodo):
    """
    The caller does not say which, because the ID already decides it
    """
    client, zenodo = fake_zenodo({"a.txt": b"contents of a"})

    client.list_files(RECORD_ID)

    assert [url for _, url in zenodo.calls] == [
        f"https://zenodo.org/api/records/{RECORD_ID}",
        f"https://zenodo.org/api/records/{RECORD_ID}/draft",
        f"https://zenodo.org/api/records/{RECORD_ID}/draft/files",
    ]


def test_list_files_published(no_token_in_env, make_recording_session, make_response):
    """
    A published record is found first time, so its files cost two calls
    """
    session = make_recording_session(
        [
            make_response(json_body={"id": 1234}),
            make_response(json_body={"entries": []}),
        ]
    )
    client = ZenodoClient(session=session)

    client.list_files(RECORD_ID)

    assert [call["url"] for call in session.calls] == [
        f"https://zenodo.org/api/records/{RECORD_ID}",
        f"https://zenodo.org/api/records/{RECORD_ID}/files",
    ]
    assert all("Authorization" not in call["headers"] for call in session.calls)


def test_list_files_record_not_found(
    no_token_in_env, make_recording_session, make_response
):
    session = make_recording_session([make_response(status_code=404)])
    client = ZenodoClient(session=session)

    with pytest.raises(RecordNotFoundError, match="check the record ID"):
        client.list_files(RECORD_ID)


def test_upload_files(fake_zenodo, local_files):
    client, zenodo = fake_zenodo()

    files = client.upload_files(RECORD_ID, list(local_files.values()), n_threads=1)

    assert sorted(zenodo.files) == ["a.txt", "b.txt"]
    assert sorted(files) == ["a.txt", "b.txt"]
    assert files["a.txt"].md5 == md5_of(b"contents of a")


def test_upload_files_skips_unchanged(fake_zenodo, local_files):
    """
    A file which is already there with the same contents is not uploaded again

    This is what makes re-running after a partial failure cheap,
    and it is unconditional because the result is identical either way.
    """
    client, zenodo = fake_zenodo({"a.txt": b"contents of a"})

    client.upload_files(RECORD_ID, list(local_files.values()), n_threads=1)

    uploaded = [
        url for method, url in zenodo.calls if method == "PUT" and "content" in url
    ]
    assert len(uploaded) == 1
    assert uploaded[0].endswith("/b.txt/content")


def test_upload_files_re_uploads_changed(fake_zenodo, local_files):
    client, zenodo = fake_zenodo({"a.txt": b"something else entirely"})

    files = client.upload_files(RECORD_ID, [local_files["a.txt"]], n_threads=1)

    assert zenodo.files["a.txt"] == b"contents of a"
    assert files["a.txt"].md5 == md5_of(b"contents of a")


def test_upload_files_never_deletes(fake_zenodo, local_files):
    """
    The single most important behavioural difference in the library
    """
    client, zenodo = fake_zenodo({"already-there.txt": b"leave me alone"})

    files = client.upload_files(RECORD_ID, [local_files["a.txt"]], n_threads=1)

    assert sorted(zenodo.files) == ["a.txt", "already-there.txt"]
    # The returned listing describes the draft, not just what we touched
    assert sorted(files) == ["a.txt", "already-there.txt"]
    assert not [method for method, _ in zenodo.calls if method == "DELETE"]


def test_mirror_files_deletes(fake_zenodo, local_files):
    """
    The other half of that difference
    """
    client, zenodo = fake_zenodo({"stale.txt": b"delete me"})

    files = client.mirror_files(RECORD_ID, [local_files["a.txt"]], n_threads=1)

    assert list(zenodo.files) == ["a.txt"]
    assert list(files) == ["a.txt"]


def test_mirror_files_deletes_before_uploading(fake_zenodo, local_files):
    """
    Renaming a large file should not need room for both copies at once
    """
    client, zenodo = fake_zenodo({"stale.txt": b"delete me"})

    client.mirror_files(RECORD_ID, [local_files["a.txt"]], n_threads=1)

    methods = [method for method, _ in zenodo.calls]
    assert methods.index("DELETE") < methods.index("PUT")


def test_mirror_files_keeps_unchanged(fake_zenodo, local_files):
    client, zenodo = fake_zenodo({"a.txt": b"contents of a"})

    files = client.mirror_files(RECORD_ID, [local_files["a.txt"]], n_threads=1)

    assert list(files) == ["a.txt"]
    assert not [method for method, _ in zenodo.calls if method in ("PUT", "DELETE")]


def test_mirror_files_empty_clears_the_draft(fake_zenodo):
    client, zenodo = fake_zenodo({"a.txt": b"a", "b.txt": b"b"})

    files = client.mirror_files(RECORD_ID, [], n_threads=1)

    assert zenodo.files == {}
    assert files == {}


def test_upload_files_in_parallel(fake_zenodo, local_files):
    client, zenodo = fake_zenodo()

    client.upload_files(RECORD_ID, list(local_files.values()), n_threads=2)

    assert sorted(zenodo.files) == ["a.txt", "b.txt"]


def test_upload_files_nothing_to_do(fake_zenodo, local_files):
    client, zenodo = fake_zenodo({"a.txt": b"contents of a", "b.txt": b"contents of b"})

    client.upload_files(RECORD_ID, list(local_files.values()), n_threads=1)

    # Only the listing, nothing else
    assert [method for method, _ in zenodo.calls] == ["GET"]


def test_delete_files(fake_zenodo):
    client, zenodo = fake_zenodo({"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"})

    client.delete_files(RECORD_ID, ["a.txt", "c.txt"])

    assert list(zenodo.files) == ["b.txt"]


def test_delete_files_nothing_to_delete(fake_zenodo):
    client, zenodo = fake_zenodo({"a.txt": b"a"})

    client.delete_files(RECORD_ID, [])

    assert zenodo.calls == []


def test_delete_all_files(fake_zenodo):
    client, zenodo = fake_zenodo({"a.txt": b"a", "b.txt": b"b"})

    client.delete_all_files(RECORD_ID)

    assert zenodo.files == {}


def test_diff_files(fake_zenodo, local_files):
    client, _ = fake_zenodo({"a.txt": b"contents of a", "stale.txt": b"stale"})

    diff = client._diff_files(RECORD_ID, list(local_files.values()))

    assert diff.unchanged == (local_files["a.txt"],)
    assert list(diff.to_upload) == [local_files["b.txt"]]
    assert diff.to_delete == ("stale.txt",)
    # The checksum is calculated once, so the upload does not have to do it again
    assert diff.to_upload[local_files["b.txt"]] == md5_of(b"contents of b")


def test_diff_files_ignores_local_directories(fake_zenodo, tmp_path):
    """
    Zenodo has no directories, so a nested file is compared under its basename
    """
    nested = tmp_path / "outputs" / "2024"
    nested.mkdir(parents=True)
    path = nested / "a.txt"
    path.write_bytes(b"contents of a")

    client, _ = fake_zenodo({"a.txt": b"contents of a"})

    diff = client._diff_files(RECORD_ID, [path])

    assert diff.unchanged == (path,)
    assert diff.to_delete == ()


def test_upload_files_surfaces_failures(fake_zenodo, local_files, monkeypatch):
    """
    A failure part way through does not abandon the other files
    """
    client, zenodo = fake_zenodo()

    real_upload_file = ZenodoClient.upload_file

    def upload_file(self, record_id, path, **kwargs):
        if path.name == "a.txt":
            msg = "boom"
            raise ValueError(msg)

        return real_upload_file(self, record_id, path, **kwargs)

    # The client is a slots class, so this has to be patched on the class
    monkeypatch.setattr(ZenodoClient, "upload_file", upload_file)

    with pytest.raises(ValueError, match="boom"):
        client.upload_files(RECORD_ID, list(local_files.values()), n_threads=1)

    # The file which could be uploaded still was
    assert list(zenodo.files) == ["b.txt"]
