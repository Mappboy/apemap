"""Frozen John Paul College case permits independent member resolutions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from apemap.education_context import CONTEXT_FIELDS
from apemap.ingest.matching import SchoolMatcher
from apemap.review.integration import Records, _project
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    school_review_id,
    validate_events,
)


def test_john_paul_college_split_and_research_preserve_each_attendance(
    tmp_path: Path,
) -> None:
    name = "John Paul College"
    assert school_review_id(name) == "school:0477b661fc755dd3"
    (tmp_path / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Suburb\n"
        "45994,John Paul College,Catholic,Secondary,VIC,Frankston\n"
        "48982,John Paul College,Catholic,Secondary,WA,Kalgoorlie\n",
        encoding="utf-8",
    )
    matcher = SchoolMatcher(external_dir=tmp_path)
    sources: Records = {
        "members": [
            {"member_id": f"aph-{aph}", "aph_id": aph} for aph in ("297964", "298800")
        ],
        "institutions": [
            {"institution_id": "acara-45994", "acara_id": "45994", "school_name": name}
        ],
        "parliament_service": [],
        "member_education": [
            {
                "education_id": f"edu-{aph}-0477b661fc755dd3",
                "member_id": f"aph-{aph}",
                "institution_id": "acara-45994",
                "level": "secondary",
                "school_name_as_recorded": name,
                "attended_status": "attended_unspecified",
                "years_attended": None,
                "graduation_year": None,
                "source_url": f"https://example.org/frozen-aph/{aph}",
                "retrieved_at": "2026-10-02T10:00:00+10:00",
                "confidence": "verified",
                "reviewer_notes": "Frozen source attendance assertion",
                "evidence_origin": "aph",
                "institution_resolution": "direct",
                "resolution_source_url": None,
                **dict.fromkeys(CONTEXT_FIELDS),
                "recorded_school_id": school_review_id(name),
            }
            for aph in ("297964", "298800")
        ],
    }
    preserved = deepcopy(sources)
    events = [
        ReviewEvent(
            decision_id=f"map-{aph}",
            review_id=education_review_id(aph, name),
            entity_type="member_education",
            action="map",
            payload={
                "aph_id": aph,
                "recorded_school_name": name,
                "institution_ref": f"acara:{target}",
                "relationship_type": "direct",
            },
            source_url=f"https://example.org/frozen-identity/{aph}",
            reviewer="Fixture reviewer",
            reviewed_at="2026-10-09",
            recorded_at="2026-10-09T00:00:00+00:00",
        )
        for aph, target in (("297964", "45994"), ("298800", "48982"))
    ]
    validate_events(events, acara_ids={"45994", "48982"})
    split = _project(sources, events, matcher, set(), {})
    assert {
        row["member_id"]: row["institution_id"] for row in split["member_education"]
    } == {"aph-297964": "acara-45994", "aph-298800": "acara-48982"}
    research = replace(
        events[0],
        decision_id="research-collins",
        action="supersede",
        replacement_action="research",
        supersedes=[events[0].decision_id],
        payload={
            "aph_id": "297964",
            "recorded_school_name": name,
            "resolution_only": True,
        },
        source_url="",
        notes="Institution identity remains unresolved",
    )
    validate_events(events + [research], acara_ids={"45994", "48982"})
    unresolved = _project(sources, [research, *reversed(events)], matcher, set(), {})
    by_member = {row["member_id"]: row for row in unresolved["member_education"]}
    assert by_member["aph-297964"]["institution_resolution"] == "unresolved"
    assert by_member["aph-298800"]["institution_id"] == "acara-48982"
    mutable = {
        "institution_id",
        "institution_resolution",
        "resolution_source_url",
        *CONTEXT_FIELDS,
    }
    for result in (split, unresolved):
        rows = {row["education_id"]: row for row in result["member_education"]}
        for old in sources["member_education"]:
            current = rows[old["education_id"]]
            assert {key: value for key, value in old.items() if key not in mutable} == {
                key: value for key, value in current.items() if key not in mutable
            }
    assert sources == preserved
