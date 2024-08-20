# `openscm-zenodo`

Entrypoint for the command-line interface

**Usage**:

```console
$ openscm-zenodo [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`: Print the version number and exit
* `--no-logging`: Disable all logging.

If supplied, overrides '--logging-config'
* `--logging-level TEXT`: Logging level to use.

This is only applied if no other logging configuration flags are supplied.
* `--logging-config PATH`: Path to the logging configuration file.

This will be loaded with [loguru-config](https://github.com/erezinman/loguru-config).
If supplied, this overrides any value provided with `--log-level`.
* `--help`: Show this message and exit.

**Commands**:

* `create-new-version`: Create a new version of a recod

## `openscm-zenodo create-new-version`

Create a new version of a recod

**Usage**:

```console
$ openscm-zenodo create-new-version [OPTIONS] ANY_DEPOSITION_ID [FILES_TO_UPLOAD]...
```

**Arguments**:

* `ANY_DEPOSITION_ID`: A deposition ID related to the record you wish to interact with. You can pick any published deposit/version. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the deposit ID is 10702583.  [required]
* `[FILES_TO_UPLOAD]...`: Files to upload the the Zenodo deposition

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. For more information about generating tokens, see the 'Creating a personal access token' header of https://developers.zenodo.org/#authentication.  [env var: ZENODO_TOKEN; required]
* `--metadata-file FILE`: Path to the `.json` file containing the metadata to use for this version. The `.json` file should have a 'metadata' key, which points to a dictionary of key : value pairs.To get an example, use the `openscm-zenodo retrieve-metadata` command. For example, `openscm-zenodo retrieve-metadata 4589756`.For the complete list of supported key : value pairs, see [https://developers.zenodo.org/#representation]().
* `--publish`: Publish the newly created version after creating it and uploading the files
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--help`: Show this message and exit.
