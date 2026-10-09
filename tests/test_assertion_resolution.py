"""Member-specific institution decisions preserve their independent attendance facts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from apemap.education_context import CONTEXT_FIELDS
from apemap.ingest.matching import SchoolMatcher
from apemap.review.integration import Records, _project
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    parse_events,
    school_review_id,
    validate_event,
    validate_events,
)
from apemap.review.store import encode_event

pytestmark = pytest.mark.unit


@pytest.fixture
def assertion_matcher(tmp_path: Path) -> SchoolMatcher:
    (tmp_path / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n"
        "1,First High School,Government,Secondary,TAS,-42,147\n"
        "2,Second High School,Catholic,Secondary,VIC,-38,145\n"
        "3,Third High School,Independent,Secondary,NSW,-33,151\n",
        encoding="utf-8",
    )
    return SchoolMatcher(external_dir=tmp_path)


@pytest.fixture
def assertion_sources() -> Records:
    return {
        "members": [
            {"member_id": f"member-{name}", "aph_id": f"APH-{name.upper()}"}
            for name in ("one", "two", "three")
        ],
        "institutions": [
            {
                "institution_id": "acara-1",
                "acara_id": "1",
                "school_name": "First High School",
                "sector": "Government",
            }
        ],
        "parliament_service": [],
        "member_education": [
            {
                "education_id": f"source-{name}",
                "member_id": f"member-{name}",
                "institution_id": "acara-1",
                "school_name_as_recorded": "St. Central College",
                "level": "secondary",
                "attended_status": "graduated",
                "years_attended": "1980-1985",
                "graduation_year": 1985,
                "source_url": f"https://example.org/aph/{name}",
                "retrieved_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
                "confidence": "provisional",
                "reviewer_notes": "Source attendance evidence",
                "institution_resolution": "direct",
                "resolution_source_url": None,
                "evidence_origin": "aph",
                **dict.fromkeys(CONTEXT_FIELDS),
                "recorded_school_id": school_review_id("St. Central College"),
            }
            for name in ("one", "two", "three")
        ],
    }


def mapping(
    member: str = "one", target: str = "2", relationship: str = "direct", **extra: Any
) -> ReviewEvent:
    payload = {
        "aph_id": f"APH-{member.upper()}",
        "recorded_school_name": "Saint Central College",
        "institution_ref": f"acara:{target}",
        "relationship_type": relationship,
        **extra,
    }
    return ReviewEvent(
        decision_id=f"map-{member}",
        review_id=education_review_id(
            payload["aph_id"], payload["recorded_school_name"]
        ),
        entity_type="member_education",
        action="map",
        payload=payload,
        source_url=f"https://example.org/identity/{member}",
        reviewer="Fixture researcher",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
    )


def school_default(**extra: Any) -> ReviewEvent:
    return replace(
        mapping(),
        decision_id="school-default",
        review_id=school_review_id("St. Central College"),
        entity_type="school",
        payload={
            "recorded_name": "St. Central College",
            "institution_ref": "acara:1",
            "relationship_type": "direct",
            **extra,
        },
    )


def project(
    sources: Records, events: list[ReviewEvent], matcher: SchoolMatcher
) -> Records:
    validate_events(events, acara_ids={"1", "2", "3"})
    return _project(sources, events, matcher, set(), {})


def test_same_recorded_name_splits_by_member_and_preserves_all_source_facts(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    default = school_default()
    events = [default, mapping(), mapping("two", "3", "rename")]
    projected = project(assertion_sources, events, assertion_matcher)
    rows = projected["member_education"]
    assert [row["institution_id"] for row in rows] == ["acara-2", "acara-1", "acara-3"]
    by_id = {row["education_id"]: row for row in rows}
    mutable = {
        "institution_id",
        "institution_resolution",
        "resolution_source_url",
        *CONTEXT_FIELDS,
    }
    for source in assertion_sources["member_education"]:
        if source["education_id"] == "source-three":
            continue
        actual = by_id[source["education_id"]]
        assert {
            name: value for name, value in actual.items() if name not in mutable
        } == {name: value for name, value in source.items() if name not in mutable}
        assert actual["school_name_as_recorded"] == "St. Central College"
        assert actual["historical_context_scope"] == "assertion"
    baseline = project(assertion_sources, [default], assertion_matcher)
    assert by_id["source-three"] == next(
        row
        for row in baseline["member_education"]
        if row["education_id"] == "source-three"
    )
    assert assertion_sources["member_education"][0]["institution_id"] == "acara-1"


@pytest.mark.parametrize("relationship", ["direct", "alias", "rename", "successor"])
def test_resolution_relationships_do_not_promote_attendance_confidence(
    assertion_sources: Records, assertion_matcher: SchoolMatcher, relationship: str
) -> None:
    decision = mapping(relationship=relationship)
    row = project(assertion_sources, [decision], assertion_matcher)["member_education"][
        0
    ]
    assert row["institution_resolution"] == relationship
    assert row["resolution_source_url"] == decision.source_url
    assert row["confidence"] == "provisional"


def test_successor_context_is_assertion_scoped_without_school_wide_confirmation(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    decision = mapping(
        relationship="successor",
        attended_institution_ref="acara:1",
        attended_identity_source_url="https://example.org/original",
        historical_latitude=-41,
        historical_longitude=146,
        historical_location_source_url="https://example.org/original-campus",
        historical_broad_sector="Government",
        historical_broad_sector_source_url="https://example.org/sector",
    )
    rows = project(assertion_sources, [decision], assertion_matcher)["member_education"]
    assert rows[0]["attended_institution_id"] == "acara-1"
    assert rows[0]["historical_scope_confirmed"] is True
    assert rows[0]["historical_context_scope"] == "assertion"
    assert rows[0]["historical_latitude"] == -41
    assert rows[1]["historical_latitude"] is None


@pytest.mark.parametrize("action", ["map", "research"])
def test_assertion_decisions_override_successor_default_and_clear_its_context(
    assertion_sources: Records, assertion_matcher: SchoolMatcher, action: str
) -> None:
    default = school_default(
        relationship_type="successor",
        institution_ref="acara:2",
        historical_scope_confirmed=True,
        attended_institution_ref="acara:1",
        attended_identity_source_url="https://example.org/general-history",
        historical_broad_sector="Government",
        historical_broad_sector_source_url="https://example.org/general-sector",
    )
    decision = replace(
        mapping(),
        action=action,
        payload={
            **mapping().payload,
            **({"resolution_only": True} if action == "research" else {}),
        },
        notes="Target requires member evidence",
    )
    row = project(assertion_sources, [default, decision], assertion_matcher)[
        "member_education"
    ][0]
    assert row["historical_broad_sector"] is None
    assert row["attended_institution_id"] is None
    assert row["historical_scope_confirmed"] is None
    assert row["confidence"] == "provisional"
    assert row["source_url"] == assertion_sources["member_education"][0]["source_url"]
    if action == "research":
        assert row["institution_id"].startswith("inst-unmatched-reviewed-")
        assert row["institution_resolution"] == "unresolved"
        assert row["resolution_source_url"] is None
    else:
        assert row["institution_id"] == "acara-2"
        assert row["institution_resolution"] == "direct"


@pytest.mark.parametrize(
    "recorded",
    ["First High School", "Firzt High School", "Saint Central College"],
)
def test_research_blocks_exact_fuzzy_and_alias_matching_for_only_the_named_member(
    assertion_sources: Records, assertion_matcher: SchoolMatcher, recorded: str
) -> None:
    if recorded == "Saint Central College":
        assertion_matcher.aliases[recorded.lower()] = {
            "canonical_acara_id": "2",
            "source_url": "https://example.org/legacy-alias",
        }
    for source in assertion_sources["member_education"]:
        source["school_name_as_recorded"] = recorded
        source["recorded_school_id"] = school_review_id(recorded)
    decision = replace(
        mapping(),
        action="research",
        review_id=education_review_id("APH-ONE", recorded),
        payload={
            "aph_id": "APH-ONE",
            "recorded_school_name": recorded,
            "resolution_only": True,
        },
        notes="Disambiguate the exact register name using attendance evidence",
    )
    projected = project(assertion_sources, [decision], assertion_matcher)
    assert projected["member_education"][0]["institution_resolution"] == "unresolved"
    assert projected["member_education"][1:] == sorted(
        assertion_sources["member_education"][1:], key=lambda row: row["education_id"]
    )
    assert assertion_matcher.match(recorded).acara_id is not None


def test_supersession_replay_is_independent_of_jsonl_order(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    first = mapping(
        relationship="successor",
        historical_latitude=-41,
        historical_longitude=146,
        historical_location_source_url="https://example.org/former-campus",
    )
    research = replace(
        first,
        decision_id="research-one",
        action="supersede",
        replacement_action="research",
        supersedes=[first.decision_id],
        payload={
            "aph_id": "APH-ONE",
            "recorded_school_name": "Saint Central College",
            "resolution_only": True,
        },
        notes="Identity remains unresolved",
    )
    final = replace(
        mapping(target="3", relationship="alias"),
        decision_id="final-one",
        action="supersede",
        replacement_action="map",
        supersedes=[research.decision_id],
    )
    expected = project(assertion_sources, [first, research, final], assertion_matcher)
    for ordered in permutations([first, research, final]):
        replayed = parse_events(b"".join(encode_event(item) for item in ordered))
        assert project(assertion_sources, replayed, assertion_matcher) == expected
    assert expected["member_education"][0]["education_id"] == "source-one"
    assert expected["member_education"][0]["institution_id"] == "acara-3"
    assert expected["member_education"][0]["historical_latitude"] is None


@pytest.mark.parametrize("replacement", ["map", "research"])
@pytest.mark.parametrize("with_source", [False, True])
def test_resolution_supersession_retains_manually_added_attendance(
    assertion_sources: Records,
    assertion_matcher: SchoolMatcher,
    replacement: str,
    with_source: bool,
) -> None:
    if not with_source:
        assertion_sources["member_education"] = []
    accepted = replace(
        mapping(),
        action="accept",
        payload={
            **mapping().payload,
            "attended_status": "attended_unspecified",
            "confidence": "provisional",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
            "years_attended": "1980-1984",
        },
        notes="Manual attendance evidence",
    )
    correction = replace(
        mapping(target="3"),
        decision_id="correction",
        action="supersede",
        replacement_action=replacement,
        supersedes=[accepted.decision_id],
        payload={
            **mapping(target="3").payload,
            **({"resolution_only": True} if replacement == "research" else {}),
        },
        notes="Review the institution identity",
    )
    before = project(assertion_sources, [accepted], assertion_matcher)[
        "member_education"
    ][0]
    after = project(assertion_sources, [correction, accepted], assertion_matcher)[
        "member_education"
    ][0]
    for name in (
        "education_id",
        "years_attended",
        "source_url",
        "retrieved_at",
        "reviewer_notes",
        "evidence_origin",
        "confidence",
        "attended_status",
    ):
        assert after[name] == before[name]
    assert after["institution_resolution"] == (
        "direct" if replacement == "map" else "unresolved"
    )


def test_mapping_requires_an_existing_attendance_claim(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    assertion_sources["member_education"] = []
    with pytest.raises(ValueError, match="requires an existing attendance assertion"):
        project(assertion_sources, [mapping()], assertion_matcher)


def test_legacy_accept_preserves_the_previous_school_overlay_institution_footprint(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    default = school_default(institution_ref="acara:2", relationship_type="successor")
    accepted = replace(
        mapping(target="1"),
        action="accept",
        payload={
            "aph_id": "APH-ONE",
            "recorded_school_name": "Saint Central College",
            "institution_ref": "acara:1",
            "attended_status": "attended_unspecified",
            "confidence": "provisional",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        },
    )
    # Isolate the legacy accept: the default target survives even after this
    # attendance row is replaced, matching the schema-1 institution footprint.
    assertion_sources["member_education"] = assertion_sources["member_education"][:1]
    projected = project(assertion_sources, [default, accepted], assertion_matcher)
    assert {row["institution_id"] for row in projected["institutions"]} == {
        "acara-1",
        "acara-2",
    }
    row = projected["member_education"][0]
    assert row["institution_id"] == "acara-1"
    assert row["institution_resolution"] == "direct"
    assert row["resolution_source_url"] is None


def test_resolution_map_cannot_arbitrate_conflicting_attendance_ancestors(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    first = replace(
        mapping(),
        action="accept",
        payload={
            **mapping().payload,
            "attended_status": "attended_unspecified",
            "confidence": "provisional",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
            "years_attended": "1980-1984",
        },
    )
    other = replace(
        first,
        decision_id="other-claim",
        payload={**first.payload, "years_attended": "1981-1985"},
    )
    resolution = replace(
        mapping(),
        decision_id="resolve-conflict",
        action="supersede",
        replacement_action="map",
        supersedes=[first.decision_id, other.decision_id],
    )
    with pytest.raises(ValueError, match="conflicting attendance facts"):
        project(assertion_sources, [other, resolution, first], assertion_matcher)


@pytest.mark.parametrize("reviewed_sorts_first", [False, True])
def test_resolution_merge_cannot_choose_between_legacy_confidence_bases(
    assertion_sources: Records,
    assertion_matcher: SchoolMatcher,
    reviewed_sorts_first: bool,
) -> None:
    attendance = {
        "aph_id": "APH-ONE",
        "recorded_school_name": "Saint Central College",
        "attended_status": "attended_unspecified",
        "confidence": "verified",
        "retrieved_at": "2026-10-01T00:00:00+00:00",
    }
    automatic = replace(
        mapping(),
        decision_id="z-automatic" if reviewed_sorts_first else "a-automatic",
        action="accept",
        payload=attendance,
    )
    reviewed = replace(
        automatic,
        decision_id="a-reviewed" if reviewed_sorts_first else "z-reviewed",
        payload={**attendance, "institution_ref": "acara:2"},
    )
    assert (
        project(assertion_sources, [automatic], assertion_matcher)["member_education"][
            0
        ]["confidence"]
        == "unconfirmed"
    )
    assert (
        project(assertion_sources, [reviewed], assertion_matcher)["member_education"][
            0
        ]["confidence"]
        == "verified"
    )
    resolution = replace(
        mapping(),
        decision_id="resolve-confidence-conflict",
        action="supersede",
        replacement_action="map",
        supersedes=[automatic.decision_id, reviewed.decision_id],
    )
    with pytest.raises(ValueError, match="conflicting attendance facts"):
        project(assertion_sources, [reviewed, resolution, automatic], assertion_matcher)


def test_resolution_merge_can_change_reviewed_targets_without_changing_attendance(
    assertion_sources: Records, assertion_matcher: SchoolMatcher
) -> None:
    accepted = replace(
        mapping(),
        action="accept",
        payload={
            **mapping().payload,
            "attended_status": "attended_unspecified",
            "confidence": "provisional",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        },
    )
    other = replace(
        accepted,
        decision_id="other-reviewed-target",
        payload={**accepted.payload, "institution_ref": "acara:1"},
    )
    resolution = replace(
        mapping(target="3"),
        decision_id="resolve-reviewed-targets",
        action="supersede",
        replacement_action="map",
        supersedes=[accepted.decision_id, other.decision_id],
    )
    after = project(
        assertion_sources, [other, resolution, accepted], assertion_matcher
    )["member_education"][0]
    assert after["confidence"] == "provisional"
    assert after["institution_id"] == "acara-3"
    assert after["source_url"] == accepted.source_url


@pytest.mark.parametrize("withdrawal", ["reject", "research"])
def test_resolution_map_does_not_resurrect_a_withdrawn_manual_attendance_claim(
    assertion_sources: Records,
    assertion_matcher: SchoolMatcher,
    withdrawal: str,
) -> None:
    assertion_sources["member_education"] = []
    accepted = replace(
        mapping(),
        action="accept",
        payload={
            **mapping().payload,
            "attended_status": "attended_unspecified",
            "confidence": "provisional",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        },
    )
    rejection = replace(
        mapping(),
        decision_id="reject-claim",
        action="supersede",
        replacement_action=withdrawal,
        supersedes=[accepted.decision_id],
        notes="Attendance was disproved",
    )
    resolution = replace(
        mapping(),
        decision_id="change-identity",
        action="supersede",
        replacement_action="map",
        supersedes=[rejection.decision_id],
    )
    with pytest.raises(ValueError, match="requires an existing attendance assertion"):
        project(assertion_sources, [resolution, accepted, rejection], assertion_matcher)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"recorded_school_name": ""}, "nonempty"),
        ({"institution_ref": ""}, "nonempty"),
        ({"relationship_type": "uncertain"}, "relationship_type"),
        (
            {"historical_latitude": -41, "relationship_type": "successor"},
            "both historical",
        ),
        ({"historical_broad_sector": "Government"}, "successor relationship"),
        ({"attended_status": "graduated"}, "cannot change attendance"),
    ],
)
def test_assertion_map_validates_identity_relationship_and_read_only_claim_fields(
    changes: dict[str, Any], match: str
) -> None:
    decision = mapping(**changes)
    decision = replace(
        decision,
        review_id=education_review_id(
            decision.payload["aph_id"], decision.payload["recorded_school_name"]
        ),
    )
    with pytest.raises(ValueError, match=match):
        validate_event(decision)


def test_assertion_map_requires_relationship_source() -> None:
    with pytest.raises(ValueError, match="source_url must be a nonempty string"):
        validate_event(replace(mapping(), source_url=""))


@pytest.mark.parametrize("flag", ["true", 1, None, {}, []])
def test_resolution_only_research_marker_requires_a_boolean(flag: Any) -> None:
    with pytest.raises(ValueError, match="resolution_only must be a boolean"):
        validate_event(
            replace(
                mapping(resolution_only=flag),
                action="research",
                notes="Resolve the institution only",
            )
        )


@pytest.mark.parametrize("action", ["accept", "map", "reject"])
def test_resolution_only_marker_is_reserved_for_education_research(action: str) -> None:
    with pytest.raises(ValueError, match="only valid for education research"):
        validate_event(
            replace(mapping(resolution_only=True), action=action, notes="Fixture note")
        )


def test_resolution_only_research_requires_a_named_attendance_claim() -> None:
    with pytest.raises(ValueError, match="recorded_school_name must be a nonempty"):
        validate_event(
            replace(
                mapping(recorded_school_name="", resolution_only=True),
                review_id=education_review_id("APH-ONE", ""),
                action="research",
                notes="No recorded school name",
            )
        )


@pytest.mark.parametrize("flag", ["true", "false", 1, 0, {}, []])
def test_scoped_historical_confirmation_rejects_non_boolean_values(flag: Any) -> None:
    with pytest.raises(
        ValueError, match="historical_scope_confirmed must be a boolean"
    ):
        validate_event(
            mapping(relationship="successor", historical_scope_confirmed=flag)
        )


@pytest.mark.parametrize("flag", [None, False, True])
def test_scoped_historical_confirmation_allows_optional_boolean_values(
    flag: Any,
) -> None:
    validate_event(mapping(relationship="successor", historical_scope_confirmed=flag))
