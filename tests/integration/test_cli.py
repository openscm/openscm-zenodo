import logging
import os.path

import pytest
from click.testing import CliRunner

from openscm_zenodo.cli import cli


@pytest.mark.zenodo_token
def test_create_new_version_runs(test_data_dir, caplog):
    runner = CliRunner(mix_stderr=False)
    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(
            cli,
            [
                "--log-level",
                "DEBUG",
                "create-new-version",
                "638907",
                os.path.join(test_data_dir, "test-deposit-metadata.json"),
            ],
        )

    assert not result.exit_code, result.stderr
    # known new version
    assert result.stdout.strip() == "739845"


@pytest.mark.zenodo_token
def test_create_new_version_none_existing(test_data_dir, caplog):
    runner = CliRunner(mix_stderr=False)
    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(
            cli,
            [
                "--log-level",
                "DEBUG",
                "create-new-version",
                "894278",
                os.path.join(test_data_dir, "test-deposit-metadata.json"),
            ],
        )

    assert not result.exit_code, result.stderr
    # new version is same as deposition id
    assert result.stdout.strip() == "894278"

    using_existing_record = [
        r for r in caplog.records
        if r.message == (
            "No published versions of record, using given deposition id"
        )
    ]
    assert len(using_existing_record)
    using_existing_record = using_existing_record[0]
    assert using_existing_record.levelname == "INFO"


@pytest.mark.zenodo_token
def test_create_new_version_metadata_error(test_data_dir, caplog):
    runner = CliRunner(mix_stderr=False)
    with caplog.at_level(logging.ERROR):
        result = runner.invoke(
            cli,
            [
                "--log-level",
                "DEBUG",
                "create-new-version",
                "638907",
                os.path.join(
                    test_data_dir, "test-deposit-metadata-no-description.json"
                ),
            ],
        )

    assert result.exit_code == 1, result.stderr
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "ERROR"
    assert caplog.records[0].message.startswith("Error while uploading metadata: {")
    assert (
        '{"field": "metadata.description", "message": "Missing data for required field."}'
        in caplog.records[0].message
    )


@pytest.mark.zenodo_token
def test_upload_runs(caplog, test_data_dir):
    runner = CliRunner(mix_stderr=False)
    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(
            cli,
            [
                "upload",
                os.path.join(test_data_dir, "test-deposit-metadata.json"),
                "e428bd2f-84e5-49ae-84ed-f42da1b8e0da",
            ],
        )

    assert not result.exit_code, result.stderr
    assert not result.stdout.strip()


@pytest.mark.zenodo_token
def test_get_bucket_runs(caplog):
    runner = CliRunner(mix_stderr=False)
    with caplog.at_level(logging.DEBUG):
        result = runner.invoke(cli, ["--log-level", "DEBUG", "get-bucket", "739845",],)

    assert not result.exit_code, result.stderr
    assert result.stdout.strip() == "e428bd2f-84e5-49ae-84ed-f42da1b8e0da"
