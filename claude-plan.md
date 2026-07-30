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

### 1.1 The client — ✅ IMPLEMENTED

**Done** (sequencing step 1): `ZenodoClient`, `build_session`, `resolve_token`,
`load_env_file`, `RecordID` / `ParentID`, `_request`, and
`openscm_zenodo/exceptions.py` all exist, with unit tests. Deltas from the text
below, all deliberate:

- `mask_token` is a two-argument function (`mask_token(input, token)`), so it
  cannot be an attrs field `repr`. The field uses a small `_repr_token` instead;
  `mask_token` is still used for URLs in log and error messages.
- `get_zenodo_domain_url(zenodo_domain)` was added — the domain arrives as either
  a `str` or a `ZenodoDomain`, and three places need it as a URL.
- `resolve_token` takes `required: bool = False` rather than there being a second
  `require_token` function; `required=True` raises `MissingTokenError` instead of
  returning `None`, and `@overload` keeps the return type precise in both cases.
  It is what the CLI's write commands use.
- `ZenodoInteractor` is still present and still works. It is deleted as Parts 2–8
  replace its methods, so the branch stays green in between.

Rename `ZenodoInteractor` → **`ZenodoClient`**.

```python
@define
class ZenodoClient:
    token: str | None = field(default=None, repr=mask_token)
    """Token to authenticate with. `None` means "resolve it" — see 1.1.1."""

    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production
    timeout: int = 10
    timeout_upload: int = 60 * 60

    session: requests.Session | None = field(default=None, repr=_repr_session)
    """Caller-supplied session (dependency injection). `None` → we build one."""

    _owns_session: bool = field(init=False, default=False)
    """Did we build `session` ourselves? Governs whether `close()` closes it."""

    def __attrs_post_init__(self) -> None:
        self.token = resolve_token(self.token, zenodo_domain=self.zenodo_domain)
        if self.session is None:
            self.session = build_session()
            self._owns_session = True
```

Note what is **not** on the client: `max_retries`, `backoff_factor` and
`retry_status_forcelist` are gone. They were session configuration wearing a
client costume — the only thing the client ever did with them was forward them to
`build_session`. They now live as defaults on `build_session` (1.1.3), and a
caller who wants different values builds a session and injects it. That is one
concept in one place instead of five parameters duplicated across two APIs, and
it removes the trap where those fields look live but are silently ignored once a
session is injected.

`timeout` / `timeout_upload` stay on the client: they are per-request arguments
passed to each call, not session state.

**`session` is in the `repr`** — with a custom formatter, which is the only
reason it looked excluded in the earlier draft. The default
`<requests.sessions.Session object at 0x10f3c2d50>` is both noise and a memory
address, and this repo runs `pytest --doctest-modules` in CI
(`.github/workflows/ci.yaml:127`), so a raw address in a client `repr` would make
any doctest that echoes a `ZenodoClient` unrunnable. So:

```python
def _repr_session(session: requests.Session | None) -> str:
    return "None" if session is None else f"<{type(session).__qualname__}>"
```

That keeps the field visible and deterministic. `_owns_session` is in the `repr`
too, and it is the genuinely informative bit — "am I looking at a client with the
default transport, or one someone handed a session to?" is exactly the question
you ask when debugging from a traceback.

Authentication moves to the `Authorization: Bearer <token>` header (InvenioRDM's
preferred scheme) instead of the `?access_token=` query param.

#### 1.1.1 Token resolution

One function owns the precedence, so the library, the CLI and the tests cannot
disagree about where a token came from:

```python
def resolve_token(
    token: str | None = None,
    *,
    zenodo_domain: str | ZenodoDomain = ZenodoDomain.production,
    env: Mapping[str, str] | None = None,   # defaults to os.environ; injectable for tests
) -> str | None:
```

**Order of precedence, highest first:**

1. **Explicit argument** — `ZenodoClient(token=...)` or `resolve_token("...")`.
   Nothing overrides an explicitly-passed token.
2. **CLI `--token`** — which is not a separate mechanism at all: the CLI simply
   passes its value through as (1). This is what makes the hierarchy
   "normal" — the CLI is just another caller.
3. **`ZENODO_SANDBOX_TOKEN`**, *only* when `zenodo_domain` is the sandbox.
   Sandbox and production tokens are **not interchangeable** — a production
   token against `sandbox.zenodo.org` fails, and vice versa — so keeping both
   exported at once is the normal state for anyone who develops against sandbox
   and releases to production. Today CI works around this by piping
   `secrets.ZENODO_SANDBOX_TOKEN` into `ZENODO_TOKEN`
   (`.github/workflows/ci.yaml:63`); this makes that a first-class rule instead
   of a shell trick.
4. **`ZENODO_TOKEN`** in the process environment — the documented default, and
   the fallback for sandbox too if `ZENODO_SANDBOX_TOKEN` is unset.
5. **`.env` file** — see 1.1.2. Loaded into the environment before (3)/(4) are
   read, and never overriding a variable that is already set, so a real env var
   always beats a file on disk.

If nothing resolves, `resolve_token` returns `None` rather than raising —
unauthenticated reads of public records are a supported use (Part 5). The error
is raised **at the point of use**:

- write methods pass `requires_auth=True` to `_request`, which raises
  `MissingTokenError` *before* the request goes out;
- read methods let the server answer, and `_request` turns a `401`/`403` with no
  token resolved into a `MissingTokenError` whose message names the precedence
  chain above, instead of a bare `ZenodoHTTPError`.

`mask_token` (already in the codebase) stays the `repr` for the field, so a
client in a traceback never leaks the token.

#### 1.1.2 `.env` support — yes, but only in the CLI

**Recommendation: adopt it, and keep it out of the library's import path.**

It is worth doing: `make test-integration` already reads the token from a local
`.env` (Part 12.4), `.env` is already in `.gitignore:132`, and `python-dotenv` is
a small, dependency-free, BSD-3 package (so it clears `liccheck`). The thing to
avoid is the version that *does* complicate things for no reason — a library that
mutates `os.environ` as an import side effect, which surprises anyone embedding
`ZenodoClient` in a larger app and makes test isolation harder.

So:

- **CLI**: loads `.env` in the `@app.callback()` body, before any command body
  constructs a client:
  ```python
  load_dotenv(env_file or find_dotenv(usecwd=True), override=False)
  ```
  `override=False` is what gives us rule (5) < rules (3)/(4) for free. New global
  options: `--env-file PATH` (explicit; error if missing) and `--no-env-file`
  (skip discovery entirely).
- **Library**: never loads anything implicitly. Document the one-liner
  (`from dotenv import load_dotenv; load_dotenv()`) in the how-to guide for
  people who want it in a script or notebook.

One wrinkle this forces, and it is an improvement anyway: **drop typer's
`envvar="ZENODO_TOKEN"`** (`cli/app.py:73`). Typer resolves `envvar` at parse
time, *before* the callback body runs, so it would read the environment before
`.env` was loaded and silently ignore the file. Removing it puts the entire
precedence chain in `resolve_token` — a single, unit-testable place — with the
CLI contributing only rule (2). Cost: `--help` no longer auto-prints
`[env var: ZENODO_TOKEN]`, so the option's help text spells out the full order
explicitly (better documentation than the generated line anyway).

#### 1.1.3 Session injection

`session` is a constructor argument, so callers can supply their own configured
`requests.Session` (custom adapters, proxies, corporate CA bundle, `VCR`/
`responses` in tests, a shared connection pool across clients). We only build one
when none is given, via a **public** factory that carries all the transport
defaults:

```python
def build_session(
    *,
    max_retries: int = 5,
    backoff_factor: float = 1.0,
    retry_status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    """Session with the urllib3 Retry adapter mounted (Part 2)."""
```

This is the **only** place retry policy is expressed. Want a longer backoff, or
to retry a status we don't list? Build the session yourself and inject it:

```python
client = ZenodoClient(session=build_session(max_retries=10, backoff_factor=2.0))
```

Rules that keep injection honest:

- **We never mutate an injected session.** In particular the `Authorization`
  header is applied **per request** in `_request`, not written onto
  `session.headers`. Nice side effect: when no token resolves we send no
  `Authorization` header at all, so an injected session that carries its own
  auth keeps working.
- **Retry policy travels with the session**, so injecting one means the caller
  owns it — there is no client-level knob left to be confusingly ignored.
- **Lifecycle:** `close()` and `__enter__`/`__exit__` close the session **only if
  `_owns_session`**. Closing a session we were handed is a bug, not a courtesy.
- The `tenacity` upload retry and checksum verification (Part 2) sit above the
  session, so they still apply to injected sessions.

#### 1.1.4 Identifier types — `RecordID` as a `NewType`, not a class

**Recommendation: `NewType`, not an object.** The confusion worth defending
against is not "is this a string?", it is **"is this a record id or a parent
(concept) id?"** — two numeric strings that are silently accepted by every
endpoint and produce wrong-but-successful results.

```python
RecordID = NewType("RecordID", str)   # one version of a record
ParentID = NewType("ParentID", str)   # the all-versions "concept" id
```

- Methods **return** `RecordID` / `ParentID` (`create_record`, `new_version`,
  `get_latest_version_id`, `get_parent_id`, `create_new_version`).
- Methods **accept** `str | RecordID`, so `client.get_published("15187976")` and
  ids read from JSON/argv still work with no wrapping ceremony.
- Zero runtime cost, checked by the `mypy` + `ty` already configured in the
  template, and it documents the method map far better than nine bare `str`s.

A full `@define class RecordID` was considered and rejected: it only earns its
keep if it carries behaviour (`.url`, `.doi`, `.parent`), and that behaviour
needs the client's domain, which drags the client into an identifier object. It
would also force `str()`/converter handling at every CLI, JSON and f-string
boundary for little benefit. If URL/DOI helpers are wanted later, add free
functions — `record_url(record_id, zenodo_domain)` — rather than a class.

### 1.2 Method map (old → new)

| Old (`ZenodoInteractor`) | New (`ZenodoClient`) | Notes |
|---|---|---|
| `get_record` | `get_published(record_id)` | `GET /api/records/{id}` — published records only, see 1.2.2 |
| — | `get_record(record_id)` | published or draft, whichever it is (1.2.2) |
| `get_deposition` | `get_draft(record_id)` | `GET /api/records/{id}/draft` — read only, `404` if none |
| — | `create_record(metadata)` → `RecordID` | `POST /api/records` (brand-new record + its draft) |
| — | `create_or_get_edited_metadata_draft(record_id)` → `Record` | `POST /api/records/{id}/draft` — a published record's pending metadata edits (see 1.2.1, renamed in Part 6) |
| `create_new_version_from_latest` / `get_draft_deposition_id` | `new_version(record_id, *, import_files=False)` → `RecordID` | `POST /api/records/{id}/versions` (empty by default) — also get-or-create (1.2.1) |
| — | `import_files(record_id)` | `POST .../draft/actions/files-import` |
| `get_latest_deposition_id` | `get_latest_version_id(record_id)` → `RecordID` | via `versions` / `links.latest` |
| `get_concept_id` | `get_parent_id(record_id)` → `ParentID` | InvenioRDM "parent" id (all-versions id) |
| `update_metadata` | `update_metadata(record_id, metadata)` | `PUT /api/records/{id}/draft` |
| `get_metadata` | `get_metadata(record_id)` → `Metadata` | new schema (Part 6); no `user_controlled_only`, see the note at the top of Part 6 |
| `publish` | `publish(record_id)` | `POST .../draft/actions/publish` |
| `upload_file_to_bucket_url` | `upload_file(record_id, path, *, verify_checksum=True)` | init→content→commit (Part 2) |
| `upload_files` | `upload_files(record_id, paths, *, n_threads=4, verify_checksum=True)` | **additive**; skips files already on the draft with a matching MD5; warns on stripped paths (Part 11) |
| — | `upload_files_as_zip(record_id, paths_or_groups, ...)` | zip to preserve structure (Part 11) |
| — | `mirror_files(record_id, paths, *, n_threads=4, verify_checksum=True)` | **destructive**: draft ends up exactly `paths` (Part 3) |
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

#### 1.2.1 Can a record have more than one draft? No — and the server enforces it

Checked against InvenioRDM's `invenio-drafts-resources` service layer (the code
behind both endpoints):

- **`edit` (`POST /api/records/{id}/draft`)** — *"Draft exists - return it"*: it
  resolves the existing draft, checks permission and returns it, only creating
  one from the published record when none is found. (`edit` is the *upstream*
  service method's name; ours is `get_or_create_draft`, below.)
- **`new_version` (`POST /api/records/{id}/versions`)** — *"Draft for new version
  already exists? if so return it"*: it checks `record.versions.next_draft_id`
  and returns that draft rather than creating a sibling.

So a record version has **at most one draft**, and a record chain has **at most
one unpublished next version**. Both endpoints are already get-or-create; there
is no "create a second draft" operation to guard against.

What that means for the API:

- **`create_record` does not need an "ensure" mode.** It takes no `record_id` —
  there is no existing draft it could return, and every call is deliberately a
  brand-new record. Adding an ensure-flag there would be guessing which record
  the caller meant. Keep it single-purpose.
- **The get-or-create case is `get_or_create_draft`**, and the plan was missing
  it entirely. It is not optional: editing a **published** record requires
  `POST .../draft` first — `PUT .../draft` on a record with no draft is a `404`.
  So `update_metadata` (and `upload_files` against a published record) gains a
  `client.get_or_create_draft(record_id)` call in front of it, or an
  `ensure_draft: bool = True` parameter that does the same.
  We deliberately **do not** copy InvenioRDM's name for it (`edit`):
  `get_or_create_draft` says what it does at the call site, including that it is
  idempotent and safe to call repeatedly, whereas `client.edit(record_id)` reads
  like it is about to change something. It also sits next to `get_draft` (the
  read-only `GET`, which `404`s when there is no draft) in a way that makes the
  difference between the two obvious in autocomplete.
- **`new_version` is likewise idempotent**, which is exactly the behaviour a
  release script wants: re-running after a mid-way failure resumes the same
  draft instead of littering the record with siblings. Say so in the docstring —
  callers otherwise assume it always creates.

Caveat: this is upstream InvenioRDM behaviour, and Zenodo runs its own pinned
build. *(Part 6 renamed `get_or_create_draft` to
`create_or_get_edited_metadata_draft`, and restricted it to published records:
an unpublished record is already a draft, so there is nothing separate to edit.)*
Part 12 gets an explicit **idempotency test** (call `get_or_create_draft`
twice and `new_version` twice, assert the same id comes back both times) so a
divergence shows up as a failing test rather than a surprise in production.

#### 1.2.2 The read paths — ✅ IMPLEMENTED

**Done** (sequencing step 2): `get_published`, `get_draft`, `get_record`,
`get_metadata`, `get_parent_id` and `get_citation` (Part 10), plus the module-level
`retrieve_metadata` and `retrieve_citation`, with unit tests
(`tests/test_reads.py`) and live tests against production (read-only, no token)
and the sandbox (`tests/integration/test_read_draft_integration.py`). Deltas from
the text above, all deliberate:

- **Everything asks for `Accept: application/vnd.inveniordm.v1+json`.** Zenodo's
  default serialisation of a record is the legacy-compatible shape, which hides
  `access` and `pids` and renders `files` as a list. Part 4 found this; the read
  paths are where it starts mattering, so `INVENIORDM_JSON_ACCEPT` is sent on
  every record/draft read and the native document is what callers get.
- **The old `get_record` is `get_published`.** `get_record(record_id)` reads as
  "get whatever record this is", which is not what a method that hits
  `/api/records/{id}` and `404`s on an unpublished draft does. The name now
  belongs to the resolver below, which is what it always described.
  `get_published` / `get_draft` is a symmetric pair, in the same
  published-versus-draft vocabulary `is_draft` already uses, and it makes the
  choice at the call site an explicit one. Matching Zenodo's endpoint name is not
  worth the ambiguity, particularly as our own `record_id` refers to both.
- **`get_published` and `get_draft` never guess.** Each hits exactly one endpoint
  and lets a `404` be a `404`, so a caller who needs the published record
  specifically — or the draft specifically — can say so.
- **`get_record` is the public resolver**, and the one to reach for when you do
  not already know which of the two you have: published first, then the draft if
  we have a token, then `RecordNotFoundError`. It was originally private
  (`_get_record_or_draft`) with `get_metadata` as the only way to reach it, which
  was the wrong shape — resolving is useful on its own, and the returned record
  carries `is_draft`/`is_published` so the caller can see which they got. The
  three methods now read as a set: two specific, one that works it out.
- **`get_record` does not call `is_draft` first**, even though that is the
  obvious way to describe it. `is_draft` decides by fetching
  `/api/records/{id}`, so asking it first means fetching that endpoint to find
  out and then fetching it again to get the record. Falling back instead gives
  the same answer for one request on a published record and two on a draft.
- **The published record wins when both exist**, which is the same answer
  `is_draft(files_based=True)` gives. *(Part 6 kept it: the published record is
  what the record says to everyone else, and `get_draft` is how to read the
  pending changes. See 13.3.)*
- **The reads return `dict[str, Any]`, i.e. the parsed JSON, for now.** Typed
  models are the right end state and are listed in Part 6's work items, because
  typing a record means typing its `metadata`, which is exactly Part 6's job.
  Doing it before the schema is settled would mean writing the model twice. The
  method names deliberately do **not** say `_raw`: it would be noise while every
  read returns a `dict`, and it would need removing again as soon as they do not.
- **`get_metadata` returns the contents of `metadata`, not `{"metadata": ...}`.**
  That makes it symmetric with `update_metadata`, so metadata can be read off one
  record and applied to another without unwrapping. The legacy method returned
  the wrapper; this is part of the break.
- **`user_controlled_only` is not here yet.** Which keys Zenodo rather than the
  user controls is a schema question, so it lands with Part 6 rather than being
  guessed at now. *(Part 6's answer: it is not needed at all — `Metadata.to_json`
  only ever emits the shape Zenodo accepts.)*
- **The legacy `retrieve_metadata` is now `retrieve_metadata_legacy`**, freeing
  the name for the new helper, exactly as was done for `create_new_version`. It
  and the `retrieve-metadata` CLI command go in Part 8.

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
    files_mode: FilesMode = FilesMode.start_fresh,
    publish=False,
    n_threads=4,
) -> RecordID                                               # new version's record id
def get_reserved_doi(record_response) -> str                # reads pids.doi
```

Every helper takes `client=None` and builds a default `ZenodoClient()` when none
is passed — the same dependency-injection shape as 1.1.3, one level up.

`FilesMode` is the single knob for how a new version treats files (Part 4):
**`start_fresh`** (default), **`inherit`**, **`mirror`**.

- **`start_fresh` rather than `replace`** — "replace" invites the question
  *replace what with what?*, and reads as though something is being overwritten
  in place. Nothing is: the new version's draft simply begins empty and gets
  exactly the files you pass. `start_fresh` says that.
- **`inherit` rather than `import`** because `import` is a reserved keyword —
  `FilesMode.import` is a syntax error; `inherit` reads cleanly and describes
  carrying the previous version's files forward.
- **`mirror` rather than `sync`** — this mode *does* delete remote files that are
  not in the local list, and "sync" is not a reliable signal for that. The
  convention is split: `rsync` and `aws s3 sync` do **not** delete without
  `--delete`, while `rclone sync` does. A term that means opposite things in two
  of the three tools people know is exactly the wrong name for a destructive
  operation. `mirror` is unambiguous and has precedent for
  make-the-destination-match-exactly (`robocopy /MIR`, `lftp mirror`,
  `rsync --delete` is commonly described as mirroring). `exact_match` also works
  but reads as a comparison, not an action.

The three mode names line up one-for-one with what actually happens to the
draft's file list — nothing carried over, everything carried over, made to match
— which is the whole point of the enum.

---

## Part 2 — File upload: the commit flow + robustness — ✅ IMPLEMENTED

**Done.** `ZenodoClient.upload_file` and `delete_file`, plus two new modules —
`openscm_zenodo/checksums.py` (the shared MD5 helper and its timing logs) and
`openscm_zenodo/progress.py` (the 2.1 progress-bar contract) — with unit tests
and **live sandbox tests** (`tests/integration/test_upload_integration.py`).
Deltas from the text below, all deliberate:

- **The MD5 is computed in its own pass, not while the upload streams.** Part 3's
  diff needs the local MD5 *before* deciding whether to upload at all, so hashing
  during the upload would duplicate the work in the common path. The file is still
  read in chunks, so large files are fine.
- **Retry set is narrower than "any failure".** `should_retry_upload` retries
  `ChecksumMismatchError`, connection/timeout errors, and `ZenodoHTTPError` only
  for 429/5xx. Retrying a rejected upload four more times helps nobody.
- **The retried unit is the whole three-step flow**, not just the content `PUT`. A
  committed file's content cannot be replaced and a pending one cannot be resumed,
  so each attempt deletes and re-initialises. That also gives re-uploads and
  recovery from a half-finished upload for free.
- **Uploads return a `FileEntry`**, not a raw `dict`. It models `key`, `size`,
  `checksum` and `status`, exposes the parsed `md5`, and keeps the rest of
  Zenodo's response (timestamps, mime type, internal IDs, links) in `raw` rather
  than growing a field each time one turns out to be useful. `list_files`
  (Part 3) should return these too.
- **`MissingTokenError` is told which environment variables were checked**
  rather than hard-coding the precedence chain in its message. The variables come
  from `get_token_env_vars`, the same function that drives the lookup, so the
  message cannot drift — and it is now correct about the sandbox variable only
  applying to the sandbox.
- **`--sync`-style flags and parallelism are not here** — `upload_files`,
  `list_files` and `mirror_files` are Part 3, along with the progress-bar
  `position` slot allocator (the parameter exists, nothing allocates slots yet).
- `get_default_config` (`logging.py`) now writes through `tqdm.write` on stderr,
  which is what stops log lines shredding the bars.

InvenioRDM uploads are a three-step, explicitly-committed flow. This replaces the
single bucket `PUT`. The three steps become one `upload_file` method:

1. **Init** — `POST /api/records/{id}/draft/files` with `[{"key": filename}]`.
2. **Content** — `PUT /api/records/{id}/draft/files/{filename}/content`,
   streaming the bytes with the existing `tqdm.utils.CallbackIOWrapper` progress
   bar and `timeout_upload`. See Part 2.1 for the progress-bar contract.
3. **Commit** — `POST /api/records/{id}/draft/files/{filename}/commit`. The
   response reports the server-computed checksum (`"checksum": "md5:<hex>"`).

Robustness, integrated here and in the client:

- **Shared session + retry adapter.** All requests go through `self.session`
  (Part 1.1.3) with an `HTTPAdapter` mounting `urllib3.util.retry.Retry`
  (`total=max_retries`, `backoff_factor`, `status_forcelist=retry_status_forcelist`,
  `respect_retry_after_header=True`, all methods) — configured in `build_session`,
  not on the client. Gives status-based retry and honours Zenodo's `Retry-After`
  on 429s. ~~Also fixes the "weirdly flaky" parallelism that forced serial file
  deletes — re-enable `n_threads` there.~~ ❌ **Wrong, disproven against sandbox
  in Part 3.** The old code's comment was right: concurrent deletes on one draft
  race and Zenodo rejects the losers with `400 Not a valid value`, leaving those
  files in place. It is not a transport problem, so retries do not help. Deletes
  are serial; parallel *uploads* are fine.
- **Upload retry.** The content `PUT` streams a consumed, tqdm-wrapped file
  handle that `urllib3` cannot replay, so wrap `upload_file` in a `tenacity`
  retry that re-opens the file and resets the progress bar per attempt. See
  2.2 for why `tenacity` and not the alternatives.
- **Checksum verification.** Compute the local file's MD5 while streaming and
  compare against the checksum in the **commit** response. On mismatch raise
  `ChecksumMismatchError`, which is also in `tenacity`'s retry set so a corrupt
  transfer is retried before it fails. `verify_checksum=True` by default;
  opt-out for speed.
- **MD5 timing logs.** MD5 hashing is CPU-bound and, for large files, can be a
  non-trivial share of an upload/download and is easy to mistake for slow I/O.
  So the single shared MD5 helper (`_md5_hex` / the streaming variant, reused by
  upload here, `upload_files` / `mirror_files` in Part 3, and download in Part 5) logs its own
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

### 2.2 Why `tenacity` (and not `httpx`, or nothing)

Two different retry problems, and only one of them needs a library:

**Ordinary requests** — metadata `PUT`s, listings, deletes, publishes. Handled by
`urllib3.util.retry.Retry` mounted in `build_session`. It retries on **status
codes** (429/5xx), honours `Retry-After`, and does exponential backoff. `urllib3`
is already a transitive dependency of `requests`, so this costs nothing.

**The streaming upload `PUT`** — the one case `urllib3.Retry` cannot handle. It
retries by re-sending the *same body object*, and ours is a consumed,
non-seekable, tqdm-wrapped file handle; replaying it would send zero bytes. The
fix has to sit **above** the request, where it can re-open the file and reset the
progress bar. Same for `ChecksumMismatchError`, which is not an HTTP failure at
all — the request succeeded, the bytes were wrong — so no transport-layer retry
can see it.

Why not `httpx`:

- **`httpx`'s retry does not do what we need.** `HTTPTransport(retries=N)` retries
  **connection** errors only (`ConnectError`, `ConnectTimeout`). It explicitly
  does *not* retry on HTTP status codes — so it would not retry a single one of
  Zenodo's 429s, which is the failure we actually see. It also would not replay a
  consumed upload body, so it leaves the hard case untouched too.
- **It is a client swap, not a retry feature.** Adopting it means rewriting every
  call site, the streaming upload/download paths, `types-requests` → no stubs
  needed but new idioms, and dropping a dependency (`requests`) the project
  already has and pins. That is a large diff to buy a feature that does not
  cover our case.
- The one genuine `httpx` advantage — HTTP/2, async — is irrelevant here: this is
  a synchronous CLI/library and Zenodo does not reward connection multiplexing.

Alternatives to `tenacity` for that thin outer layer, honestly:

- **Hand-rolled loop** — `for attempt in range(n): ... except Retryable: sleep(backoff * 2**attempt + jitter)`.
  Perfectly viable, ~20 lines, zero new dependencies. The reason not to: we need
  the same policy in three places (upload content `PUT`, download, checksum
  mismatch), and hand-rolled jitter/`reraise`/last-exception handling is exactly
  the code that gets subtly wrong and untested.
- **`backoff`** — comparable API, notably less active than `tenacity`.
- **`stamina`** — nicer defaults, but it is a wrapper *over* `tenacity`, so it
  adds a dependency rather than avoiding one.

**Decision: `tenacity`.** It is the standard, it is small and widely vendored, and
it gives `retry_if_exception_type` (including our own `ChecksumMismatchError`),
capped exponential backoff with jitter, and `before_sleep` hooks for logging the
retry — all of which we would otherwise write ourselves. If dependency count ever
becomes the binding constraint, the hand-rolled loop is the fallback and the
blast radius is one decorator.

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

## Part 3 — Two file-writing methods: `upload_files` and `mirror_files` — ✅ IMPLEMENTED

**Done.** `list_files`, `upload_files`, `mirror_files`, `delete_files`,
`delete_all_files` and the shared `_diff_files`, with unit tests and live sandbox
tests (`tests/integration/test_upload_files_integration.py`). Deltas from the text
below, all deliberate:

- **Deletes are serial, and there is no `n_threads` on them.** Found by the live
  test: deleting in parallel makes Zenodo reject some deletes with
  `400 Not a valid value` and the files stay put. `mirror_files` still takes
  `n_threads`, but it only applies to the uploads.
- **`list_files` returns `dict[str, FileEntry]`, not `dict[str, str]`.** The plan
  wanted name→md5 here and name→size in Part 5; one type serves both, and
  `FileEntry.md5` gives the diff what it needs.
- **No `verify_checksum` on `upload_files`/`mirror_files`.** Working out what to
  upload needs each local checksum anyway, so checking it afterwards is free —
  the flag would only remove a safety net in exchange for nothing. `upload_file`
  keeps it (there the hash is a real extra read) and gained `local_md5=` so the
  plural methods can hand over the checksum they already computed, rather than
  the file being hashed twice.
- **Both return the draft's files after the call** (`dict[str, FileEntry]`),
  derived from the listing plus what was uploaded, with no extra request.
- **Bar layout:** tqdm counts lines *downwards*, so the overall files bar sits at
  line zero, i.e. at the top, with the per-file bars beneath it. The overall bar
  uses `leave=True` and stays once the operation finishes — "uploaded 40 of 40
  files" is worth keeping — while the per-file bars keep `leave=False` and are
  erased as each file completes. `scripts/demo-progress-bars.py` shows this
  without touching Zenodo.
- **The progress-bar position allocator** (2.1) landed here rather than in Part 2,
  since this is where parallelism arrives: `PositionAllocator` hands each worker a
  line and takes it back when the file finishes. Running out of lines raises,
  rather than falling back to letting tqdm choose: it cannot happen when `n_slots`
  matches the number of workers, so it would only ever mean the two had been wired
  up wrongly. `tqdm.set_lock()` is *not* called
  — tqdm's default write lock already covers threads, so setting our own would be
  redundant.
- **Basename collisions** (`DuplicateFileKeyError`) are still Part 11; today two
  paths with the same name silently collapse to one.



There is no `sync_files` and no `delete_extraneous` flag. The ambiguity of "sync"
is not fixed by documenting the flag — it is fixed by never making the caller
read a flag to find out whether a call deletes their data. **Two methods, one of
which is destructive and says so in its name:**

```python
def list_files(self, record_id: str) -> dict[str, str]:
    """Map of filename -> md5 hex for the draft's current files."""

def upload_files(self, record_id, paths, *, n_threads=4, ...) -> ...:
    """Add `paths` to the draft. Never deletes anything."""

def mirror_files(self, record_id, paths, *, n_threads=4, ...) -> ...:
    """Make the draft's files exactly `paths`. Deletes anything else."""
```

`GET /api/records/{id}/draft/files` returns each file's `key` (name) and
`checksum`, so both methods diff locally with no downloads, reusing the Part-2
streaming MD5 helper:

```python
remote = self.list_files(record_id)                 # name -> md5
want = {p.name: p for p in paths}
to_upload = [p for name, p in want.items() if remote.get(name) != _md5_hex(p)]
to_delete = [n for n in remote if n not in want]    # mirror_files only
```

- **`upload_files`** does the `to_upload` half. Skipping files whose name+MD5
  already match is a pure optimisation — the resulting draft is identical either
  way — so it is unconditional rather than a flag, and re-runs after a partial
  failure are cheap. This is why the old `--sync` distinction disappears from the
  CLI: plain upload is *already* the incremental one.
- **`mirror_files`** does both halves: deletes first, then uploads, so a rename
  doesn't transiently exceed a quota. It is the only method in the library that
  removes files the caller didn't name, and the name is the warning.
- Both share one private `_diff_files` helper, so there is no duplicated diff
  logic to drift.
- `delete_files` / `delete_all_files` stay as the explicit, targeted deletes.

Naming symmetry with Part 4: `mirror_files` is what `FilesMode.mirror` calls, and
`upload_files` is what the other two modes call.

---

## Part 4 — New versions — ✅ IMPLEMENTED

**Done.** `new_version` (renamed `create_or_get_new_version` in Part 6, since it
returns the version already in progress rather than always creating one),
`import_files`, `publish`, `update_metadata`,
`get_latest_version_id`, `FilesMode` and the `create_new_version` helper, with
unit tests and live sandbox tests
(`tests/integration/test_versions_integration.py`). Deltas and findings, all
verified against the sandbox:

- **`new_version` works from *any* published version, not just the latest.** The
  legacy API required the latest id, which is why the old code looked it up
  first; InvenioRDM does not. `get_latest_version_id` is still there because it
  is useful, but `create_new_version` no longer needs it.
- **`new_version` really is get-or-create on Zenodo's build** — calling it twice,
  and calling it from two different versions of the same record, all return the
  same draft id. §1.2.1's caveat is now a passing test.
- **`import_files` only works into an *empty* draft.** A second import fails with
  `400 "Please remove all files first."`, which would break exactly the re-run
  that Part 4 promises. So `import_files` checks first and returns `False`
  instead of failing when the draft already has files. The underlying Zenodo
  behaviour has its own test, so we notice if it changes.
- **`FilesMode.mirror` refuses to run without an explicit `files`.** Mirroring
  deletes whatever is not listed, so it may not happen by omission; pass
  `files=[]` to mean "no files".
- **A metadata-only `PUT .../draft` does *not* wipe `access`** (checked with a
  `restricted` record). So `update_metadata` can nest under `metadata` and leave
  everything else alone. It is otherwise a pass-through — schema translation and
  validation are still Part 6.
- **Useful for Part 6:** Zenodo validates metadata when a record is **published**,
  not when the draft is created or updated. A draft will happily accept an
  incomplete document and then fail at publish with, for example,
  `metadata.publisher: Missing publisher field required for DOI registration.`
  That is a good argument for Part 6's client-side validation helper: it is the
  only way to catch this before the irreversible step.
- **Useful for Part 6:** Zenodo's default serialisation of
  `/api/records/{id}[/draft]` is a *legacy-compatible* shape which hides `access`
  and `pids` and renders `files` as a list.
  `Accept: application/vnd.inveniordm.v1+json` returns the native InvenioRDM
  document (`access`, `pids`, `parent`, `versions`, `is_draft`, …). Part 6 will
  need that header.
- **The legacy module-level `create_new_version` is now
  `create_new_version_legacy`** so the new one can take the planned name. It and
  its CLI command go in Part 8.
- **Publishing is tested live like everything else.** Published sandbox records
  cannot be deleted, so `tests/integration/test_publish_integration.py` leaves
  records behind rather than cleaning up, which is why it is kept short. Having a
  token is taken as consent to publish to the sandbox, so there is no extra
  marker; `Part 12.1`'s "publish-path tests are marked and kept few" is therefore
  only half true — kept few, not separately marked.



`new_version` is trivial on InvenioRDM: `POST /api/records/{id}/versions` returns
a draft with **no files**. Inheriting the previous version's files is the opt-in
action `import_files`. `FilesMode` in the high-level `create_new_version`
expresses the three sensible policies:

- **`start_fresh`** (default) — empty draft, `upload_files(files)`. The "don't
  carry anything over" behaviour that was impossible on the legacy API.
- **`inherit`** — `import_files` first (reuse previous files, no storage
  duplication), then `upload_files(files)` on top.
- **`mirror`** — `import_files`, then `mirror_files(files)`: unchanged inherited
  files stay (no re-upload), **inherited files not in `files` are deleted**, only
  changed/new files are transferred. The efficient release path. Named `mirror`,
  not `sync`, precisely because it deletes — see the naming note in Part 1.3.

```python
new_id = create_new_version(
    record_id, client,
    metadata=meta, files=paths, files_mode=FilesMode.mirror, publish=True,
)
```

`new_version` is get-or-create (Part 1.2.1), so re-running `create_new_version`
after a failed upload resumes the same draft rather than creating a second one.

---

## Part 5 — File retrieval (download) — ✅ IMPLEMENTED

**Done.** `download_file`, `download_files` and `retrieve_files`, with unit tests
and live tests against both production (read-only, no token) and the sandbox
(`tests/integration/test_download_integration.py`). Deltas from the text below,
all deliberate:

- **No `draft` argument, anywhere.** A record is either published or still a
  draft and its ID already says which, so there is a named method for asking:
  `is_draft(record_id)`. 5.2's `draft` switch is gone, and so is
  `download_files(draft=...)`.
- **A record ID which we cannot find is an error, not something to work around.**
  Asking for an ID means believing the record is there, so `is_draft` looks for
  the published record, then (if we have a token) for a draft, and raises
  `RecordNotFoundError` if neither is there. The message depends on whether we
  had a token, and *which* one: with one, "we could not find it, even using the
  token from `$ZENODO_TOKEN`" plus a reminder that sandbox and production tokens
  are not interchangeable; without one, "drafts and restricted records are not
  visible without a token, supply one if this is either of those". Naming the
  token's origin next to the domain is the point — a production environment
  variable against `sandbox.zenodo.org` is the usual reason a record which is
  definitely there cannot be found, and seeing the two side by side gives that
  away immediately. `resolve_token_with_source` returns where the token came
  from alongside it, and the client keeps it on `token_source`, which is a
  description and never the token itself, so it is safe in messages and in the
  `repr`. That removes the guessing entirely — by the
  time we answer, we know we can see the record — and it means a typo'd ID says
  so instead of surfacing a bare `404` from wherever we happened to look last.
  Two earlier attempts got this wrong by *inferring* draft-ness from something
  being absent, which cannot distinguish a draft from a restricted record from
  a typo.
- **Verified: a published record *can* have a draft, and it makes no difference
  here.** `POST /api/records/{id}/draft` on a published record gives an object
  with the same ID and both `is_draft` and `is_published` true. Its **metadata
  can be edited in place** and published again, keeping the same ID and DOI; its
  **files cannot** — Zenodo answers `403 "Bucket is locked for modifications."`,
  which is precisely why `new_version` exists. So for anything to do with files,
  an edit draft of a published record simply *is* the published record, and
  `is_draft` never even looks for one: finding the published record has already
  answered the question. Deleting an edit draft leaves the published record
  untouched, and a `GET` on `/draft` does not create one — a freshly published
  record `404`s until a draft is explicitly asked for. This is the behaviour
  §1.2.1's `get_or_create_draft` will wrap
  (`create_or_get_edited_metadata_draft`, as of Part 6).
- **The write paths do not go through `list_files`.** `_diff_files`,
  `import_files` and `delete_all_files` always mean the draft, so they use a
  private `_list_draft_files` and there is no resolution step, and no ambiguity,
  on the upload path.
- **Downloads follow the `content` link from the listing** rather than building a
  URL. Zenodo gives one per file for both published records and drafts, which is
  what removes the last place the two endpoints would have had to be told apart.
  The listing also supplies `size` for the progress bar and `checksum` for
  verification, so the one extra request pays for itself.
- **The checksum is verified before the file is renamed into place**, not after.
  The first version verified after the atomic rename, which meant a corrupted
  download still landed under its real name — caught by a test. Now a failure of
  any kind removes the `.part` file and leaves nothing behind at all.
- **Verification is nearly free here**, unlike upload: the bytes are hashed as
  they arrive, so there is no extra read. `verify_checksum` is still there but
  there is little reason to turn it off.
- **`FileNotOnRecordError`** is new: asking for a name which is not on the record
  lists the names which are, since a typo is the usual cause. `download_files`
  checks every requested name up front, so it fails before downloading anything
  rather than part way through.
- **`should_retry_upload` is now `should_retry_transfer`**, and the retry policy
  is built by a shared `_build_transfer_retrying`, since uploads and downloads
  want exactly the same thing.
- **`download_files` takes `dest: Path | Mapping[str, Path]`** — a directory, or
  exactly where each file goes. The mapping also says *which* files are wanted,
  so passing `filenames` as well is an error. Looping over `download_file` would
  give the same control but lose the parallelism, which is why this is worth a
  union rather than a documentation note.
- **`FileEntry.key` is `FileEntry.filename`**, so the whole API says `filename`;
  `key` is Zenodo's word for it and is still in `raw`. `content_url` is a real
  field parsed in `from_json`, so a response shaped differently to what we expect
  fails where we read it rather than much later.
- **The module-level helper is `download_files`, not `retrieve_files`** —
  matching the method it wraps beats matching `retrieve_metadata`.



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
(`"md5:<hex>"`) — the same metadata the Part 3 diff already relies on.

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
  matches the remote checksum (cheap, idempotent re-runs, mirroring the Part 3
  upload diff).

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

## Part 6 — Metadata schema (the main migration cost) — ✅ IMPLEMENTED

**Done** (sequencing step 3). `openscm_zenodo/metadata.py` (`Metadata`, `Creator`,
`PersonOrOrg`, `Affiliation`, `Identifier`, `Right`, `load_metadata`,
`as_metadata`, `check_not_legacy`), `Record` in `zenodo.py`, the typed reads,
`create_or_get_edited_metadata_draft` / `has_edited_metadata_draft`,
the `update_metadata` guard and `publish`'s
pre-flight validation, with unit tests (`tests/test_metadata.py`, and the write
paths in `tests/test_versions.py`) and live sandbox tests
(`tests/integration/test_metadata_integration.py`, plus the in-place correction
in `test_publish_integration.py`). 13.3 is settled — see below. Deltas from the
text below, all deliberate:

- **The models are split by what Zenodo requires of them.** `Person` /
  `Organisation` and `Creator` / `Contributor` are separate classes rather than
  one with a `type` and an optional `role`, so "a person needs a `family_name`,
  an organisation needs a `name`, a contributor needs a `role` and a creator may
  not have one" is guaranteed at construction instead of being re-checked in
  `find_problems`.
- **The whole schema is modelled**, not just the common fields: `subjects`,
  `contributors`, `dates`, `related_identifiers`, `funding` and `languages` as
  well. `raw` is empty for the production record the tests read, which is the
  point — it is now the place things Zenodo *adds later* land, rather than the
  place half the schema lives.
- **`Metadata.from_file` replaced `load_metadata`, and `as_metadata` is gone.**
  Methods take a `Metadata` and nothing else, so a `Metadata | Mapping | Path`
  union does not leak through every signature; converting happens once, at the
  boundary, where the error can name the file.
- **Zenodo discards values it cannot read rather than refusing them**, verified
  against the sandbox: a `publication_date` of `"2021-13-45"` and a `version` of
  200 characters both come back `200` with the field simply absent. That is the
  strongest argument for validating at all, and it is why `update_metadata` now
  diffs what came back against what it sent and warns about the difference.
- **`publication_date` is EDTF level 0, not `YYYY-MM-DD`** — also verified:
  `"2021"`, `"2021-03"` and `"2021-03-09/2021-04-10"` are all accepted. The
  first version of `find_problems` would have rejected metadata Zenodo is happy
  with. `is_edtf_date` checks the field widths itself and hands the values to
  `datetime.date`, rather than using a regular expression: the near misses
  (`"2021-3-09"`, full-width digits, a leading `+`) are the whole point, and
  they are easier to enumerate as tests than to read out of a pattern.
- **Warnings go through `warnings.warn`, not the logger.** The logger is
  disabled until a caller enables it, so a warning about Zenodo quietly
  discarding a field never reached the person who most needed it. They carry a
  `ZenodoWarning` category so they can be silenced or promoted as a group.
- **Vocabulary checks cover every vocabulary field**, not just `resource_type`:
  `find_unknown_vocabulary_values` also checks identifier schemes and relation
  types, and names where each unrecognised value is.
- **Two draft readers, because Zenodo has one endpoint for two things.**
  `get_draft` returns an unpublished record and never a published record's
  pending metadata edits; `get_edited_metadata_draft` reads those, without
  starting any. The distinction is not cosmetic — the endpoint answers `200` in
  both cases, so the returned document has to be inspected.
- **There is no `user_controlled_only`, and it is not coming.** It existed so
  that metadata read off one record could safely be written to another;
  `Metadata.to_json` only ever emits the shape Zenodo accepts, so that is now
  unconditional and there is nothing left for a flag to strip. What Zenodo adds
  on the way out is not a set of server-owned *keys* — it is display text
  expanded into the vocabulary entries themselves (`resource_type.title` in
  every locale, `rights[].description`/`icon`/`props`), so a key-stripping flag
  was the wrong shape for the problem in the first place.
- **Verified against the sandbox: Zenodo accepts the expanded shape back.**
  Sending a production record's metadata verbatim, expansions and all, is
  accepted. So dropping them is about not disagreeing with Zenodo, not about
  being rejected — but it does mean `to_json` is stable, which is what
  `test_to_json_is_stable` pins.
- **`Record.is_published` is gone.** It and `is_draft` were two names for one
  question, so `is_draft` is now simply its opposite, and
  `is_edited_metadata_draft` carries what `is_published` was really being used
  for on the draft endpoint.
- **`Metadata`'s fields are all optional.** A draft's metadata legitimately is
  partial: Zenodo only checks completeness at publish. So `from_json` tolerates
  anything and `find_problems`/`validate` are where completeness lives.
- **Validation is split by when it bites.** Structural mistakes (legacy schema,
  legacy creators) raise at parse time, so they cannot reach the wire.
  Completeness is checked by `publish(validate=True)`, not by
  `update_metadata` — filling a draft in over several calls is normal, and
  refusing it would break that. Confirmed live: Zenodo reports one *round* of
  missing fields at a time, so its own answer never lists everything.
- **`Record` lives in `zenodo.py`, not its own module.** It needs `RecordID` and
  `ParentID`, and moving those out of `zenodo.py` would break every
  `[...][openscm_zenodo.zenodo.RecordID]` docs cross-reference for no gain.
- **`get_or_create_draft` is `create_or_get_edited_metadata_draft`, and returns
  a `Record`.** An edit draft keeps the record's ID, so the ID is not news; the
  draft is. The name says which of the two draft-shaped things it means, and the
  method applies only to published records — a record which was never published
  *is* a draft, and asking it for one raises `RecordNotPublishedError`.
  `has_draft` became `has_edited_metadata_draft` on the same grounds.
- **`new_version` is `create_or_get_new_version`**, at both the method and the
  helper, because it returns the version already in progress rather than always
  creating one. **`create_or_get_*` is the house word order** for this pattern,
  so the metadata one matches it.
- **Every method which takes a record ID also takes a `Record`.** One
  `get_record_id` coercion, applied at every public entry point, with a test
  which walks the class and fails if a method is added without it — a missed
  coercion would put a `repr` in a URL path, which is a silent wrong request
  rather than a crash.
- **`update_metadata` does not check first, it translates the failure.** The
  guard is a `404` from the `PUT` turned into `RecordNotWritableError` (or
  `RecordNotFoundError`, distinguished by one look at `/api/records/{id}` on the
  error path), so the happy path costs nothing extra.
- **`RecordNotWritableError` covers both files and metadata**, via a `what`
  parameter, rather than being two exceptions. 13.5's step 1 message for files
  is the `what="files"` branch and is already written.

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
- **Give the reads a real return type.** `get_published`, `get_draft`,
  `get_record` and `get_metadata` all return `dict[str, Any]` today
  (see 1.2.2). Introduce `Metadata` and a `Record` (which carries `metadata`,
  `access`, `pids`, `parent`, `versions`, `is_draft`/`is_published`) and return
  those instead, in the style of `FileEntry` — parsed fields for what we use,
  `raw` for the rest, so a response shaped differently to what we expect fails
  where we read it. This belongs **here** rather than in step 2: typing a record
  means typing its `metadata`, so doing it before the schema is settled means
  writing the model twice. `is_draft` (13.3) is the other thing this unlocks —
  `is_draft`/`is_published` are fields on the record, so a typed record makes the
  two-questions-not-one point in 13.3 concrete.
- `user_controlled_only` now strips InvenioRDM server-managed keys (`pids`,
  `publication_date` if server-set, etc.). This is what `get_metadata` is still
  missing from 1.3's signature, deliberately: which keys Zenodo controls is a
  schema question.
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
    [--n-threads 4] [--mirror] \
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

- **`upload-files`** — thin wrapper over `client.upload_files` (Part 2), which
  already skips files whose name+MD5 match the draft, so re-runs in CI only
  transfer what changed with no flag needed. The old `--sync` therefore has
  nothing left to switch on and is gone; `--delete-extraneous` is replaced by a
  single **`--mirror`**, which calls `mirror_files` (Part 3) and makes the draft
  match the local set exactly, deleting anything else. One destructive flag, named
  for what it does. Warns when a local path is stripped, and `--zip` bundles
  everything into one archive to preserve structure (Part 11); grouping into
  several archives is Python-only.
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

Global options: `--token`, `--zenodo-domain`, `--version`, `--no-logging`,
`--logging-level`, `--logging-config`, plus **`--env-file PATH`** and
**`--no-env-file`** (Part 1.1.2). `--token` no longer declares typer's
`envvar="ZENODO_TOKEN"`; it is passed through to `ZenodoClient(token=...)` and
the environment/`.env` fallbacks are handled by `resolve_token`, so the whole
precedence chain lives in one place. The option's help text states the order
explicitly: `--token` → `ZENODO_SANDBOX_TOKEN` (sandbox only) → `ZENODO_TOKEN` →
`.env`.

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
  `tenacity>=8` and `python-dotenv>=1` (CLI `.env` support, Part 1.1.2 — small,
  no transitive deps, BSD-3 so `liccheck` is happy). ~~fix `description`~~ ✅ done in Part 0 (commit `72c0ecc`);
  ~~fix the `keyords` typo → `keywords`~~ ✅ done in commit `104b956`. Still
  outstanding here: add `"download"` to `keywords` (currently
  `["openscm", "zenodo", "command-line"]`).
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
    mismatching commit checksum → `ChecksumMismatchError`), the shared
    `_diff_files` logic behind `upload_files` / `mirror_files` (including that
    `upload_files` never deletes and `mirror_files` does), `reserve_doi`, and
    `load_metadata`.
  - CLI tests for the three retained commands (Part 8): `upload-files` with and
    without `--mirror`, `download-files` with `--draft` and with a subset of
    filenames, and `retrieve-citation` across `--format`/`--style`.
  - Unit tests for the MD5 timing logs: with a `caplog`-style capture, assert
    the shared helper emits the start/completion `debug` records and the
    over-threshold `info`/`warning`, and that it is silent below the threshold.
  - Unit tests for download (mock the file listing + streamed content):
    checksum mismatch → `ChecksumMismatchError`, the `overwrite` guard, the
    skip-when-local-MD5-matches fast path, and `draft=True`/`draft=False`
    hitting the right endpoints.
  - towncrier fragment for the breaking release.

---

## Part 10 — Citation export (configurable formats) — ✅ IMPLEMENTED

**Done** (with the rest of sequencing step 2). `CitationFormat`,
`CITATION_FORMAT_ACCEPT`, `KNOWN_CITATION_STYLES`, `CITATION_STYLE_ALIASES`,
`ZenodoClient.get_citation` and the module-level `retrieve_citation`, with unit
tests and live production tests covering **every** format and several styles
(`tests/integration/test_read_integration.py`). Deltas from the text below:

- **The old implementation was not broken after all, and 10.1's claim below is
  wrong.** `get_bibtex_entry` requests `/records/{id}/export/bibtex` — the *web*
  path, not `/api/records/{id}/export/bibtex` — and that still returns `200`
  today. Only the `/api/...` variant `404`s, which is what was checked. So the
  motivation for Part 10 is what it can do (every export format, styled
  citations, one code path through `_request`), not a dead endpoint. The wider
  point about drift stands: nothing would have told us either way, which is what
  Part 12's live tests are for, and `test_get_citation_formats` is now that test.
- **The style rules are split in two**, which is how both of 10.2's sentences can
  hold at once. A style in `CITATION_STYLE_ALIASES` — one we have *verified*
  Zenodo rejects — raises `UnknownCitationStyleError` before the request, naming
  the ID which does work. Any other unrecognised style is sent with a warning, so
  the CSL styles we have not enumerated stay reachable.
- **`style`/`locale` for a non-`citation` format are only warned about when they
  were actually changed**, so passing the defaults around (as the CLI will) is
  silent.
- **`warn_unknown_style: bool = True`** silences the unchecked-style warning for
  someone who uses a style they know works. It does not silence
  `UnknownCitationStyleError` — "we have not checked this" and "we have checked
  this and it does not work" are different things, and there is a test saying so.
  Part 8 should give it `--no-warn-unknown-style`, alongside Part 11's
  `--no-warn-path-stripped`.
- **`CitationFormat`'s values are the user-facing names** (`datacite-json`), not
  the mime types, with `CITATION_FORMAT_ACCEPT` doing the translation. Collapsing
  the two would put mime types on the command line and in help text, and would
  tie a public identifier to a transport detail Zenodo could change underneath
  us. The ×9 parametrised tests mean a format added without an `Accept` entry
  fails the suite rather than raising a `KeyError` at a user.

### 10.1 The endpoint (verified against live Zenodo, 2026-07-25)

~~**The current implementation is already broken.** `zenodo.py:205` requests
`/records/{id}/export/bibtex`; that path now returns **404** for every format
(`bibtex`, `csl`, `datacite-json`, `datacite-xml`, `dublincore`, `json-ld`,
`marcxml`, `dcat-ap` all checked).~~ ❌ **Wrong** — see the note above: the code
requests the web path, which still works. InvenioRDM serves exports through
**content negotiation on the record itself**, and that is what we use:

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
  `upload_files`, `mirror_files` and `upload_files_as_zip`; `--no-warn-path-stripped`
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
error message says exactly that. `mirror_files` (Part 3) diffs on remote name
and gains the same check.

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
the Part 3 diff, which skips files whose name+MD5 already match — every
re-run would re-upload the whole archive. So `deterministic=True` (the default):

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

| Method / endpoint | Client method | Target | Live test? |
|---|---|---|---|
| `GET /api/records/{id}` | `get_published` | prod (read) | ✅ |
| `GET /api/records/{id}` + `Accept` (×9 formats) | `get_citation` | prod (read) | ✅ |
| `GET /api/records/{id}?style=&locale=` | `get_citation(fmt=citation)` | prod (read) | ✅ |
| `GET /api/records/{id}/files` | `list_files(draft=False)` | prod (read) | ✅ |
| `GET /api/records/{id}/files/{name}/content` | `download_file(draft=False)` | prod (read) | ✅ |
| `GET /api/records/{id}/versions` / `links.latest` | `get_latest_version_id` | prod (read) | ✅ |
| parent id | `get_parent_id` | prod (read) | ✅ |
| `POST /api/records` | `create_record` | sandbox |  |
| `GET /api/records/{id}/draft` | `get_draft` | sandbox | ✅ |
| `POST /api/records/{id}/draft` | `create_or_get_edited_metadata_draft` | sandbox | ✅ |
| `PUT /api/records/{id}/draft` | `update_metadata` | sandbox | ✅ |
| `POST /api/records/{id}/draft/files` | upload init | sandbox | ✅ |
| `PUT .../draft/files/{name}/content` | upload content | sandbox | ✅ |
| `POST .../draft/files/{name}/commit` | upload commit | sandbox | ✅ |
| `GET /api/records/{id}/draft/files` | `list_files(draft=True)` | sandbox | ✅ |
| `GET .../draft/files/{name}/content` | `download_file(draft=True)` | sandbox | ✅ |
| `DELETE .../draft/files/{name}` | `delete_files` | sandbox | ✅ |
| `POST .../draft/pids/doi` | `reserve_doi` | sandbox |  |
| `POST .../draft/actions/publish` | `publish` | sandbox | ✅ |
| `POST /api/records/{id}/versions` | `new_version` | sandbox | ✅ |
| `POST .../draft/actions/files-import` | `import_files` | sandbox | ✅ |

### 12.3 Scenario tests

On top of per-endpoint coverage:

- **Full lifecycle** (sandbox): `create_record` → `update_metadata` →
  `reserve_doi` → `upload_files` → `list_files` → `publish` → `new_version` for
  each `FilesMode` → `mirror_files` → `download_files` round-trip into a temp dir
  with checksum verification.
- **Restricted access** (sandbox): create a restricted record, then confirm
  download succeeds with a token and returns `403` (as `ZenodoHTTPError`) without
  one — the embargoed path from Part 5.
- **Idempotent re-run**: `upload_files` twice with unchanged files uploads nothing
  the second time; `download_files` twice re-downloads nothing.
- **Draft idempotency** (sandbox, Part 1.2.1): `create_or_get_edited_metadata_draft` twice on a
  published record returns the same draft id, and `new_version` twice returns the
  same next-version id — pinning the upstream InvenioRDM get-or-create behaviour
  against Zenodo's actual build.
- **`mirror_files` deletes, `upload_files` does not** (sandbox): upload two files,
  then call each with only one of them and assert the remote file list — the
  single most important behavioural difference in the library.
- **Token precedence** (unit, Part 1.1.1): `resolve_token` with an injected `env`
  mapping — explicit beats `ZENODO_SANDBOX_TOKEN` beats `ZENODO_TOKEN`; sandbox
  domain prefers `ZENODO_SANDBOX_TOKEN`; production ignores it; nothing set
  returns `None`, and a write then raises `MissingTokenError` before any request.
  Plus a CLI test that a `.env` in `tmp_path` is picked up but does **not**
  override a real env var.
- **Path stripping and zipping** (sandbox, Part 11): upload `sub/dir/f.nc` and
  assert it lands as `f.nc` with the warning emitted (and silent under
  `warn_path_stripped=False`); colliding basenames raise `DuplicateFileKeyError`
  before any request; `upload_files_as_zip` round-trips — upload an archive,
  download it, unzip it, and assert the directory structure survived.
- **Zip determinism** (unit, Part 11.3): zipping the same inputs twice produces
  byte-identical archives, and a zip-then-`upload_files` re-run uploads nothing the
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

## Part 13 — File writes never target a published record

Part 5 settled one principle — **`draft` is never a parameter of the public API**
— and the audit below confirms it held everywhere. This part adds the second and
records what it takes to guarantee it *ourselves*.

> **Invariant.** A published record's **files** are immutable to us. Every file
> write goes to a draft, and we establish that the target *is* an unpublished
> draft rather than relying on Zenodo to refuse. `new_version` is the documented
> exception: it addresses a published record in order to create a *new* one and
> never modifies it.
>
> **Metadata is deliberately out of scope here.** A published record's metadata
> *can* legitimately be changed in place, so "never target a published record" is
> the wrong rule for it. That is 13.3, and it stays open.

### 13.1 Where "draft" lives today (audit, for reference)

| Surface | How draft-ness is decided | Status |
|---|---|---|
| `download_file`, `download_files`, `list_files` | `is_draft(record_id)`, then the matching listing endpoint; content follows the listing's `content_url`, so the two endpoints are never told apart twice | ✅ |
| **file writes** — `upload_file(s)`, `mirror_files`, `delete_file(s)`, `delete_all_files`, `import_files` | hard-coded `/draft` in the URL | ✅ in effect, see 13.2 |
| **metadata / lifecycle** — `update_metadata`, `publish` | hard-coded `/draft` in the URL; a `404` from `update_metadata` becomes `RecordNotWritableError` | ✅ settled, see 13.3 |
| `new_version` | `POST /api/records/{id}/versions` — published by design | ✅ the documented exception |
| `is_draft(record_id)` | the question itself: published first, then draft | ✅ — `files_based` is gone, `has_draft` is the other question (13.3) |
| `ZenodoInteractor.*` (legacy) | the legacy deposit API's own draft rules | legacy; dies with Parts 6/8, not worth hardening |

No public method takes a `draft` argument, and `is_draft` no longer takes a
`files_based` argument either — 13.3 split it into two questions instead.

### 13.2 The invariant today: upheld, but by Zenodo rather than by us

There are **eight** mutating requests in `ZenodoClient`. Five are file writes
(`delete_file`, `_initialise_file`, `_upload_file_content`, `_commit_file`,
`import_files`), two are metadata/lifecycle (`update_metadata`, `publish`), and
one is `new_version`. All seven of the first two groups are addressed to
`/api/records/{id}/draft/...`, and nothing in the client ever issues `POST
/api/records/{id}/draft` — **we never create an edit draft of a published
record.**

For file writes, that holds up under both reachable states of a published record:

| | No edit draft | Has an edit draft |
|---|---|---|
| `upload_*`, `delete_*`, `mirror_files` | `404` — refused | `403 "Bucket is locked for modifications."` — refused (verified in Part 5) |
| `_diff_files` / `_list_files_at(draft=True)` | `404` | `200`, listing the published files |
| `import_files` | `404` | sees files already there, returns `False` — a silent no-op |

**So there is no correctness hole on the file side: Zenodo refuses every file
write against a published record.** This is worth stating plainly, because it sets
the priority — the work below is hardening and error quality, not a bug fix, and
it does not need to block Part 6.

What is still worth doing, and why:

1. **The guarantee is currently the server's, not ours.** It rests on Zenodo
   continuing to lock published buckets. Checking it ourselves is the same
   standing-guard argument as Part 12's scheduled CI run: it turns "Zenodo changed
   the rules" into a clear failure rather than a surprise.
2. **The failure lands in the wrong place.** With an edit draft present, the file
   *listing* succeeds, so `mirror_files` computes a plausible diff and only fails
   once it starts issuing requests. Failing at the front door is better than
   failing halfway through a batch.
3. **The error is unhelpful.** A bare `404` or `403` does not tell the user the
   thing they need to hear, which is "this record is published — use
   `new_version`".
4. **`import_files` reports success-ish.** Returning `False` means "already had
   files", which on a published record is misleading — nothing was inherited and
   nothing ever could be.

**The fix: `_assert_writable(record_id)` at the top of every public file-write
method**, raising `RecordNotWritableError` unless `is_draft(record_id,
files_based=True)` is `True`.

- **Cost**: one `GET` per write *call* (not per file), which `upload_files`
  amortises over the whole batch.
- **Race-safe in the direction that matters.** The guard says draft, the record is
  published, the write then fails at the server (`404`/`403`) — so the guard never
  lets through a write the server would accept. The other direction cannot happen:
  published is terminal, so a `False` never goes stale. See 13.4.
- **Not applied to `update_metadata` or `publish`.** Those are 13.3's problem, and
  guarding them now would pre-empt a design decision we have not made — it would
  rule out in-place metadata correction, which is a feature Zenodo genuinely
  offers.

### 13.3 Metadata — ✅ SETTLED (with Part 6)

**Settled**, and the `NotImplementedError` is gone. What was decided, against the
four questions below:

- **`is_draft` loses `files_based` entirely.** The question it names — "has this
  record been published?" — never had two answers; the second question was
  hiding behind the flag. It is now
  **`has_edited_metadata_draft(record_id)`**: "does this published record have
  metadata edits which have not gone out?". A published record with a pending
  correction answers `False` to `is_draft` and `True` to that, and both are
  useful. It applies **only to published records** — an unpublished record *is*
  a draft, so the question does not arise and it raises
  `RecordNotPublishedError` rather than answering `False` — and it needs a token
  (drafts are invisible without one).
- **It costs a request of its own, and has to.** Verified against the sandbox:
  `GET /api/records/{id}` reports `is_draft: false` for a published record
  whether or not an edited metadata draft exists. The only document which says
  so is the draft itself, where `is_draft` and `is_published` are *both* true —
  which is exactly what `Record.is_edited_metadata_draft` reads. So the answer
  cannot be derived from a record already in hand, and
  `test_a_published_record_never_reports_pending_edits` pins that, so we notice
  if Zenodo starts saying.
- **A read returns the published record**, and `get_draft` returns the pending
  changes. The published record is what the record says to everyone else, so it
  is the one an unqualified read should mean. `test_get_record_prefers_the_published_record`
  keeps pinning it; the docstring now states it as a decision rather than as a
  provisional answer.
- **`update_metadata` does not create the draft it needs.** It refuses a
  published record whose edits have not been started, with
  `RecordNotWritableError` naming `create_or_get_edited_metadata_draft` and
  `create_or_get_new_version`.
- **In-place correction is opt-in, and the opt-in is
  `create_or_get_edited_metadata_draft`.** Not a flag: starting the edits is
  already an explicit act, it is idempotent, and it reads at the call site as
  what it is. So the sequence is `create_or_get_edited_metadata_draft` →
  `update_metadata` → `publish`, and each step says what it does.
- **`publish` is not guarded.** It is the second half of the sequence above, and
  it already `404`s without a draft. It does now validate the draft's metadata
  first (Part 6), which is a different concern.

Verified live in `test_editing_a_published_record_in_place`: a published record
is refused, taking the draft makes the same call succeed, the public metadata
does not change until the draft is published, and the ID is unchanged throughout.

The original text is kept below for the reasoning.

---

`is_draft(..., files_based=False)` raises `NotImplementedError` and **stays that
way for now**. This is a deliberate placeholder, not an oversight.

For files, "draft or published" is a clean binary and the ID answers it: a
published record's files are locked, so an edit draft of a published record *is*
the published record as far as files are concerned. For metadata that split does
not hold. A published record's metadata **can** be changed in place, by taking an
edit draft, editing it and publishing again under the same ID and DOI. So a
published record can simultaneously have live metadata and pending, unpublished
metadata, and "what is this record's metadata?" has two legitimate answers.

Concretely, `update_metadata` and `publish` behave like this against a published
record which has an edit draft: **both succeed**, and the pair of them changes a
live public record's metadata under the same ID and DOI. That is not a bug — it is
Zenodo's in-place correction feature working — but we have not decided whether we
want it reachable, and if so how deliberately.

What has to be decided:

- **Which metadata does a read return** — the published metadata, or the edit
  draft's pending changes if there are any? These differ, and callers plausibly
  want either.
- **Whether `update_metadata` should create the draft it needs.** It is `PUT
  /api/records/{id}/draft` today, which `404`s on a published record with no edit
  draft. §1.2.1's `get_or_create_draft` is the missing piece.
- **Whether in-place correction is opt-in.** The likely shape: `update_metadata`
  refuses a published record by default, and correcting one means explicitly
  obtaining the edit draft first — so a caller cannot alter a public record
  without having said that is what they meant.
- **Whether the answer stays one boolean.** It probably does not — "is this
  published?" and "does it have unpublished changes?" are two questions, and
  `is_draft` currently answers only the first.

This is Part 6's problem (it is the same schema work), and the
`NotImplementedError` message should be the thing that surfaces it.

### 13.4 The publish race

The only transition that matters is **draft → published**, and it is one-way.
That, plus the fact that `is_draft` **checks the published endpoint first**, gives
a property worth writing down so nobody "tidies" the order later:

> `is_draft` can never return the *wrong* answer. A `False` came from finding the
> published record, and published is terminal. A `True` came from not finding it,
> which was true at that instant.

What it *can* do is fail, or go stale between the check and the act. Four windows,
all low-probability (they need someone else to publish the record mid-call), none
of which risks data:

1. **`is_draft` raises a spurious `RecordNotFoundError`.** Published check `404`s
   (still a draft), the record is published, draft check `404`s (publishing
   removes the draft). We then claim a record that exists and is now public cannot
   be found. **Fix:** re-check `/api/records/{id}` once on the error path, before
   raising. Costs nothing in the normal case.
2. **`list_files` fails with a bare `404`.** `is_draft` said draft, the publish
   lands, `/draft/files` is gone. **Fix:** on `404` from the draft listing, fall
   back to the published listing once.
3. **A download dies mid-transfer.** The listing handed out draft `content_url`s
   which stop resolving. `should_retry_transfer` does not retry `404`, so this
   fails fast rather than burning five attempts — but the message is a raw `404`.
   **Fix (optional):** re-resolve the listing once on `404` and retry.
4. **A file write fails with a bare `404`/`403`.** The guard passes, the publish
   lands, and the write hits a record that is now published. The guard cannot
   prevent this one — nothing can, the state changed after the check — and Zenodo
   refuses the write either way, so the job is purely to report it well.
   **Fix:** catch the `404` (no draft) and the `403` (bucket locked) at the write
   sites and raise the same error the guard raises, naming `new_version` as the way
   forward, so the two paths are indistinguishable to the caller.

**No data-loss risk in any of these.** `mirror_files` deletes before uploading, but
a delete against a locked bucket is refused with `403`, so an unlucky publish
cannot empty a record.

**Design alternative considered and rejected for now:** have `list_files` try the
published listing first and fall back to the draft listing, using `is_draft` only
to build the diagnostic error when both fail. That closes races 1 and 2 by
construction and costs one fewer request in both the draft and published cases.
It is the better shape, but it moves the resolution out of `is_draft` and is a
bigger change than the targeted fallbacks above; revisit when Part 6 forces
`is_draft` open anyway.

### 13.5 Implementation sequence

Steps 1–3 are the file-write invariant. **None of it is urgent** — 13.2 establishes
that Zenodo already refuses every file write against a published record — so this
can land whenever it is convenient, and does not gate Part 6. Step 5 is the one
that does need Part 6.

1. **`RecordNotWritableError`** (`exceptions.py`) — "record X on <domain> is
   published, so its files cannot be changed. To release a change, create a new
   version with `new_version`." Carries `record_id` and the domain, in the style of
   `RecordNotFoundError`. Nothing depends on this step, so it goes first. Note the
   message says *files*, not "files and metadata" — 13.3 is unsettled and the error
   should not assert something we have not decided.

2. **`_assert_writable(record_id)` on every public file-write method** —
   `upload_file`, `upload_files`, `mirror_files`, `delete_file`, `delete_files`,
   `delete_all_files`, `import_files`. Calls `is_draft(record_id,
   files_based=True)` and raises `RecordNotWritableError` unless it is `True`.
   - **Not** on `new_version` — the documented exception; its docstring should say
     so.
   - **Not** on `update_metadata` or `publish` — 13.3, deliberately left alone.
   - Watch the call graph: `mirror_files` → `delete_files` → `delete_file` and
     `upload_files` → `upload_file` would each re-check. Guard at the public entry
     points only and let the private helpers stay unguarded, or the per-file
     methods will add a `GET` per file. This is the one place to be careful.
   - While here, fix `import_files`' misleading `False` on a published record: the
     guard fires before the "already has files" check, so it becomes an error
     rather than a no-op.

3. **Translate the late failures** (race window 4) — `404`/`403` from a file-write
   site becomes the same `RecordNotWritableError`, so a record published between
   the guard and the write is reported identically to one that was published all
   along.

4. **Close the read-side race windows** (1–3 of 13.4) — `is_draft` re-checks the
   published endpoint before raising `RecordNotFoundError`; `list_files` falls back
   to the published listing on a `404` from the draft listing; optionally
   re-resolve a download's listing once on `404`. Independent of steps 1–3 and can
   land either side of them.

5. ~~**Settle metadata (13.3) as part of Part 6**, with `get_or_create_draft`
   from §1.2.1 — including whether `update_metadata` and `publish` get a guard of
   their own and what the opt-in to in-place correction looks like.~~
   ✅ **DONE** with Part 6; the decisions are written up in 13.3.

**Tests** (Part 12). Steps 1–4 are unit-level with a recording session — the races
are not reproducible against the live API:

- every file-write method against a published record raises
  `RecordNotWritableError`, parametrised over the methods so that a new file-write
  method which forgets the guard fails the suite;
- both published shapes are covered: no edit draft (`404`) and with an edit draft
  (listing `200`, then `403`) — the second is the one that currently fails late;
- `new_version` against a published record still works — the exception stays an
  exception;
- a file write whose guard passes but whose request `404`s/`403`s raises the same
  error;
- `is_draft` re-checks before raising `RecordNotFoundError`; the draft listing
  `404` falls back to the published listing.

Live (sandbox, `@pytest.mark.zenodo_token`): publish a draft, then confirm
`upload_files` and `delete_all_files` against it both raise
`RecordNotWritableError`. Leave `update_metadata` out of the live suite until 13.3
is settled — against a published record it currently *succeeds*, and a test which
pins that in place would be pinning the wrong behaviour.

---

## Suggested sequencing

**Where we are.** Steps 0, 1, 2, 3, 6 and 7 are done, and step 5 is done apart
from Part 11. They were taken out of order: uploads, mirror, versions and
download (Parts 2–5) landed before the read paths and before the metadata
schema, so the transport was exercised by the file work instead. Nothing
downstream broke as a result — `update_metadata` landed as a pass-through with
the schema explicitly deferred to Part 6, and step 3 then did the schema work it
always did. The outstanding steps, in the order to take them, are: **4**, the
**Part 11** remainder of 5, **2b**, **8** and **9**.

Step 4 is now smaller than it was: `create_or_get_edited_metadata_draft`,
`update_metadata` and
`publish` landed with step 3, because the metadata decisions (13.3) are what they
turn on. What is left of it is `create_record`, `reserve_doi`, and confirming
`new_version` / `import_files` / `delete_files` need nothing further.

0. ~~**`copier update` (Part 0)** — refresh the template from `v0.14.2`, keep
   `include_cli: true`, fix `project_description_short`, then re-lock and run
   `make check`. Own commit, before any library work.~~ ✅ **DONE** (commit
   `72c0ecc`, template now `v0.15.4`).
1. ~~**Client + transport foundation** — new `ZenodoClient` skeleton, injectable
   session + public `build_session`, `urllib3.Retry` adapter, `_request`,
   exceptions module, Bearer auth, `resolve_token` precedence chain and the
   `RecordID`/`ParentID` `NewType`s (Part 1.1).~~ ✅ **DONE** — see the note at
   the top of 1.1.
2. ~~**Read paths** — `list_files` (landed with Part 3), plus `get_published`,
   `get_draft`, `get_metadata`, `get_citation` (Part 10). Cheap, and they
   exercise the transport.~~ ✅ **DONE** — plus `get_parent_id`, which is the
   same kind of read. See the note at the top of Part 10 for the citation work,
   and 1.2.2 below for the record/metadata reads.
2b. **File-write guard (Part 13.5, steps 1–4)** — `RecordNotWritableError` and
   `_assert_writable` on the file-write methods, plus the race-window fixes. Small
   and self-contained, and **not urgent**: Zenodo already refuses these writes
   (13.2), so this is hardening and error quality. Slot it in wherever convenient;
   it does not gate step 3.
3. ~~**Metadata (Part 6)** — schema rewrite + `load_metadata` + validation.
   Biggest item; do it early so everything downstream uses the right shape.
   Part 13.3 — the `is_draft(files_based=False)` `NotImplementedError`, and
   whether `update_metadata`/`publish` may target a published record — is
   settled here.~~ ✅ **DONE** — plus the typed `Record`,
   `create_or_get_edited_metadata_draft` and `has_edited_metadata_draft`.
   See the note at the top of Part 6 and the decisions in 13.3.
4. **Write paths** — `create_record` and `reserve_doi` (Part 7). ~~`get_or_create_draft`
   (Part 1.2.1), `update_metadata`, `publish`~~ ✅ **DONE with step 3**;
   `new_version` / `import_files` / `delete_files` landed with Parts 3–4.
5. **Uploads (Parts 2, 11)** — ~~the init→content→commit `upload_file`,
   `tenacity` upload retry, checksum verification, `upload_files` parallelism,
   the shared `leave=False` progress-bar helper (Part 2.1)~~ ✅ **DONE** (Part 2,
   and the parallelism/progress-bar allocator with Part 3). **Still outstanding:
   Part 11** — the path-stripping warning + basename-collision error
   (11.1–11.2), and `zipping.py` + `upload_files_as_zip` (11.3).
6. ~~**Mirror + versions (Parts 3–4)** — `mirror_files`, then
   `create_new_version` with `FilesMode`.~~ ✅ **DONE** — see the notes at the
   top of Parts 3 and 4.
7. ~~**Download (Part 5)** — `list_files(draft=...)`, `download_file` /
   `download_files` / `retrieve_files`. Reuses the session, checksum helper,
   progress-bar helper and `tenacity` retry from step 5, so it slots in cheaply
   once uploads exist.~~ ✅ **DONE** — see the notes at the top of Part 5
   (`list_files` has no `draft` argument, and the helper is `download_files`).
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
