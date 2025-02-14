# Changelog

Versions follow [Semantic Versioning](https://semver.org/) (`<major>.<minor>.<patch>`).

Backward incompatible (breaking) changes will only be introduced in major versions
with advance notice in the **Deprecations** section of releases.

<!--
You should *NOT* be adding new changelog entries to this file,
this file is managed by towncrier.
See `changelog/README.md`.

You *may* edit previous changelogs to fix problems like typo corrections or such.
To add a new changelog entry, please see
`changelog/README.md`
and https://pip.pypa.io/en/latest/development/contributing/#news-entries,
noting that we use the `changelog` directory instead of news,
markdown instead of restructured text and use slightly different categories
from the examples given in that link.
-->

<!-- towncrier release notes start -->

## OpenSCM Zenodo v0.4.0 (2025-02-14)

### 🆕 Features

- Added [`ZenodoInteractor.get_draft_deposition_id`][openscm_zenodo.ZenodoInteractor.get_draft_deposition_id].
  This makes it possible to get the draft deposition ID, even if you're already created the draft.
  It is the recommended way of getting a draft deposition ID,
  superseding [`ZenodoInteractor.create_new_version_from_latest`][openscm_zenodo.ZenodoInteractor.create_new_version_from_latest].
  ([`ZenodoInteractor.create_new_version_from_latest`][openscm_zenodo.ZenodoInteractor.create_new_version_from_latest]
  is still available, it just fails if you have already created a draft.) ([#15](https://github.com/openscm/OpenSCM-zenodo/pull/15))


## OpenSCM Zenodo v0.3.1 (2024-09-26)

### 🆕 Features

- Added the `--user-controlled-only` flag to `openscm-zenodo retrieve-metadata`.
  This is the flag to use if you want to use the retrieved metadata as the starting point for the next version of a deposit. ([#13](https://github.com/openscm/OpenSCM-zenodo/pull/13))

### 🔧 Trivial/Internal Changes

- [#14](https://github.com/openscm/OpenSCM-zenodo/pull/14)


## OpenSCM Zenodo v0.3.0 (2024-08-25)


### 🆕 Features

- Added bibtex metadata retrieval.
  Specifically: `openscm-zenodo retrieve-bibtex`,
  [`retrieve_bibtex_entry`][openscm_zenodo.zenodo.retrieve_bibtex_entry]
  and [`get_bibtex_entry`][openscm_zenodo.zenodo.ZenodoInteractor.get_bibtex_entry]. ([#12](https://github.com/openscm/openscm-zenodo/pull/12))

### 🎉 Improvements

- Added support for parallel uploads to `openscm-zenodo create-new-version`. ([#12](https://github.com/openscm/openscm-zenodo/pull/12))


## OpenSCM Zenodo v0.2.6 (2024-08-22)


### 🔧 Trivial/Internal Changes

- [#11](https://github.com/openscm/openscm-zenodo/pull/11)


## OpenSCM Zenodo v0.2.4 (2024-08-22)


### 🆕 Features

- Added the `--reserve-doi` flag to the `openscm-zenodo update-metadata` command.
  Also added [`get_reserved_doi`][openscm_zenodo.zenodo.get_reserved_doi]. ([#10](https://github.com/openscm/openscm-zenodo/pull/10))

### 🔧 Trivial/Internal Changes

- [#10](https://github.com/openscm/openscm-zenodo/pull/10)


## OpenSCM Zenodo v0.2.3 (2024-08-21)


### 🔧 Trivial/Internal Changes

- [#9](https://github.com/openscm/openscm-zenodo/pull/9)


## OpenSCM Zenodo v0.2.2 (2024-08-21)


### 🔧 Trivial/Internal Changes

- [#8](https://github.com/openscm/openscm-zenodo/pull/8)


## OpenSCM Zenodo v0.2.1 (2024-08-21)


No significant changes.


## OpenSCM Zenodo v0.2.0 (2024-08-21)


No significant changes.
