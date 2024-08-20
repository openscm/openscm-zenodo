<!--- --8<-- [start:description] -->
# OpenSCM-Zenodo

Validation of input4MIPs data (checking file formats, metadata etc.).

**Key info :**
[![Docs](https://readthedocs.org/projects/openscm-zenodo/badge/?version=latest)](https://openscm-zenodo.readthedocs.io)
[![Main branch: supported Python versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fopenscm%2Fopenscm-zenodo%2Fmain%2Fpyproject.toml)](https://github.com/openscm/openscm-zenodo/blob/main/pyproject.toml)
[![Licence](https://img.shields.io/pypi/l/openscm-zenodo?label=licence)](https://github.com/openscm/openscm-zenodo/blob/main/LICENCE)

**PyPI :**
[![PyPI](https://img.shields.io/pypi/v/openscm-zenodo.svg)](https://pypi.org/project/openscm-zenodo/)
[![PyPI install](https://github.com/openscm/openscm-zenodo/actions/workflows/install-pypi.yaml/badge.svg?branch=main)](https://github.com/openscm/openscm-zenodo/actions/workflows/install-pypi.yaml)

<!--- TBD
**Conda :**
[![Conda](https://img.shields.io/conda/vn/conda-forge/openscm-zenodo.svg)](https://anaconda.org/conda-forge/openscm-zenodo)
[![Conda platforms](https://img.shields.io/conda/pn/conda-forge/openscm-zenodo.svg)](https://anaconda.org/conda-forge/openscm-zenodo)
[![Conda install](https://github.com/openscm/openscm-zenodo/actions/workflows/install-conda.yaml/badge.svg?branch=main)](https://github.com/openscm/openscm-zenodo/actions/workflows/install-conda.yaml)
-->

**Tests :**
[![CI](https://github.com/openscm/openscm-zenodo/actions/workflows/ci.yaml/badge.svg?branch=main)](https://github.com/openscm/openscm-zenodo/actions/workflows/ci.yaml)
[![Coverage](https://codecov.io/gh/openscm/openscm-zenodo/branch/main/graph/badge.svg)](https://codecov.io/gh/openscm/openscm-zenodo)

**Other info :**
[![Last Commit](https://img.shields.io/github/last-commit/openscm/openscm-zenodo.svg)](https://github.com/openscm/openscm-zenodo/commits/main)
[![Contributors](https://img.shields.io/github/contributors/openscm/openscm-zenodo.svg)](https://github.com/openscm/openscm-zenodo/graphs/contributors)

## Status

- development: the project is actively being worked on

<!--- --8<-- [end:description] -->

## Installation

<!--- --8<-- [start:installation] -->
### As an application

If you want to use openscm-zenodo as an application,
for example you just want to use its command-line interface,
then we recommend using the 'locked' version of the package.
This version pins the version of all dependencies too,
which reduces the chance of installation issues
because of breaking updates to dependencies.

The locked version of openscm-zenodo can be installed with

<!---
=== "mamba"
    ```sh
    mamba install -c conda-forge openscm-zenodo-locked
    ```

    [mamba](https://mamba.readthedocs.io/en/latest/)
    is our recommend way to install the package
    because it has better handling of the compiled dependencies
    (like cfunits).

=== "conda"
    ```sh
    conda install -c conda-forge openscm-zenodo-locked
    ```

    [conda](https://docs.conda.io/projects/conda/en/stable/)
    is a good way to install the package
    because it has better handling of the compiled dependencies
    (like cfunits).
-->

=== "pip"
    ```sh
    pip install openscm-zenodo[locked]
    ```

    [pip](https://pip.pypa.io/en/stable/)
    is a standard way to install Python packages.

### As a library

If you want to use openscm-zenodo as a library,
for example you want to use it
as a dependency in another package/application that you're building,
then we recommend installing the package with the commands below.
This method provides the loosest pins possible of all dependencies.
This gives you, the package/application developer,
as much freedom as possible to set the versions of different packages.
However, the tradeoff with this freedom is that you may install
incompatible versions of openscm-zenodo's dependencies
(we cannot test all combinations of dependencies,
particularly ones which haven't been released yet!).
Hence, you may run into installation issues.
If you believe these are because of a problem in openscm-zenodo,
please [raise an issue](https://github.com/openscm/openscm-zenodo/issues/new/choose).

The (non-locked) version of openscm-zenodo can be installed with

<!---
=== "mamba"
    ```sh
    mamba install -c conda-forge openscm-zenodo
    ```

    [mamba](https://mamba.readthedocs.io/en/latest/)
    is our recommend way to install the package
    because it has better handling of the compiled dependencies
    (like cfunits).

=== "conda"
    ```sh
    conda install -c conda-forge openscm-zenodo
    ```

    [conda](https://docs.conda.io/projects/conda/en/stable/)
    is a good way to install the package
    because it has better handling of the compiled dependencies
    (like cfunits).
-->

=== "pip"
    ```sh
    pip install openscm-zenodo
    ```

    [pip](https://pip.pypa.io/en/stable/)
    is a standard way to install Python packages.
    We make no guarantees that this will actually work
    because pip's handling of the compiled dependencies
    is not guaranteed.

Additional dependencies can be installed using

<!---
=== "mamba"
    If you are installing with mamba, we recommend
    installing the extras by hand because there is no stable
    solution yet (see [conda issue #7502](https://github.com/conda/conda/issues/7502))

=== "conda"
    If you are installing with conda, we recommend
    installing the extras by hand because there is no stable
    solution yet (see [conda issue #7502](https://github.com/conda/conda/issues/7502))
-->

=== "pip"
    ```sh
    # To add notebook dependencies
    pip install openscm-zenodo[notebooks]
    ```

### For developers

For development, we rely on [pdm](https://pdm-project.org/en/latest/)
for all our dependency management.
To get started, you will need to make sure that pdm is installed
([instructions here](https://pdm-project.org/en/latest/#installation),
although we found that installing with [pipx](https://pipx.pypa.io/stable/installation/)
worked perfectly for us).

For all of our work, we use our `Makefile`.
You can read the instructions out and run the commands by hand if you wish,
but we generally discourage this because it can be error prone.
In order to create your environment, run `make virtual-environment`.

If there are any issues, the messages from the `Makefile` should guide you
through. If not, please raise an issue in the
[issue tracker](https://github.com/openscm/openscm-zenodo/issues).

For the rest of our developer docs, please see [development][development-reference].

<!--- --8<-- [end:installation] -->
