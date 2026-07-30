"""
Zenodo's metadata schema

They follow the same rule as
[`FileEntry`][openscm_zenodo.zenodo.FileEntry]:
the parts we model are pulled out into typed fields,
everything else Zenodo sends is kept in `raw`,
and `to_json` puts the two back together.
So metadata can be read off one record and applied to another
without anything being dropped on the way through,
and the fields you are most likely to want are typed rather than
buried in nested dictionaries.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from attrs import define, field, fields

from openscm_zenodo.exceptions import MetadataValidationError

KNOWN_RESOURCE_TYPES: tuple[str, ...] = (
    "dataset",
    "image",
    "image-diagram",
    "image-figure",
    "image-photo",
    "image-plot",
    "lesson",
    "model",
    "other",
    "poster",
    "presentation",
    "publication",
    "publication-annotationcollection",
    "publication-article",
    "publication-book",
    "publication-conferencepaper",
    "publication-datamanagementplan",
    "publication-deliverable",
    "publication-milestone",
    "publication-preprint",
    "publication-report",
    "publication-section",
    "publication-softwaredocumentation",
    "publication-technicalnote",
    "publication-thesis",
    "publication-workingpaper",
    "software",
    "video",
    "workflow",
)
"""
Resource type IDs we have seen Zenodo accept

Zenodo's vocabulary is longer than this and it changes,
so an ID which is not here is not necessarily wrong.
We warn about the ones we do not recognise rather than rejecting them.
"""

KNOWN_IDENTIFIER_SCHEMES: tuple[str, ...] = (
    "ads",
    "arxiv",
    "ark",
    "bibcode",
    "doi",
    "ean13",
    "eissn",
    "gnd",
    "handle",
    "igsn",
    "isbn",
    "isni",
    "issn",
    "istc",
    "lissn",
    "lsid",
    "orcid",
    "pmid",
    "pmcid",
    "purl",
    "ror",
    "swh",
    "upc",
    "url",
    "urn",
    "w3id",
)
"""
Identifier schemes we have seen Zenodo accept

This is the vocabulary behind an
[`Identifier`][openscm_zenodo.metadata.Identifier]'s `scheme` and a
[`RelatedIdentifier`][openscm_zenodo.metadata.RelatedIdentifier]'s.
Note that Zenodo usually works the scheme out from the identifier itself, so
the most common reason to see a warning about one is a typo.

The same caveat as
[`KNOWN_RESOURCE_TYPES`][openscm_zenodo.metadata.KNOWN_RESOURCE_TYPES] applies:
this is what we have seen, not what Zenodo will take.
"""

KNOWN_RELATION_TYPES: tuple[str, ...] = (
    "cites",
    "compiles",
    "continues",
    "describes",
    "documents",
    "hasmetadata",
    "haspart",
    "hasversion",
    "isadaptedby",
    "isadaptionof",
    "iscitedby",
    "iscompiledby",
    "iscontinuedby",
    "isderivedfrom",
    "isdescribedby",
    "isdocumentedby",
    "isidenticalto",
    "ismetadatafor",
    "isnewversionof",
    "isobsoletedby",
    "isoriginalformof",
    "ispartof",
    "ispreviousversionof",
    "ispublishedin",
    "isreferencedby",
    "isrequiredby",
    "isreviewedby",
    "issourceof",
    "issupplementedby",
    "issupplementto",
    "isvariantformof",
    "isversionof",
    "obsoletes",
    "references",
    "requires",
    "reviews",
    "sources",
)
"""
Relation types we have seen Zenodo accept

This is the vocabulary behind a
[`RelatedIdentifier`][openscm_zenodo.metadata.RelatedIdentifier]'s
`relation_type`. Zenodo spells these all lower case and without punctuation, so
the DataCite spelling (`isSupplementTo`) is not one it takes — which is the
mistake this list is most likely to catch.

The same caveat as
[`KNOWN_RESOURCE_TYPES`][openscm_zenodo.metadata.KNOWN_RESOURCE_TYPES] applies:
this is what we have seen, not what Zenodo will take.
"""

_LEGACY_KEYS: dict[str, str] = {
    "upload_type": (
        "`upload_type` is now `resource_type`, "
        'and it takes a vocabulary ID, e.g. `{"id": "dataset"}`'
    ),
    "publication_type": (
        "`publication_type` is now folded into `resource_type`, "
        'e.g. `{"id": "publication-article"}`'
    ),
    "image_type": (
        '`image_type` is now folded into `resource_type`, e.g. `{"id": "image-figure"}`'
    ),
    "access_right": (
        "`access_right` is no longer part of the metadata. "
        "Access is set on the record itself, "
        'in the `access` object, e.g. `{"record": "public", "files": "public"}`'
    ),
    "embargo_date": (
        "`embargo_date` is no longer part of the metadata. "
        "It now lives in the record's `access.embargo` object"
    ),
    "license": (
        '`license` is now `rights`, which is a list, e.g. `[{"id": "cc-by-4.0"}]`'
    ),
    "keywords": (
        "`keywords` is now `subjects`, "
        'and each entry is an object, e.g. `[{"subject": "climate"}]`'
    ),
    "notes": ("`notes` is now an entry in `additional_descriptions`"),
    "prereserve_doi": (
        "`prereserve_doi` is gone. "
        "Reserve a DOI with `ZenodoClient.reserve_doi` instead"
    ),
    "doi": ("`doi` is no longer part of the metadata, it lives in the record's `pids`"),
    "journal_title": (
        "the journal, imprint, conference and thesis fields are now Zenodo "
        "custom fields, so they live in the record's `custom_fields`, "
        "not in `metadata`"
    ),
    "communities": (
        "`communities` is no longer part of the metadata, "
        "it lives in the record's `parent.communities`"
    ),
}
"""
Keys which only make sense in the legacy deposit schema

Mapped to what to do instead.
Passing one of these is the clearest possible signal
that metadata written for the old API has been handed to the new one,
so we say so rather than letting Zenodo reject it later
with a message about a field we did not send.
"""


def _vocabulary_id(value: Any) -> str | None:
    """
    Pull the ID out of a vocabulary entry

    Zenodo nests these (`{"id": "dataset"}`) and expands them with display text
    on the way out. We hold the ID, because the nesting and the display text
    carry nothing we could not ask Zenodo for.

    Parameters
    ----------
    value
        The vocabulary entry, or an ID which is already a string

    Returns
    -------
    :
        The ID, or `None` if there is not one
    """
    if isinstance(value, Mapping):
        res = value.get("id")

        return None if res is None else str(res)

    if value is None:
        return None

    return str(value)


def _localised(value: Any) -> str | None:
    """
    Pull the English text out of a localised string

    Zenodo expands vocabulary titles into every locale it has.

    Parameters
    ----------
    value
        The localised string, or a string which is already plain

    Returns
    -------
    :
        The text, or `None` if there is not any
    """
    if isinstance(value, Mapping):
        res = value.get("en", next(iter(value.values()), None))

        return None if res is None else str(res)

    return None if value is None else str(value)


@define
class Identifier:
    """
    A persistent identifier for a person or an organisation
    """

    scheme: str
    """Scheme the identifier belongs to, e.g. `"orcid"`, `"ror"`, `"gnd"`"""

    identifier: str
    """The identifier itself, e.g. `"0000-0002-4767-2723"`"""

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Identifier:
        """
        Initialise from Zenodo's description of an identifier

        Parameters
        ----------
        raw
            Zenodo's description of the identifier

        Returns
        -------
        :
            Initialised `Identifier`

        Raises
        ------
        KeyError
            `raw` is not shaped the way Zenodo describes an identifier
        """
        return cls(scheme=raw["scheme"], identifier=raw["identifier"])

    def to_json(self) -> dict[str, str]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The identifier, as Zenodo describes one
        """
        return {"scheme": self.scheme, "identifier": self.identifier}


@define
class Affiliation:
    """
    An organisation a creator or contributor is affiliated with

    Zenodo takes either a vocabulary ID or a name, and one of the two is
    required. It sends *both* back for an entry which has an ID, because it
    expands the ID into its name on the way out. We keep both when it does,
    because the name is the only part a human can read, but only the ID is sent
    back: Zenodo derives the name from it and rejects one which disagrees.
    """

    name: str | None = None
    """
    Name of the organisation, spelled out

    Use this for an organisation which is not in Zenodo's vocabulary.
    """

    id: str | None = None
    """
    ID of the organisation in Zenodo's affiliations vocabulary (a ROR ID)

    Zenodo fills in `name` itself when this is given.
    """

    def __attrs_post_init__(self) -> None:
        """
        Check that this affiliation is one Zenodo could act on

        Raises
        ------
        MetadataValidationError
            The affiliation has neither a name nor an ID
        """
        if self.name is None and self.id is None:
            problems = ["An affiliation needs a `name` or an `id`, this has neither."]
            raise MetadataValidationError(
                problems, description="describe an affiliation"
            )

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Affiliation:
        """
        Initialise from Zenodo's description of an affiliation

        Parameters
        ----------
        raw
            Zenodo's description of the affiliation

        Returns
        -------
        :
            Initialised `Affiliation`
        """
        return cls(name=raw.get("name"), id=raw.get("id"))

    def to_json(self) -> dict[str, str]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The affiliation, as Zenodo describes one.

            An ID is sent on its own if we have one,
            because Zenodo derives the name from it
            and rejects a name which does not match.
        """
        if self.id is not None:
            return {"id": self.id}

        # `__attrs_post_init__` has already ruled out both being `None`
        return {"name": str(self.name)}


@define
class Person:
    """
    A person, as a creator of or contributor to a record
    """

    TYPE: ClassVar[str] = "personal"
    """The `type` Zenodo uses for a person"""

    family_name: str
    """
    Family name
    """

    given_name: str | None = None
    """Given name"""

    identifiers: tuple[Identifier, ...] = ()
    """Persistent identifiers, e.g. an ORCID"""

    @property
    def name(self) -> str:
        """
        Name to show for this person

        Returns
        -------
        :
            The name, assembled the way Zenodo assembles it
        """
        if self.given_name:
            return f"{self.family_name}, {self.given_name}"

        return self.family_name

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Person:
        """
        Initialise from Zenodo's description of a person

        Parameters
        ----------
        raw
            Zenodo's `person_or_org` object

        Returns
        -------
        :
            Initialised `Person`

        Raises
        ------
        MetadataValidationError
            The person has no name of any kind

        Notes
        -----
        Zenodo sends a computed `name` alongside `family_name`,
        and some older records have only the `name`.
        We fall back to it rather than refusing to read such a record.
        """
        family_name = raw.get("family_name") or raw.get("name")
        if not family_name:
            problems = [
                "A person needs a `family_name`, this has neither that nor a `name`. "
                f"We were given: {dict(raw)!r}."
            ]
            raise MetadataValidationError(problems, description="describe a person")

        return cls(
            family_name=family_name,
            given_name=raw.get("given_name"),
            identifiers=tuple(
                Identifier.from_json(v) for v in raw.get("identifiers", [])
            ),
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The person, as Zenodo describes one
        """
        res: dict[str, Any] = {"type": self.TYPE, "family_name": self.family_name}

        if self.given_name is not None:
            res["given_name"] = self.given_name

        if self.identifiers:
            res["identifiers"] = [v.to_json() for v in self.identifiers]

        return res


@define
class Organisation:
    """
    An organisation, as a creator of or contributor to a record
    """

    TYPE: ClassVar[str] = "organizational"
    """
    The `type` Zenodo uses for an organisation

    (Zenodo spells this the American way).
    """

    name: str
    """Name of the organisation"""

    identifiers: tuple[Identifier, ...] = ()
    """Persistent identifiers, e.g. a [ROR](https://ror.org) ID"""

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Organisation:
        """
        Initialise from Zenodo's description of an organisation

        Parameters
        ----------
        raw
            Zenodo's `person_or_org` object

        Returns
        -------
        :
            Initialised `Organisation`

        Raises
        ------
        MetadataValidationError
            The organisation has no name
        """
        name = raw.get("name")
        if not name:
            problems = [
                "An organisation needs a `name`, this has none. "
                f"We were given: {dict(raw)!r}."
            ]
            raise MetadataValidationError(
                problems, description="describe an organisation"
            )

        return cls(
            name=name,
            identifiers=tuple(
                Identifier.from_json(v) for v in raw.get("identifiers", [])
            ),
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The organisation, as Zenodo describes one
        """
        res: dict[str, Any] = {"type": self.TYPE, "name": self.name}

        if self.identifiers:
            res["identifiers"] = [v.to_json() for v in self.identifiers]

        return res


def _entity_from_json(raw: Mapping[str, Any]) -> Person | Organisation:
    """
    Build a person or an organisation, whichever Zenodo says this is

    Parameters
    ----------
    raw
        Zenodo's `person_or_org` object

    Returns
    -------
    :
        The person or organisation
    """
    if raw.get("type") == Organisation.TYPE:
        return Organisation.from_json(raw)

    return Person.from_json(raw)


@define
class Creator:
    """
    A creator of a record

    A [`Contributor`][openscm_zenodo.metadata.Contributor] is the same thing
    plus a role.
    """

    entity: Person | Organisation
    """Who this is"""

    affiliations: tuple[Affiliation, ...] = ()
    """Organisations they are affiliated with"""

    @property
    def name(self) -> str:
        """
        Name to show for this creator

        Returns
        -------
        :
            The creator's name
        """
        return self.entity.name

    @classmethod
    def person(
        cls,
        family_name: str,
        given_name: str | None = None,
        *,
        orcid: str | None = None,
        affiliations: Iterable[str | Affiliation] = (),
        **kwargs: Any,
    ) -> Any:
        """
        Create a person

        This is the shortcut for the common case.
        Anything it does not cover can be built through
        [`Person`][openscm_zenodo.metadata.Person] directly.

        Parameters
        ----------
        family_name
            Family name

        given_name
            Given name

        orcid
            ORCID, without the `https://orcid.org/` prefix

        affiliations
            Organisations they are affiliated with.

            Strings are taken as organisation names.

        **kwargs
            Passed on to the class being built.

            This is how [`Contributor`][openscm_zenodo.metadata.Contributor]
            takes its `role`.

        Returns
        -------
        :
            Initialised `Creator`, or `Contributor` if called on one

        Examples
        --------
        >>> creator = Creator.person("Nicholls", "Zebedee", orcid="0000-0002-4767-2723")
        >>> creator.name
        'Nicholls, Zebedee'
        >>> Contributor.person("Nicholls", role="datacurator").role
        'datacurator'
        """
        return cls(
            entity=Person(
                family_name=family_name,
                given_name=given_name,
                identifiers=(
                    (Identifier(scheme="orcid", identifier=orcid),)
                    if orcid is not None
                    else ()
                ),
            ),
            affiliations=_as_affiliations(affiliations),
            **kwargs,
        )

    @classmethod
    def organisation(
        cls,
        name: str,
        *,
        ror: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Create an organisation

        Parameters
        ----------
        name
            Name of the organisation

        ror
            [ROR](https://ror.org) ID, without the `https://ror.org/` prefix

        **kwargs
            Passed on to the class being built,
            e.g. a [`Contributor`][openscm_zenodo.metadata.Contributor]'s `role`

        Returns
        -------
        :
            Initialised `Creator`, or `Contributor` if called on one

        Examples
        --------
        >>> Creator.organisation("Climate Resource").to_json()
        {'person_or_org': {'type': 'organizational', 'name': 'Climate Resource'}}
        """
        return cls(
            entity=Organisation(
                name=name,
                identifiers=(
                    (Identifier(scheme="ror", identifier=ror),)
                    if ror is not None
                    else ()
                ),
            ),
            **kwargs,
        )

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Any:
        """
        Initialise from Zenodo's description of a creator

        Parameters
        ----------
        raw
            Zenodo's description of the creator

        Returns
        -------
        :
            Initialised `Creator`

        Raises
        ------
        MetadataValidationError
            `raw` is a legacy deposit API creator,
            i.e. a bare `name`/`affiliation`/`orcid` rather than a `person_or_org`
        """
        if "person_or_org" not in raw:
            problems = [
                "Creators are now `{'person_or_org': {...}, 'affiliations': [...]}`, "
                "not `{'name': ..., 'affiliation': ..., 'orcid': ...}`. "
                "Build them with `Creator.person` or `Creator.organisation`. "
                f"We were given: {dict(raw)!r}."
            ]
            raise MetadataValidationError(problems, description="describe a creator")

        return cls(
            entity=_entity_from_json(raw["person_or_org"]),
            affiliations=tuple(
                Affiliation.from_json(v) for v in raw.get("affiliations", [])
            ),
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The creator, as Zenodo describes one
        """
        res: dict[str, Any] = {"person_or_org": self.entity.to_json()}

        if self.affiliations:
            res["affiliations"] = [v.to_json() for v in self.affiliations]

        return res


@define
class Contributor(Creator):
    """
    A contributor to a record

    The same shape as a [`Creator`][openscm_zenodo.metadata.Creator],
    except that Zenodo requires a role and rejects a creator which has one.
    """

    role: str = field(kw_only=True)
    """
    What they did

    A vocabulary ID, e.g. `"datacurator"`, `"projectmanager"`, `"other"`.
    """

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Contributor:
        """
        Initialise from Zenodo's description of a contributor

        Parameters
        ----------
        raw
            Zenodo's description of the contributor

        Returns
        -------
        :
            Initialised `Contributor`

        Raises
        ------
        MetadataValidationError
            The contributor has no role, or is a legacy deposit API contributor
        """
        role = _vocabulary_id(raw.get("role"))
        if role is None:
            problems = [
                "A contributor needs a `role`, this has none. "
                f"We were given: {dict(raw)!r}."
            ]
            raise MetadataValidationError(
                problems, description="describe a contributor"
            )

        creator = Creator.from_json(raw)

        return cls(entity=creator.entity, affiliations=creator.affiliations, role=role)

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The contributor, as Zenodo describes one
        """
        res = super().to_json()
        res["role"] = {"id": self.role}

        return res


@define
class Right:
    """
    A licence, or other rights statement, attached to a record

    Like [`Affiliation`][openscm_zenodo.metadata.Affiliation], Zenodo takes
    either a vocabulary ID or a title of your own, sends both back for an entry
    which has an ID, and only the ID is sent back to Zenodo.
    """

    id: str | None = None
    """
    ID in Zenodo's licence vocabulary, which is SPDX, e.g. `"cc-by-4.0"`

    Zenodo fills in the title, description and link itself when this is given.
    """

    title: str | None = None
    """Title of the licence, for one which is not in Zenodo's vocabulary"""

    link: str | None = None
    """Link to the licence text, for one which is not in Zenodo's vocabulary"""

    def __attrs_post_init__(self) -> None:
        """
        Check that this rights statement is one Zenodo could act on

        Raises
        ------
        MetadataValidationError
            The rights statement has neither an ID nor a title
        """
        if self.id is None and self.title is None:
            problems = [
                "A rights statement needs an `id` (an SPDX licence ID) "
                "or a `title`, this has neither."
            ]
            raise MetadataValidationError(
                problems, description="describe a rights statement"
            )

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Right:
        """
        Initialise from Zenodo's description of a rights statement

        Parameters
        ----------
        raw
            Zenodo's description of the rights statement

        Returns
        -------
        :
            Initialised `Right`
        """
        return cls(
            id=raw.get("id"), title=_localised(raw.get("title")), link=raw.get("link")
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The rights statement, as Zenodo describes one
        """
        if self.id is not None:
            return {"id": self.id}

        res: dict[str, Any] = {"title": {"en": self.title}}
        if self.link is not None:
            res["link"] = self.link

        return res


@define
class Subject:
    """
    A keyword or subject heading
    """

    subject: str | None = None
    """Free-text keyword, e.g. `"climate"`"""

    id: str | None = None
    """ID in one of Zenodo's subject vocabularies, for a controlled term"""

    def __attrs_post_init__(self) -> None:
        """
        Check that this subject is one Zenodo could act on

        Raises
        ------
        MetadataValidationError
            The subject has neither a term nor an ID
        """
        if self.subject is None and self.id is None:
            problems = ["A subject needs a `subject` or an `id`, this has neither."]
            raise MetadataValidationError(problems, description="describe a subject")

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Subject:
        """
        Initialise from Zenodo's description of a subject

        Parameters
        ----------
        raw
            Zenodo's description of the subject

        Returns
        -------
        :
            Initialised `Subject`
        """
        return cls(subject=raw.get("subject"), id=raw.get("id"))

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The subject, as Zenodo describes one
        """
        if self.id is not None:
            return {"id": self.id}

        return {"subject": str(self.subject)}


@define
class Date:
    """
    A date which is not the publication date, e.g. when the data was collected
    """

    date: str
    """
    The date, or an interval of them

    Zenodo takes EDTF level 0: `YYYY`, `YYYY-MM`, `YYYY-MM-DD`,
    or two of those separated by a `/`.
    """

    type: str
    """
    What the date means

    A vocabulary ID, e.g. `"collected"`, `"created"`, `"valid"`, `"other"`.
    """

    description: str | None = None
    """Free text saying more about the date"""

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Date:
        """
        Initialise from Zenodo's description of a date

        Parameters
        ----------
        raw
            Zenodo's description of the date

        Returns
        -------
        :
            Initialised `Date`

        Raises
        ------
        MetadataValidationError
            The date has no `date` or no `type`
        """
        date = raw.get("date")
        type_ = _vocabulary_id(raw.get("type"))
        if not date or type_ is None:
            problems = [
                f"A date needs a `date` and a `type`. We were given: {dict(raw)!r}."
            ]
            raise MetadataValidationError(problems, description="describe a date")

        return cls(date=date, type=type_, description=raw.get("description"))

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The date, as Zenodo describes one
        """
        res: dict[str, Any] = {"date": self.date, "type": {"id": self.type}}
        if self.description is not None:
            res["description"] = self.description

        return res


@define
class RelatedIdentifier:
    """
    Something else this record relates to, e.g. the paper which describes it
    """

    identifier: str
    """The identifier itself, e.g. `"10.5194/gmd-13-5175-2020"`"""

    scheme: str
    """What kind of identifier it is, e.g. `"doi"`, `"url"`, `"isbn"`"""

    relation_type: str
    """
    How this record relates to it

    A vocabulary ID, e.g. `"cites"`, `"issupplementto"`, `"isnewversionof"`.
    """

    resource_type: str | None = None
    """
    What kind of thing the other end is, e.g. `"publication-article"`

    Optional, and only worth setting when it is not obvious.
    """

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> RelatedIdentifier:
        """
        Initialise from Zenodo's description of a related identifier

        Parameters
        ----------
        raw
            Zenodo's description of the related identifier

        Returns
        -------
        :
            Initialised `RelatedIdentifier`

        Raises
        ------
        MetadataValidationError
            The related identifier is missing something Zenodo requires
        """
        identifier = raw.get("identifier")
        scheme = raw.get("scheme")
        relation_type = _vocabulary_id(raw.get("relation_type"))
        if not identifier or not scheme or relation_type is None:
            problems = [
                "A related identifier needs an `identifier`, a `scheme` "
                f"and a `relation_type`. We were given: {dict(raw)!r}."
            ]
            raise MetadataValidationError(
                problems, description="describe a related identifier"
            )

        return cls(
            identifier=identifier,
            scheme=scheme,
            relation_type=relation_type,
            resource_type=_vocabulary_id(raw.get("resource_type")),
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The related identifier, as Zenodo describes one
        """
        res: dict[str, Any] = {
            "identifier": self.identifier,
            "scheme": self.scheme,
            "relation_type": {"id": self.relation_type},
        }
        if self.resource_type is not None:
            res["resource_type"] = {"id": self.resource_type}

        return res


@define
class Funder:
    """
    Who paid for the work
    """

    id: str | None = None
    """ID in Zenodo's funders vocabulary (a ROR ID or a Crossref funder ID)"""

    name: str | None = None
    """Name of the funder, for one which is not in Zenodo's vocabulary"""

    def __attrs_post_init__(self) -> None:
        """
        Check that this funder is one Zenodo could act on

        Raises
        ------
        MetadataValidationError
            The funder has neither an ID nor a name
        """
        if self.id is None and self.name is None:
            problems = ["A funder needs an `id` or a `name`, this has neither."]
            raise MetadataValidationError(problems, description="describe a funder")

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Funder:
        """
        Initialise from Zenodo's description of a funder

        Parameters
        ----------
        raw
            Zenodo's description of the funder

        Returns
        -------
        :
            Initialised `Funder`
        """
        return cls(id=raw.get("id"), name=raw.get("name"))

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The funder, as Zenodo describes one
        """
        if self.id is not None:
            return {"id": self.id}

        return {"name": str(self.name)}


@define
class Award:
    """
    The grant the work was done under
    """

    id: str | None = None
    """ID in Zenodo's awards vocabulary, e.g. `"021nxhr62::101003536"`"""

    title: str | None = None
    """Title of the award, for one which is not in Zenodo's vocabulary"""

    number: str | None = None
    """Grant number, for an award which is not in Zenodo's vocabulary"""

    def __attrs_post_init__(self) -> None:
        """
        Check that this award is one Zenodo could act on

        Raises
        ------
        MetadataValidationError
            The award has neither an ID nor a title and number
        """
        if self.id is None and (self.title is None or self.number is None):
            problems = [
                "An award needs an `id`, or both a `title` and a `number`. "
                f"This has id={self.id!r}, title={self.title!r}, "
                f"number={self.number!r}."
            ]
            raise MetadataValidationError(problems, description="describe an award")

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Award:
        """
        Initialise from Zenodo's description of an award

        Parameters
        ----------
        raw
            Zenodo's description of the award

        Returns
        -------
        :
            Initialised `Award`
        """
        return cls(
            id=raw.get("id"),
            title=_localised(raw.get("title")),
            number=raw.get("number"),
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The award, as Zenodo describes one
        """
        if self.id is not None:
            return {"id": self.id}

        return {"title": {"en": self.title}, "number": str(self.number)}


@define
class Funding:
    """
    A funder, an award, or both
    """

    funder: Funder | None = None
    """Who paid"""

    award: Award | None = None
    """The grant they paid under"""

    def __attrs_post_init__(self) -> None:
        """
        Check that this funding entry is one Zenodo could act on

        Raises
        ------
        MetadataValidationError
            The entry has neither a funder nor an award
        """
        if self.funder is None and self.award is None:
            problems = [
                "A funding entry needs a `funder` or an `award`, this has neither."
            ]
            raise MetadataValidationError(problems, description="describe funding")

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Funding:
        """
        Initialise from Zenodo's description of a funding entry

        Parameters
        ----------
        raw
            Zenodo's description of the funding entry

        Returns
        -------
        :
            Initialised `Funding`
        """
        funder = raw.get("funder")
        award = raw.get("award")

        return cls(
            funder=None if funder is None else Funder.from_json(funder),
            award=None if award is None else Award.from_json(award),
        )

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The funding entry, as Zenodo describes one
        """
        res: dict[str, Any] = {}
        if self.funder is not None:
            res["funder"] = self.funder.to_json()

        if self.award is not None:
            res["award"] = self.award.to_json()

        return res


@define
class Metadata:
    """
    A record's metadata

    Every field is optional, because a draft's metadata legitimately is:
    Zenodo only checks that metadata is complete when a record is published,
    so a half-filled draft is a normal thing to have and to read back.
    [`validate`][openscm_zenodo.metadata.Metadata.validate] is where
    completeness is checked, and it is the thing to call
    before publishing rather than after.

    Examples
    --------
    >>> metadata = Metadata(
    ...     title="A dataset which is not real",
    ...     resource_type="dataset",
    ...     creators=(Creator.organisation("Climate Resource"),),
    ...     publication_date="2026-07-28",
    ...     publisher="Zenodo",
    ... )
    >>> metadata.to_json()["creators"]
    [{'person_or_org': {'type': 'organizational', 'name': 'Climate Resource'}}]
    >>> metadata.validate()
    """

    title: str | None = None
    """Title of the record"""

    resource_type: str | None = None
    """
    What kind of thing the record is

    A vocabulary ID, e.g. `"dataset"`, `"software"`, `"publication-article"`;
    see [`KNOWN_RESOURCE_TYPES`][openscm_zenodo.metadata.KNOWN_RESOURCE_TYPES].
    """

    creators: tuple[Creator, ...] = ()
    """Who made the thing the record describes"""

    publication_date: str | None = None
    """
    Date the thing was published

    Zenodo takes EDTF level 0, so `"2021"` and `"2021-03"` are as valid as
    `"2021-03-09"`, and so is an interval (`"2021-03-09/2021-04-10"`).
    A value it cannot parse is **silently discarded** rather than refused,
    which is why
    [`find_problems`][openscm_zenodo.metadata.Metadata.find_problems]
    checks the format itself.
    """

    publisher: str | None = None
    """
    Who published it

    Zenodo requires this to register a DOI,
    so a record without it cannot be published.
    `"Zenodo"` is the usual answer for something which is only on Zenodo.
    """

    description: str | None = None
    """Description of the record, which may be HTML"""

    version: str | None = None
    """
    Version of the thing the record describes, e.g. `"v1.2.0"`

    Zenodo does not check the format, so any scheme works.
    It does silently discard values longer than about 190 characters,
    the same way it discards a `publication_date` it cannot parse.
    """

    rights: tuple[Right, ...] = ()
    """Licence, or other rights statement"""

    subjects: tuple[Subject, ...] = ()
    """Keywords and subject headings"""

    contributors: tuple[Contributor, ...] = ()
    """People and organisations who contributed without being creators"""

    dates: tuple[Date, ...] = ()
    """Dates other than the publication date"""

    related_identifiers: tuple[RelatedIdentifier, ...] = ()
    """Other things this record relates to"""

    funding: tuple[Funding, ...] = ()
    """Who paid for the work"""

    languages: tuple[str, ...] = ()
    """
    Languages the thing is in

    Vocabulary IDs, which are ISO 639-3 codes, e.g. `"eng"`.
    """

    raw: dict[str, Any] = field(factory=dict, repr=False)
    """
    Everything Zenodo sent which we do not model

    `additional_titles`, `additional_descriptions`, `locations`, `references`,
    `sizes`, `formats` and anything Zenodo adds later all land here,
    and go straight back out again through
    [`to_json`][openscm_zenodo.metadata.Metadata.to_json],
    so passing metadata through this class does not quietly drop any of it.
    """

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Metadata:
        """
        Initialise from Zenodo's description of a record's metadata

        Parameters
        ----------
        raw
            Contents of the record's `metadata` key

        Returns
        -------
        :
            Initialised `Metadata`

        Raises
        ------
        MetadataValidationError
            `raw` is written against the legacy deposit API's schema,
            or one of its entries is missing something Zenodo requires.

            The message says which key gave it away and what to use instead.

        Examples
        --------
        >>> metadata = Metadata.from_json(
        ...     {
        ...         "title": "A dataset which is not real",
        ...         "resource_type": {"id": "dataset", "title": {"en": "Dataset"}},
        ...         "rights": [{"id": "cc-by-4.0", "title": {"en": "CC BY 4.0"}}],
        ...     }
        ... )
        >>> metadata.resource_type
        'dataset'
        >>> metadata.rights
        (Right(id='cc-by-4.0', title='CC BY 4.0', link=None),)

        The display bits Zenodo added are dropped on the way back out,
        because they are Zenodo's to fill in.

        >>> metadata.to_json()["rights"]
        [{'id': 'cc-by-4.0'}]
        """
        check_not_legacy(raw)

        return cls(
            title=raw.get("title"),
            resource_type=_vocabulary_id(raw.get("resource_type")),
            creators=tuple(Creator.from_json(v) for v in raw.get("creators", [])),
            publication_date=raw.get("publication_date"),
            publisher=raw.get("publisher"),
            description=raw.get("description"),
            version=raw.get("version"),
            rights=tuple(Right.from_json(v) for v in raw.get("rights", [])),
            subjects=tuple(Subject.from_json(v) for v in raw.get("subjects", [])),
            contributors=tuple(
                Contributor.from_json(v) for v in raw.get("contributors", [])
            ),
            dates=tuple(Date.from_json(v) for v in raw.get("dates", [])),
            related_identifiers=tuple(
                RelatedIdentifier.from_json(v)
                for v in raw.get("related_identifiers", [])
            ),
            funding=tuple(Funding.from_json(v) for v in raw.get("funding", [])),
            languages=tuple(str(_vocabulary_id(v)) for v in raw.get("languages", [])),
            raw={k: v for k, v in raw.items() if k not in get_metadata_modelled_keys()},
        )

    @classmethod
    def from_file(cls, path: Path | str) -> Metadata:
        """
        Load metadata from a JSON file

        Parameters
        ----------
        path
            File to load.

            Both `{"metadata": {...}}` and a bare `{...}` are accepted.
            Metadata never has a `metadata` key of its own,
            so there is nothing ambiguous about accepting both.

        Returns
        -------
        :
            The metadata in the file

        Raises
        ------
        MetadataValidationError
            The file is written against the legacy deposit API's schema
        """
        with open(Path(path)) as fh:
            loaded = json.load(fh)

        if "metadata" in loaded:
            loaded = loaded["metadata"]

        return cls.from_json(loaded)

    def to_json(self) -> dict[str, Any]:
        """
        Convert to the shape Zenodo expects

        Returns
        -------
        :
            The metadata, as Zenodo describes it.

            This is what goes in a record's `metadata` key.
            Fields we do not have are left out rather than sent as `null`,
            and everything in `raw` is passed through untouched.

        Raises
        ------
        MetadataValidationError
            `raw` holds a key which we model.

            That would be two sources of truth for one field,
            and `raw` would silently win, so we refuse instead.
            `from_json` never produces this; setting `raw` by hand can.
        """
        clashes = sorted(set(self.raw) & set(get_metadata_modelled_keys()))
        if clashes:
            problems = [
                f"`raw` holds {key!r}, which is modelled as `Metadata.{key}`. "
                f"Set the field instead."
                for key in clashes
            ]
            raise MetadataValidationError(problems, description="talk to Zenodo")

        res: dict[str, Any] = {}

        for key, value in (
            ("title", self.title),
            ("publication_date", self.publication_date),
            ("publisher", self.publisher),
            ("description", self.description),
            ("version", self.version),
        ):
            if value is not None:
                res[key] = value

        if self.resource_type is not None:
            res["resource_type"] = {"id": self.resource_type}

        for key, entries in (
            ("creators", self.creators),
            ("rights", self.rights),
            ("subjects", self.subjects),
            ("contributors", self.contributors),
            ("dates", self.dates),
            ("related_identifiers", self.related_identifiers),
            ("funding", self.funding),
        ):
            if entries:
                res[key] = [v.to_json() for v in entries]

        if self.languages:
            res["languages"] = [{"id": v} for v in self.languages]

        res.update(self.raw)

        return res

    def find_problems(self) -> tuple[str, ...]:
        """
        Find the reasons Zenodo would refuse to publish a record with this metadata

        Zenodo only validates metadata at publish time,
        which is the one step which cannot be undone,
        so this is how to find out before getting there.

        Returns
        -------
        :
            The problems we found, one sentence each.

            Empty if we found none, which is not a promise that Zenodo agrees:
            Zenodo has the last word and its rules can change.

        Examples
        --------
        >>> for problem in Metadata(title="A title and nothing else").find_problems():
        ...     print(problem)
        No creators. Add at least one, e.g. with `Creator.person`.
        No `resource_type`. Set one, e.g. `"dataset"`.
        No `publication_date`. Set one, e.g. `"2026-07-28"`.
        No `publisher`. Zenodo requires one to register a DOI, e.g. `"Zenodo"`.
        """
        problems: list[str] = []

        if not self.title:
            problems.append("No `title`. Every record needs one.")

        if not self.creators:
            problems.append(
                "No creators. Add at least one, e.g. with `Creator.person`."
            )

        if not self.resource_type:
            problems.append('No `resource_type`. Set one, e.g. `"dataset"`.')

        if not self.publication_date:
            problems.append('No `publication_date`. Set one, e.g. `"2026-07-28"`.')

        elif not is_edtf_date(self.publication_date):
            problems.append(
                f"`publication_date` is {self.publication_date!r}, "
                "which Zenodo cannot read as a date. "
                "It takes `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, "
                "or two of those separated by a `/`. "
                "Zenodo discards a date it cannot read rather than refusing it, "
                "so this would go missing without a word."
            )

        for i, date in enumerate(self.dates):
            if not is_edtf_date(date.date):
                problems.append(
                    f"`dates[{i}]` is {date.date!r}, "
                    "which Zenodo cannot read as a date."
                )

        if not self.publisher:
            problems.append(
                "No `publisher`. "
                'Zenodo requires one to register a DOI, e.g. `"Zenodo"`.'
            )

        return tuple(problems)

    def validate(self, *, description: str = "publish a record") -> None:
        """
        Check that Zenodo would accept this metadata, and say so if it would not

        Parameters
        ----------
        description
            Description of what the metadata was going to be used for,
            used in the error message

        Raises
        ------
        MetadataValidationError
            We found at least one problem.

            Every problem we found is in the message,
            rather than one per round trip.
        """
        problems = self.find_problems()
        if problems:
            raise MetadataValidationError(problems, description=description)

    def find_unknown_vocabulary_values(self) -> tuple[str, ...]:
        """
        Find vocabulary values we do not recognise

        Zenodo's vocabularies are longer than the lists we keep and they change,
        so anything found here is worth **warning** about rather than refusing:
        it is as likely to mean our list is out of date as that the metadata is
        wrong. The one thing it catches reliably is a typo, and a
        DataCite-spelled relation type (`isSupplementTo` rather than
        `issupplementto`), which Zenodo does not take.

        Returns
        -------
        :
            One report per value we did not recognise.

            Each names where the value is, the closest thing we do know of if
            there is one, and everything we know of for that field, so that a
            typo can be fixed from the message without going looking.

            Empty if we recognised everything.

        Examples
        --------
        >>> metadata = Metadata(
        ...     related_identifiers=(
        ...         RelatedIdentifier(
        ...             identifier="10.1234/5678",
        ...             scheme="doi",
        ...             relation_type="isSupplementTo",
        ...         ),
        ...     ),
        ... )
        >>> (unknown,) = metadata.find_unknown_vocabulary_values()
        >>> print(unknown.split("Values we know of")[0].strip())
        `related_identifiers[0].relation_type` is 'isSupplementTo', which is not one we know of. Did you mean 'issupplementto'?

        The rest of each report is the vocabulary in full, see
        [`describe_unknown_vocabulary_value`][openscm_zenodo.metadata.describe_unknown_vocabulary_value].

        >>> "'issupplementto', 'isvariantformof'" in unknown
        True
        """  # noqa: E501
        checks: list[tuple[str, str | None, tuple[str, ...], str]] = [
            (
                "resource_type",
                self.resource_type,
                KNOWN_RESOURCE_TYPES,
                "KNOWN_RESOURCE_TYPES",
            )
        ]

        for label, entries in (
            ("creators", self.creators),
            ("contributors", self.contributors),
        ):
            for i, entry in enumerate(entries):
                for j, identifier in enumerate(entry.entity.identifiers):
                    checks.append(
                        (
                            f"{label}[{i}].identifiers[{j}].scheme",
                            identifier.scheme,
                            KNOWN_IDENTIFIER_SCHEMES,
                            "KNOWN_IDENTIFIER_SCHEMES",
                        )
                    )

        for i, related in enumerate(self.related_identifiers):
            checks.extend(
                (
                    (
                        f"related_identifiers[{i}].scheme",
                        related.scheme,
                        KNOWN_IDENTIFIER_SCHEMES,
                        "KNOWN_IDENTIFIER_SCHEMES",
                    ),
                    (
                        f"related_identifiers[{i}].relation_type",
                        related.relation_type,
                        KNOWN_RELATION_TYPES,
                        "KNOWN_RELATION_TYPES",
                    ),
                    (
                        f"related_identifiers[{i}].resource_type",
                        related.resource_type,
                        KNOWN_RESOURCE_TYPES,
                        "KNOWN_RESOURCE_TYPES",
                    ),
                )
            )

        return tuple(
            describe_unknown_vocabulary_value(
                value, where=where, known=known, known_name=name
            )
            for where, value, known, name in checks
            if value is not None and value not in known
        )


def describe_unknown_vocabulary_value(
    value: str, *, where: str, known: Collection[str], known_name: str
) -> str:
    """
    Describe a vocabulary value we do not know of, and what to do about it

    Parameters
    ----------
    value
        The value we do not know of

    where
        Where in the metadata it is, e.g. `"related_identifiers[0].scheme"`

    known
        The values we do know of for this field

    known_name
        Name of the constant `known` came from, so the message can point at it

    Returns
    -------
    :
        The description, naming the closest value we know of if there is one

    Examples
    --------
    >>> print(
    ...     describe_unknown_vocabulary_value(
    ...         "datset",
    ...         where="resource_type",
    ...         known=("dataset", "image"),
    ...         known_name="KNOWN_RESOURCE_TYPES",
    ...     )
    ... )
    `resource_type` is 'datset', which is not one we know of. Did you mean 'dataset'? Values we know of, from `openscm_zenodo.metadata.KNOWN_RESOURCE_TYPES`: 'dataset', 'image'.

    Nothing is suggested when nothing is close.

    >>> print(
    ...     describe_unknown_vocabulary_value(
    ...         "pamphlet",
    ...         where="resource_type",
    ...         known=("dataset", "image"),
    ...         known_name="KNOWN_RESOURCE_TYPES",
    ...     )
    ... )
    `resource_type` is 'pamphlet', which is not one we know of. Values we know of, from `openscm_zenodo.metadata.KNOWN_RESOURCE_TYPES`: 'dataset', 'image'.
    """  # noqa: E501
    res = f"`{where}` is {value!r}, which is not one we know of. "

    # Matched against the lower-cased value, because these vocabularies are all
    # lower case and a value which differs only in case is the most common miss:
    # `isSupplementTo` is DataCite's spelling of `issupplementto`.
    suggestions = difflib.get_close_matches(value.lower(), known, n=1)
    if suggestions:
        res += f"Did you mean {suggestions[0]!r}? "

    known_formatted = ", ".join(repr(known_value) for known_value in sorted(known))
    res += (
        f"Values we know of, from `openscm_zenodo.metadata.{known_name}`: "
        f"{known_formatted}."
    )

    return res


def find_discarded_metadata(
    *, sent: Mapping[str, Any], got: Metadata
) -> tuple[str, ...]:
    """
    Find metadata Zenodo took and then threw away

    Zenodo does not refuse a value it cannot parse — it accepts the request with
    a `200` and stores nothing, so the field goes missing without a word.
    Comparing what came back with what was sent is the only way to notice.

    Parameters
    ----------
    sent
        The metadata which was sent

    got
        The metadata Zenodo sent back

    Returns
    -------
    :
        Keys which went out and did not come back, sorted

    Examples
    --------
    >>> find_discarded_metadata(
    ...     sent={"title": "A title", "publication_date": "the third of March"},
    ...     got=Metadata(title="A title"),
    ... )
    ('publication_date',)
    """
    returned = got.to_json()

    return tuple(sorted(key for key in sent if key not in returned))


def get_metadata_modelled_keys() -> tuple[str, ...]:
    """
    Get the keys which [`Metadata`][openscm_zenodo.metadata.Metadata] models

    These are its fields, minus `raw`, which is where everything else goes.
    Reading them off the class means a field cannot be added
    without `raw` and `to_json` learning about it at the same time.

    Returns
    -------
    :
        The keys we model

    Examples
    --------
    >>> keys = get_metadata_modelled_keys()
    >>> "creators" in keys, "raw" in keys
    (True, False)
    """
    return tuple(f.name for f in fields(Metadata) if f.name != "raw")


def _as_affiliations(
    affiliations: Iterable[str | Affiliation],
) -> tuple[Affiliation, ...]:
    """
    Convert affiliations given as names into `Affiliation`s

    Parameters
    ----------
    affiliations
        Affiliations, as names or as `Affiliation`s

    Returns
    -------
    :
        The affiliations
    """
    return tuple(Affiliation(name=v) if isinstance(v, str) else v for v in affiliations)


def is_edtf_date(value: str) -> bool:
    """
    Could Zenodo read this as a date?

    Zenodo takes EDTF level 0: a year, a year and month, or a full date,
    optionally as an interval of two of those separated by a `/`.
    A plain `YYYY-MM-DD` check would reject `"2021"`, which Zenodo is happy with,
    and `datetime.date.fromisoformat` would accept `"20210309"`, which it is not.

    Parameters
    ----------
    value
        Value to check

    Returns
    -------
    :
        `True` if Zenodo could read it

    Examples
    --------
    >>> [is_edtf_date(v) for v in ("2021", "2021-03", "2021-03-09", "2021-03/2021-04")]
    [True, True, True, True]
    >>> [is_edtf_date(v) for v in ("2021-13", "09-03-2021", "", "20210309")]
    [False, False, False, False]
    """
    parts = value.split("/")
    max_interval_parts = 2
    if len(parts) > max_interval_parts:
        return False

    return all(_is_edtf_single_date(part) for part in parts)


def _is_edtf_single_date(value: str) -> bool:
    """
    Could Zenodo read this as a single date, i.e. not an interval?

    The fields have to be checked for shape and for meaning, and the two are
    easy to conflate: `"2021-13"` is the right shape and not a real month, while
    `"2021-3"` is a real month written the wrong way and Zenodo will not take it.

    Parameters
    ----------
    value
        Value to check

    Returns
    -------
    :
        `True` if Zenodo could read it
    """
    fields = value.split("-")
    widths = (4, 2, 2)

    if len(fields) > len(widths):
        return False

    for field_value, width in zip(fields, widths):
        # `isdigit` is true for digits we cannot use, e.g. "²" and "٢",
        # and `int` would accept surrounding whitespace and a leading sign,
        # so we do this instead.
        if len(field_value) != width or not field_value.isascii():
            return False

        if not field_value.isdigit():
            return False

    numbers = [int(field_value) for field_value in fields]

    # `datetime.date` needs all three, and the shorter EDTF forms do not give
    # them: `"2021"` and `"2021-03"` stand for a whole year and a whole month.
    # Which day we pick does not matter, only that the month and day we hand
    # over are ones a calendar has, so the first of the month it is.
    year = numbers[0]
    month = numbers[1] if len(numbers) > 1 else 1
    day = numbers[2] if len(numbers) > 2 else 1  # noqa: PLR2004

    try:
        dt.date(year, month, day)

    except ValueError:
        # Couldn't parse the date so must be e.g. a real month with an impossible day
        # or a month of 0 or 13
        return False

    return True


def check_not_legacy(raw: Mapping[str, Any]) -> None:
    """
    Check that metadata is not written against the legacy deposit API's schema

    The legacy schema and the InvenioRDM one share enough key names
    (`title`, `description`, `version`, `publication_date`)
    that metadata written for the old API looks plausible until Zenodo sees it,
    at which point the failure is about the fields we did *not* send.
    Saying so here, naming the key which gave it away, is much more useful.

    Parameters
    ----------
    raw
        Metadata to check

    Raises
    ------
    MetadataValidationError
        The metadata uses at least one key
        which only exists in the legacy schema

    Examples
    --------
    >>> check_not_legacy({"title": "Fine", "upload_type": "dataset"})
    Traceback (most recent call last):
        ...
    openscm_zenodo.exceptions.MetadataValidationError: This metadata cannot be used to talk to Zenodo: it is written against the legacy deposit API's schema, which Zenodo no longer accepts. Problems we found:
    - `upload_type` is now `resource_type`, and it takes a vocabulary ID, e.g. `{"id": "dataset"}`
    """  # noqa: E501
    problems = [_LEGACY_KEYS[key] for key in _LEGACY_KEYS if key in raw]

    if problems:
        raise MetadataValidationError(
            problems,
            description=(
                "talk to Zenodo: it is written against "
                "the legacy deposit API's schema, "
                "which Zenodo no longer accepts"
            ),
        )
