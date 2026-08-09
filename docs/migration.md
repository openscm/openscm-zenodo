# Migrating from v0.x

Zenodo replaced its deposit API with [InvenioRDM](https://inveniordm.docs.cern.ch/),
and this release is written against InvenioRDM. That is a different API with a
different metadata schema, so **this is a clean break**: there is no
compatibility layer, and metadata written for the old API is refused rather than
translated.

This page is the shortest path from working v0.x code to working code today.

## The class

`ZenodoInteractor` is gone. Use
[`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient].

```python
from openscm_zenodo import ZenodoClient, ZenodoDomain

client = ZenodoClient(zenodo_domain=ZenodoDomain.sandbox)
```

The client resolves the token itself, so you no longer read `ZENODO_TOKEN` out of
the environment and pass it in. It looks in, in order: the `token` argument,
`ZENODO_SANDBOX_TOKEN` (sandbox domain only), `ZENODO_TOKEN`, then a `.env` file.
**If you talk to the sandbox, put its token in `ZENODO_SANDBOX_TOKEN`**: sandbox
and production tokens are not interchangeable, and a production token against the
sandbox fails in a way which looks like the record not existing.

| v0.x | Now |
|---|---|
| `ZenodoInteractor(token=..., ...)` | `ZenodoClient(token=..., ...)` |
| `get_deposition(id)` | [`get_record`][openscm_zenodo.zenodo.ZenodoClient.get_record] |
| `get_metadata(id, user_controlled_only=...)` | [`get_metadata`][openscm_zenodo.zenodo.ZenodoClient.get_metadata] — there is no `user_controlled_only`, because the schema separates what you control from what Zenodo does |
| `update_metadata(id, metadata=...)` | [`update_metadata`][openscm_zenodo.zenodo.ZenodoClient.update_metadata], which takes a [`Metadata`][openscm_zenodo.metadata.Metadata] |
| `get_draft_deposition_id` / `create_new_version_from_latest` | [`create_or_get_new_version`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_new_version] |
| `get_latest_deposition_id` | [`get_latest_version_id`][openscm_zenodo.zenodo.ZenodoClient.get_latest_version_id] |
| `get_concept_id` | [`get_parent_id`][openscm_zenodo.zenodo.ZenodoClient.get_parent_id] — InvenioRDM's word for the concept record is the *parent* |
| `upload_files(id, to_upload=...)` | [`upload_files`][openscm_zenodo.zenodo.ZenodoClient.upload_files] |
| `remove_files` / `remove_all_files` | [`delete_files`][openscm_zenodo.zenodo.ZenodoClient.delete_files] / [`delete_all_files`][openscm_zenodo.zenodo.ZenodoClient.delete_all_files] |
| `get_bibtex_entry` | [`get_citation`][openscm_zenodo.zenodo.ZenodoClient.get_citation] |
| `publish(id)` | [`publish`][openscm_zenodo.zenodo.ZenodoClient.publish] |
| `get_bucket_url`, `upload_file_to_bucket_url`, `remove_file_id`, `remove_files_by_id` | gone: InvenioRDM has no buckets and no file IDs, files are addressed by name |
| `retrieve_metadata_legacy`, `retrieve_bibtex_entry`, `create_new_version_legacy`, `get_reserved_doi_legacy` | [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata], [`retrieve_citation`][openscm_zenodo.zenodo.retrieve_citation], [`create_or_get_new_version`][openscm_zenodo.zenodo.create_or_get_new_version], [`Record.doi`][openscm_zenodo.zenodo.Record.doi] |

## The metadata schema

This is the part which needs real work. The two schemas share enough key names
(`title`, `description`, `version`, `publication_date`) that old metadata looks
plausible right up until Zenodo rejects it for the fields you did *not* send, so
we check for legacy keys up front and name the one which gave it away.

Build metadata with [`Metadata`][openscm_zenodo.metadata.Metadata], which puts
the fields you are likely to need behind named arguments and validates them
before anything is sent:

```python
from openscm_zenodo import Creator, Metadata

metadata = Metadata(
    title="My dataset",
    resource_type="dataset",
    creators=(Creator.person("Nicholls", "Zebedee", orcid="0000-0002-4767-2723"),),
    publication_date="2026-08-04",
    publisher="Zenodo",
    description="What this is.",
    version="v1.0.0",
)
```

The keys which moved:

| v0.x key | Now |
|---|---|
| `upload_type` | `resource_type`, taking a vocabulary ID, e.g. `{"id": "dataset"}` |
| `publication_type`, `image_type` | folded into `resource_type`, e.g. `{"id": "publication-article"}` |
| `license` | `rights`, which is a list, e.g. `[{"id": "cc-by-4.0"}]` |
| `keywords` | `subjects`, each an object, e.g. `[{"subject": "climate"}]` |
| `notes` | an entry in `additional_descriptions` |
| `access_right` | not metadata at all: the record's `access` object, via [`update_access`][openscm_zenodo.zenodo.ZenodoClient.update_access] |
| `embargo_date` | the record's `access.embargo` object, via [`Embargo`][openscm_zenodo.zenodo.Embargo] |
| `prereserve_doi` | [`reserve_or_get_doi`][openscm_zenodo.zenodo.ZenodoClient.reserve_or_get_doi] |
| `doi` | the record's `pids`, read as [`Record.doi`][openscm_zenodo.zenodo.Record.doi] |
| `journal_title` and the imprint, conference and thesis fields | Zenodo custom fields, so they live in the record's `custom_fields`, not in `metadata` |
| `communities` | **not supported**, see below |

Anything the named arguments do not cover goes in `Metadata.raw`, which is passed
through as-is. See ["Zenodo's metadata
schema"](further-background/metadata-schema.md) for the full picture.

## The command line

The CLI is now three commands, for the things which are genuinely better from a
shell: moving bytes in and out, and getting a citation. The metadata and
versioning commands are gone, because they took structured JSON input which
belongs in a program where the schema above can be built and validated.

| Removed command | Do this instead |
|---|---|
| `retrieve-metadata [--user-controlled-only]` | `retrieve_metadata(id)` |
| `update-metadata --metadata-file FILE` | `update_metadata(id, Metadata.from_file(FILE))` |
| `update-metadata --reserve-doi` | `reserve_or_get_doi(id)` |
| `remove-files [--all]` | `delete_files(id, names)` / `delete_all_files(id)` |
| `create-new-version [--publish --metadata-file --n-threads]` | `create_or_get_new_version(...)` |

`retrieve-bibtex` is **renamed and generalised** to `retrieve-citation`.
`--format bibtex` is the default, so it reproduces the old behaviour:

```sh
openscm-zenodo retrieve-bibtex   1234    # v0.x
openscm-zenodo retrieve-citation 1234    # now, same output
```

The commands which stayed changed too:

- `upload-files` gained `--mirror`, `--zip`, `--n-threads` and `--no-progress`.
  `--mirror` replaces `--delete-extraneous`, and is the only flag which deletes
  anything. The old `--sync` is gone because it has nothing left to switch on:
  files already on the draft with the same contents are never re-uploaded.
- `download-files` is new. It takes remote filenames the same way `upload-files`
  takes local paths, and downloading everything is what you get by naming none.
  Drafts, restricted and embargoed records need no special flag — the record ID
  says whether it is a draft, and access is just the token.
- `--token` no longer reads `ZENODO_TOKEN` through typer. The whole precedence
  chain, including `ZENODO_SANDBOX_TOKEN` and `.env`, is in one place, so the CLI
  and the Python API agree about where a token comes from.

## Putting a record in a community is no longer supported

On the legacy API, `communities` was a metadata field, so it came along with
everything else. **This release drops that capability**, and it is a removal
rather than a rename.

InvenioRDM has no such field. Submitting a record to a community creates a
**review request**, which a curator of that community accepts or declines, and a
record bound to a community is published by that acceptance rather than by
`publish`. That is an asynchronous flow with a state machine owned by somebody
else, and supporting it properly means testing paths our own account cannot
reach.

So use Zenodo's web interface, which is the right tool for a step that involves
another person anyway. Metadata carrying a legacy `communities` key is refused
with a message saying so, rather than being silently dropped, and the communities
a record is already in are readable from `Record.raw["parent"]["communities"]`.
