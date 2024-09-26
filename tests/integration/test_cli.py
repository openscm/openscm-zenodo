"""
Integration tests of our command-line interface
"""

from __future__ import annotations

import copy
import json
import os.path

import pytest
from typer.testing import CliRunner

from openscm_zenodo.cli.app import app
from openscm_zenodo.zenodo import ZenodoDomain, ZenodoInteractor, retrieve_metadata

runner = CliRunner(mix_stderr=False)


@pytest.mark.zenodo_token
def test_default_end_to_end_flow_cli(test_data_dir):
    """
    Test we can start with an ID and end up publishing a new version
    """
    any_deposition_id = "101709"
    metadata_file = test_data_dir / "test-deposit-metadata.json"
    sub_dir_file = test_data_dir / "sub-dir" / "file-in-sub-dir.txt"
    files_to_upload = [metadata_file, sub_dir_file]

    res = runner.invoke(
        app,
        [
            "create-new-version",
            any_deposition_id,
            *[str(f) for f in files_to_upload],
            "--n-threads",
            1,
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
            "--publish",
            "--metadata-file",
            str(metadata_file),
        ],
    )
    assert res.exit_code == 0, res.stderr

    zenoodo_interactor = ZenodoInteractor(
        token=os.environ["ZENODO_TOKEN"],
        zenodo_domain=ZenodoDomain.sandbox.value,
    )

    latest_deposition_id = zenoodo_interactor.get_latest_deposition_id(
        any_deposition_id=any_deposition_id,
    )
    # Check that only the newly created deposition ID went to stdout.
    # This makes it easy to pipe the output of the create-new-version
    # command elsewhere, if desired.
    assert res.stdout == f"{latest_deposition_id}\n"
    assert latest_deposition_id != any_deposition_id

    publish_response_json = zenoodo_interactor.get_deposition(
        deposition_id=latest_deposition_id
    ).json()

    with open(metadata_file) as fh:
        metadata = json.load(fh)

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


@pytest.mark.zenodo_token
def test_retrieve_metadata(test_data_dir):
    """
    Test we can retrieve metadata from Zenodo
    """
    deposition_id = "101709"

    res = runner.invoke(
        app,
        [
            "retrieve-metadata",
            deposition_id,
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
        ],
    )
    assert res.exit_code == 0, res.stderr
    exp = {
        "metadata": {
            "access_right": "open",
            "creators": [{"affiliation": None, "name": "Nicholls, Zebedee"}],
            "doi": "10.5072/zenodo.101709",
            "imprint_publisher": "Zenodo",
            "license": "cc-by-4.0",
            "prereserve_doi": {"doi": "10.5281/zenodo.101709", "recid": 101709},
            "publication_date": "2024-08-20",
            "title": "OpenSCM-Zenodo testing 0",
            "upload_type": "dataset",
        }
    }
    assert res.stdout == f"{json.dumps(exp, indent=2, sort_keys=True)}\n"


@pytest.mark.zenodo_token
def test_retrieve_metadata_user_controlled_only(test_data_dir):
    """
    Test we can retrieve only user-controlled metadata from Zenodo
    """
    deposition_id = "101709"

    res = runner.invoke(
        app,
        [
            "retrieve-metadata",
            deposition_id,
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
            "--user-controlled-only",
        ],
    )
    assert res.exit_code == 0, res.stderr
    exp = {
        "metadata": {
            "access_right": "open",
            "creators": [{"affiliation": None, "name": "Nicholls, Zebedee"}],
            "license": "cc-by-4.0",
            "title": "OpenSCM-Zenodo testing 0",
            "upload_type": "dataset",
        }
    }
    assert res.stdout == f"{json.dumps(exp, indent=2, sort_keys=True)}\n"


def test_retrieve_bibtex(test_data_dir):
    """
    Test we can retrieve a bibtex entry from Zenodo
    """
    deposition_id = "4589756"

    res = runner.invoke(
        app,
        [
            "retrieve-bibtex",
            deposition_id,
        ],
    )
    assert res.exit_code == 0, res.stderr
    exp = """@dataset{zebedee_nicholls_2021_4589756,
  author       = {Zebedee Nicholls and
                  Jared Lewis},
  title        = {{Reduced Complexity Model Intercomparison Project
                   (RCMIP) protocol}},
  month        = mar,
  year         = 2021,
  publisher    = {Zenodo},
  version      = {v5.1.0},
  doi          = {10.5281/zenodo.4589756},
  url          = {https://doi.org/10.5281/zenodo.4589756}
}"""

    # The result has trailing whitespace, which we remove here
    res_compare = "\n".join([v.rstrip() for v in res.stdout.splitlines()])
    assert res_compare == exp


@pytest.mark.zenodo_token
def test_update_metadata(tmp_path):
    """
    Test we can update metadata on Zenodo
    """
    deposition_id = "101845"
    zenoodo_interactor = ZenodoInteractor(
        token=os.environ["ZENODO_TOKEN"],
        zenodo_domain=ZenodoDomain.sandbox.value,
    )
    metadata_file_new = tmp_path / "test-update-metadata-new.json"
    metadata_file_start = tmp_path / "test-update-metadata-original.json"

    # Firstly, retrieve the metadata so we can put it back after we're done
    metadata_start = zenoodo_interactor.get_metadata(deposition_id)
    with open(metadata_file_start, "w") as fh:
        json.dump(metadata_start, fh)

    metadata_new = copy.deepcopy(metadata_start)

    new_title = "New title"
    assert metadata_start["metadata"]["title"] != new_title
    metadata_new["metadata"]["title"] = new_title

    with open(metadata_file_new, "w") as fh:
        json.dump(metadata_new, fh)

    res = runner.invoke(
        app,
        [
            "update-metadata",
            deposition_id,
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
            "--metadata-file",
            str(metadata_file_new),
        ],
    )
    assert res.exit_code == 0, res.stderr
    assert not res.stdout

    metadata_res = retrieve_metadata(
        deposition_id, zenoodo_interactor=zenoodo_interactor
    )
    assert metadata_res == metadata_new

    # Put the metadata back
    res = runner.invoke(
        app,
        [
            "update-metadata",
            deposition_id,
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
            "--metadata-file",
            str(metadata_file_start),
        ],
    )
    assert res.exit_code == 0, res.stderr
    assert not res.stdout

    metadata_res = zenoodo_interactor.get_metadata(deposition_id)
    assert metadata_res == metadata_start


@pytest.mark.zenodo_token
def test_delete_upload_files(test_data_dir):
    """
    Test we can delete files from and upload files to Zenodo
    """
    deposition_id = "101845"
    metadata_file = test_data_dir / "test-deposit-metadata.json"
    sub_dir_file = test_data_dir / "sub-dir" / "file-in-sub-dir.txt"
    another_sub_dir_file = test_data_dir / "sub-dir" / "another-file.txt"
    files_to_upload = [metadata_file, sub_dir_file, another_sub_dir_file]

    zenoodo_interactor = ZenodoInteractor(
        token=os.environ["ZENODO_TOKEN"],
        zenodo_domain=ZenodoDomain.sandbox.value,
    )

    # Firstly, remove a file entry on the draft
    res = runner.invoke(
        app,
        [
            "remove-files",
            deposition_id,
            str(files_to_upload[0]),
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
        ],
    )
    assert res.exit_code == 0, res.stderr
    assert not res.stdout

    publish_response_json_after_first_removal = zenoodo_interactor.get_deposition(
        deposition_id=deposition_id
    ).json()
    assert (
        len(publish_response_json_after_first_removal["files"])
        == len(files_to_upload) - 1
    )

    # Then, remove all files
    res = runner.invoke(
        app,
        [
            "remove-files",
            deposition_id,
            "--all",
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
        ],
    )
    assert res.exit_code == 0, res.stderr
    assert not res.stdout

    publish_response_json_after_removal = zenoodo_interactor.get_deposition(
        deposition_id=deposition_id
    ).json()
    assert len(publish_response_json_after_removal["files"]) == 0

    # Upload some files
    res = runner.invoke(
        app,
        [
            "upload-files",
            deposition_id,
            *[str(f) for f in files_to_upload],
            "--zenodo-domain",
            "https://sandbox.zenodo.org",
        ],
    )
    assert res.exit_code == 0, res.stderr
    assert not res.stdout

    publish_response_json_after_upload = zenoodo_interactor.get_deposition(
        deposition_id=deposition_id
    ).json()
    assert len(publish_response_json_after_upload["files"]) == len(files_to_upload)
    # Zenodo doesn't support directories, so uploaded files should be flat.
    # Zipping files is the work around apparently.
    # See https://support.zenodo.org/help/en-gb/1-upload-deposit/74-can-i-upload-folders-directories
    publish_response_uploaded_files = [
        file_record["filename"]
        for file_record in publish_response_json_after_upload["files"]
    ]
    assert set(publish_response_uploaded_files) == set(
        [f.name for f in files_to_upload]
    )
