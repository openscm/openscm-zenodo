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
                "create-new-version",
                "638907",
                os.path.join(test_data_dir, "test-deposit-metadata.json")
            ],
        )

    assert not result.exit_code, result.stderr
    # known new version
    assert result.stdout.strip() == "739845"
