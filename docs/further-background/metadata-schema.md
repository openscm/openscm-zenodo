# Zenodo's metadata schema

`openscm_zenodo.metadata` is Zenodo's metadata schema, as classes.

## The shape

A record's metadata is a JSON object.
The fields most records use:

```json
{
  "title": "RCMIP protocol",
  "resource_type": {"id": "dataset"},
  "creators": [
    {
      "person_or_org": {
        "type": "personal",
        "family_name": "Nicholls",
        "given_name": "Zebedee",
        "identifiers": [{"scheme": "orcid", "identifier": "0000-0002-4767-2723"}]
      },
      "affiliations": [{"name": "University of Melbourne"}]
    }
  ],
  "publication_date": "2021-03-09",
  "publisher": "Zenodo",
  "description": "...",
  "version": "v5.1.0",
  "rights": [{"id": "cc-by-sa-4.0"}],
  "subjects": [{"subject": "climate"}]
}
```

Two things are worth noticing straight away.

**Vocabulary fields are objects with an `id`**, not strings.
`resource_type`, `rights`, `languages`, a contributor's `role` and an
affiliation's ROR are all IDs into a controlled vocabulary Zenodo publishes.
[`Metadata`][openscm_zenodo.metadata.Metadata] holds them as the ID itself
(`metadata.resource_type == "dataset"`) because the nesting carries nothing.

**Access is not metadata.** `access_right` and `embargo_date` used to live in
here; they are now the record's own `access` object, alongside `metadata`
rather than inside it. The same goes for DOIs, which live in `pids`.

## Where each field lives

| Zenodo | In our model |
|---|---|
| `title`, `publisher`, `description`, `version` | `Metadata.title`, `.publisher`, `.description`, `.version` |
| `resource_type: {"id": "dataset"}` | `Metadata.resource_type` — the ID itself, `"dataset"` |
| `publication_date` | `Metadata.publication_date` |
| `creators[]` | `Metadata.creators`, a [`Creator`][openscm_zenodo.metadata.Creator] each, wrapping a [`Person`][openscm_zenodo.metadata.Person] or an [`Organisation`][openscm_zenodo.metadata.Organisation] |
| `contributors[]` | `Metadata.contributors`, a [`Contributor`][openscm_zenodo.metadata.Contributor] each (a `Creator` plus a required `role`) |
| `rights[]` | `Metadata.rights`, a [`Right`][openscm_zenodo.metadata.Right] each |
| `subjects[]` | `Metadata.subjects`, a [`Subject`][openscm_zenodo.metadata.Subject] each |
| `dates[]` | `Metadata.dates`, a [`Date`][openscm_zenodo.metadata.Date] each |
| `related_identifiers[]` | `Metadata.related_identifiers`, a [`RelatedIdentifier`][openscm_zenodo.metadata.RelatedIdentifier] each |
| `funding[]` | `Metadata.funding`, a [`Funding`][openscm_zenodo.metadata.Funding] each, holding a [`Funder`][openscm_zenodo.metadata.Funder] and an [`Award`][openscm_zenodo.metadata.Award] |
| `languages[]` | `Metadata.languages`, the IDs themselves, e.g. `("eng",)` |
| anything else | `Metadata.raw`, and written back out untouched |
| `access`, `pids`, `custom_fields`, `parent` | not metadata at all — they are the record's, see [`Record`][openscm_zenodo.zenodo.Record] |

## Coming from the legacy schema

| Legacy | InvenioRDM | In our model |
|---|---|---|
| `upload_type: "dataset"` | `resource_type: {"id": "dataset"}` | `Metadata.resource_type = "dataset"` |
| `publication_type`, `image_type` | folded into `resource_type` | `Metadata.resource_type = "publication-article"` |
| `creators: [{"name": ..., "affiliation": ..., "orcid": ...}]` | `creators: [{"person_or_org": {...}, "affiliations": [...]}]` | `Creator.person("Nicholls", "Zebedee", orcid=..., affiliations=[...])` |
| `contributors: [{..., "type": "DataCurator"}]` | `contributors: [{..., "role": {"id": "datacurator"}}]` | `Contributor.person(..., role="datacurator")` |
| `license: "cc-by-4.0"` | `rights: [{"id": "cc-by-4.0"}]` | `Metadata.rights = (Right(id="cc-by-4.0"),)` |
| `keywords: ["climate"]` | `subjects: [{"subject": "climate"}]` | `Metadata.subjects = (Subject(subject="climate"),)` |
| `related_identifiers: [{..., "relation": "isSupplementTo"}]` | `relation_type: {"id": "issupplementto"}` | `RelatedIdentifier(..., relation_type="issupplementto")` |
| `grants: [{"id": "10.13039/501100000780::101003536"}]` | `funding: [{"funder": {...}, "award": {...}}]` | `Funding(funder=Funder(id=...), award=Award(id=...))` |
| `notes` | an entry in `additional_descriptions` | `Metadata.raw["additional_descriptions"]` |
| `access_right`, `embargo_date` | the record's `access` object | `Record.access` (read-only for now, see below) |
| `prereserve_doi`, `doi` | the record's `pids` | `Record.doi`, and `reserve_doi` to reserve one |
| `journal_title`, `imprint_*`, `conference_*`, `thesis_*` | the record's `custom_fields` | `Record.raw["custom_fields"]` |
| `communities` | the record's `parent.communities` | `Record.raw["parent"]["communities"]` |

You do not have to remember this table:
passing any of the left-hand keys raises
[`MetadataValidationError`][openscm_zenodo.exceptions.MetadataValidationError]
with the right-hand side in the message, before anything is sent.

## Building metadata

```python
from openscm_zenodo import Creator, Metadata

metadata = Metadata(
    title="RCMIP protocol",
    resource_type="dataset",
    creators=(
        Creator.person("Nicholls", "Zebedee", orcid="0000-0002-4767-2723"),
        Creator.organisation("Climate Resource"),
    ),
    publication_date="2021-03-09",
    publisher="Zenodo",
)
```

Every field is optional, because a draft's metadata legitimately is —
Zenodo accepts an incomplete draft and only checks it at publish time.
[`find_problems`][openscm_zenodo.metadata.Metadata.find_problems] and
[`validate`][openscm_zenodo.metadata.Metadata.validate] are where completeness
is checked, and
[`publish`][openscm_zenodo.zenodo.ZenodoClient.publish] calls `validate` for you
before the step which cannot be undone.

Metadata can also be loaded from a JSON file with
[`Metadata.from_file`][openscm_zenodo.metadata.Metadata.from_file].
Methods which take metadata take a `Metadata` and nothing else, so a file or a
JSON blob is converted once, where the error can name the file, rather than at
every call site.

### Zenodo discards what it cannot read, rather than refusing it

This is the surprising part of the schema and the reason validation is worth
having. Send a `publication_date` of `"2021-13-45"` and Zenodo answers `200`
and stores nothing — the field simply is not there afterwards. The same goes
for a `version` longer than about 190 characters.

So:

- `publication_date` is checked against what Zenodo can actually read, which is
  EDTF level 0: `2021`, `2021-03` and `2021-03-09` are all valid, and so is an
  interval like `2021-03-09/2021-04-10`. A plain `YYYY-MM-DD` check would
  reject dates Zenodo is perfectly happy with.
- [`update_metadata`][openscm_zenodo.zenodo.ZenodoClient.update_metadata]
  compares what came back with what it sent and warns about anything Zenodo
  dropped. Pass `warn_discarded=False` if you would rather it did not.
- Vocabulary values are checked against the lists in `openscm_zenodo.metadata`
  by
  [`find_unknown_vocabulary_values`][openscm_zenodo.metadata.Metadata.find_unknown_vocabulary_values],
  which suggests the closest value it knows of and then lists the lot. Those
  lists are what we have seen Zenodo accept rather than what it will accept, so
  this is a warning; `warn_unknown_vocabulary=False` turns it off.

Both of these go through `warnings.warn`, with a
[`ZenodoWarning`][openscm_zenodo.exceptions.ZenodoWarning] category, rather than
through the logger — the logger is off until you turn it on, and these are
things you need to hear either way.

## Reading it back, and writing it somewhere else

[`get_metadata`][openscm_zenodo.zenodo.ZenodoClient.get_metadata] hands back a
`Metadata`, and `update_metadata` takes one, so this works:

```python
client.update_metadata(new_record_id, client.get_metadata(old_record_id))
```

Nothing is lost on the way through. Fields we do not model are carried in
`Metadata.raw` and written straight back out. What *is* dropped is the display
text Zenodo adds to vocabulary entries on the way out (a resource type's title
in every locale it has, a licence's description and icon): that is Zenodo's to
fill in, and sending it back would only be a way to disagree with it.

## Metadata on a published record

A published record's files can never be changed, but its metadata can:
take a draft of the record, edit the draft, then publish the draft,
and the correction lands under the same ID and DOI.

Because that changes what a public record says, it is deliberately not
something which happens by accident.
[`update_metadata`][openscm_zenodo.zenodo.ZenodoClient.update_metadata]
refuses a published record whose edits have not been started; starting them
with
[`create_or_get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_edited_metadata_draft]
is how you say that is what you meant:

```python
draft_record = client.create_or_get_edited_metadata_draft(record_id)  # the opt-in
client.update_metadata(draft_record, corrected_metadata)
client.publish(draft_record)
```

To release the change as a *new* version instead — which is what you want if
the files change too — use
[`create_or_get_new_version`][openscm_zenodo.zenodo.ZenodoClient.create_or_get_new_version].

### The two questions

- **Has this record never been published?**
  [`is_draft`][openscm_zenodo.zenodo.ZenodoClient.is_draft].
- **Does this published record have metadata edits which have not gone out?**
  [`has_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.has_edited_metadata_draft].

They are separate because they are about different things, and the second only
applies to a published record — a record which was never published *is* a
draft, so asking whether it has one raises
[`DraftRecordDraftMetadataEditsError`][openscm_zenodo.exceptions.DraftRecordDraftMetadataEditsError]
rather than answering.

On a record you already have, `Record.is_draft` answers the first question, and
`Record.is_edited_metadata_draft` says that *this document* is a published
record's pending edits.

### Reading a draft

The two are read by two methods, because Zenodo serves them from one endpoint
and they are not the same thing:

- [`get_draft`][openscm_zenodo.zenodo.ZenodoClient.get_draft] reads an
  unpublished record. It **never** returns a published record's pending metadata
  edits; a published record raises
  [`PublishedRecordDraftError`][openscm_zenodo.exceptions.PublishedRecordDraftError],
  whether or not edits are under way.
  Use [`create_or_get_new_version`][openscm_zenodo.zenodo.create_or_get_new_version]
  to create a new version (or get one if a new version is already being edited) of a published record.
- [`get_edited_metadata_draft`][openscm_zenodo.zenodo.ZenodoClient.get_edited_metadata_draft]
  reads those edits, and only reads: use
  `create_or_get_edited_metadata_draft` to start them, which is a deliberate act
  and so is not something a read does as a side effect. A published record with
  nothing under way raises
  [`DraftMetadataEditsNotFoundError`][openscm_zenodo.exceptions.DraftMetadataEditsNotFoundError],
  and says how to start some.

## What is not here yet

Writing the parts of a record which live *outside* `metadata` — `access` and
its embargo, `custom_fields`, `files.enabled` — is not supported. Those land
with `create_record`, which has to set a record's access at creation anyway.
They can all be read: `Record.access` is an
[`Access`][openscm_zenodo.zenodo.Access], and the rest is in `Record.raw`.
