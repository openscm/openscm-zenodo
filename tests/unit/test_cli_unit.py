import os.path
from unittest.mock import patch

from click.testing import CliRunner

from openscm_zenodo.cli import cli


def test_upload_defaults(caplog, test_data_dir):
    runner = CliRunner(mix_stderr=False)

    tfile = os.path.join(test_data_dir, "test-deposit-metadata.json")
    tbucket = "bucket"
    with patch("openscm_zenodo.cli.upload_file") as mock_upload:
        result = runner.invoke(cli, ["upload", tfile, tbucket,],)

    assert not result.exit_code, result.stderr
    mock_upload.assert_called_with(
        filepath=tfile,
        bucket=tbucket,
        root_dir=None,
        zenodo_url="sandbox.zenodo.org",
        token=None,
    )


def test_upload_root_dir(caplog, test_data_dir):
    runner = CliRunner(mix_stderr=False)

    tfile = os.path.join(test_data_dir, "test-deposit-metadata.json")
    tbucket = "bucket"
    troot_dir = "path/to/somewhere"
    with patch("openscm_zenodo.cli.upload_file") as mock_upload:
        result = runner.invoke(
            cli, ["upload", tfile, tbucket, "--root-dir", troot_dir],
        )

    assert not result.exit_code, result.stderr
    mock_upload.assert_called_with(
        filepath=tfile,
        bucket=tbucket,
        root_dir=troot_dir,
        zenodo_url="sandbox.zenodo.org",
        token=None,
    )
