"""
Zenodo interactions handling
"""
import json
import logging
import os.path

import requests

from .uploading import upload_with_progress_bar

_LOGGER = logging.getLogger(__name__)


def _get_deposit(deposition_id, zenodo_url, token):
    url_to_hit = "https://{}/api/deposit/depositions/{}".format(
        zenodo_url, deposition_id
    )
    headers_bucket_request = {"Accept": "application/json"}

    _LOGGER.debug("Sending request to: %s", url_to_hit)
    response = requests.get(
        url_to_hit, headers=headers_bucket_request, params={"access_token": token},
    )
    response.raise_for_status()

    return response


def _get_new_version(deposition_id, zenodo_url, token):
    _LOGGER.info("Creating new version for deposition id: %s", deposition_id)

    _LOGGER.debug("Retrieving deposit")
    response = _get_deposit(deposition_id, zenodo_url, token)

    latest_version_deposition_id = response.json()["links"]["latest"].split("/")[-1]
    _LOGGER.debug("Latest version of record: %s", latest_version_deposition_id)

    url_to_hit = "https://{}/api/deposit/depositions/{}/actions/newversion".format(
        zenodo_url, latest_version_deposition_id
    )

    _LOGGER.debug("Posting to: %s", url_to_hit)

    response = requests.post(url_to_hit, params={"access_token": token},)
    response.raise_for_status()

    new_version = response.json()["links"]["latest_draft"].split("/")[-1]

    _LOGGER.info("New version: %s", new_version)

    return new_version


def _remove_all_files(deposition_id, zenodo_url, token):
    _LOGGER.info("Removing all files at deposition id: %s", deposition_id)

    url_to_hit = "https://{}/api/deposit/depositions/{}/files".format(
        zenodo_url, deposition_id
    )

    _LOGGER.debug("Sending to: %s", url_to_hit)

    response = requests.get(url_to_hit, params={"access_token": token})
    response.raise_for_status()

    for file_entry in response.json():
        _LOGGER.info("Removing file: %s", file_entry["id"])

        url_to_hit = "https://{}/api/deposit/depositions/{}/files/{}".format(
            zenodo_url, deposition_id, file_entry["id"]
        )
        _LOGGER.debug("Posting to: %s", url_to_hit)

        response_file = requests.delete(url_to_hit, params={"access_token": token},)
        response_file.raise_for_status()

    _LOGGER.info("Finished removing all files")


def _set_upload_metadata(deposition_id, zenodo_url, token, deposit_metadata):
    _LOGGER.info("Setting metadata for deposition id: %s", deposition_id)

    url_to_hit = "https://{}/api/deposit/depositions/{}".format(
        zenodo_url, deposition_id
    )

    _LOGGER.debug("Sending to: %s", url_to_hit)

    response = requests.put(
        url_to_hit,
        params={"access_token": token},
        data=json.dumps(deposit_metadata),
        headers={"Content-Type": "application/json"},
    )

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        _LOGGER.error("Error while uploading metadata: %s", response.text)
        raise

    _LOGGER.debug("Successfully set metdata")


def create_new_zenodo_version(deposition_id, zenodo_url, token, deposit_metadata):
    """
    Create a new version of a Zenodo record (i.e. a specific deposition ID)

    Parameters
    ----------
    deposition_id : str
        Deposition ID of any DOI which represents a specific version of the record, but crucially **not** the DOI which represents all versions of the record (this won't work).

    zenodo_url : str
        Zenodo url to upload the file to (e.g. ``sandbox.zenodo.org`` or
        ``zenodo.org``)

    token : str
        Token to use to authenticate the request

    deposit_metadata : str
        Path to file containing metadata for this new version

    Returns
    -------
    str
        The deposition ID of the new version of the record
    """
    with open(deposit_metadata, "r") as fileh:
        deposit_metadata_loaded = json.load(fileh)
    # validate deposit_metadata_loaded

    new_version = _get_new_version(deposition_id, zenodo_url, token)

    _remove_all_files(new_version, zenodo_url, token)
    _set_upload_metadata(new_version, zenodo_url, token, deposit_metadata_loaded)

    return new_version


def upload_file(filepath, bucket, zenodo_url, token):
    """
    Upload file to Zenodo

    Parameters
    ----------
    filepath : str
        Path to file to upload

    bucket : str
        Bucket to upload the file to

    zenodo_url : str
        Zenodo url to upload the file to (e.g. ``sandbox.zenodo.org`` or
        ``zenodo.org``)

    token : str
        Token to use to authenticate the upload
    """
    _LOGGER.info(
        "Uploading file `%s` to bucket `%s` at `%s`", filepath, bucket, zenodo_url
    )

    upload_url_no_token = "https://{}/api/files/{}/{}?access_token=".format(
        zenodo_url, bucket, os.path.basename(filepath),
    )
    _LOGGER.debug("Upload url: %s", upload_url_no_token)
    upload_url = "{}{}".format(upload_url_no_token, token)

    upload_with_progress_bar(filepath, upload_url)


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

    _LOGGER.debug("Retrieving deposit")
    response = _get_deposit(deposition_id, zenodo_url, token)

    bucket = response.json()["links"]["bucket"]
    _LOGGER.debug("Full url for bucket: %s", bucket)

    bucket = bucket.split("/")[-1]
    _LOGGER.info("Successfully retrieved bucket: %s", bucket)

    return bucket
