import logging

import requests

_LOGGER = logging.getLogger(__name__)


def get_bucket_id(deposition_id, zenodo_url, token):
    """
    Get bucket ID for a given Zenodo deposition ID

    Parameters
    ----------
    deposition_id : str
        Deposition ID to check

    zenodo_url : str
        Zenodo url to upload the file to (e.g. ``sandbox.zenodo.org`` or
        ``zenodo.org``)

    token : str
        Token to use to authenticate the request

    Returns
    -------
    str
        The bucket associated with ``deposition_id``
    """
    _LOGGER.info("Determining bucket for deposition_id: %s", deposition_id)

    url_to_hit = "https://{}/api/deposit/depositions/{}".format(
        zenodo_url, deposition_id
    )
    headers_bucket_request = {"Accept": "application/json"}

    _LOGGER.debug("Sending request to: %s", url_to_hit)
    response = requests.get(
        url_to_hit, headers=headers_bucket_request, params={"access_token": token},
    )
    response.raise_for_status()

    bucket = response.json()["links"]["bucket"]
    _LOGGER.debug("Full url for bucket: %s", bucket)

    bucket = bucket.split("/")[-1]
    _LOGGER.info("Successfully retrieved bucket: %s", bucket)

    return bucket


def create_new_zenodo_version(deposition_id, zenodo_url, token, deposit_metadata):
    raise NotImplementedError
    # get new version
    # set upload metadata
