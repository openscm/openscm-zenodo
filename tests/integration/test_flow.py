"""
Test our Zenodo interaction flows
"""

from __future__ import annotations

import json
import os

import pytest
import requests

from openscm_zenodo.zenodo import ZenodoDomain, ZenodoInteractor, get_reserved_doi


@pytest.mark.zenodo_token
def test_default_end_to_end_flow(test_data_dir):
    """
    Test we can start with an ID and end up publishing a new version
    """
    any_deposition_id = "101709"
    metadata_file = test_data_dir / "test-deposit-metadata.json"
    sub_dir_file = test_data_dir / "sub-dir" / "file-in-sub-dir.txt"
    files_to_upload = [metadata_file, sub_dir_file]

    zenoodo_interactor = ZenodoInteractor(
        token=os.environ["ZENODO_TOKEN"],
        zenodo_domain=ZenodoDomain.sandbox.value,
    )

    latest_deposition_id = zenoodo_interactor.get_latest_deposition_id(
        any_deposition_id=any_deposition_id,
    )

    # Sometimes you have to delete drafts manually by hand before this works.
    # I'm not sure why this happens,
    # the sandbox seems to somehow end up in a weird state.
    new_deposition_id = zenoodo_interactor.create_new_version_from_latest(
        latest_deposition_id=latest_deposition_id
    ).json()["id"]

    # Optional, you might want the previous version's files in some cases
    remove_all_files_responses = zenoodo_interactor.remove_all_files(
        deposition_id=new_deposition_id
    )
    assert all(
        isinstance(response, requests.models.Response)
        for response in remove_all_files_responses
    )

    with open(metadata_file) as fh:
        metadata = json.load(fh)

    update_metadata_response = zenoodo_interactor.update_metadata(
        deposition_id=new_deposition_id,
        metadata=metadata,
    )
    assert isinstance(update_metadata_response, requests.models.Response)

    # Just a test that this exists really, but handy trick to know
    reserved_doi = get_reserved_doi(update_metadata_response)
    assert "10.5281/zenodo" in reserved_doi

    bucket_url = zenoodo_interactor.get_bucket_url(deposition_id=new_deposition_id)

    for file in files_to_upload:
        resp = zenoodo_interactor.upload_file_to_bucket_url(
            file,
            bucket_url=bucket_url,
        )
        assert isinstance(resp, requests.models.Response)

    publish_response = zenoodo_interactor.publish(deposition_id=new_deposition_id)
    assert isinstance(publish_response, requests.models.Response)

    publish_response_json = publish_response.json()

    # These keys differ in the response because they are updated by Zenodo
    zenodo_altered_keys = ["prereserve_doi"]

    comparable_metadata_from_user = {
        k: v for k, v in metadata["metadata"].items() if k not in zenodo_altered_keys
    }
    comparable_metadata_from_publish_response = {
        k: v
        for k, v in publish_response_json["metadata"].items()
        if k in metadata["metadata"] and k not in zenodo_altered_keys
    }
    assert comparable_metadata_from_user == comparable_metadata_from_publish_response

    assert len(publish_response_json["files"]) == len(files_to_upload)
    # Zenodo doesn't support directories, so uploaded files should be flat.
    # Zipping files is the work around apparently.
    # See https://support.zenodo.org/help/en-gb/1-upload-deposit/74-can-i-upload-folders-directories
    publish_response_uploaded_files = [
        file_record["filename"] for file_record in publish_response_json["files"]
    ]
    assert set(publish_response_uploaded_files) == set(
        [f.name for f in files_to_upload]
    )
