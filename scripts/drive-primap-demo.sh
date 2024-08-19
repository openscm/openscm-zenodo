#!/bin/bash

# Download current entry
python scripts/grab-primap.py

# Get the data of interest out of the current entry
python scripts/extract-primap.py

# Then you update the description however you want.
# I just did it by hand.
python scripts/write-primap-modified.py

# Then you can upload
# Full docs here:
# https://openscm-zenodo.readthedocs.io/en/latest/usage.html#usage

# Get the version ID for the new version
# This is just the numbers at the end of the 'all-versions' zenodo ref,
# I took this from
# https://zenodo.org/doi/10.5281/zenodo.4479171
ZENODO_URL="zenodo.org"
PRIMAP_DEPOSITION_ID="4479171"
ZENODO_TOKEN=TOKEN openscm-zenodo create-new-version "${PRIMAP_DEPOSITION_ID}" primap-zenodo-modified.json --zenodo-url $ZENODO_URL

# Get the new version's bucket
# The output of the previous command gives you the version.
# I will make this easier to capture, but for now just note it and update here
PRIMAP_VERSION_ZENODO="123446"
ZENODO_TOKEN=TOKEN openscm-zenodo get-bucket "${PRIMAP_VERSION_ZENODO}" --zenodo-url $ZENODO_URL

# Upload files.
# You could obviously write loops etc. here
# The output of the previous command gives you the bucket.
# I will make this easier to capture, but for now just note it and update here
PRIMAP_BUCKET_ZENODO="128adjsd3"
openscm-zenodo upload FILE_TO_UPLOAD "${PRIMAP_BUCKET_ZENODO}" --zenodo-url $ZENODO_URL
