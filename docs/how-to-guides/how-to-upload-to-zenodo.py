# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.16.6
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% [markdown]
# # How to upload to Zenodo
#
# Here we describe how to upload files to Zenodo.

# %% [markdown]
# ## Imports

# %%
import copy
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

from loguru import logger

from openscm_zenodo import ZenodoDomain, ZenodoInteractor

# %% [markdown]
# We enable logging in this notebook so you can see what is going on in more detail.

# %%
logger.configure(handlers=[dict(sink=sys.stderr, level="INFO")])
logger.enable("openscm_zenodo")

# %% [markdown]
# ## Files to upload
#
# Before you can get started,
# you will need to have a file to upload.

# %%
to_upload = Path(tempfile.mkdtemp()) / "demo.txt"
with open(to_upload, "w") as fh:
    fh.write("Your content\n")
    fh.write("will be better than this!")

# %% [markdown] jp-MarkdownHeadingCollapsed=true
# ## Zenodo token
#
# In order to interact with the API,
# you will need a token for Zenodo.
# To create the token, go to https://zenodo.org/account/settings/applications/tokens/new/.
# Make sure that the token has "deposit:actions" and "deposit:write" permissions,
# otherwise you won't be able to do anything.
# Put your token somewhere safe,
# if you leak it then others can do whatever they want with your Zenodo records!
# (If you do leak your token, just revoke it, then no more damage can happen.)

# %% [markdown]
# ## Zenodo interactor
#
# The `ZenodoInteractor` class is our key class for interacting with Zenodo.
# This class allows you to create new versions of deposits,
# upload files and manipulate metadata.

# %%
zi = ZenodoInteractor(
    # When we run this example in our CI and the docs build,
    # we set the environment variable before we run.
    token=os.environ["ZENODO_TOKEN"],
    # You will need a token in order to upload
    # In this example we use the sandbox domain.
    # You will want to use the production domain
    # once you're ready to actually post things.
    zenodo_domain=ZenodoDomain.sandbox,
)
zi

# %% [markdown]
# ## Interact with zenodo

# %% [markdown]
# ### Make new version
#
# The first step is to make a new version
# i.e. get a new draft deposition ID.
# To do this, we start with any deposition ID.

# %%
# This is the record we use for testing: https://sandbox.zenodo.org/records/166701
any_deposition_id = "166701"

# %% [markdown]
# From this we get the latest deposition ID.

# %%
latest_deposition_id = zi.get_latest_deposition_id(any_deposition_id)
latest_deposition_id

# %% [markdown]
# Now we can get a draft deposition ID.
# This will either use an existing draft,
# or create a new draft if no existing draft exists.

# %%
draft_deposition_id = zi.get_draft_deposition_id(latest_deposition_id)
draft_deposition_id

# %% [markdown]
# ## Upload metadata
#
# The next thing to do is to upload/update any metadata we wish to.
# Here we simply get the old metadata,
# but you have full flexibility to start fresh if you wish.
# The docs on what is allowed here are not great,
# but [these docs](https://developers.zenodo.org/#representation)
# are a better start than nothing.

# %%
metadata_current = zi.get_metadata(latest_deposition_id, user_controlled_only=True)

metadata_current

# %% [markdown]
# Update the metadata.

# %%
timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
metadata_updated = copy.deepcopy(metadata_current)
metadata_updated["metadata"]["description"] = (
    "Test upload for OpenSCM-Zenodo, generated from the docs."
)
metadata_updated["metadata"]["title"] = f"OpenSCM-Zenodo docs run {timestamp}"
metadata_updated["metadata"]["creators"][0]["affiliation"] = (
    "how-to-upload-to-zenodo.py"
)
metadata_updated

# %% [markdown]
# Update the metadata on Zenodo.

# %%
zi.update_metadata(
    deposition_id=draft_deposition_id,
    metadata=metadata_updated,
).raise_for_status()

# %% [markdown]
# ## Upload the files
#
# Now we can also upload our files to the draft.

# %%
files_to_upload_l = [to_upload]
zi.upload_files(
    deposition_id=draft_deposition_id,
    to_upload=files_to_upload_l,
)

# %% [markdown]
# ## Publish the version
#
# If you want, you can even publish the version.

# %%
zi.publish(draft_deposition_id).raise_for_status()
print(
    "The published record is available at: "
    f"{zi.zenodo_domain.value}/records/{draft_deposition_id}"
)

# %% [markdown]
# ## Conclusion
#
# This gives a basic demonstration of how to use OpenSCM-Zenodo.
# We hope this helps and can support your use case,
# whatever it is.
