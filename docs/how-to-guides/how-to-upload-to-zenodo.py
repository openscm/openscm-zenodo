# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.17.2
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # How to upload to Zenodo
#
# Here we upload files to Zenodo, publish them, and download them again.
# Everything runs against the [Zenodo sandbox](https://sandbox.zenodo.org),
# so you can follow along without putting anything on the real Zenodo.

# %% [markdown]
# ## Imports

# %%
import datetime as dt
import sys
import tempfile
from pathlib import Path

from loguru import logger

from openscm_zenodo import (
    CitationFormat,
    Creator,
    Metadata,
    ZenodoClient,
    ZenodoDomain,
)

# %% [markdown]
# We enable logging in this notebook so you can see what is going on in more detail.

# %%
logger.configure(handlers=[dict(sink=sys.stderr, level="INFO")])
logger.enable("openscm_zenodo")

# %% [markdown]
# ## Files to upload
#
# Before you can get started,
# you will need some files to upload.

# %%
working_dir = Path(tempfile.mkdtemp())

to_upload = []
for name in ("demo.txt", "demo-2.txt"):
    path = working_dir / name
    path.write_text(f"Your content will be better than this ({name})!\n")
    to_upload.append(path)

to_upload

# %% [markdown] editable=true slideshow={"slide_type": ""}
# ## Zenodo token
#
# In order to interact with the API,
# you will need a token for Zenodo.
# To create the token, go to https://zenodo.org/account/settings/applications/tokens/new/
# (or https://sandbox.zenodo.org/account/settings/applications/tokens/new/
# for the sandbox, which is a separate account with separate tokens).
# Put your token somewhere safe,
# if you leak it then others can do whatever they want with your Zenodo records!
# (If you do leak your token, just revoke it, then no more damage can happen.)
#
# You do not have to pass the token to us.
# If you do not, we look for it in, in order of preference:
#
# 1. `ZENODO_SANDBOX_TOKEN`, when you are using the sandbox domain
# 1. `ZENODO_TOKEN`
# 1. if using the command-line, you can also specify a `.env` file to use
#
# The sandbox variable comes first because sandbox and production tokens are not
# interchangeable, and using a production token against the sandbox fails in a way
# which looks like the record not existing.
# Keeping them in separate variables means you can have both set at once.

# %% editable=true slideshow={"slide_type": ""} tags=["remove_input"]
import dotenv

# This loads the `.env` file when running locally.
# When building the docs on RtD, we pre-set the environment variable instead.
#
# The `remove_input` tag hides this cell
# so it doesn't appear in the built docs.
dotenv.load_dotenv()
del dotenv

# %% [markdown]
# ## Client
#
# [`ZenodoClient`][openscm_zenodo.zenodo.ZenodoClient] is the class for
# interacting with Zenodo. It creates records, moves files in and out,
# and edits metadata.

# %%
client = ZenodoClient(
    # In this example we use the sandbox domain.
    # You will want to use the production domain
    # once you're ready to actually post things.
    zenodo_domain=ZenodoDomain.sandbox,
)
client

# %% [markdown]
# The token never appears in the representation above,
# so a notebook like this one is safe to share.
# What the client will tell you is *where* it found the token,
# which is the thing you actually want to know when a record cannot be found.

# %%
client.token_source

# %% [markdown]
# ## Create a record
#
# A new record starts as a draft.
# Nothing is public until you publish it,
# and a draft can be deleted.

# %%
record = client.create_record()
record.record_id

# %% [markdown]
# ## Metadata
#
# Zenodo runs on [InvenioRDM](https://inveniordm.docs.cern.ch/),
# and its metadata schema is
# [documented here](../further-background/metadata-schema.md).
# The parts you are most likely to need have their own arguments on
# [`Metadata`][openscm_zenodo.metadata.Metadata];
# anything else goes in `raw`.

# %%
timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

metadata = Metadata(
    title=f"OpenSCM-Zenodo docs run {timestamp}",
    resource_type="dataset",
    creators=(
        Creator.person("Nicholls", "Zebedee", affiliations=["Climate Resource"]),
        Creator.organisation("openscm-zenodo"),
    ),
    publication_date=dt.date.today().isoformat(),
    publisher="Zenodo",
    description="Test upload for OpenSCM-Zenodo, generated from the docs.",
    version="v1.0.0",
)
metadata

# %% [markdown]
# Send it to the draft.
# This replaces the draft's metadata rather than merging into it,
# so what you pass here is what the record ends up with.

# %%
client.update_metadata(record.record_id, metadata)

# %% [markdown]
# This method replaces rather than merges,
# so **changing one field means sending all of them**.
# However, you don't have to write the rest out again:
# read what is on the record with
# [`get_metadata`][openscm_zenodo.zenodo.ZenodoClient.get_metadata],
# change the field you care about, and send it back to get a merge.

# %%
current = client.get_metadata(record.record_id)
current.description = "A better description, without restating everything else."

client.update_metadata(record.record_id, current)
client.get_metadata(record.record_id).description

# %% [markdown]
# ## Upload the files
#
# Now we can upload our files to the draft.
#
# Files which are already on the draft with the same contents are left alone,
# so running this again after a failure part way through
# only transfers what is still missing.

# %%
client.upload_files(record.record_id, to_upload, progress=True)

# %% [markdown]
# Note that **Zenodo has no directories**.
# Each file lands under its own name, whatever local directories it sat in, and
# we warn you when that loses information.
# If you need the structure kept, use
# [`upload_files_as_zip`][openscm_zenodo.zenodo.ZenodoClient.upload_files_as_zip],
# which bundles everything into one archive
# (Zenodo's web interface shows what is inside an archive, so it stays browsable)
# or create multiple archives using [`zip_files`][openscm_zenodo.zipping.zip_files]
# and then upload them using
# [`upload_files`][openscm_zenodo.zenodo.ZenodoClient.upload_files].

# %% [markdown]
# ## Download the files
#
# The files are on the draft now, so we can fetch them back.
# There is nothing to say about the record being a draft:
# the record ID already says which it is, and we work it out from there.

# %%
from_draft = working_dir / "from-draft"
client.download_files(record.record_id, from_draft, progress=True)

# %%
sorted(path.name for path in from_draft.iterdir())

# %% [markdown]
# **Restricted and embargoed records need nothing special either.**
# Access is just the token, so a record only you can see downloads exactly like
# a public one, as long as the token belongs to somebody with access.

# %% [markdown]
# ## Publish the version
#
# If you want, you can publish the version.
#
# **This cannot be undone.**
# A published record cannot be deleted, and its files can no longer be changed —
# changing files means making a new version, with
# [`create_or_get_new_version`][openscm_zenodo.ZenodoClient.create_or_get_new_version].
# Uploading to or deleting from a published record raises
# [`RecordNotWritableError`][openscm_zenodo.exceptions.RecordNotWritableError]
# rather than sending anything.

# %%
published_id = client.publish(record.record_id)
print(f"The published record is at: {client.zenodo_domain_url}/records/{published_id}")

# %% [markdown]
# Downloading from the published record is the same call as before.

# %%
from_published = working_dir / "from-published"
client.download_files(published_id, from_published, progress=True)

sorted(path.name for path in from_published.iterdir())

# %% [markdown]
# ## Cite the record
#
# Publishing mints a DOI, and Zenodo will render the citation for you.

# %%
client.get_record(published_id).doi

# %%
print(client.get_citation(published_id))

# %% [markdown]
# BibTeX is the default.
# Ask for `CitationFormat.citation` to get a human-readable string instead,
# in whichever [CSL](https://citationstyles.org/) style you want.

# %%
print(
    client.get_citation(
        published_id, fmt=CitationFormat.citation, style="chicago-author-date"
    )
)

# %% [markdown]
# ## From the command line
#
# Moving bytes in and out, and getting a citation, are also available as
# commands, which is usually what you want from a script or from CI:
# see the [CLI documentation](../cli/index.md).
#
# Everything else — metadata, new versions, access — is Python only,
# because it takes structured input which is better handled in Python.

# %% [markdown]
# ## Conclusion
#
# This gives a basic demonstration of how to use OpenSCM-Zenodo.
# We hope this helps and can support your use case,
# whatever it is.
