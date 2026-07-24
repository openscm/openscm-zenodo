# openscm-zenodo upgrade plan

Status: proposed (branch `upgrades`)
Author: drafted with Claude, 2026-07-23

## Summary

Rebuild `openscm-zenodo` on Zenodo's native **InvenioRDM records API**
(`/api/records/...`) and drop everything tied to the legacy Deposit API. Zenodo
migrated its platform to InvenioRDM (late 2023); `/api/deposit/depositions/...`
is now a backward-compatibility shim in maintenance mode. We move fully to the
native API in a **single breaking release** — no dual-path, no compatibility
layer, no deprecation window.

This is a deliberate breaking change. We take the opportunity to:

1. **Redesign the public API and its names** around InvenioRDM concepts
   (records, drafts, versions, the init→content→commit file flow) so the code
   reads the way the API works.
2. **Add the robustness the old code lacked**: retry/backoff, a shared session,
   post-upload checksum verification, real exceptions.
3. **Add first-class file sync** (upload only what changed) and **clean-slate /
   file-reusing new versions**, both of which the new API makes natural.
4. **Remove the CLI entirely** — the library becomes the only public surface.

The one hard constraint to design around: InvenioRDM uses a **different metadata
schema** from the legacy deposit form (see Part 6). That is the single biggest
piece of work and the most visible break for downstream users.

---

## Part 1 — Target public API

`openscm_zenodo/zenodo.py` is rewritten around the InvenioRDM draft lifecycle.
Names change to match: a **record** is identified by a `record_id`; the editable
object is its **draft**; a **new version** is a fresh draft with a new id.
"Deposition" terminology is retired.

### 1.1 The client

Rename `ZenodoInteractor` → **`ZenodoClient`**.

```python
@define
class ZenodoClient:
    token: Optional[str] = field(
        factory=lambda: os.environ.get("ZENODO_TOKEN"),
        repr=lambda v: "***",
    )
    zenodo_domain: Union[str, ZenodoDomain] = ZenodoDomain.production
    timeout: int = 10
    timeout_upload: int = 60 * 60

    # robustness config (Part 2)
    max_retries: int = 5
    backoff_factor: float = 1.0
    retry_status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)

    # lazily-built requests.Session with the retry adapter mounted
    _session: requests.Session = field(init=False, factory=_build_session)
```

Authentication moves to the `Authorization: Bearer <token>` header (InvenioRDM's
preferred scheme) instead of the `?access_token=` query param. Token defaults
from `ZENODO_TOKEN`.

### 1.2 Method map (old → new)

| Old (`ZenodoInteractor`) | New (`ZenodoClient`) | Notes |
|---|---|---|
| `get_record` | `get_record(record_id)` | `GET /api/records/{id}` |
| `get_deposition` | `get_draft(record_id)` | `GET /api/records/{id}/draft` |
| — | `create_record(metadata)` → `record_id` | `POST /api/records` (brand-new draft) |
| `create_new_version_from_latest` / `get_draft_deposition_id` | `new_version(record_id, *, import_files=False)` → `record_id` | `POST /api/records/{id}/versions` (empty by default) |
| — | `import_files(record_id)` | `POST .../draft/actions/files-import` |
| `get_latest_deposition_id` | `get_latest_version_id(record_id)` | via `versions` / `links.latest` |
| `get_concept_id` | `get_parent_id(record_id)` | InvenioRDM "parent" id (all-versions id) |
| `update_metadata` | `update_metadata(record_id, metadata)` | `PUT /api/records/{id}/draft` |
| `get_metadata` | `get_metadata(record_id, *, user_controlled_only=False)` | new schema (Part 6) |
| `publish` | `publish(record_id)` | `POST .../draft/actions/publish` |
| `upload_file_to_bucket_url` | `upload_file(record_id, path, *, verify_checksum=True)` | init→content→commit (Part 2) |
| `upload_files` | `upload_files(record_id, paths, *, n_threads=4, verify_checksum=True)` | |
| — | `sync_files(record_id, paths, *, delete_extraneous=False, n_threads=4)` | Part 3 |
| `remove_files` | `delete_files(record_id, filenames)` | delete by **name** |
| `remove_all_files` | `delete_all_files(record_id)` | |
| `remove_file_id` / `remove_files_by_id` | removed | no numeric file ids in the new API |
| `get_bucket_url` | removed | no bucket in the new flow |
| `get_bibtex_entry` | `get_bibtex(record_id)` | export via `Accept` header (verify) |
| — | `list_files(record_id, *, draft=True)` → `dict[str, str]` | `GET .../files` — draft or published record (Parts 3, 5) |
| — | `download_file(record_id, filename, dest, *, draft=False, ...)` → `Path` | `GET .../files/{name}/content` (Part 5) |
| — | `download_files(record_id, dest_dir, *, filenames=None, draft=False, ...)` → `list[Path]` | Part 5 |
| — | `reserve_doi(record_id)` → `str` | `POST .../draft/pids/doi` (Part 7) |
| `get_response` | `_request(...)` (private) | routes through the session |

### 1.3 Module-level helpers

Keep a small functional surface for common one-shot tasks; rename for clarity:

```python
def retrieve_metadata(record_id, client=None, *, user_controlled_only=False) -> Metadata
def retrieve_bibtex(record_id, client=None) -> str          # was retrieve_bibtex_entry
def retrieve_files(                                          # download (Part 5)
    record_id, dest_dir, client=None, *, draft=False, filenames=None, ...
) -> list[Path]
def create_new_version(                                     # high-level convenience
    record_id,
    client,
    *,
    metadata=None,
    files=None,
    files_mode: FilesMode = FilesMode.replace,
    publish=False,
    n_threads=4,
) -> str                                                    # new version's record_id
def get_reserved_doi(record_response) -> str                # reads pids.doi
```

`FilesMode` is the single knob for how a new version treats files (Part 4):
`replace` (default), `inherit`, `sync`. (We use `inherit` rather than `import`
because `import` is a reserved keyword — `FilesMode.import` is a syntax error;
`inherit` reads cleanly and describes carrying the previous version's files
forward.)

---

## Part 2 — File upload: the commit flow + robustness

InvenioRDM uploads are a three-step, explicitly-committed flow. This replaces the
single bucket `PUT`. The three steps become one `upload_file` method:

1. **Init** — `POST /api/records/{id}/draft/files` with `[{"key": filename}]`.
2. **Content** — `PUT /api/records/{id}/draft/files/{filename}/content`,
   streaming the bytes with the existing `tqdm.utils.CallbackIOWrapper` progress
   bar and `timeout_upload`.
3. **Commit** — `POST /api/records/{id}/draft/files/{filename}/commit`. The
   response reports the server-computed checksum (`"checksum": "md5:<hex>"`).

Robustness, integrated here and in the client:

- **Shared session + retry adapter.** All requests go through `self._session`
  with an `HTTPAdapter` mounting `urllib3.util.retry.Retry`
  (`total=max_retries`, `backoff_factor`, `status_forcelist=retry_status_forcelist`,
  `respect_retry_after_header=True`, all methods). Gives status-based retry and
  honours Zenodo's `Retry-After` on 429s. Also fixes the "weirdly flaky"
  parallelism that forced serial file deletes — re-enable `n_threads` there.
- **Upload retry.** The content `PUT` streams a consumed, tqdm-wrapped file
  handle that `urllib3` cannot replay, so wrap `upload_file` in a `tenacity`
  retry that re-opens the file and resets the progress bar per attempt. (We use
  `tenacity`, not `httpx`: `httpx` is a client swap, not a retry library, and its
  built-in retry doesn't cover HTTP status codes anyway. `urllib3.Retry` handles
  ordinary requests; `tenacity` handles the streaming upload's re-open case.)
- **Checksum verification.** Compute the local file's MD5 while streaming and
  compare against the checksum in the **commit** response. On mismatch raise
  `ChecksumMismatchError`, which is also in `tenacity`'s retry set so a corrupt
  transfer is retried before it fails. `verify_checksum=True` by default;
  opt-out for speed.
- **MD5 timing logs.** MD5 hashing is CPU-bound and, for large files, can be a
  non-trivial share of an upload/download and is easy to mistake for slow I/O.
  So the single shared MD5 helper (`_md5_hex` / the streaming variant, reused by
  upload here, `sync_files` in Part 3, and download in Part 5) logs its own
  timing, gated behind logging levels so it is silent by default:
  - a `logger.debug` at the **start** — `"Computing MD5 for {path} ({size})"`;
  - a `logger.debug` on **completion** — elapsed seconds and derived MB/s;
  - a `logger.info` (or `warning`) only when a single hash exceeds a threshold
    (e.g. > a few seconds), so the "why is this slow?" case surfaces even at a
    coarser level without spamming logs for small files.
  Users flip this on via the retained `setup_logging(...)` (Part 8) — e.g. a
  `DEBUG` level on the `openscm_zenodo` logger — to see whether hashing, not the
  network, is the bottleneck. The messages use the same module `logger` as the
  rest of the client so no extra configuration surface is needed.
- **Two-phase safety.** A draft can be left with an initialised-but-uncommitted
  file if the process dies mid-upload. `upload_files` cleans up or surfaces
  partial state clearly rather than leaving an unpublishable draft silently.

### Exceptions

New `openscm_zenodo/exceptions.py`:

- `ZenodoError` (base)
- `ZenodoHTTPError(ZenodoError)` — wraps the response, surfaces the parsed
  InvenioRDM error body (the `errors[].field/messages` structure) in its message.
  Replaces the current `print(response.json()); raise`.
- `ChecksumMismatchError(ZenodoError)`

`_request` logs the masked error body via `logger.error` and raises
`ZenodoHTTPError`.

---

## Part 3 — File sync (upload only what changed)

Make idempotent, diff-based uploads a first-class operation. `GET
/api/records/{id}/draft/files` returns each file's `key` (name) and `checksum`,
so we diff locally with no downloads.

```python
def list_files(self, record_id: str) -> dict[str, str]:
    """Map of filename -> md5 hex for the draft's current files."""

def sync_files(
    self,
    record_id: str,
    paths: Collection[Path],
    *,
    delete_extraneous: bool = False,
    n_threads: int = 4,
) -> ...:
    remote = self.list_files(record_id)                # name -> md5
    want = {p.name: p for p in paths}
    to_upload = [p for name, p in want.items() if remote.get(name) != _md5_hex(p)]
    to_delete = [n for n in remote if n not in want] if delete_extraneous else []
    # delete_files(to_delete); upload_files(to_upload)
```

Behaviour: skip files whose name+MD5 already match (cheap re-runs), upload only
new/changed files, optionally delete remote files not in the local set so the
draft ends up exactly matching `paths`. Reuses the Part-2 streaming MD5 helper.

---

## Part 4 — New versions

`new_version` is trivial on InvenioRDM: `POST /api/records/{id}/versions` returns
a draft with **no files**. Inheriting the previous version's files is the opt-in
action `import_files`. `FilesMode` in the high-level `create_new_version`
expresses the three sensible policies:

- **`replace`** (default) — empty draft, upload only `files`. The "don't carry
  anything over" behaviour that was impossible on the legacy API.
- **`inherit`** — `import_files` first (reuse previous files, no storage
  duplication), then add `files` on top.
- **`sync`** — `import_files`, then `sync_files(delete_extraneous=True)` against
  `files`: unchanged inherited files stay (no re-upload), removed ones are
  deleted, only changed/new files are transferred. The efficient release path.

```python
new_id = create_new_version(
    record_id, client,
    metadata=meta, files=paths, files_mode=FilesMode.sync, publish=True,
)
```

---

## Part 5 — File retrieval (download)

Downloading files is the read-side counterpart to Part 2's upload. InvenioRDM
serves file content on both **published records** and **drafts**, and the same
shared session — which already sends `Authorization: Bearer <token>` on every
request (Part 1.1) — is exactly what unlocks **restricted/embargoed** files.
Public files download with or without a token; restricted ones require a token
whose owner has access and otherwise return `403` (surfaced as
`ZenodoHTTPError`). There is deliberately **no separate code path for embargoed
records** — access is just the token on the session, so the same methods cover
public and login-only records.

### 5.1 Endpoints

| Target | List | Content |
|---|---|---|
| Published record | `GET /api/records/{id}/files` | `GET /api/records/{id}/files/{filename}/content` |
| Draft | `GET /api/records/{id}/draft/files` | `GET /api/records/{id}/draft/files/{filename}/content` |

Each listing entry carries `key` (filename), `size`, and `checksum`
(`"md5:<hex>"`) — the same metadata `sync_files` (Part 3) already relies on.

### 5.2 `list_files` generalised

Part 3's `list_files` is generalised to serve both sync and download by adding a
`draft` switch (it defaults to `True` to preserve the Part-3 sync behaviour):

```python
def list_files(self, record_id: str, *, draft: bool = True) -> dict[str, str]:
    """Map filename -> md5 hex. `draft=True` lists the draft's files,
    `draft=False` the published record's."""
```

### 5.3 Methods

```python
def download_file(
    self,
    record_id: str,
    filename: str,
    dest: Path,                 # target file path, or a directory to drop `filename` into
    *,
    draft: bool = False,
    verify_checksum: bool = True,
    overwrite: bool = False,
) -> Path:
    """Stream one file's content to `dest`."""

def download_files(
    self,
    record_id: str,
    dest_dir: Path,
    *,
    filenames: Optional[Collection[str]] = None,   # None -> every file on the record
    draft: bool = False,
    n_threads: int = 4,
    verify_checksum: bool = True,
    overwrite: bool = False,
) -> list[Path]:
    """List the record's files, then download them into `dest_dir`."""
```

`draft=False` (published record) is the default — the common case is pulling a
released dataset. Pass `draft=True` to fetch from an unpublished draft.

Implementation mirrors the upload path in reverse:

- **Streaming to disk** via `response.iter_content` (chunked), with a `tqdm`
  progress bar sized from the listing's `size` and `timeout_upload` for the long
  transfer. Write to a temporary `*.part` file and atomically rename on success,
  so an interrupted download never leaves a truncated file in place.
- **Checksum verification.** Compute the local MD5 while streaming and compare
  against the listing's `checksum`. On mismatch raise `ChecksumMismatchError`
  (reused from Part 2), which is in `tenacity`'s retry set so a corrupt transfer
  is re-fetched before it fails. `verify_checksum=True` by default.
- **Retry.** Ordinary `GET` retries come from the shared session's
  `urllib3.Retry` (Part 2). The streaming read is wrapped in the same `tenacity`
  retry as uploads, so a mid-stream connection drop restarts the download and
  resets the progress bar and `*.part` file.
- **Overwrite guard.** Refuse to clobber an existing destination unless
  `overwrite=True`, and skip re-downloading a file whose local MD5 already
  matches the remote checksum (cheap, idempotent re-runs, matching `sync_files`'
  spirit).

### 5.4 Module-level helper

```python
def retrieve_files(
    record_id, dest_dir, client=None, *,
    draft=False, filenames=None, n_threads=4,
    verify_checksum=True, overwrite=False,
) -> list[Path]
```

A one-shot download that sits alongside `retrieve_metadata` / `retrieve_bibtex`.
It builds a default `ZenodoClient` when `client is None`; that client picks up
`ZENODO_TOKEN` from the environment, which is all embargoed/login-only access
needs.

---

## Part 6 — Metadata schema (the main migration cost)

InvenioRDM's metadata schema differs substantially from the legacy deposit form.
This is a **breaking change for every caller** and the bulk of the work.

Key differences to handle and document:

- Creators: `metadata.creators[].person_or_org` with `type` /
  `family_name` / `given_name` / `identifiers` (ORCID as
  `{"scheme": "orcid", ...}`) plus `affiliations[]` — not a single `name`.
- `resource_type`: controlled-vocabulary id, e.g. `{"id": "dataset"}`.
- Access: an `access` object (`record`/`files` = `public`/`restricted`) replaces
  `access_right`.
- Licenses: `metadata.rights[]` with SPDX ids, e.g. `[{"id": "cc-by-sa-4.0"}]`.
- DOI/PIDs: under `pids` (Part 7), not `prereserve_doi`.

Representative shape returned/accepted:

```json
{
  "metadata": {
    "title": "RCMIP protocol",
    "resource_type": {"id": "dataset"},
    "creators": [
      {"person_or_org": {"type": "personal", "family_name": "Nicholls",
                          "given_name": "Zebedee",
                          "identifiers": [{"scheme": "orcid",
                                           "identifier": "0000-0002-4767-2723"}]},
       "affiliations": [{"name": "Uni Melbourne"}]}
    ],
    "publication_date": "2021-03-09",
    "rights": [{"id": "cc-by-sa-4.0"}],
    "description": "..."
  }
}
```

Work items:

- Rewrite `get_metadata` / `update_metadata` for the new schema.
- `user_controlled_only` now strips InvenioRDM server-managed keys (`pids`,
  `publication_date` if server-set, etc.).
- Provide a small **validation helper** that catches the common schema mistakes
  with a clear error before hitting the API.
- Rewrite every docstring example (the `retrieve_metadata` doctest currently
  shows the legacy shape) and add a metadata reference to the docs.
- `load_metadata(path)` helper: load a JSON file with a top-level `metadata` key
  (absorbs the old CLI's `--metadata-file` convenience). `update_metadata` and
  `create_new_version` accept `Union[Metadata, Path]`.

---

## Part 7 — DOI reservation

Replaces the legacy `prereserve_doi` flag. Add `reserve_doi(record_id) -> str`:
`POST /api/records/{id}/draft/pids/doi` reserves a DOI on the draft; the reserved
value is read from the draft's `pids.doi.identifier`. `get_reserved_doi(response)`
reads the same field from any draft response. This preserves the capability the
old `update-metadata --reserve-doi` CLI flag provided, as a proper method.

---

## Part 8 — Remove the CLI

The `typer` app is deleted; the library is the only interface. Every CLI
capability maps to a Python call:

| Old CLI | Python equivalent |
|---|---|
| `retrieve-metadata [--user-controlled-only]` | `retrieve_metadata(id, user_controlled_only=...)` |
| `retrieve-bibtex` | `retrieve_bibtex(id)` |
| `update-metadata --metadata-file` | `update_metadata(id, load_metadata(path))` |
| `update-metadata --reserve-doi` | `reserve_doi(id)` + `get_reserved_doi(...)` |
| `upload-files [--n-threads]` | `client.upload_files(id, paths, n_threads=...)` |
| `remove-files [--all]` | `client.delete_files(...)` / `client.delete_all_files(id)` |
| `create-new-version [--publish --metadata-file --n-threads]` | `create_new_version(...)` |
| `--token` / `ZENODO_TOKEN` | `ZenodoClient(token=...)`, defaults from env |
| `--zenodo-domain` | `ZenodoClient(zenodo_domain=...)` |
| `--version` | `openscm_zenodo.__version__` |
| `--no-logging` / `--logging-level` / `--logging-config` | `setup_logging(...)` |

Deletions:

- `src/openscm_zenodo/cli/` (whole package).
- `[project.scripts]` entry in `pyproject.toml`.
- `typer` dependency; add `tenacity>=8`.
- CLI tests (`tests/integration/test_cli*.py`) and CLI docs (`docs/cli/`,
  `docs/api/openscm_zenodo/cli/`, plus navigation entries).

Retained as library functions (were only *called* by the CLI): `setup_logging`,
`get_default_config`, `mask_token`, `__version__`. `loguru-config` stays as an
optional extra for file-based logging config.

---

## Part 9 — Packaging, docs, tests

- **pyproject.toml:** remove `[project.scripts]` and `typer`; add `tenacity>=8`;
  fix `description` ("Python library for uploading to Zenodo."), the `keyords`
  typo → `keywords` (drop `"command-line"`), and update classifiers to a library.
- **Docs/README:** rewrite all examples as Python using `ZenodoClient` and the
  helpers, including a download example (`retrieve_files` / `download_files`)
  that shows fetching from both published and draft records and notes that
  embargoed/login-only records just need `ZENODO_TOKEN` set. Add an InvenioRDM
  metadata reference. Delete the CLI docs tree.
  Add a prominent **migration guide** covering (a) the new metadata schema and
  (b) the CLI → Python mapping. Bump to a new major version and lead the
  changelog with the breaking change.
- **Tests:**
  - Sandbox integration tests for the full draft lifecycle: `create_record` →
    `upload_files` (init/content/commit) → `publish` → `new_version` with each
    `FilesMode` → `sync_files` → `download_files` (round-trip the published files
    into a temp dir and verify checksums). Include a restricted-access download
    (create a restricted record, then fetch it with vs. without a token) to
    exercise the embargoed/login-only path. (Sandbox runs InvenioRDM, so it is
    the correct target.)
  - Unit tests for the **metadata translation/validation** (Part 6) — highest
    risk.
  - Unit tests for retry (mock 429→200), checksum verification (mock a
    mismatching commit checksum → `ChecksumMismatchError`), `sync_files` diffing,
    `reserve_doi`, and `load_metadata`.
  - Unit tests for the MD5 timing logs: with a `caplog`-style capture, assert
    the shared helper emits the start/completion `debug` records and the
    over-threshold `info`/`warning`, and that it is silent below the threshold.
  - Unit tests for download (mock the file listing + streamed content):
    checksum mismatch → `ChecksumMismatchError`, the `overwrite` guard, the
    skip-when-local-MD5-matches fast path, and `draft=True`/`draft=False`
    hitting the right endpoints.
  - towncrier fragment for the breaking release.

---

## Suggested sequencing

1. **Client + transport foundation** — new `ZenodoClient` skeleton, shared
   session, `urllib3.Retry` adapter, `_request`, exceptions module. Bearer auth.
2. **Read paths** — `get_record`, `get_draft`, `get_metadata`, `get_bibtex`,
   `list_files`. Cheap, and they exercise the transport.
3. **Metadata (Part 6)** — schema rewrite + `load_metadata` + validation. Biggest
   item; do it early so everything downstream uses the right shape.
4. **Write paths** — `create_record`, `update_metadata`, `publish`,
   `reserve_doi`, `new_version` / `import_files`, `delete_files`.
5. **Uploads (Part 2)** — the init→content→commit `upload_file`, `tenacity`
   upload retry, checksum verification, `upload_files` parallelism.
6. **Sync + versions (Parts 3–4)** — `sync_files`, then `create_new_version`
   with `FilesMode`.
7. **Download (Part 5)** — `list_files(draft=...)`, `download_file` /
   `download_files` / `retrieve_files`. Reuses the session, checksum helper, and
   `tenacity` retry from step 5, so it slots in cheaply once uploads exist.
8. **Remove CLI + packaging/docs/tests (Parts 8–9).**

Steps 1–2 stand up the new transport; step 3 de-risks the schema early; 4–6 build
the write/upload/version features on it; 7 finishes the breaking release.

---

## Explicitly out of scope

- Any backward-compatibility with the legacy Deposit API or its metadata schema.
  This is a clean break.
- A local record-id cache like `zenodo-client`'s PyStow store — the stateless
  "you pass the id" model is kept deliberately (no cache-vs-server drift).
- Async I/O — threaded uploads with retry cover throughput without an async
  rewrite.
