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
4. **Cut the CLI down to three commands** — `upload-files`, `download-files`
   and `retrieve-citation`. The library is the primary surface; the CLI keeps
   only the tasks that are genuinely useful from a shell script or CI job
   (Part 8).

The one hard constraint to design around: InvenioRDM uses a **different metadata
schema** from the legacy deposit form (see Part 6). That is the single biggest
piece of work and the most visible break for downstream users.

---

## Part 0 — Repository template update (`copier update`) — ✅ DONE

**Done by the user in commit `72c0ecc` ("Update copier").** The template is now at
`_commit: v0.15.4`, `include_cli` stayed `true`, and `project_description_short`
(and hence `pyproject.toml`'s `description`) is
`"Python API and command-line tool for interacting with zenodo."` — same intent as
the wording proposed below, so Part 9 should treat the `description` field as
already settled and only fix the `keyords` → `keywords` typo. Original plan text
kept below for reference.

The repo is generated from `gl:znicholls/copier-core-python-repository`, currently
pinned at `_commit: v0.14.2` in `.copier-answers.yml`. Before touching the
library, bring the scaffolding up to date and re-answer the questions this plan
invalidates, so template-owned files (CI workflows, `Makefile`, docs scaffolding,
`pyproject.toml` boilerplate) are not fought over later.

```bash
uvx copier update --trust      # add --defaults to keep all current answers
```

Answers to change during the update:

- `include_cli`: **stays `true`**. The CLI is trimmed to three commands rather
  than deleted (Part 8), so the template must keep generating
  `src/openscm_zenodo/cli/`, the `[project.scripts]` entry, and the CLI docs
  tree.
- `project_description_short`: `"Command-line tool for uploading to zenodo."` →
  **`"Python library and CLI for uploading to and downloading from Zenodo."`** —
  the package is now library-first with a small CLI, and it does both directions.
  Setting it here keeps `pyproject.toml` and the docs in sync with the template
  (Part 9).

Everything else (package manager `uv`, notebook-based docs, `track_lock_file`,
name/email/URL) stays as-is.

Notes:

- Do this **first and as its own commit**, so the template diff is reviewable
  separately from the API rewrite. Merge conflicts from an update spanning
  several template versions are much easier to resolve against an untouched
  working tree.
- `.copier-answers.yml` is machine-managed ("NEVER EDIT MANUALLY") — change the
  two answers through the `copier update` prompts, not by editing the file.
- After the update, re-run the lock/bootstrap and full checks
  (`make virtual-environment`, `make check`) and fix any fallout from newer
  template pins before starting Part 1.
- Re-run `copier update` at the end if the template has moved again, but expect
  the substantive answer changes to already be in place.

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
| `upload_files` | `upload_files(record_id, paths, *, n_threads=4, verify_checksum=True)` | warns on stripped paths (Part 11) |
| — | `upload_files_as_zip(record_id, paths_or_groups, ...)` | zip to preserve structure (Part 11) |
| — | `sync_files(record_id, paths, *, delete_extraneous=False, n_threads=4)` | Part 3 |
| `remove_files` | `delete_files(record_id, filenames)` | delete by **name** |
| `remove_all_files` | `delete_all_files(record_id)` | |
| `remove_file_id` / `remove_files_by_id` | removed | no numeric file ids in the new API |
| `get_bucket_url` | removed | no bucket in the new flow |
| `get_bibtex_entry` | `get_citation(record_id, *, fmt=..., style=..., locale=...)` | generalised to any export format / citation style (Part 10) |
| — | `list_files(record_id, *, draft=True)` → `dict[str, str]` | `GET .../files` — draft or published record (Parts 3, 5) |
| — | `download_file(record_id, filename, dest, *, draft=False, ...)` → `Path` | `GET .../files/{name}/content` (Part 5) |
| — | `download_files(record_id, dest_dir, *, filenames=None, draft=False, ...)` → `list[Path]` | Part 5 |
| — | `reserve_doi(record_id)` → `str` | `POST .../draft/pids/doi` (Part 7) |
| `get_response` | `_request(...)` (private) | routes through the session |

### 1.3 Module-level helpers

Keep a small functional surface for common one-shot tasks; rename for clarity:

```python
def retrieve_metadata(record_id, client=None, *, user_controlled_only=False) -> Metadata
def retrieve_citation(                                       # was retrieve_bibtex_entry
    record_id, client=None, *,
    fmt: CitationFormat = CitationFormat.bibtex,
    style: str = "apa", locale: str = "en-US",
) -> str
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
   bar and `timeout_upload`. See Part 2.1 for the progress-bar contract.
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

### 2.1 Progress bars (shared by upload and download)

**One bar per file, which disappears when that file finishes.** This is a single
shared helper used by upload (Part 2), download (Part 5) and both CLI commands
(Part 8), so the two directions look identical.

The current `TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT` (`zenodo.py:27`) sets only
`unit`/`unit_scale`/`unit_divisor` — it does not set `leave`, so bars default to
`leave=True` and pile up. Replace it with one dict used by both directions:

```python
TQDM_FILE_PROGRESS_KWARGS_DEFAULT = dict(
    unit="B",
    unit_scale=True,
    unit_divisor=1024,
    leave=False,        # bar is erased when the file completes
    dynamic_ncols=True,
)
```

Rules:

- **`leave=False`** is what makes the bar vanish on completion — the transfer is
  wrapped in a `with tqdm(...)` block, so the bar is torn down when the file's
  transfer exits, whether it succeeded or raised.
- **`desc` is the file's name as it will appear on Zenodo** (i.e. `path.name` —
  Part 11), truncated, so with several bars on screen it is obvious which file
  each one tracks.
- **`total`** comes from `os.stat(path).st_size` on upload and from the listing's
  `size` on download. If a download response has no usable size, fall back to a
  bar with `total=None` (a spinner-style bar) rather than no bar.
- **Parallel transfers (`n_threads > 1`)** need care or the bars corrupt each
  other:
  - give each worker a stable `position` slot (0..n_threads-1), recycled as
    files complete, so bars don't jump around;
  - pass a shared `threading.RLock` via `tqdm.set_lock()` / `lock_args` so
    concurrent redraws don't interleave;
  - route any log output emitted during a transfer through `tqdm.write()` so log
    lines don't shred the bars. This matters because the MD5 timing logs above
    fire mid-transfer.
- **Non-interactive use.** Pass `disable=None`, which makes `tqdm` silence itself
  automatically when stderr is not a TTY — so CI logs and piped output stay
  clean, and the library stays quiet when embedded. Expose an explicit
  `progress: bool = True` parameter on `upload_file(s)` / `download_file(s)` for
  callers who want to force bars off (or on) regardless.
- **Retries** (`tenacity`, above) must **reset** the bar on each attempt, not
  continue a partially-filled one: the retry re-opens the file, so it also
  re-creates the bar (or calls `bar.reset()`), otherwise a retried upload shows a
  bar past 100%.
- The overall `upload_files`/`download_files` call may additionally show a
  **files-completed** bar (`unit="file"`, `total=len(paths)`). This one also uses
  `leave=False`, so once the whole operation finishes the terminal is left clean.

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

- **Streaming to disk** via `response.iter_content` (chunked), with a per-file
  `tqdm` progress bar sized from the listing's `size` and `timeout_upload` for the
  long transfer. The bar follows the **same Part 2.1 contract as uploads** —
  `leave=False` so it disappears when the file completes, `desc` set to the
  filename, `position` slots under `n_threads`. Write to a temporary `*.part`
  file and atomically rename on success, so an interrupted download never leaves
  a truncated file in place.
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

## Part 8 — CLI: cut down to three commands

The `typer` app stays, but only for the tasks that are genuinely better from a
shell — moving bytes in and out of Zenodo, and getting a citation string. The
metadata/versioning commands go, because they take structured JSON input and
belong in Python where the new schema (Part 6) can be built and validated.

### 8.1 Retained commands

```bash
# Upload local files to a draft
openscm-zenodo upload-files RECORD_ID FILE... \
    [--n-threads 4] [--sync] [--delete-extraneous] \
    [--no-verify-checksum] [--no-progress] \
    [--zip [NAME]] [--zip-base-dir DIR] [--no-warn-path-stripped]

# Download files from a published record, a draft, or a restricted/embargoed record
openscm-zenodo download-files RECORD_ID [FILENAME...] \
    [--dest-dir .] [--draft] [--n-threads 4] \
    [--overwrite] [--no-verify-checksum] [--no-progress]

# Get a citation, BibTeX by default
openscm-zenodo retrieve-citation RECORD_ID \
    [--format bibtex] [--style apa] [--locale en-US] [--output FILE]
```

**Files are passed the same way in both directions**: a `RECORD_ID` followed by a
variadic positional list of files. `upload-files` takes local paths; the
symmetrical `download-files` takes remote filenames, and **omitting them means
"all files on the record"** (there is no equivalent default for upload, where an
empty list is an error). Everything that is not "which files" — destination,
threading, progress, access — stays an option on both. Concretely, this replaces
the earlier repeatable `--filename` option on `download-files`, so the two
commands read as mirror images:

```bash
openscm-zenodo upload-files   1234 data/a.nc data/b.nc
openscm-zenodo download-files 1234 a.nc b.nc --dest-dir data/
```

Notes on each:

- **`upload-files`** — thin wrapper over `client.upload_files` (Part 2).
  `--sync` switches it to `sync_files` (Part 3) so re-runs only transfer changed
  files, and `--delete-extraneous` (implies `--sync`) makes the draft match the
  local set exactly. This is the one place the CLI earns its keep in CI.
  Warns when a local path is stripped, and `--zip` bundles everything into one
  archive to preserve structure (Part 11); grouping into several archives is
  Python-only.
- **`download-files`** — wraps `download_files` (Part 5). `--dest-dir` defaults to
  the current directory. `--draft` targets an unpublished draft.
  **Embargoed / restricted records need no special flag** — access is just the
  token on the session, so `ZENODO_TOKEN` (or `--token`) is the whole story
  (Part 5). A `403` surfaces as a clear "no access with this token" error rather
  than a traceback.
- **`retrieve-citation`** — wraps `get_citation` (Part 10). **With no options it
  emits BibTeX**: `--format` defaults to `bibtex`. `--style`/`--locale` apply only
  to `--format citation` (the styled human-readable string, where `apa` is the
  default) and are ignored with a warning otherwise. Writes to stdout by default
  so it pipes, or to `--output`. Invalid `--format` is rejected by typer's enum
  handling; an invalid `--style` is caught client-side against the known-good list
  before the request (Part 10).

Both transfer commands show **one progress bar per file that disappears when that
file completes**, per the shared Part 2.1 contract; `--no-progress` forces them
off, and they self-disable when stderr is not a TTY.

Global options are unchanged: `--token`, `--zenodo-domain`, `--version`,
`--no-logging`, `--logging-level`, `--logging-config`.

### 8.2 Removed commands

| Removed CLI | Python equivalent |
|---|---|
| `retrieve-metadata [--user-controlled-only]` | `retrieve_metadata(id, user_controlled_only=...)` |
| `update-metadata --metadata-file` | `update_metadata(id, load_metadata(path))` |
| `update-metadata --reserve-doi` | `reserve_doi(id)` + `get_reserved_doi(...)` |
| `remove-files [--all]` | `client.delete_files(...)` / `client.delete_all_files(id)` |
| `create-new-version [--publish --metadata-file --n-threads]` | `create_new_version(...)` |

`retrieve-bibtex` is not removed but **renamed and generalised** to
`retrieve-citation` (`--format bibtex` reproduces the old behaviour).

Note: `retrieve-metadata` is the one removal that might be missed — it is
read-only and scriptable, the same shape as the commands we kept. It is cheap to
re-add later as a fourth command if that turns out to be wanted; the plan drops
it only because it wasn't in the keep-list.

### 8.3 Consequences

- `typer` **stays** a runtime dependency; add `tenacity>=8`.
- `[project.scripts]` stays; `include_cli: true` stays in `.copier-answers.yml`
  (Part 0).
- CLI tests and docs are **rewritten, not deleted**: `tests/integration/test_cli*.py`
  shrinks to the three retained commands, and `docs/cli/` documents them (with
  the embargoed-download example spelled out).
- `setup_logging`, `get_default_config`, `mask_token`, `__version__` stay as
  library functions used by the CLI. `loguru-config` stays an optional extra.

---

## Part 9 — Packaging, docs, tests

- **pyproject.toml:** keep `[project.scripts]` and `typer` (Part 8); add
  `tenacity>=8`; fix `description` ("Python library and CLI for uploading to and
  downloading from Zenodo." — already handled by the Part 0
  `project_description_short` answer if the field is template-owned) and the
  `keyords` typo → `keywords` (keep `"command-line"`, add `"download"`).
- **Docs/README:** rewrite all examples as Python using `ZenodoClient` and the
  helpers, including a download example (`retrieve_files` / `download_files`)
  that shows fetching from both published and draft records and notes that
  embargoed/login-only records just need `ZENODO_TOKEN` set. Add an InvenioRDM
  metadata reference. **Rewrite** (don't delete) the CLI docs tree for the three
  retained commands, including an embargoed-download example and the citation
  format/style table from Part 10.
  Add a prominent **migration guide** covering (a) the new metadata schema,
  (b) the removed CLI commands → Python mapping, and (c) `retrieve-bibtex` →
  `retrieve-citation --format bibtex`. Bump to a new major version and lead the
  changelog with the breaking change.
- **Tests:**
  - **Live-API integration coverage of every endpoint we call** — see Part 12.
  - Unit tests for the **metadata translation/validation** (Part 6) — highest
    risk.
  - Unit tests for retry (mock 429→200), checksum verification (mock a
    mismatching commit checksum → `ChecksumMismatchError`), `sync_files` diffing,
    `reserve_doi`, and `load_metadata`.
  - CLI tests for the three retained commands (Part 8): `upload-files` with and
    without `--sync`, `download-files` with `--draft` and with a subset of
    `--filename`s, and `retrieve-citation` across `--format`/`--style`.
  - Unit tests for the MD5 timing logs: with a `caplog`-style capture, assert
    the shared helper emits the start/completion `debug` records and the
    over-threshold `info`/`warning`, and that it is silent below the threshold.
  - Unit tests for download (mock the file listing + streamed content):
    checksum mismatch → `ChecksumMismatchError`, the `overwrite` guard, the
    skip-when-local-MD5-matches fast path, and `draft=True`/`draft=False`
    hitting the right endpoints.
  - towncrier fragment for the breaking release.

---

## Part 10 — Citation export (configurable formats)

`get_bibtex_entry` becomes `get_citation`, covering every serialisation Zenodo
offers plus styled citation strings. This backs the `retrieve-citation` command
(Part 8).

### 10.1 The endpoint (verified against live Zenodo, 2026-07-25)

**The current implementation is already broken.** `zenodo.py:205` requests
`/records/{id}/export/bibtex`; that path now returns **404** for every format
(`bibtex`, `csl`, `datacite-json`, `datacite-xml`, `dublincore`, `json-ld`,
`marcxml`, `dcat-ap` all checked). InvenioRDM serves exports through **content
negotiation on the record itself** instead:

```
GET /api/records/{id}     Accept: <mime type>
```

Verified `200` on `https://zenodo.org/api/records/4589756`:

| `CitationFormat` | Accept header |
|---|---|
| `bibtex` | `application/x-bibtex` |
| `csl` | `application/vnd.citationstyles.csl+json` |
| `datacite_json` | `application/vnd.datacite.datacite+json` |
| `datacite_xml` | `application/vnd.datacite.datacite+xml` |
| `dublin_core` | `application/x-dc+xml` |
| `json_ld` | `application/ld+json` |
| `marcxml` | `application/marcxml+xml` |
| `dcat` | `application/dcat+xml` |
| `citation` | `text/x-bibliography` (+ `style`/`locale`, below) |

### 10.2 Styled citation strings

`Accept: text/x-bibliography` renders a human-readable citation and takes
`?style=<csl-style-id>&locale=<locale>`. Without a valid style it returns
**400 `{"message": "Citation string style not found."}`**, so `style` defaults to
`apa`.

```console
$ ... /api/records/4589756?style=apa&locale=en-US
Zebedee Nicholls& Jared Lewis. (2021). Reduced Complexity Model Intercomparison
Project (RCMIP) protocol (Version v5.1.0) [Dataset]. Zenodo.
https://doi.org/10.5281/zenodo.4589756
```

Style ids are **full CSL style filenames**, not the short names the Zenodo web UI
shows. Verified accepted: `apa`, `harvard-cite-them-right`, `ieee`,
`chicago-author-date`, `nature`, `science`, `modern-language-association`,
`american-medical-association`, `acm-sig-proceedings`, `bibtex`. Verified
**rejected** (400): `mla`, `vancouver`, `harvard1`,
`chicago-note-bibliography`, `chicago-fullnote-bibliography`.

That gap is a usability trap — `--style mla` is the obvious thing to type and it
400s. So: keep a `KNOWN_CITATION_STYLES` tuple of the verified ids, validate
`style` against it client-side, and raise a `ValueError`/typer error listing the
valid ids plus an alias hint (`mla` → `modern-language-association`,
`vancouver` → `american-medical-association`). Do **not** hard-restrict to the
list — allow an escape hatch (any string passes with a warning) since Zenodo
accepts more CSL styles than we enumerate, and a server 400 is still surfaced
cleanly.

### 10.3 Signature

```python
class CitationFormat(str, Enum):
    bibtex = "bibtex"
    csl = "csl"
    datacite_json = "datacite-json"
    datacite_xml = "datacite-xml"
    dublin_core = "dublin-core"
    json_ld = "json-ld"
    marcxml = "marcxml"
    dcat = "dcat"
    citation = "citation"          # styled, human-readable

def get_citation(
    self,
    record_id: str,
    *,
    fmt: CitationFormat = CitationFormat.bibtex,
    style: str = "apa",            # `citation` only
    locale: str = "en-US",         # `citation` only
) -> str:
    """Export a record's citation/metadata in `fmt`."""
```

**BibTeX is the default** — `get_citation(record_id)` and
`openscm-zenodo retrieve-citation RECORD_ID` with no further arguments both return
a BibTeX entry, matching what the old `retrieve-bibtex` / `get_bibtex_entry` did.
`style` is a *CSL citation style* and only has meaning for `fmt=citation`; it is
deliberately a separate axis from `fmt`, so the BibTeX default lives on `fmt` and
`style`'s own default (`apa`) never applies unless the styled format is asked for.
(`bibtex` happens to also be a valid CSL style id, but reaching BibTeX that way —
`fmt=citation, style=bibtex` — routes through the citation-string renderer and is
not the supported path; use `fmt=bibtex`.)

Goes through `_request` (shared session, retry, `ZenodoHTTPError`) and returns
`response.text`. `style`/`locale` are only sent for `fmt=citation`; passing them
with another format logs a warning.

### 10.4 Tests

- Unit: each `CitationFormat` sends the right `Accept` header; `style`/`locale`
  are sent only for `citation`; an unknown style raises before any request;
  a mocked 400 surfaces as `ZenodoHTTPError` with the Zenodo message.
- Integration (marked, hits production Zenodo read-only, record `4589756`):
  `bibtex` and `citation`+`apa` return non-empty text — this is the regression
  test for the 404 that the `/export/` path silently became.

---

## Part 11 — Local paths are stripped; zipping to preserve structure

**Zenodo has no directories.** InvenioRDM identifies a file by a flat name, and
Zenodo's own guidance (already quoted in `upload_file_to_bucket_url`'s docstring)
is to upload a ZIP if you want structure — the web UI then displays the archive's
contents. The current code reflects this by uploading
`f"{bucket_url}/{to_upload.name}"` (`zenodo.py:~765`), i.e. the local basename,
always.

There is deliberately **no API for setting the remote name** — the name on Zenodo
is always `path.name`. What we add is (a) telling the user when that silently
loses information, and (b) a zip helper for when they want the structure kept.

### 11.1 Warn when a path is stripped

If any upload path has a parent (`path.parent not in (Path("."), Path(""))`), emit
a **warning naming the file and what it will land as**:

```
Uploading 'outputs/2024/data.nc' as 'data.nc': Zenodo has no directories, so the
local path is stripped. Pass warn_path_stripped=False to silence this, or use
upload_files_as_zip(...) to preserve the structure.
```

- One warning per affected file, via `logger.warning` (the message points at both
  escape hatches, so the fix is discoverable from the warning alone).
- Silenced by **`warn_path_stripped: bool = True`** on `upload_file`,
  `upload_files`, `sync_files` and `upload_files_as_zip`; `--no-warn-path-stripped`
  on the CLI.
- Warn only when something is actually lost: a bare `data.nc` or `./data.nc`
  produces no warning.

### 11.2 Basename collisions are an error, not a warning

Separate from the warning above, and stricter: if two paths in one call reduce to
the same basename, raise **`DuplicateFileKeyError`** (new, in `exceptions.py`)
naming both local paths.

```python
upload_files(id, [Path("2024/data.nc"), Path("2025/data.nc")])   # -> DuplicateFileKeyError
```

Currently the second silently overwrites the first, so this is a genuine bug fix.
It is an exception rather than a warning because there is no correct
interpretation — the user must either rename, upload separately, or zip. The
error message says exactly that. `sync_files` (Part 3) diffs on remote name and
gains the same check.

### 11.3 Zipping to preserve structure

New module `openscm_zenodo/zipping.py`. A standalone builder plus an upload
convenience:

```python
def zip_files(
    paths: Collection[Path],
    dest: Path,                          # the .zip to write
    *,
    base_dir: Optional[Path] = None,     # paths inside the zip are relative to this
    compression: int = zipfile.ZIP_DEFLATED,
    deterministic: bool = True,          # see below
) -> Path:
    """Zip `paths`, preserving their structure relative to `base_dir`."""
```

- **`base_dir` defaults to the common ancestor** of `paths`
  (`os.path.commonpath`), which is what makes the obvious call do the obvious
  thing: `zip_files([Path("out/2024/a.nc"), Path("out/2025/b.nc")], ...)` stores
  `2024/a.nc` and `2025/b.nc`. Pass `base_dir` explicitly to keep a higher prefix
  (e.g. `out/…`).
- **Absolute paths are never stored as absolute** — everything is made relative to
  `base_dir`; a path outside `base_dir` is an error.
- **Collisions inside the archive** (two files with the same path relative to
  `base_dir`) raise `DuplicateFileKeyError`, same as 11.2.

Upload convenience on the client:

```python
def upload_files_as_zip(
    self,
    record_id: str,
    paths: Union[Collection[Path], Mapping[str, Collection[Path]]],
    *,
    base_dir: Optional[Path] = None,
    zip_name: str = "archive.zip",       # collection form only
    keep_zips: Optional[Path] = None,    # write the archives here instead of a temp dir
    **upload_kwargs,                     # n_threads, verify_checksum, progress, ...
) -> ...:
```

- **Collection form** — everything goes into one archive named `zip_name`.
- **Mapping form** — `{zip_name: paths}` groups files into several archives, which
  is the "which files in which zip" control:

  ```python
  client.upload_files_as_zip(
      record_id,
      {
          "inputs.zip": [Path("in/a.nc"), Path("in/b.nc")],
          "outputs.zip": list(Path("out").rglob("*.nc")),
      },
  )
  ```

  A file may appear in more than one group (deliberately allowed — groups are
  independent archives). Duplicate `zip_name` keys are impossible by construction;
  a `zip_name` not ending in `.zip` is a warning, not an error.
- Archives are built in a `tempfile.TemporaryDirectory` and deleted after upload
  unless `keep_zips` is given. The zip build itself gets a progress bar following
  the Part 2.1 contract (`leave=False`), since zipping a large tree is slow enough
  to look like a hang.
- **Mapping form is Python-only.** The CLI exposes only the single-archive case
  (11.4).

#### Determinism matters here (`deterministic=True`)

A stock `zipfile` archive embeds each member's mtime, so re-zipping the *same*
files produces a **different MD5 every time**. That would quietly defeat
`sync_files` (Part 3), which skips files whose name+MD5 already match — every
sync would re-upload the whole archive. So `deterministic=True` (the default):

- sort members by their in-archive path,
- pin each `ZipInfo.date_time` to a fixed timestamp,
- pin `external_attr`/`create_system` so permissions and host OS don't leak in.

This makes zip-then-sync idempotent, which is the whole point of Part 3. Part 12
gets a test that zipping the same inputs twice yields identical bytes.

### 11.4 CLI

`--zip [NAME]` on `upload-files` bundles **all** positional paths into one
archive (default name `archive.zip`) and uploads that instead of the individual
files, with `--zip-base-dir` for the prefix:

```bash
openscm-zenodo upload-files 1234 out/2024/a.nc out/2025/b.nc --zip results.zip
```

Grouping files into multiple archives is **not exposed on the CLI** — expressing
"which files in which zip" as flags is more awkward than just writing the two
lines of Python, so the docs point at `upload_files_as_zip`'s mapping form
instead.

---
## Part 12 — Live-API integration tests (every endpoint we use)

The rule: **no endpoint ships without a test that has hit the real thing.** The
whole premise of this rewrite is that the legacy API drifted out from under us —
`/export/bibtex` silently became a 404 (Part 10) and nobody noticed, because
nothing exercised it. Mocked tests would not have caught that.

### 12.1 Infrastructure

Existing scaffolding to build on rather than reinvent: `tests/conftest.py:29`
already defines `ZENODO_TOKEN_AVAILABLE` and a `zenodo_token` marker that skips
when the env var is missing (`conftest.py:34`), and `ZenodoDomain.sandbox`
(`zenodo.py:41`) exists. Extend with:

- **Two markers.** `zenodo_token` (needs write access → sandbox) and a new
  `zenodo_live_read` for unauthenticated production reads. Read-only tests run
  without any credentials, so CI gets real coverage on PRs from forks.
- **Sandbox for anything that writes.** `sandbox.zenodo.org` runs InvenioRDM, so
  it is the correct target and keeps test records out of production.
- **Session-scoped draft fixture** that creates one draft, yields it, and
  **cleans up in teardown** (delete the draft; published sandbox records cannot be
  deleted, so publish-path tests are marked and kept few). Avoids leaving litter
  and keeps the suite fast.
- **A single pinned production record for reads** — `4589756` (already the
  doctest example at `zenodo.py:992`).
- **`--n-threads 1` where output is asserted**, so parallel progress bars don't
  make assertions flaky.
- **Rate limiting.** Zenodo throttles (hence `429` in
  `retry_status_forcelist`); mark the suite so it can run serially, and don't
  fan out.

### 12.2 Endpoint matrix

Every row must have at least one test that reaches live Zenodo. Ticked off as
implemented:

| Method / endpoint | Client method | Target |
|---|---|---|
| `GET /api/records/{id}` | `get_record` | prod (read) |
| `GET /api/records/{id}` + `Accept` (×9 formats) | `get_citation` | prod (read) |
| `GET /api/records/{id}?style=&locale=` | `get_citation(fmt=citation)` | prod (read) |
| `GET /api/records/{id}/files` | `list_files(draft=False)` | prod (read) |
| `GET /api/records/{id}/files/{name}/content` | `download_file(draft=False)` | prod (read) |
| `GET /api/records/{id}/versions` / `links.latest` | `get_latest_version_id` | prod (read) |
| parent id | `get_parent_id` | prod (read) |
| `POST /api/records` | `create_record` | sandbox |
| `GET /api/records/{id}/draft` | `get_draft` | sandbox |
| `PUT /api/records/{id}/draft` | `update_metadata` | sandbox |
| `POST /api/records/{id}/draft/files` | upload init | sandbox |
| `PUT .../draft/files/{name}/content` | upload content | sandbox |
| `POST .../draft/files/{name}/commit` | upload commit | sandbox |
| `GET /api/records/{id}/draft/files` | `list_files(draft=True)` | sandbox |
| `GET .../draft/files/{name}/content` | `download_file(draft=True)` | sandbox |
| `DELETE .../draft/files/{name}` | `delete_files` | sandbox |
| `POST .../draft/pids/doi` | `reserve_doi` | sandbox |
| `POST .../draft/actions/publish` | `publish` | sandbox |
| `POST /api/records/{id}/versions` | `new_version` | sandbox |
| `POST .../draft/actions/files-import` | `import_files` | sandbox |

### 12.3 Scenario tests

On top of per-endpoint coverage:

- **Full lifecycle** (sandbox): `create_record` → `update_metadata` →
  `reserve_doi` → `upload_files` → `list_files` → `publish` → `new_version` for
  each `FilesMode` → `sync_files` → `download_files` round-trip into a temp dir
  with checksum verification.
- **Restricted access** (sandbox): create a restricted record, then confirm
  download succeeds with a token and returns `403` (as `ZenodoHTTPError`) without
  one — the embargoed path from Part 5.
- **Idempotent re-run**: `sync_files` twice with unchanged files uploads nothing
  the second time; `download_files` twice re-downloads nothing.
- **Path stripping and zipping** (sandbox, Part 11): upload `sub/dir/f.nc` and
  assert it lands as `f.nc` with the warning emitted (and silent under
  `warn_path_stripped=False`); colliding basenames raise `DuplicateFileKeyError`
  before any request; `upload_files_as_zip` round-trips — upload an archive,
  download it, unzip it, and assert the directory structure survived.
- **Zip determinism** (unit, Part 11.3): zipping the same inputs twice produces
  byte-identical archives, and a zip-then-`sync_files` re-run uploads nothing the
  second time. This guards the interaction that would otherwise silently make
  every sync re-upload the full archive.
- **Metadata schema** (sandbox): round-trip a full InvenioRDM metadata document
  through `update_metadata` → `get_metadata` and assert nothing is lost; plus a
  deliberately malformed document to confirm the real API's error body renders
  through `ZenodoHTTPError` readably.
- **CLI end-to-end** (sandbox) for the three retained commands via typer's
  `CliRunner`, including `retrieve-citation` with no arguments returning BibTeX.

### 12.4 Running them

- `make test-integration` (token from `.env`, which already holds `ZENODO_TOKEN`)
  runs the sandbox suite; the read-only suite runs unconditionally.
- **In CI**: read-only tests on every PR; the token-requiring suite on `main` and
  on a **scheduled (e.g. weekly) run**. The scheduled run is the point — it is
  what turns "Zenodo changed their API" into a failing build instead of a bug
  report, which is exactly the failure mode that produced the dead `/export/`
  path.

---

## Suggested sequencing

0. ~~**`copier update` (Part 0)** — refresh the template from `v0.14.2`, keep
   `include_cli: true`, fix `project_description_short`, then re-lock and run
   `make check`. Own commit, before any library work.~~ ✅ **DONE** (commit
   `72c0ecc`, template now `v0.15.4`).
1. **Client + transport foundation** — new `ZenodoClient` skeleton, shared
   session, `urllib3.Retry` adapter, `_request`, exceptions module. Bearer auth.
2. **Read paths** — `get_record`, `get_draft`, `get_metadata`, `get_citation`
   (Part 10), `list_files`. Cheap, and they exercise the transport.
3. **Metadata (Part 6)** — schema rewrite + `load_metadata` + validation. Biggest
   item; do it early so everything downstream uses the right shape.
4. **Write paths** — `create_record`, `update_metadata`, `publish`,
   `reserve_doi`, `new_version` / `import_files`, `delete_files`.
5. **Uploads (Parts 2, 11)** — the init→content→commit `upload_file`, `tenacity`
   upload retry, checksum verification, `upload_files` parallelism, the shared
   `leave=False` progress-bar helper (Part 2.1), and the path-stripping warning +
   basename-collision error (Part 11.1–11.2). `zipping.py` and
   `upload_files_as_zip` (Part 11.3) land here too — but note the determinism
   requirement only pays off once `sync_files` exists in step 6.
6. **Sync + versions (Parts 3–4)** — `sync_files`, then `create_new_version`
   with `FilesMode`.
7. **Download (Part 5)** — `list_files(draft=...)`, `download_file` /
   `download_files` / `retrieve_files`. Reuses the session, checksum helper,
   progress-bar helper and `tenacity` retry from step 5, so it slots in cheaply
   once uploads exist.
8. **Trim the CLI + packaging/docs (Parts 8–9).** The three retained commands are
   thin wrappers, so they land last, once `upload_files`, `download_files` and
   `get_citation` all exist.
9. **Live-API integration suite (Part 12).** Grow it *alongside* steps 2–8 —
   each endpoint gets its live test as it is written, not in a batch at the end.
   Step 9 is just the final sweep: confirm the Part 12.2 matrix is fully ticked,
   add the scenario tests, and wire up the scheduled CI run.

Step 0 settles the scaffolding; steps 1–2 stand up the new transport; step 3
de-risks the schema early; 4–6 build the write/upload/version features on it;
7–8 finish the breaking release; step 9 is the standing guard against Zenodo
changing under us.

---

## Explicitly out of scope

- Any backward-compatibility with the legacy Deposit API or its metadata schema.
  This is a clean break.
- A local record-id cache like `zenodo-client`'s PyStow store — the stateless
  "you pass the id" model is kept deliberately (no cache-vs-server drift).
- Async I/O — threaded uploads with retry cover throughput without an async
  rewrite.
