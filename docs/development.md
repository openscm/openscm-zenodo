[](){#development-reference}
# Development

Notes for developers. If you want to get involved, please do!
We welcome all kinds of contributions, for example:

- docs fixes/clarifications
- bug reports
- bug fixes
- feature requests
- pull requests
- tutorials

## Workflows

<!---
Note: will make more sense once we have a copier template again.
This section was auto-generated by the copier template
and the text below is just a placeholder to get you started.
The workflows section will likely need to be updated
to be project specific as the project's norms are established.
-->

We don't mind whether you use a branching or forking workflow.
However, please only push to your own branches,
pushing to other people's branches is often a recipe for disaster,
is never required in our experience
so is best avoided.

Try and keep your merge requests as small as possible
(focus on one thing if you can).
This makes life much easier for reviewers
which allows contributions to be accepted at a faster rate.

## Language

We use British English for our development.
We do this for consistency with the broader work context of our lead developers.

## Versioning

This package follows the version format
described in [PEP440](https://peps.python.org/pep-0440/)
and [Semantic Versioning](https://semver.org/)
to describe how the version should change
depending on the updates to the code base.
Our changelog entries and compiled CHANGELOG
[TODO: docs explaining our CHANGELOG approach/reference to changelog/README.md]
allow us to identify where key changes were made.

## Dependency management

We manage our dependencies using [pdm](https://pdm-project.org/en/latest/).
This provides us with all the key dependency tools we need,
most notably [pdm-build-locked](https://pdm-build-locked.readthedocs.io/en/stable/).

[](){releasing-reference}
## Releasing

Releasing is semi-automated via a CI job.
The CI job requires the type of version bump
that will be performed to be manually specified.
See the pdm-bump docs for the
[list of available bump rules](https://github.com/carstencodes/pdm-bump#usage).

### Standard process

The steps required are the following:


1. Bump the version: manually trigger the "bump" workflow from the main branch
   (see here: [bump workflow](https://github.com/openscm/openscm-zenodo/actions/workflows/bump.yaml)).
   A valid "bump_rule" (see [pdm-bump's docs](https://github.com/carstencodes/pdm-bump#usage)) will need to be specified.
   This will then trigger a draft release.

1. Edit the draft release which has been created
   (see here:
   [project releases](https://github.com/openscm/openscm-zenodo/releases)).
   Once you are happy with the release (removed placeholders, added key
   announcements etc.) then hit 'Publish release'. This triggers a release to
   PyPI (which you can then add to the release if you want).


1. That's it, release done, make noise on social media of choice, do whatever
   else

1. Enjoy the newly available version

## Read the Docs

Our documentation is hosted by
[Read the Docs (RtD)](https://www.readthedocs.org/), a service for which we are
very grateful. The RtD configuration can be found in the `.readthedocs.yaml`
file in the root of this repository. The docs are automatically
deployed at
[openscm-zenodo.readthedocs.io](https://openscm-zenodo.readthedocs.io/en/latest/).