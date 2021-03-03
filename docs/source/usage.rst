Usage
=====

The typical usage workflow looks something like the following:

#. Setup your metadata in a ``.json`` file, this is your ``DEPOSIT_METADATA``
#. Create a new version for your record i.e. ``openscm-zenodo create-new-version DEPOSITION_ID DEPOSIT_METADATA``. Note the new version which is output by this program e.g. ``739845``
#. Get the bucket (in which you'll upload files) for your new version i.e. ``openscm-zenodo get-bucket VERSION``. Note the bucket which is output by this program e.g. ``e428bd2f-84e5-49ae-84ed-f42da1b8e0da``.
#. Upload your file to the bucket e.g. ``openscm-zenodo upload FILE_TO_UPLOAD BUCKET``
#. Go to zenodo and check your new record, the publishing step is performed manually via the Zenodo GUI
