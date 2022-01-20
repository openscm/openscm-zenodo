Usage
=====

The typical usage workflow looks something like the following:

#. Create an API token for either `zenodo.org <https://zenodo.org/account/settings/applications/tokens/new/>`_ or `sandbox.zenodo.org <https://sandbox.zenodo.org/account/settings/applications/tokens/new/>`_ with a scope of ``deposit:write``. Make sure you specify the ``ZENODO_URL`` which matches the account for which you created the token.
#. Setup the metadata for the deposition in a ``.json`` file. Note the filename, this is your ``DEPOSIT_METADATA`` below. An example of a simple metadata file is shown below, and additional information about the metadata fields can be found in the `Zenodo Developer Docs <https://developers.zenodo.org/#representation>`_.

    .. code-block:: json

        {
            "metadata": {
                "title": "title",
                "upload_type": "software",
                "description": "description here"
            }
        }

#. Create a new version for your record i.e. ``ZENODO_TOKEN=TOKEN openscm-zenodo create-new-version DEPOSITION_ID DEPOSIT_METADATA --zenodo-url ZENODO_URL``. Note the new version which is output by this program e.g. ``739845``
#. Get the bucket (in which you'll upload files) for your new version i.e. ``ZENODO_TOKEN=TOKEN openscm-zenodo get-bucket VERSION --zenodo-url ZENODO_URL``. Note the bucket which is output by this program e.g. ``e428bd2f-84e5-49ae-84ed-f42da1b8e0da``.
#. Upload your file to the bucket e.g. ``openscm-zenodo upload FILE_TO_UPLOAD BUCKET --zenodo-url ZENODO_URL`` (or specify the root directory that you want to strip from the full filepath e.g. ``openscm-zenodo upload FILE_TO_UPLOAD BUCKET --root-dir /path/to/strip --zenodo-url ZENODO_URL``)
#. Go to zenodo and check your new record, the publishing step is performed manually via the Zenodo GUI
