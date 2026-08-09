# `openscm-zenodo`

Entrypoint for the command-line interface

**Usage**:

```console
$ openscm-zenodo [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`: Print the version number and exit
* `-v, --verbose`: Show more about what is going on. Repeat for more still.

Nothing shows the narrative of what happened, `-v` adds the requests and
decisions behind it, and `-vv` adds everything.  [default: 0]
* `-q, --quiet`: Show less. Repeat for less still.

`-q` leaves warnings and errors, `-qq` only errors.
Use `--no-logging` for silence.  [default: 0]
* `--no-logging`: Disable all logging.

If supplied, overrides `--logging-config`
* `--logging-level TEXT`: Logging level to use.

For when you want to name a level rather than count `-v`s.
This is only applied if no other logging configuration flags are supplied.
* `--logging-config PATH`: Path to the logging configuration file.

This will be loaded with (https://github.com/erezinman/loguru-config).
If supplied, this overrides any value provided with `--log-level`.
* `--env-file FILE`: Path to a `.env` file from which to load environment variables.

If not supplied, we look for a `.env` file
in the current working directory and its parents.
Variables which are already set in the environment are not overridden.
* `--help`: Show this message and exit.

**Commands**:

* `upload-files`: Upload files to a record&#x27;s draft
* `download-files`: Download a record&#x27;s files
* `retrieve-citation`: Retrieve a record&#x27;s citation

## `openscm-zenodo upload-files`

Upload files to a record&#x27;s draft

Files which are already on the draft with the same contents are left alone,
so re-running this after a failure part way through only sends what is
still missing.

**Usage**:

```console
$ openscm-zenodo upload-files [OPTIONS] RECORD_ID FILES_TO_UPLOAD...
```

**Arguments**:

* `RECORD_ID`: The ID of the record you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the record ID is 10702583.  [required]
* `FILES_TO_UPLOAD...`: Files to upload. Zenodo has no directories, so each file lands under its own name and any local directories above it are lost, see `--zip`.  [required]

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. If not supplied, we use, in order of preference: the `ZENODO_SANDBOX_TOKEN` environment variable (only when using the sandbox domain), then the `ZENODO_TOKEN` environment variable, then any value found in a `.env` file (see `--env-file`). For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--n-threads INTEGER`: Number of files to transfer at once  [default: 4]
* `--mirror`: Make the record contain exactly the files given.

**This deletes files on the Zenodo record.**
Anything on the record which is not in the files given is removed.
Without this, files are only added and updated, never deleted.
* `--zip NAME`: Upload the files as a single archive under this name.

Zenodo has no directories, so this is the way to keep the structure of what
you upload. Cannot be combined with `--mirror`.
* `--zip-base-dir DIRECTORY`: Directory the paths inside the archive are relative to.

Only applies with `--zip`.
If not supplied, we use the deepest directory which contains every file.
* `--warn-path-stripped / --no-warn-path-stripped`: Warn when a file&#x27;s local directories are about to be lost. Nothing is stripped with `--zip`, so this does not apply there.  [default: warn-path-stripped]
* `--progress / --no-progress`: Show a progress bar per file.

Progress bars turn themselves off when stderr is not a terminal,
so this is only needed to silence them in a terminal.  [default: progress]
* `--help`: Show this message and exit.

## `openscm-zenodo download-files`

Download a record&#x27;s files

This works for a published record and for an unpublished draft; the record
ID says which, so there is nothing to declare. Restricted and embargoed
records need no special handling either: they need a token whose owner has
access, which is the same `--token` as everything else.

Files which are already where they are going, with the same contents, are
not downloaded again.

**Usage**:

```console
$ openscm-zenodo download-files [OPTIONS] RECORD_ID [FILENAMES]...
```

**Arguments**:

* `RECORD_ID`: The ID of the record you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the record ID is 10702583.  [required]
* `[FILENAMES]...`: Names of the files to download, as they appear on Zenodo. If none are given, every file on the record is downloaded.

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. If not supplied, we use, in order of preference: the `ZENODO_SANDBOX_TOKEN` environment variable (only when using the sandbox domain), then the `ZENODO_TOKEN` environment variable, then any value found in a `.env` file (see `--env-file`). For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--dest-dir DIRECTORY`: Directory to write the files into, created if it is not there  [default: .]
* `--n-threads INTEGER`: Number of files to transfer at once  [default: 4]
* `--overwrite`: Replace existing local files whose contents differ
* `--verify-checksum / --no-verify-checksum`: Check that we received what Zenodo says it sent. The bytes are hashed as they arrive, so this is nearly free.  [default: verify-checksum]
* `--progress / --no-progress`: Show a progress bar per file.

Progress bars turn themselves off when stderr is not a terminal,
so this is only needed to silence them in a terminal.  [default: progress]
* `--help`: Show this message and exit.

## `openscm-zenodo retrieve-citation`

Retrieve a record&#x27;s citation

With no options, this prints the record&#x27;s BibTeX entry.

**Usage**:

```console
$ openscm-zenodo retrieve-citation [OPTIONS] RECORD_ID
```

**Arguments**:

* `RECORD_ID`: The ID of the record you wish to interact with. This ID is most easily extracted from the URL provided by Zenodo. It is just the digits at the end of that link. For example, if Zenodo URL is https://zenodo.org/records/10702583, then the record ID is 10702583.  [required]

**Options**:

* `--token TEXT`: Zenodo token to use for this interaction. If not supplied, we use, in order of preference: the `ZENODO_SANDBOX_TOKEN` environment variable (only when using the sandbox domain), then the `ZENODO_TOKEN` environment variable, then any value found in a `.env` file (see `--env-file`). For more information about generating tokens, see the &#x27;Creating a personal access token&#x27; header of https://developers.zenodo.org/#authentication.
* `--zenodo-domain [https://zenodo.org|https://sandbox.zenodo.org]`: The zenodo domain with which you want to interact.  [default: https://zenodo.org]
* `--format [bibtex|csl|datacite-json|datacite-xml|dublin-core|json-ld|marcxml|dcat|citation]`: Format to get the citation in.  [default: bibtex]
* `--style TEXT`: Citation style to render in. This only applies to `--format citation`, and is ignored, with a warning, for any other format. Zenodo wants full CSL style IDs, e.g. `chicago-author-date`.  [default: apa]
* `--locale TEXT`: Locale to render in. As with `--style`, this only applies to `--format citation`.  [default: en-US]
* `--output FILE`: File to write the citation to. If not supplied, we use stdout.
* `--help`: Show this message and exit.
