import copy
import json

with open("primap-zenodo.json", "r") as fh:
    base = json.load(fh)

with open("primap-zenodo-description-modified.html", "r") as fh:
    new_description = fh.read()

modified = copy.deepcopy(base)
modified["metadata"]["description"] = new_description

with open("primap-zenodo-modified.json", "w") as fh:
    json.dump(modified, fh, sort_keys=True, indent=2)
