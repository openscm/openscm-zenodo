import os.path
from unittest.mock import patch

import pytest

from openscm_zenodo.zenodo import upload_file


@pytest.mark.parametrize("tfilepath,root_dir,exp_upload_path", (
    (os.path.join("/path", "to", "file.txt"), None, "file.txt"),
    # without trailing separator on root dir
    (os.path.join("/path", "to", "file.txt"), os.path.join("/path"), os.path.join("to", "file.txt")),
    # with trailing separator on root dir
    (os.path.join("/path", "to", "file.txt"), os.path.join("/path/"), os.path.join("to", "file.txt")),
))
def test_upload_file(tfilepath, root_dir, exp_upload_path):
    tbucket = "bucket"
    tzenodo_url = "zenodo.org"
    ttoken = "123ladidi"

    expected_upload_url = "https://{}/api/files/{}/{}?access_token={}".format(
        tzenodo_url, tbucket, exp_upload_path, ttoken
    )

    call_kwargs = {}
    if root_dir is not None:
        call_kwargs["root_dir"] = root_dir

    with patch("openscm_zenodo.zenodo.upload_with_progress_bar") as mock_upload:
        res = upload_file(
            tfilepath,
            tbucket,
            tzenodo_url,
            ttoken,
            **call_kwargs,
        )

    assert res is None
    mock_upload.assert_called_with(
        tfilepath,
        expected_upload_url
    )
