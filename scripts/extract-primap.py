import json


with open("primap-10705512-zenodo.json", "r") as fh:
    zenodo_raw = json.load(fh)

# Can add others here of course
metadata_keys_to_keep = [
    "title",
    # "upload_type",
    "description",
    "creators",
    "language",
    "license",
    "keywords",
    "references",
]
zenodo_metadata_extract = {
    "metadata": {k: zenodo_raw["metadata"][k] for k in metadata_keys_to_keep}
}
with open("primap-zenodo.json", "w") as fh:
    json.dump(zenodo_metadata_extract, fh, sort_keys=True, indent=2)

with open("primap-zenodo-description.html", "w") as fh:
    fh.write(zenodo_metadata_extract["metadata"]["description"])
