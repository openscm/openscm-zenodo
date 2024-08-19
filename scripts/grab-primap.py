import json
import requests

response = requests.get("https://zenodo.org/api/records/10705513")
response.raise_for_status()

with open("primap-10705512-zenodo.json", "w") as fh:
    json.dump(response.json(), fh, sort_keys=True, indent=2)
