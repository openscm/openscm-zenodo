"""
Upload file(s) to Zenodo

Thanks https://gist.github.com/tyhoff/b757e6af83c1fd2b7b83057adf02c139
for the progress bar.
"""
import logging
import os
import os.path

import requests
from tqdm import tqdm
from tqdm.utils import CallbackIOWrapper

_LOGGER = logging.getLogger(__name__)


"""dict: Configuration for upload progress bar"""
TQDM_KWARGS = dict(unit="B", unit_scale=True, unit_divisor=1024,)


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
    _LOGGER.info("Uploading %s to %s %s", filepath, bucket, zenodo_url)

    upload_url_no_token = "https://{}/api/files/{}/{}?access_token=".format(
        zenodo_url, bucket, os.path.basename(filepath),
    )
    _LOGGER.debug("Upload url: %s", upload_url_no_token)
    upload_url = "{}{}".format(upload_url_no_token, token,)

    file_size = os.stat(filepath).st_size

    with open(filepath, "rb") as file_handle:
        with tqdm(total=file_size, **TQDM_KWARGS) as tqdm_bar:
            wrapped_file = CallbackIOWrapper(tqdm_bar.update, file_handle, "read")
            requests.put(upload_url, data=wrapped_file)
