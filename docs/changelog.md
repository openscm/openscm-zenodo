# Changelog

Versions follow [Semantic Versioning](https://semver.org/) (`<major>.<minor>.<patch>`).

Backward incompatible (breaking) changes will only be introduced in major versions
with advance notice in the **Deprecations** section of releases.


<!--
You should *NOT* be adding new changelog entries to this file, this
file is managed by towncrier. See changelog/README.md.

You *may* edit previous changelogs to fix problems like typo corrections or such.
To add a new changelog entry, please see
https://pip.pypa.io/en/latest/development/contributing/#news-entries,
noting that we use the `changelog` directory instead of news, md instead
of rst and use slightly different categories.
-->

<!-- towncrier release notes start -->

## OpenSCM-Zenodo v0.3.0 (2024-08-25)


### 🆕 Features

- Added bibtex metadata retrieval.
  Specifically: `openscm-zenodo retrieve-bibtex`,
  [`retrieve_bibtex_entry`][openscm_zenodo.zenodo.retrieve_bibtex_entry]
  and [`get_bibtex_entry`][openscm_zenodo.zenodo.ZenodoInteractor.get_bibtex_entry]. ([#12](https://github.com/climate-resource/input4mips_validation/pulls/12))

### 🎉 Improvements

- Added support for parallel uploads to `openscm-zenodo create-new-version`. ([#12](https://github.com/climate-resource/input4mips_validation/pulls/12))


## OpenSCM-Zenodo v0.2.6 (2024-08-22)


### 🔧 Trivial/Internal Changes

- [#11](https://github.com/climate-resource/input4mips_validation/pulls/11)


## OpenSCM-Zenodo v0.2.4 (2024-08-22)


### 🆕 Features

- Added the `--reserve-doi` flag to the `openscm-zenodo update-metadata` command.
  Also added [`get_reserved_doi`][openscm_zenodo.zenodo.get_reserved_doi]. ([#10](https://github.com/climate-resource/input4mips_validation/pulls/10))

### 🔧 Trivial/Internal Changes

- [#10](https://github.com/climate-resource/input4mips_validation/pulls/10)


## OpenSCM-Zenodo v0.2.3 (2024-08-21)


### 🔧 Trivial/Internal Changes

- [#9](https://github.com/climate-resource/input4mips_validation/pulls/9)


## OpenSCM-Zenodo v0.2.2 (2024-08-21)


### 🔧 Trivial/Internal Changes

- [#8](https://github.com/climate-resource/input4mips_validation/pulls/8)


## OpenSCM-Zenodo v0.2.1 (2024-08-21)


No significant changes.


## OpenSCM-Zenodo v0.2.0 (2024-08-21)


No significant changes.
