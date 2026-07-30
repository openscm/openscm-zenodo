"""
Tests of the metadata models
"""

from __future__ import annotations

import json

import pytest

from openscm_zenodo.exceptions import MetadataValidationError
from openscm_zenodo.metadata import (
    KNOWN_IDENTIFIER_SCHEMES,
    KNOWN_RELATION_TYPES,
    KNOWN_RESOURCE_TYPES,
    Affiliation,
    Award,
    Contributor,
    Creator,
    Date,
    Funder,
    Funding,
    Identifier,
    Metadata,
    Organisation,
    Person,
    RelatedIdentifier,
    Right,
    Subject,
    describe_unknown_vocabulary_value,
    get_metadata_modelled_keys,
    is_edtf_date,
)

ZENODO_METADATA = {
    "title": "A record which is not real",
    "resource_type": {"id": "dataset", "title": {"en": "Dataset", "de": "Datensatz"}},
    "creators": [
        {
            "person_or_org": {
                "type": "personal",
                "name": "Zebedee Nicholls",
                "family_name": "Nicholls",
                "given_name": "Zebedee",
                "identifiers": [
                    {"scheme": "orcid", "identifier": "0000-0002-4767-2723"}
                ],
            },
            "affiliations": [{"name": "Climate Resource"}],
        },
        {"person_or_org": {"type": "organizational", "name": "Climate Resource"}},
    ],
    "publication_date": "2026-07-28",
    "publisher": "Zenodo",
    "description": "Something to describe it",
    "version": "v1.0.0",
    "rights": [
        {
            "id": "cc-by-4.0",
            "title": {"en": "Creative Commons Attribution 4.0 International"},
            "icon": "cc-by-icon",
            "props": {"scheme": "spdx"},
        }
    ],
    "subjects": [{"subject": "climate"}],
    "languages": [{"id": "eng", "title": {"en": "English"}}],
    "contributors": [
        {
            "person_or_org": {"type": "organizational", "name": "A contributor"},
            "role": {"id": "datacurator", "title": {"en": "Data curator"}},
        }
    ],
    "dates": [
        {
            "date": "2020-01/2020-12",
            "type": {"id": "collected", "title": {"en": "Collected"}},
            "description": "When the data was collected",
        }
    ],
    "related_identifiers": [
        {
            "identifier": "10.5194/gmd-13-5175-2020",
            "scheme": "doi",
            "relation_type": {"id": "issupplementto"},
            "resource_type": {"id": "publication-article"},
        }
    ],
    "funding": [
        {
            "funder": {"id": "00k4n6c32", "name": "European Commission"},
            "award": {"id": "00k4n6c32::101003536"},
        }
    ],
    "additional_titles": [{"title": "Another title"}],
}
"""Metadata shaped the way Zenodo sends it, expansions and all"""


def test_from_json_pulls_out_what_we_model():
    metadata = Metadata.from_json(ZENODO_METADATA)

    assert metadata.title == "A record which is not real"
    assert metadata.resource_type == "dataset"
    assert metadata.publication_date == "2026-07-28"
    assert metadata.publisher == "Zenodo"
    assert metadata.version == "v1.0.0"
    assert metadata.rights == (
        Right(id="cc-by-4.0", title="Creative Commons Attribution 4.0 International"),
    )


def test_from_json_parses_creators():
    person, organisation = Metadata.from_json(ZENODO_METADATA).creators

    assert person.entity == Person(
        family_name="Nicholls",
        given_name="Zebedee",
        identifiers=(Identifier(scheme="orcid", identifier="0000-0002-4767-2723"),),
    )
    assert person.name == "Nicholls, Zebedee"
    assert person.affiliations == (Affiliation(name="Climate Resource"),)

    assert organisation.entity == Organisation(name="Climate Resource")
    assert organisation.name == "Climate Resource"


def test_what_we_do_not_model_is_kept():
    """
    Anything we do not model goes in `raw` and comes back out again

    This is what makes it safe to read metadata off one record
    and apply it to another: nothing is quietly dropped on the way through.
    """
    metadata = Metadata.from_json(ZENODO_METADATA)

    # `subjects` and `languages` are modelled now, so `raw` only holds
    # what we genuinely do not model
    assert metadata.raw == {"additional_titles": [{"title": "Another title"}]}
    assert metadata.to_json()["additional_titles"] == [{"title": "Another title"}]


def test_to_json_drops_zenodo_display_text():
    """
    The text Zenodo adds to vocabulary entries is Zenodo's to fill in

    Sending it back would only be a way to disagree with it.
    """
    as_json = Metadata.from_json(ZENODO_METADATA).to_json()

    assert as_json["resource_type"] == {"id": "dataset"}
    assert as_json["rights"] == [{"id": "cc-by-4.0"}]


def test_to_json_is_stable():
    """
    Reading, writing and reading again gives the same thing
    """
    as_json = Metadata.from_json(ZENODO_METADATA).to_json()

    assert Metadata.from_json(as_json).to_json() == as_json


def test_to_json_leaves_out_what_we_do_not_have():
    """
    Fields we do not have are left out, rather than sent as `null`

    Zenodo treats an explicit `null` as a value, so this matters.
    """
    assert Metadata(title="Just a title").to_json() == {"title": "Just a title"}


def test_creator_person():
    creator = Creator.person(
        "Nicholls",
        "Zebedee",
        orcid="0000-0002-4767-2723",
        affiliations=["Climate Resource"],
    )

    assert creator.to_json() == {
        "person_or_org": {
            "type": "personal",
            "family_name": "Nicholls",
            "given_name": "Zebedee",
            "identifiers": [{"scheme": "orcid", "identifier": "0000-0002-4767-2723"}],
        },
        "affiliations": [{"name": "Climate Resource"}],
    }


def test_creator_organisation():
    creator = Creator.organisation("Climate Resource", ror="04ttjf776")

    assert creator.to_json() == {
        "person_or_org": {
            "type": "organizational",
            "name": "Climate Resource",
            "identifiers": [{"scheme": "ror", "identifier": "04ttjf776"}],
        }
    }


def test_contributor_role_is_flattened():
    """
    A contributor's role is a vocabulary ID, which we hold as the ID itself
    """
    contributor = Contributor.from_json(
        {
            "person_or_org": {"type": "organizational", "name": "Climate Resource"},
            "role": {"id": "datacurator", "title": {"en": "Data curator"}},
        }
    )

    assert contributor.role == "datacurator"
    assert contributor.to_json()["role"] == {"id": "datacurator"}


def test_contributor_without_a_role_is_refused():
    """
    Zenodo requires a role on a contributor, so the model does too
    """
    with pytest.raises(MetadataValidationError, match="needs a `role`"):
        Contributor.from_json(
            {"person_or_org": {"type": "organizational", "name": "Climate Resource"}}
        )


def test_creator_and_contributor_are_different_things():
    """
    A creator has no role, and Zenodo rejects one which does

    This is the reason they are two classes rather than one with an optional
    role: the difference is a rule, not a convention.
    """
    assert "role" not in Creator.organisation("Climate Resource").to_json()
    assert Contributor.organisation("Climate Resource", role="other").role == "other"


def test_person_needs_a_name():
    with pytest.raises(MetadataValidationError, match="needs a `family_name`"):
        Person.from_json({"type": "personal", "given_name": "Zebedee"})


def test_person_falls_back_to_the_computed_name():
    """
    Some older records only carry the computed `name`, and are still readable
    """
    person = Person.from_json({"type": "personal", "name": "Zebedee Nicholls"})

    assert person.family_name == "Zebedee Nicholls"


def test_organisation_needs_a_name():
    with pytest.raises(MetadataValidationError, match="needs a `name`"):
        Organisation.from_json({"type": "organizational"})


def test_affiliation_with_an_id_sends_only_the_id():
    """
    Zenodo derives the name from the ID and rejects one which does not match
    """
    assert Affiliation(name="Whatever", id="04ttjf776").to_json() == {"id": "04ttjf776"}


def test_person_name_from_parts():
    assert Person(family_name="Nicholls", given_name="Zebedee").name == (
        "Nicholls, Zebedee"
    )
    assert Person(family_name="Nicholls").name == "Nicholls"


def test_right_without_an_id():
    """
    A licence which is not in Zenodo's vocabulary keeps its own title and link
    """
    right = Right(title="A bespoke licence", link="https://example.com/licence")

    assert right.to_json() == {
        "title": {"en": "A bespoke licence"},
        "link": "https://example.com/licence",
    }


def test_find_problems_of_complete_metadata():
    assert Metadata.from_json(ZENODO_METADATA).find_problems() == ()


@pytest.mark.parametrize(
    "dropped, expected",
    (
        pytest.param("title", "`title`", id="title"),
        pytest.param("creators", "No creators", id="creators"),
        pytest.param("resource_type", "`resource_type`", id="resource_type"),
        pytest.param("publication_date", "`publication_date`", id="publication_date"),
        pytest.param("publisher", "`publisher`", id="publisher"),
    ),
)
def test_find_problems_reports_what_is_missing(dropped, expected):
    metadata = Metadata.from_json(
        {k: v for k, v in ZENODO_METADATA.items() if k != dropped}
    )

    problems = metadata.find_problems()

    assert len(problems) == 1
    assert expected in problems[0]


def test_find_problems_reports_every_problem_at_once():
    """
    One round trip per problem is not a way to find out what is wrong
    """
    assert len(Metadata().find_problems()) > 1


def test_find_problems_publication_date_format():
    metadata = Metadata.from_json({**ZENODO_METADATA, "publication_date": "28/07/2026"})

    (problem,) = metadata.find_problems()

    assert "YYYY-MM-DD" in problem


def test_creator_without_a_name_never_gets_as_far_as_find_problems():
    """
    Splitting Person and Organisation moves this from a check to an impossibility
    """
    with pytest.raises(MetadataValidationError):
        Metadata.from_json(
            {**ZENODO_METADATA, "creators": [{"person_or_org": {"type": "personal"}}]}
        )


def test_validate_raises_with_every_problem():
    with pytest.raises(MetadataValidationError) as exc_info:
        Metadata().validate()

    assert exc_info.value.problems == Metadata().find_problems()
    for problem in exc_info.value.problems:
        assert problem in str(exc_info.value)


def test_validate_of_complete_metadata_is_quiet():
    assert Metadata.from_json(ZENODO_METADATA).validate() is None


@pytest.mark.parametrize(
    "key, value, expected",
    (
        pytest.param("upload_type", "dataset", "resource_type", id="upload_type"),
        pytest.param("access_right", "open", "access", id="access_right"),
        pytest.param("license", "cc-by-4.0", "rights", id="license"),
        pytest.param("keywords", ["climate"], "subjects", id="keywords"),
        pytest.param("prereserve_doi", True, "reserve_doi", id="prereserve_doi"),
        pytest.param("doi", "10.5281/zenodo.1", "pids", id="doi"),
    ),
)
def test_legacy_metadata_is_refused_with_the_translation(key, value, expected):
    """
    Metadata written for the legacy API says so, and says what to write instead

    Left to Zenodo, the failure is about the fields we did *not* send,
    which is a long way from the mistake which was made.
    """
    with pytest.raises(MetadataValidationError) as exc_info:
        Metadata.from_json({"title": "A title", key: value})

    (problem,) = exc_info.value.problems
    assert key in problem
    assert expected in problem


def test_legacy_creators_are_refused():
    with pytest.raises(MetadataValidationError, match="person_or_org"):
        Metadata.from_json(
            {
                "title": "A title",
                "creators": [{"name": "Nicholls, Zebedee", "affiliation": "Somewhere"}],
            }
        )


def test_find_unknown_vocabulary_values_of_a_known_record():
    assert Metadata.from_json(ZENODO_METADATA).find_unknown_vocabulary_values() == ()
    # Nothing set at all is not something to warn about,
    # it is something for `find_problems` to complain about
    assert Metadata().find_unknown_vocabulary_values() == ()


@pytest.mark.parametrize(
    "overrides, expected",
    (
        pytest.param(
            {"resource_type": {"id": "pottery"}},
            "`resource_type` is 'pottery'",
            id="resource-type",
        ),
        pytest.param(
            {
                "creators": [
                    {
                        "person_or_org": {
                            "type": "personal",
                            "family_name": "Nicholls",
                            "identifiers": [{"scheme": "orchid", "identifier": "0000"}],
                        }
                    }
                ]
            },
            "`creators[0].identifiers[0].scheme` is 'orchid'",
            id="creator-identifier-scheme",
        ),
        pytest.param(
            {
                "related_identifiers": [
                    {
                        "identifier": "10.1234/5678",
                        "scheme": "doi",
                        # The DataCite spelling, which Zenodo does not take
                        "relation_type": {"id": "isSupplementTo"},
                    }
                ]
            },
            "`related_identifiers[0].relation_type` is 'isSupplementTo'",
            id="relation-type",
        ),
        pytest.param(
            {
                "related_identifiers": [
                    {
                        "identifier": "10.1234/5678",
                        "scheme": "dooi",
                        "relation_type": {"id": "cites"},
                    }
                ]
            },
            "`related_identifiers[0].scheme` is 'dooi'",
            id="related-identifier-scheme",
        ),
        pytest.param(
            {
                "related_identifiers": [
                    {
                        "identifier": "10.1234/5678",
                        "scheme": "doi",
                        "relation_type": {"id": "cites"},
                        "resource_type": {"id": "pamphlet"},
                    }
                ]
            },
            "`related_identifiers[0].resource_type` is 'pamphlet'",
            id="related-identifier-resource-type",
        ),
    ),
)
def test_find_unknown_vocabulary_values(overrides, expected):
    """
    A value we do not know of is named, along with where it is

    These are warnings rather than errors: Zenodo's vocabularies are longer than
    ours and they change, so an unknown value is as likely to mean our list is
    stale as that the metadata is wrong.
    """
    metadata = Metadata.from_json({**ZENODO_METADATA, **overrides})

    (unknown,) = metadata.find_unknown_vocabulary_values()

    assert expected in unknown


def test_from_file_wrapped(tmp_path):
    """
    A file shaped like a record, i.e. what the legacy `--metadata-file` took
    """
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"metadata": ZENODO_METADATA}))

    assert Metadata.from_file(path) == Metadata.from_json(ZENODO_METADATA)


def test_from_file_bare(tmp_path):
    """
    A file holding the metadata itself, i.e. what `get_metadata` gives you
    """
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(ZENODO_METADATA))

    assert Metadata.from_file(path) == Metadata.from_json(ZENODO_METADATA)


def test_from_file_legacy(tmp_path):
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"metadata": {"upload_type": "dataset"}}))

    with pytest.raises(MetadataValidationError, match="upload_type"):
        Metadata.from_file(path)


def test_modelled_keys_come_from_the_class():
    """
    A new field cannot be added without `raw` and `to_json` learning about it
    """
    modelled = get_metadata_modelled_keys()

    assert "creators" in modelled
    assert "funding" in modelled
    assert "raw" not in modelled


def test_to_json_refuses_a_raw_which_clashes():
    """
    Two sources of truth for one field, with `raw` silently winning, is a trap
    """
    metadata = Metadata(title="A title", raw={"title": "A different title"})

    with pytest.raises(MetadataValidationError, match=r"modelled as `Metadata\.title`"):
        metadata.to_json()


def test_from_json_parses_the_rest_of_the_schema():
    metadata = Metadata.from_json(ZENODO_METADATA)

    assert metadata.subjects == (Subject(subject="climate"),)
    assert metadata.languages == ("eng",)
    assert metadata.contributors == (
        Contributor(entity=Organisation(name="A contributor"), role="datacurator"),
    )
    assert metadata.dates == (
        Date(
            date="2020-01/2020-12",
            type="collected",
            description="When the data was collected",
        ),
    )
    assert metadata.related_identifiers == (
        RelatedIdentifier(
            identifier="10.5194/gmd-13-5175-2020",
            scheme="doi",
            relation_type="issupplementto",
            resource_type="publication-article",
        ),
    )
    assert metadata.funding == (
        Funding(
            funder=Funder(id="00k4n6c32", name="European Commission"),
            award=Award(id="00k4n6c32::101003536"),
        ),
    )


def test_to_json_of_the_rest_of_the_schema():
    as_json = Metadata.from_json(ZENODO_METADATA).to_json()

    assert as_json["subjects"] == [{"subject": "climate"}]
    assert as_json["languages"] == [{"id": "eng"}]
    assert as_json["contributors"] == [
        {
            "person_or_org": {"type": "organizational", "name": "A contributor"},
            "role": {"id": "datacurator"},
        }
    ]
    assert as_json["dates"] == [
        {
            "date": "2020-01/2020-12",
            "type": {"id": "collected"},
            "description": "When the data was collected",
        }
    ]
    assert as_json["related_identifiers"] == [
        {
            "identifier": "10.5194/gmd-13-5175-2020",
            "scheme": "doi",
            "relation_type": {"id": "issupplementto"},
            "resource_type": {"id": "publication-article"},
        }
    ]
    assert as_json["funding"] == [
        {"funder": {"id": "00k4n6c32"}, "award": {"id": "00k4n6c32::101003536"}}
    ]


@pytest.mark.parametrize(
    "cls, kwargs",
    (
        pytest.param(Affiliation, {}, id="affiliation"),
        pytest.param(Right, {}, id="right"),
        pytest.param(Subject, {}, id="subject"),
        pytest.param(Funder, {}, id="funder"),
        pytest.param(Funding, {}, id="funding"),
    ),
)
def test_entries_which_say_nothing_are_refused(cls, kwargs):
    """
    Zenodo takes one of two forms for these, and an empty one is neither
    """
    with pytest.raises(MetadataValidationError):
        cls(**kwargs)


def test_award_needs_an_id_or_a_title_and_number():
    with pytest.raises(MetadataValidationError, match="both a `title` and a `number`"):
        Award(title="A grant")

    assert Award(title="A grant", number="12345").to_json() == {
        "title": {"en": "A grant"},
        "number": "12345",
    }


@pytest.mark.parametrize(
    "value, expected",
    (
        pytest.param("2021", True, id="year"),
        pytest.param("2021-03", True, id="year-month"),
        pytest.param("2021-03-09", True, id="full"),
        pytest.param("2021-03-09/2021-04-10", True, id="interval"),
        pytest.param("2021/2022", True, id="interval-of-years"),
        pytest.param("2021-13", False, id="month-13"),
        pytest.param("2021-02-30", False, id="day-30-of-february"),
        pytest.param("09-03-2021", False, id="day-first"),
        pytest.param("20210309", False, id="no-separators"),
        pytest.param("", False, id="empty"),
        pytest.param("2021/2022/2023", False, id="three-parts"),
    ),
)
def test_is_edtf_date(value, expected):
    """
    Zenodo takes EDTF level 0, which is wider than `YYYY-MM-DD`

    Getting this wrong in either direction is expensive: too strict and we
    reject metadata Zenodo is happy with, too loose and Zenodo silently
    discards the date.
    """
    assert is_edtf_date(value) is expected


def test_find_problems_partial_publication_date_is_fine():
    """
    `2021` is a real publication date as far as Zenodo is concerned
    """
    metadata = Metadata.from_json({**ZENODO_METADATA, "publication_date": "2021"})

    assert metadata.find_problems() == ()


def test_find_problems_catches_a_date_zenodo_would_discard():
    metadata = Metadata.from_json({**ZENODO_METADATA, "publication_date": "2021-13-45"})

    (problem,) = metadata.find_problems()

    assert "discards a date it cannot read" in problem


def test_find_problems_checks_other_dates_too():
    metadata = Metadata.from_json(
        {**ZENODO_METADATA, "dates": [{"date": "whenever", "type": {"id": "other"}}]}
    )

    (problem,) = metadata.find_problems()

    assert "`dates[0]`" in problem


@pytest.mark.parametrize(
    "value",
    (
        pytest.param("2021-3-09", id="single-digit-month"),
        pytest.param("2021-03-9", id="single-digit-day"),
        pytest.param("21-03-09", id="two-digit-year"),
        pytest.param(" 2021", id="leading-space"),
        pytest.param("2021 ", id="trailing-space"),
        pytest.param("+2021", id="signed-year"),
        # Built rather than written out, so that the source stays ASCII
        pytest.param(
            "".join(chr(0xFF10 + int(d)) for d in "2021"), id="full-width-digits"
        ),
        pytest.param("2021-03-09-01", id="too-many-fields"),
        pytest.param("2021--03", id="empty-field"),
    ),
)
def test_is_edtf_date_rejects_near_misses(value):
    """
    The near misses are the ones worth pinning

    Zenodo does not refuse a date it cannot read, it discards it, so anything
    which slips through here goes missing from the record without a word.
    `str.isdigit` alone would accept the full-width digits, and `int` alone
    would accept the whitespace and the sign.
    """
    assert is_edtf_date(value) is False


@pytest.mark.parametrize(
    "value, known, expected",
    (
        pytest.param("datset", KNOWN_RESOURCE_TYPES, "dataset", id="transposition"),
        pytest.param(
            "publication-artcile",
            KNOWN_RESOURCE_TYPES,
            "publication-article",
            id="transposition-in-a-compound",
        ),
        pytest.param("orchid", KNOWN_IDENTIFIER_SCHEMES, "orcid", id="scheme-typo"),
        pytest.param("DOI", KNOWN_IDENTIFIER_SCHEMES, "doi", id="wrong-case"),
        pytest.param(
            "isSupplementTo",
            KNOWN_RELATION_TYPES,
            "issupplementto",
            id="datacite-spelling",
        ),
    ),
)
def test_describe_unknown_vocabulary_value_suggests(value, known, expected):
    """
    The suggestion is what makes the warning actionable

    Case is the one worth having a test for: Zenodo's vocabularies are all lower
    case, and the DataCite spellings people arrive with are not, so matching has
    to be done against the lower-cased value or the most common mistake gets no
    suggestion at all.
    """
    described = describe_unknown_vocabulary_value(
        value, where="somewhere", known=known, known_name="KNOWN_THINGS"
    )

    assert f"Did you mean {expected!r}?" in described


def test_describe_unknown_vocabulary_value_without_a_suggestion():
    """
    Nothing is suggested when nothing is close, rather than something silly
    """
    described = describe_unknown_vocabulary_value(
        "a-completely-different-thing",
        where="resource_type",
        known=KNOWN_RESOURCE_TYPES,
        known_name="KNOWN_RESOURCE_TYPES",
    )

    assert "Did you mean" not in described


def test_describe_unknown_vocabulary_value_lists_the_lot():
    """
    The full vocabulary is in the message, and it says where it came from

    Zenodo's own error for a bad vocabulary value does not list the options, so
    this is the difference between fixing it from the message and going looking.
    """
    described = describe_unknown_vocabulary_value(
        "pottery",
        where="resource_type",
        known=KNOWN_RESOURCE_TYPES,
        known_name="KNOWN_RESOURCE_TYPES",
    )

    assert "`openscm_zenodo.metadata.KNOWN_RESOURCE_TYPES`" in described
    for known in KNOWN_RESOURCE_TYPES:
        assert repr(known) in described
