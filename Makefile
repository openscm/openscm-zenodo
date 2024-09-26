# Makefile to help automate key steps

.DEFAULT_GOAL := help
# Will likely fail on Windows, but Makefiles are in general not Windows
# compatible so we're not too worried
TEMP_FILE := $(shell mktemp)

# A helper script to get short descriptions of each target in the Makefile
define PRINT_HELP_PYSCRIPT
import re, sys

for line in sys.stdin:
	match = re.match(r'^([\$$\(\)a-zA-Z_-]+):.*?## (.*)$$', line)
	if match:
		target, help = match.groups()
		print("%-30s %s" % (target, help))
endef
export PRINT_HELP_PYSCRIPT


.PHONY: help
help:  ## print short description of each target
	@python3 -c "$$PRINT_HELP_PYSCRIPT" < $(MAKEFILE_LIST)

.PHONY: checks
checks:  ## run all the linting checks of the codebase
	@echo "=== pre-commit ==="; pdm run pre-commit run --all-files || echo "--- pre-commit failed ---" >&2; \
		echo "=== mypy ==="; MYPYPATH=stubs pdm run mypy src || echo "--- mypy failed ---" >&2; \
		echo "======"

.PHONY: ruff-fixes
ruff-fixes:  ## fix the code using ruff
    # format before and after checking so that the formatted stuff is checked and
    # the fixed stuff is formatted
	pdm run ruff format src tests scripts docs
	pdm run ruff check src tests scripts docs --fix
	pdm run ruff format src tests scripts docs


.PHONY: test
test:  ## run the tests
	pdm run pytest src tests -r a -v --doctest-modules --cov=src

# Note on code coverage and testing:
# You must specify cov=src.
# Otherwise, funny things happen when doctests are involved.
# If you want to debug what is going on with coverage,
# we have found that adding COVERAGE_DEBUG=trace
# to the front of the below command
# can be very helpful as it shows you
# if coverage is tracking the coverage
# of all of the expected files or not.
# We are sure that the coverage maintainers would appreciate a PR
# that improves the coverage handling when there are doctests
# and a `src` layout like ours.

docs/cli/index.md: src/openscm_zenodo/cli/__init__.py  ## auto-generate the typer app docs
	pdm run typer openscm_zenodo.cli utils docs --output docs/cli/index.md --name openscm-zenodo

.PHONY: docs
docs: docs/cli/index.md  ## build the docs
	pdm run mkdocs build

.PHONY: docs-strict
docs-strict: docs/cli/index.md  ## build the docs strictly (e.g. raise an error on warnings, this most closely mirrors what we do in the CI)
	pdm run mkdocs build --strict

.PHONY: docs-serve
docs-serve: docs/cli/index.md  ## serve the docs locally
	pdm run mkdocs serve

.PHONY: changelog-draft
changelog-draft:  ## compile a draft of the next changelog
	pdm run towncrier build --draft --version draft

.PHONY: licence-check
licence-check:  ## Check that licences of the dependencies are suitable
	# Will likely fail on Windows, but Makefiles are in general not Windows
	# compatible so we're not too worried
	pdm export --without=tests --without=docs --without=dev > $(TEMP_FILE)
	pdm run liccheck -r $(TEMP_FILE) -R licence-check.txt
	rm -f $(TEMP_FILE)

.PHONY: virtual-environment
virtual-environment:  ## update virtual environment, create a new one if it doesn't already exist
	pdm lock --dev --group :all --strategy inherit_metadata
	pdm install --dev --group :all
	pdm run pre-commit install
