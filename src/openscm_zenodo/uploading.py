"""
File uploading handling

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


def upload_with_progress_bar(filepath, upload_url):
    """
    Upload file with a progress bar

    Parameters
    ----------
    filepath : str
        Path to file to upload

    upload_url : str
        URL to put the file onto
    """
    _LOGGER.info("Uploading %s to %s", filepath, upload_url)

    file_size = os.stat(filepath).st_size

    with open(filepath, "rb") as file_handle:
        with tqdm(total=file_size, **TQDM_KWARGS) as tqdm_bar:
            wrapped_file = CallbackIOWrapper(tqdm_bar.update, file_handle, "read")
            requests.put(upload_url, data=wrapped_file)
