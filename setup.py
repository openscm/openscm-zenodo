from setuptools import find_packages, setup
from setuptools.command.test import test as TestCommand

import versioneer

PACKAGE_NAME = "openscm-zenodo"
DESCRIPTION = "Command-line tool for uploading to zenodo"
KEYWORDS = ["zenodo", "command-line"]

AUTHORS = [
    ("Jared Lewis", "jared.lewis@climate-energy-college.org"),
    ("Zeb Nicholls", "zebedee.nicholls@climate-energy-college.org"),
]
EMAIL = "jared.lewis@climate-energy-college.org"
URL = "https://github.com/openscm/openscm-zenodo"
PROJECT_URLS = {
    "Bug Reports": "https://github.com/openscm/openscm-zenodo/issues",
    "Documentation": "https://OpenSCMZenodo.readthedocs.io/en/latest",
    "Source": "https://github.com/openscm/openscm-zenodo",
}
LICENSE = "3-Clause BSD License"
CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "License :: OSI Approved :: BSD License",
    "Intended Audience :: Developers",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3.6",
    "Programming Language :: Python :: 3.7",
    "Programming Language :: Python :: 3.8",
    "Programming Language :: Python :: 3.9",
]

ENTRY_POINTS = {"console_scripts": ["openscm-zenodo = openscm_zenodo.cli:cli"]}

REQUIREMENTS = ["click", "requests", "tqdm"]
REQUIREMENTS_OPTIONAL = []
REQUIREMENTS_TESTS = [
    "codecov",
    "pytest",
    "pytest-console-scripts",
    "pytest-cov",
] + REQUIREMENTS_OPTIONAL
REQUIREMENTS_DOCS = ["sphinx>2.1", "sphinx_click", "sphinx_rtd_theme"]
REQUIREMENTS_DEPLOY = ["twine>=1.11.0", "setuptools>=41.2", "wheel>=0.31.0"]

REQUIREMENTS_DEV = [
    *["bandit", "black==19.10b0", "flake8", "isort>=5", "pydocstyle", "pylint",],
    *REQUIREMENTS_OPTIONAL,
    *REQUIREMENTS_TESTS,
    *REQUIREMENTS_DOCS,
    *REQUIREMENTS_DEPLOY,
]

REQUIREMENTS_EXTRAS = {
    "optional": REQUIREMENTS_OPTIONAL,
    "docs": REQUIREMENTS_DOCS,
    "tests": REQUIREMENTS_TESTS,
    "deploy": REQUIREMENTS_DEPLOY,
    "dev": REQUIREMENTS_DEV,
}


SOURCE_DIR = "src"

PACKAGES = find_packages(SOURCE_DIR)  # no exclude as only searching in `src`
PACKAGE_DIR = {"": SOURCE_DIR}
PACKAGE_DATA = {}


README = "README.rst"

# Get the long description from the README file
with open(README, "r") as f:
    README_LINES = ["OpenSCM-Zenodo", "==============", ""]
    add_line = False
    for line in f:
        if line.strip() == ".. sec-begin-long-description":
            add_line = True
        elif line.strip() == ".. sec-end-long-description":
            break
        elif add_line:
            README_LINES.append(line.strip())

if len(README_LINES) < 3:
    raise RuntimeError("Insufficient description given")


class OpenSCMZenodo(TestCommand):
    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        import pytest

        pytest.main(self.test_args)


cmdclass = versioneer.get_cmdclass()
cmdclass.update({"test": OpenSCMZenodo})

setup(
    name=PACKAGE_NAME,
    version=versioneer.get_version(),
    description=DESCRIPTION,
    long_description="\n".join(README_LINES),
    long_description_content_type="text/x-rst",
    author=", ".join([author[0] for author in AUTHORS]),
    author_email=", ".join([author[1] for author in AUTHORS]),
    url=URL,
    project_urls=PROJECT_URLS,
    license=LICENSE,
    classifiers=CLASSIFIERS,
    keywords=KEYWORDS,
    packages=PACKAGES,
    package_dir=PACKAGE_DIR,
    package_data=PACKAGE_DATA,
    include_package_data=True,
    install_requires=REQUIREMENTS,
    extras_require=REQUIREMENTS_EXTRAS,
    cmdclass=cmdclass,
    entry_points=ENTRY_POINTS,
)
