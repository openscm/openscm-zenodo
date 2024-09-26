"""
Zenodo interactions handling
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os.path
from collections.abc import Collection, Iterable
from enum import Enum, auto
from pathlib import Path
from typing import Any, Optional, Union

import requests
import tqdm
import tqdm.utils
from attrs import define
from loguru import logger
from typing_extensions import TypeAlias

from openscm_zenodo.logging import mask_token

_LOGGER = logging.getLogger(__name__)

TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT = dict(
    unit="B",
    unit_scale=True,
    unit_divisor=1024,
)
"""Default configuration for upload progress bar"""


class ZenodoDomain(str, Enum):
    """
    Supported zenodo URLs
    """

    production = "https://zenodo.org"
    sandbox = "https://sandbox.zenodo.org"


class RestAction(Enum):
    """
    Known rest actions
    """

    get = auto()
    """Get request"""

    post = auto()
    """
    Post request

    This should only add new data, it should not modify existing data.
    """

    put = auto()
    """
    Put request

    This modifies existing data.
    """

    delete = auto()
    """Delete request"""


MetadataType: TypeAlias = dict[str, dict[str, str]]


@define
class ZenodoInteractor:
    """
    Class for interacting with Zenodo
    """

    token: Optional[str] = None
    """Token to use for authenticating interactions with the Zenodo domain"""

    zenodo_domain: Union[str, ZenodoDomain] = ZenodoDomain.production
    """Zenodo domain to interact with"""

    timeout: int = 10
    """Timeout to apply to requests calls"""

    timeout_upload: int = 60 * 60
    """Timeout to apply to uploads"""

    def get_response(
        self,
        post_domain_part: str,
        rest_action: RestAction = RestAction.get,
        params: Union[dict[str, str], None] = None,
        **kwargs: Any,
    ) -> requests.models.Response:
        """
        Get a response from Zenodo

        Parameters
        ----------
        post_domain_part
            The post-domain part of the URL to hit.

            In other words, the API to hit.
            For example, "/api/deposit/depositions/1858949"

        params
            Headers to use as part of the request.

            The authentication token is automatically added
            before passing to the relevant requests action
            so you don't need to included that in `params`.

        **kwargs
            Passed to the relevant requests action.

        Returns
        -------
        :
            Response from the URL that was hit
        """
        if isinstance(self.zenodo_domain, ZenodoDomain):
            zenodo_domain = self.zenodo_domain.value

        else:
            zenodo_domain = self.zenodo_domain

        if params is None:
            params = {}

        if self.token:
            params["access_token"] = self.token

        url_to_hit = f"{zenodo_domain}{post_domain_part}"
        # Mask just in case the user put the token in the URL by accident
        logger.debug(
            f"Sending {rest_action} request to "
            f"{mask_token(url_to_hit, token=self.token)}"
        )

        requests_kwargs = dict(
            params=params,
            **kwargs,
        )
        if rest_action == RestAction.get:
            response = requests.get(url_to_hit, **requests_kwargs, timeout=self.timeout)

        elif rest_action == RestAction.post:
            response = requests.post(
                url_to_hit, **requests_kwargs, timeout=self.timeout
            )

        elif rest_action == RestAction.put:
            response = requests.put(url_to_hit, **requests_kwargs, timeout=self.timeout)

        elif rest_action == RestAction.delete:
            response = requests.delete(
                url_to_hit, **requests_kwargs, timeout=self.timeout
            )

        else:
            raise NotImplementedError(rest_action)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            print(response.json())
            raise

        return response

    def get_record(
        self,
        record_id: str,
    ) -> requests.models.Response:
        """
        Get a record from Zenodo

        Parameters
        ----------
        record_id
            The ID of the record

        Returns
        -------
        :
            The Zenodo record
        """
        logger.info(f"Retrieving record {record_id!r}")
        response = self.get_response(f"/api/records/{record_id}")

        return response

    def get_deposition(
        self,
        deposition_id: str,
    ) -> requests.models.Response:
        """
        Get a deposition from Zenodo

        Parameters
        ----------
        deposition_id
            The ID of the deposition

        Returns
        -------
        :
            The Zenodo deposition
        """
        logger.info(f"Retrieving deposition {deposition_id!r}")
        response = self.get_response(f"/api/deposit/depositions/{deposition_id}")

        return response

    def get_metadata(
        self,
        deposition_id: str,
        user_controlled_only: bool = False,
    ) -> MetadataType:
        """
        Get the metadata for a given deposition ID

        Parameters
        ----------
        deposition_id
            The ID of the deposition

        user_controlled_only
            Only return metadata keys that the user can control.

            If this is `True`, the metadata keys controlled by Zenodo
            (e.g. the DOI)
            are removed from the returned metadata.
            This flag is important to use
            if you want to use the retrieved metadata
            as the starting point for the next version of a deposit.

        Returns
        -------
        :
            Metadata, in a form which could be used directly with the Zenodo API

            For an example, see the docstring of
            [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata].
        """
        logger.info(f"Retrieving metadata for {deposition_id=!r}")
        if self.token:
            deposition = self.get_deposition(deposition_id)

        else:
            deposition = self.get_record(deposition_id)

        metadata = {"metadata": deposition.json()["metadata"]}

        if user_controlled_only:
            for k in [
                "doi",
                "imprint_publisher",
                "prereserve_doi",
                "publication_date",
                "relations",
            ]:
                if k in metadata["metadata"]:
                    metadata["metadata"].pop(k)

        return metadata

    def get_bibtex_entry(
        self,
        deposition_id: str,
    ) -> str:
        """
        Get the bibtex entry for a given deposition ID

        Parameters
        ----------
        deposition_id
            The ID of the deposition

        Returns
        -------
        :
            Bibtex entry for `deposition_id`.
        """
        logger.info(f"Retrieving bibtex entry for {deposition_id=!r}")
        response = self.get_response(f"/records/{deposition_id}/export/bibtex")

        bibtex_entry = response.text

        return bibtex_entry

    def get_latest_deposition_id(
        self,
        any_deposition_id: str,
    ) -> str:
        """
        Get the latest deposition ID from any deposition ID which is part of the record

        For example, we can take the deposition ID
        from the first version of a record which was published
        and always be given back the deposition ID of the latest record in the series.

        Parameters
        ----------
        any_deposition_id
            Any deposition ID which belongs to the series/record of interest.

            This can be obtained from the URL of any deposit in the series.
            For example, if the Zenodo URL is
            https://sandbox.zenodo.org/records/101709,
            then you can pass in "101709" as `any_deposition_id`.

        Returns
        -------
        :
            ID of the latest deposition in the series/record
        """
        logger.info(
            "Retrieving the ID of the latest deposition in the series "
            f"which includes deposition ID {any_deposition_id!r}"
        )
        record = self.get_record(record_id=any_deposition_id)
        record_json = record.json()

        record_latest = requests.get(
            record_json["links"]["latest"], timeout=self.timeout
        )

        latest_deposition_id = str(record_latest.json()["id"])
        logger.info(
            f"For deposition ID {any_deposition_id!r}, "
            "the ID of the latest deposition in the series is "
            f"{latest_deposition_id!r}"
        )

        return latest_deposition_id

    def create_new_version_from_latest(
        self,
        latest_deposition_id: str,
    ) -> requests.models.Response:
        """
        Create a new version of a record from the latest deposition ID

        Parameters
        ----------
        latest_deposition_id
            The ID of the latest deposition.

            This is the ID of the latest version from a collection of records.
            For example, if there is v1.0.0, v2.0.0 and v3.0.0 on Zenodo,
            this should be the deposition ID of v3.0.0.

        Returns
        -------
        :
            The new version's record from Zenodo

        Notes
        -----
        From https://developers.zenodo.org/#new-version

        ...
        - The id used to create this new version has to be the id of the latest version.
          It is not possible to use the global id that references all the versions.

        We replicate this logic here.
        To create a new version from the all records ID that references all versions,
        use [`create_new_version`][openscm_zenodo.zenodo.create_new_version].
        """
        logger.info(f"Creating a new version from {latest_deposition_id=!r}")

        try:
            create_new_version_response = self.get_response(
                post_domain_part=f"/api/deposit/depositions/{latest_deposition_id}/actions/newversion",
                rest_action=RestAction.post,
            )
            logger.info(
                "Successfully created new version. "
                "The new version's deposition id is "
                f"{create_new_version_response.json()['id']!r}"
            )

        except requests.exceptions.HTTPError as exc:
            exc_response_json = exc.response.json()
            if (
                exc_response_json["errors"][0]["messages"][0]
                == "Please remove all files first."
            ):
                # TODO: consider just not raising an error in this case
                msg = (
                    "You must remove all the files in the current draft version "
                    "before you can call the 'create a new version' "
                    "API again without error. "
                    "Having said that, this error means that you already have a draft, "
                    "hence you probably don't need to call the "
                    "'create a new version' API in the first place."
                )

                raise AssertionError(msg) from exc

            raise

        # I am pretty sure the below text from https://developers.zenodo.org/#new-version
        # is wrong because the above appears to return the new version's record.
        #
        # Text I think is wrong from https://developers.zenodo.org/#new-version:
        #
        # - The response body of this action
        #   is NOT the new version deposit, but the original resource.
        #   The new version deposition can be accessed through the "latest_draft"
        #   under "links" in the response body.

        return create_new_version_response

    def get_bucket_url(self, deposition_id: str) -> str:
        """
        Get the bucket URL for a given deposition ID

        Parameters
        ----------
        deposition_id
            Deposition ID for which to get the bucket URL

        Returns
        -------
        :
            Bucket URL for `deposition_id`
        """
        logger.info(f"Retrieving bucket URL for {deposition_id=!r}")

        deposit_id_response = self.get_response(
            post_domain_part=f"/api/deposit/depositions/{deposition_id}",
        )

        bucket_url = str(deposit_id_response.json()["links"]["bucket"])
        logger.info(f"Successfully retrieved {bucket_url=!r} for {deposition_id=!r}")

        return bucket_url

    def update_metadata(
        self, deposition_id: str, metadata: MetadataType
    ) -> requests.models.Response:
        """
        Update the metadata for a given deposition

        Parameters
        ----------
        deposition_id
            Deposition ID of which to update the metadata

        metadata
            Metadata to apply to the deposition

            For the complete list of supported key : value pairs supported by Zenodo,
            see [https://developers.zenodo.org/#representation]().
            You do not need to provide values for all the metadata keys,
            only the ones relevant to you.

            For an example, see the docstring of
            [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata].

        Returns
        -------
        :
            Response to the metadata update request.
        """
        logger.info(f"Updating metadata for {deposition_id=!r}")
        logger.debug(f"New metadata: {metadata}")

        update_metadata_response = self.get_response(
            post_domain_part=f"/api/deposit/depositions/{deposition_id}",
            rest_action=RestAction.put,
            data=json.dumps(metadata),
            headers={"Content-Type": "application/json"},
        )

        return update_metadata_response

    def upload_file_to_bucket_url(
        self,
        to_upload: Path,
        bucket_url: str,
        tqdm_kwargs: Optional[dict[str, Any]] = None,
    ) -> requests.models.Response:
        """
        Upload a file to a bucket URL

        This is a relatively low-level function,
        which requires you to have already determined
        the bucket URL to upload to yourself.

        Note that zenodo does not allow you to upload folders.
        As noted in [this response](https://support.zenodo.org/help/en-gb/1-upload-deposit/74-can-i-upload-folders-directories):

        > Instead, you can create a ZIP archive and upload it,
        > in which case Zenodo will display the file structure inside the ZIP.

        Parameters
        ----------
        to_upload
            File to upload

        bucket_url
            The bucket URL to use for the upload

        tqdm_kwargs
            Keyword arguments to use with our progress bar.

            If not supplied, we use
            [`TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT`][openscm_zenodo.zenodo.TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT].

        Returns
        -------
        :
            The response from the file upload request
        """
        if tqdm_kwargs is None:
            tqdm_kwargs = TQDM_UPLOAD_PROGRESS_KWARGS_DEFAULT

        upload_url = f"{bucket_url}/{to_upload.name}"

        logger.info(f"Uploading {to_upload} to {upload_url=!r}")

        file_size = os.stat(to_upload).st_size
        with tqdm.tqdm(total=file_size, **tqdm_kwargs) as tqdm_bar:
            with open(to_upload, "rb") as file_handle:
                wrapped_file = tqdm.utils.CallbackIOWrapper(
                    tqdm_bar.update, file_handle, "read"
                )
                response = requests.put(
                    upload_url,
                    data=wrapped_file,
                    params={"access_token": self.token},
                    timeout=self.timeout_upload,
                )

        response.raise_for_status()
        logger.info(f"Successfully uploaded {to_upload}")
        return response

    def upload_files(
        self,
        deposition_id: str,
        to_upload: Collection[Path],
        tqdm_kwargs: Optional[dict[str, Any]] = None,
        n_threads: int = 4,
    ) -> tuple[requests.models.Response, ...]:
        """
        Upload file(s) to a deposition

        Note that zenodo does not allow you to upload folders.
        As noted in [this response](https://support.zenodo.org/help/en-gb/1-upload-deposit/74-can-i-upload-folders-directories):

        > Instead, you can create a ZIP archive and upload it,
        > in which case Zenodo will display the file structure inside the ZIP.

        Parameters
        ----------
        deposition_id
            ID of the deposition to upload to

        to_upload
            File(s) to upload

        tqdm_kwargs
            Keyword arguments to use with our progress bar.

            Passed to
            [`upload_file_to_bucket_url`][openscm_zenodo.zenodo.ZenodoInteractor.upload_file_to_bucket_url].

        n_threads
            Number of threads to use for the uploads.

        Returns
        -------
        :
            The response(s) from the file upload request(s)
        """
        logger.info(
            f"Uploading {len(to_upload)} {'files' if len(to_upload) > 1 else 'file'} "
            f"to {deposition_id=!r}"
        )
        bucket_url = self.get_bucket_url(deposition_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [
                executor.submit(
                    self.upload_file_to_bucket_url,
                    to_upload=file,
                    bucket_url=bucket_url,
                    tqdm_kwargs=tqdm_kwargs,
                )
                for file in tqdm.tqdm(to_upload, desc="Submitting files to queue")
            ]

            responses = tuple(
                [
                    future.result()
                    for future in tqdm.tqdm(
                        concurrent.futures.as_completed(futures),
                        desc="Files to upload",
                        total=len(futures),
                    )
                ]
            )

        return responses

    def publish(self, deposition_id: str) -> requests.models.Response:
        """
        Publish a deposition

        Note that this only works on draft depositions.

        Parameters
        ----------
        deposition_id
            Deposition ID to publish

        Returns
        -------
        :
            Response from the publish request
        """
        logger.info(f"Publishing {deposition_id=!r}")
        response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/actions/publish",
            rest_action=RestAction.post,
        )
        logger.info(f"Successfully published {deposition_id=!r}")

        return response

    def delete_deposition(self, deposition_id: str) -> None:
        """
        Delete a deposition

        Note that this only works on draft depositions.

        Parameters
        ----------
        deposition_id
            Deposition ID to delete
        """
        logger.info(f"Deleting {deposition_id=!r}")
        self.get_response(
            f"/api/deposit/depositions/{deposition_id}",
            rest_action=RestAction.delete,
        )
        logger.info(f"Successfully deleted {deposition_id=!r}")

    def remove_files(
        self,
        deposition_id: str,
        to_remove: Collection[Path],
        # # Off until parallelism works
        # n_threads: int = 4,
    ) -> tuple[requests.models.Response, ...]:
        """
        Remove file(s) from a deposition

        Parameters
        ----------
        deposition_id
            ID of the deposition to alter

        to_remove
            File(s) to remove

        Returns
        -------
        :
            The response(s) from the file removal request(s)
        """
        logger.info(
            f"Removing {len(to_remove)} {'files' if len(to_remove) > 1 else 'file'} "
            f"from {deposition_id=!r}"
        )
        filenames_to_delete = set(f.name for f in to_remove)

        files_response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/files",
        )
        file_ids_to_remove = [
            v["id"]
            for v in files_response.json()
            if v["filename"] in filenames_to_delete
        ]

        return self.remove_files_by_id(
            file_ids_to_remove=file_ids_to_remove,
            deposition_id=deposition_id,
        )

    def remove_files_by_id(
        self,
        deposition_id: str,
        file_ids_to_remove: Iterable[str],
        # Off until parallelism works
        # n_threads: int = 4,
    ) -> tuple[requests.models.Response, ...]:
        """
        Remove file(s) from a deposition, using their IDs

        Parameters
        ----------
        deposition_id
            ID of the deposition to alter

        file_ids_to_remove
            ID of file(s) to remove

        Returns
        -------
        :
            The response(s) from the file removal request(s)
        """
        # Wanted to do this in parallel, but weirdly flaky
        responses = tuple(
            [
                self.remove_file_id(deposition_id=deposition_id, to_remove_id=file_id)
                for file_id in tqdm.tqdm(file_ids_to_remove, desc="Files to remove")
            ]
        )
        # with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:
        #     futures = [
        #         executor.submit(
        #             self.remove_file_id,
        #             to_remove_id=file_id,
        #             deposition_id=deposition_id,
        #         )
        #         for file_id in tqdm.tqdm(
        #             file_ids_to_remove, desc="Submitting files to queue"
        #         )
        #     ]
        #
        #     responses = tuple(
        #         [
        #             future.result()
        #             for future in tqdm.tqdm(
        #                 concurrent.futures.as_completed(futures),
        #                 desc="Files to remove",
        #                 total=len(futures),
        #             )
        #         ]
        #     )

        return responses

    def remove_file_id(
        self,
        deposition_id: str,
        to_remove_id: str,
    ) -> requests.models.Response:
        """
        Remove a file from a deposition, using its ID

        Parameters
        ----------
        deposition_id
            ID of the deposition to alter

        to_remove_id
            ID of the file to remove

        Returns
        -------
        :
            The response from the file removal request
        """
        response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/files/{to_remove_id}",
            rest_action=RestAction.delete,
        )

        return response

    def remove_all_files(
        self,
        deposition_id: str,
        # # Off until parallelism works
        # n_threads: int = 4
    ) -> tuple[requests.models.Response, ...]:
        """
        Remove all the files currently associated with a given deposition

        Parameters
        ----------
        deposition_id
            Deposition ID from which to remove all files

        Returns
        -------
        :
            The response(s) from the file removal request(s)
        """
        logger.info(f"Removing all files from {deposition_id=!r}")
        files_response = self.get_response(
            f"/api/deposit/depositions/{deposition_id}/files",
        )

        file_ids_to_remove = [v["id"] for v in files_response.json()]

        return self.remove_files_by_id(
            deposition_id=deposition_id,
            file_ids_to_remove=file_ids_to_remove,
            # n_threads=n_threads,
        )


def retrieve_metadata(
    deposition_id: str,
    zenoodo_interactor: Optional[ZenodoInteractor] = None,
) -> dict[str, dict[str, str]]:
    r"""
    Retrieve metadata associated with a given deposition ID

    Parameters
    ----------
    deposition_id
        The ID of the deposition

    zenoodo_interactor
        Object to use to interact with Zenodo.

        If not supplied, we use a default interactor with no authentication.

    Returns
    -------
    :
        Metadata, in a form which could be used directly with the Zenodo API.

    Examples
    --------
    >>> import json
    >>> res_raw = retrieve_metadata("4589756")
    >>> res_json = json.dumps(res_raw, indent=2, sort_keys=True)
    >>> print(res_json)
    {
      "metadata": {
        "access_right": "open",
        "creators": [
          {
            "affiliation": "Australian-German Climate & Energy College, University of Melbourne",
            "name": "Zebedee Nicholls",
            "orcid": "0000-0002-4767-2723"
          },
          {
            "affiliation": "Australian-German Climate & Energy College, University of Melbourne",
            "name": "Jared Lewis",
            "orcid": "0000-0002-8155-8924"
          }
        ],
        "description": "Reduced Complexity Model Intercomparison Project (RCMIP) protocol. The protocol defines all of RCMIP's experiments as well as RCMIP's submission template. If used, please also cite Nicholls et al., GMD 2020 (https://doi.org/10.5194/gmd-13-5175-2020).",
        "doi": "10.5281/zenodo.4589756",
        "keywords": [
          "rcmip",
          "protocol",
          "climate",
          "reduced-complexity",
          "model",
          "models",
          "intercomparison",
          "comparison"
        ],
        "language": "eng",
        "license": {
          "id": "cc-by-sa-4.0"
        },
        "publication_date": "2021-03-09",
        "relations": {
          "version": [
            {
              "index": 1,
              "is_last": true,
              "parent": {
                "pid_type": "recid",
                "pid_value": "4589726"
              }
            }
          ]
        },
        "resource_type": {
          "title": "Dataset",
          "type": "dataset"
        },
        "title": "Reduced Complexity Model Intercomparison Project (RCMIP) protocol",
        "version": "v5.1.0"
      }
    }
    """  # noqa: E501
    if zenoodo_interactor is None:
        zenoodo_interactor = ZenodoInteractor()

    return zenoodo_interactor.get_metadata(deposition_id)


def retrieve_bibtex_entry(
    deposition_id: str,
    zenoodo_interactor: Optional[ZenodoInteractor] = None,
) -> str:
    r"""
    Retrieve the bibtext entry associated with a given deposition ID

    Parameters
    ----------
    deposition_id
        The ID of the deposition

    zenoodo_interactor
        Object to use to interact with Zenodo.

        If not supplied, we use a default interactor with no authentication.

    Returns
    -------
    :
        Bibtex entry for deposition ID `deposition_id`.

    Examples
    --------
    >>> res = retrieve_bibtex_entry("4589756")
    >>> # There are trailing newlines in the Zenodo response.
    >>> # We strip them here
    >>> res_disp = "\n".join([v.rstrip() for v in res.splitlines()])
    >>> print(res_disp)
    @dataset{zebedee_nicholls_2021_4589756,
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
    }
    """
    if zenoodo_interactor is None:
        zenoodo_interactor = ZenodoInteractor()

    return zenoodo_interactor.get_bibtex_entry(deposition_id)


def create_new_version(  # noqa: PLR0913
    any_deposition_id: str,
    zenoodo_interactor: ZenodoInteractor,
    metadata: Optional[MetadataType] = None,
    publish: bool = False,
    files_to_upload: Optional[list[Path]] = None,
    n_threads: int = 4,
) -> str:
    """
    Create a new version of a given record

    This starts from the ID of any deposition in the record/series.

    Parameters
    ----------
    any_deposition_id
        Any deposition ID which belongs to the series/record of interest.

        This can be obtained from the URL of any deposit in the series.
        For example, if the Zenodo URL is
        https://sandbox.zenodo.org/records/101709,
        then you can pass in "101709" as `any_deposition_id`.

    zenoodo_interactor
        Object to use to interact with Zenodo

    metadata
        Path to the file that contains the metadata to apply to the new version.

        If not supplied, the metadata from the previous version will not be updated.

        For futher information about the required form,
        see the docstring of
        [`update_metadata`][openscm_zenodo.zenodo.ZenodoInteractor.update_metadata].
        To get an example, see the docstring of
        [`retrieve_metadata`][openscm_zenodo.zenodo.retrieve_metadata].

    publish
        Should we publish the newly created version once we have uploaded the files?

    files_to_upload
        If supplied, the files to upload to the newly created version.

    n_threads
        If `files_to_upload` is supplied,
        the number of threads to use for parallel uploads.

    Returns
    -------
    :
        Deposition ID of the new version
    """
    latest_deposition_id = zenoodo_interactor.get_latest_deposition_id(
        any_deposition_id=any_deposition_id,
    )

    new_deposition_id = zenoodo_interactor.create_new_version_from_latest(
        latest_deposition_id=latest_deposition_id
    ).json()["id"]

    if metadata is not None:
        zenoodo_interactor.update_metadata(
            deposition_id=new_deposition_id,
            metadata=metadata,
        )

    if files_to_upload is not None:
        zenoodo_interactor.upload_files(
            deposition_id=new_deposition_id,
            to_upload=files_to_upload,
            n_threads=n_threads,
        )

    if publish:
        zenoodo_interactor.publish(new_deposition_id)

    return str(new_deposition_id)


def get_reserved_doi(zenodo_record_response: requests.models.Response) -> str:
    """
    Get the reserved DOI from a Zenodo record response

    We think that this works
    with basically any response related to retrieving a record from Zenodo,
    because it basically just looks at the metadata field.
    However, it may not support all responses.
    You have been warned.

    Parameters
    ----------
    zenodo_record_response
        The Zenodo response for a record, from which to get the reserved DOI.

    Returns
    -------
    :
        The record's reserved DOI
    """
    return str(zenodo_record_response.json()["metadata"]["prereserve_doi"]["doi"])
