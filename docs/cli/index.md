# `openscm-zenodo`

Entrypoint for the command-line interface

**Usage**:

```console
$ openscm-zenodo [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`: Print the version number and exit
* `--no-logging`: Disable all logging.

If supplied, overrides `--logging-config`
* `--logging-level TEXT`: Logging level to use.

This is only applied if no other logging configuration flags are supplied.
* `--logging-config PATH`: Path to the logging configuration file.

This will be loaded with (https://github.com/erezinman/loguru-config).
If supplied, this overrides any value provided with `--log-level`.
* `--help`: Show this message and exit.

**Commands**:

* `retrieve-metadata`: Retrieve metadata
* `retrieve-bibtex`: Retrieve bibtex entry
* `update-metadata`: Update metadata
* `upload-files`: Upload files to a Zenodo deposition
* `remove-files`: Remove files from a Zenodo deposition
* `create-new-version`: Create a new version of a record

## `openscm-zenodo retrieve-metadata`

Retrieve metadata

**Usage**:

```console
$ openscm-zenodo retrieve-metadata [OPTIONS] DEPOSITION_ID
```

**Arguments**:

* `DEPOSITION_ID`: The ID of the deposition you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposition ID is 10702583.  [required]

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN]
* `--user-controlled-only / --no-user-controlled-only`: Only return metadata keys that the user can control.

If this is `True`, the metadata keys controlled by Zenodo (e.g. the DOI)
are removed from the returned metadata.
This flag is important to use
if you want to use the retrieved metadata
as the starting point for the next version of a deposit.  [default: no-user-controlled-only]
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--help`: Show this message and exit.

## `openscm-zenodo retrieve-bibtex`

Retrieve bibtex entry

**Usage**:

```console
$ openscm-zenodo retrieve-bibtex [OPTIONS] DEPOSITION_ID
```

**Arguments**:

* `DEPOSITION_ID`: The ID of the deposition you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposition ID is 10702583.  [required]

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN]
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--help`: Show this message and exit.

## `openscm-zenodo update-metadata`

Update metadata

If the `--reserve-doi` flag is used,
this prints the reserved DOI to stdout.

**Usage**:

```console
$ openscm-zenodo update-metadata [OPTIONS] DEPOSITION_ID
```

**Arguments**:

* `DEPOSITION_ID`: The ID of the deposition you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposition ID is 10702583.  [required]

**Options**:

* `--metadata-file FILE`: Path to the `.json` file containing the metadata to use for this version. The `.json` file should have a single &#x27;metadata&#x27; key, which points to a dictionary of key : value pairs.For futher information about the required form, see the docstring of [`update_metadata`]. To get an example, see the docstring of [`retrieve_metadata`].  [required]
* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN; required]
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--reserve-doi`: Reserve a DOI while updating the metadata. This will overwrite any value in the metadata file supplied.
* `--help`: Show this message and exit.

## `openscm-zenodo upload-files`

Upload files to a Zenodo deposition

**Usage**:

```console
$ openscm-zenodo upload-files [OPTIONS] DEPOSITION_ID FILES_TO_UPLOAD...
```

**Arguments**:

* `DEPOSITION_ID`: The ID of the deposition you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposition ID is 10702583.  [required]
* `FILES_TO_UPLOAD...`: Files to upload to the Zenodo deposition  [required]

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN; required]
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--n-threads INTEGER`: Number of threads to use for parallel processing  [default: 4]
* `--help`: Show this message and exit.

## `openscm-zenodo remove-files`

Remove files from a Zenodo deposition

**Usage**:

```console
$ openscm-zenodo remove-files [OPTIONS] DEPOSITION_ID [FILES_TO_REMOVE]...
```

**Arguments**:

* `DEPOSITION_ID`: The ID of the deposition you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposition ID is 10702583.  [required]
* `[FILES_TO_REMOVE]...`: Files to remove from the Zenodo deposition

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN; required]
* `--all`: Remove all files
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--help`: Show this message and exit.

## `openscm-zenodo create-new-version`

Create a new version of a record

**Usage**:

```console
$ openscm-zenodo create-new-version [OPTIONS] ANY_DEPOSITION_ID [FILES_TO_UPLOAD]...
```

**Arguments**:

* `ANY_DEPOSITION_ID`: A deposition ID related to the record you wish to interact with. You can pick any published deposit/version. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposit ID is 10702583.  [required]
* `[FILES_TO_UPLOAD]...`: Files to upload to the Zenodo deposition

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN; required]
* `--metadata-file FILE`: Path to the `.json` file containing the metadata to use for this version. The `.json` file should have a single &#x27;metadata&#x27; key, which points to a dictionary of key : value pairs.For futher information about the required form, see the docstring of [`update_metadata`]. To get an example, see the docstring of [`retrieve_metadata`].
* `--publish`: Publish the newly created version after creating it and uploading the files
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--n-threads INTEGER`: Number of threads to use for parallel processing  [default: 4]
* `--help`: Show this message and exit.
