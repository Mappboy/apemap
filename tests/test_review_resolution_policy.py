"""Typed unresolved school reasons require independent, sourced member resolution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from apemap.db import get_connection
from apemap.education_context import CONTEXT_FIELDS
from apemap.ingest.matching import SchoolMatcher
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.review.integration import Records, _project, configure_review_matcher
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    parse_events,
    school_review_id,
    validate_event,
    validate_events,
)
from apemap.review.resolution import (
    requires_individual_resolution,
    resolution_summary,
)
from apemap.review.store import encode_event
from tests.test_historical_coverage import historical_member
from tests.test_review_ingestion import service_event

pytestmark = pytest.mark.unit


@pytest.fixture
def policy_matcher(tmp_path: Path) -> SchoolMatcher:
    (tmp_path / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n"
        "1,First High School,Government,Secondary,TAS,-42,147\n"
        "2,Second High School,Catholic,Secondary,VIC,-38,145\n"
        "3,Third High School,Independent,Secondary,NSW,-33,151\n",
        encoding="utf-8",
    )
    return SchoolMatcher(external_dir=tmp_path)


def school_policy(
    name: str = "St. Central College", reason: str = "ambiguous_name"
) -> ReviewEvent:
    return ReviewEvent(
        decision_id="school-policy",
        review_id=school_review_id(name),
        entity_type="school",
        action="research",
        payload={
            "recorded_name": name,
            "resolution_reason": reason,
            "requires_individual_resolution": True,
        },
        notes="Review each member's school identity separately",
        reviewer="Fixture researcher",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
    )


def school_default(name: str = "St. Central College") -> ReviewEvent:
    return replace(
        school_policy(name),
        decision_id="old-default",
        action="map",
        payload={
            "recorded_name": name,
            "institution_ref": "acara:1",
            "relationship_type": "successor",
            "historical_scope_confirmed": True,
            "historical_broad_sector": "Government",
            "historical_broad_sector_source_url": "https://example.org/history",
        },
        source_url="https://example.org/school-identity",
    )


def assertion(member: str = "one", action: str = "map") -> ReviewEvent:
    name = "Saint Central College"
    payload: dict[str, Any] = {"aph_id": member, "recorded_school_name": name}
    if action in {"accept", "map"}:
        payload.update(institution_ref="acara:2", relationship_type="direct")
    if action == "accept":
        payload.update(
            attended_status="graduated",
            confidence="verified",
            retrieved_at="2026-10-01T00:00:00+00:00",
            years_attended="1980-1985",
            graduation_year=1985,
        )
    return replace(
        school_policy(),
        decision_id=f"{action}-{member}",
        review_id=education_review_id(member, name),
        entity_type="member_education",
        action=action,
        payload=payload,
        source_url=f"https://example.org/attendance/{member}",
        notes="Independent attendance evidence",
    )


def sources(name: str = "St. Central College") -> Records:
    return {
        "members": [
            {"member_id": f"member-{member}", "aph_id": member}
            for member in ("one", "two", "three")
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
                "education_id": f"source-{member}",
                "member_id": f"member-{member}",
                "institution_id": "acara-1",
                "school_name_as_recorded": name,
                "level": "secondary",
                "attended_status": "graduated",
                "years_attended": "1980-1985",
                "graduation_year": 1985,
                "source_url": f"https://example.org/source/{member}",
                "retrieved_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
                "confidence": confidence,
                "reviewer_notes": "Preserved source attendance evidence",
                "institution_resolution": "direct",
                "resolution_source_url": None,
                "evidence_origin": "aph",
                **dict.fromkeys(CONTEXT_FIELDS),
                "recorded_school_id": school_review_id(name),
            }
            for member, confidence in zip(
                ("one", "two", "three"),
                ("verified", "provisional", "unconfirmed"),
            )
        ],
    }


def project(
    base: Records, events: list[ReviewEvent], matcher: SchoolMatcher
) -> Records:
    validate_events(events, acara_ids={"1", "2", "3"})
    configure_review_matcher(matcher, events)
    return _project(base, events, matcher, set(), {})


def attendance_facts(row: dict[str, Any]) -> dict[str, Any]:
    resolution_fields = {
        "institution_id",
        "institution_resolution",
        "resolution_source_url",
        *CONTEXT_FIELDS,
    }
    return {key: value for key, value in row.items() if key not in resolution_fields}


@pytest.mark.parametrize("reason", ["ambiguous_name", "no_suitable_candidate"])
def test_reason_preserves_research_action_and_status(reason: str) -> None:
    policy = school_policy(reason=reason)
    validate_event(policy)
    assert requires_individual_resolution(policy)
    assert policy.status == "needs_research"
    assert policy.effective_action == "research"
    assert parse_events(encode_event(policy)) == [policy]
    assert not requires_individual_resolution(None)
    legacy = replace(policy, payload={"recorded_name": "St. Central College"})
    assert not requires_individual_resolution(legacy)
    assert not requires_individual_resolution(assertion())


@pytest.mark.parametrize("reason", ["uncertain", "", None, True, [], {}])
def test_invalid_resolution_reason_is_rejected(reason: Any) -> None:
    policy = school_policy()
    with pytest.raises(ValueError, match="resolution_reason"):
        validate_event(
            replace(policy, payload={**policy.payload, "resolution_reason": reason})
        )


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_typed_school_reason_requires_explicit_individual_policy(flag: Any) -> None:
    policy = school_policy()
    payload = {**policy.payload, "requires_individual_resolution": flag}
    if flag is None:
        payload.pop("requires_individual_resolution")
    with pytest.raises(ValueError, match="requires_individual_resolution"):
        validate_event(replace(policy, payload=payload))


def test_individual_policy_requires_reason_and_school_research_scope() -> None:
    policy = school_policy()
    with pytest.raises(ValueError, match="resolution_reason"):
        validate_event(
            replace(
                policy,
                payload={
                    "recorded_name": "St. Central College",
                    "requires_individual_resolution": True,
                },
            )
        )
    with pytest.raises(ValueError, match="only valid for school research"):
        validate_event(
            replace(
                assertion(action="reject"),
                payload={
                    **assertion(action="reject").payload,
                    "requires_individual_resolution": True,
                },
            )
        )
    with pytest.raises(ValueError, match="resolution_reason"):
        validate_event(replace(policy, action="reject"))


@pytest.mark.parametrize("flag", [None, "true", 1, [], {}])
def test_reasonless_individual_flag_still_requires_a_boolean(flag: Any) -> None:
    policy = school_policy()
    with pytest.raises(ValueError, match="must be a boolean"):
        validate_event(
            replace(
                policy,
                payload={
                    "recorded_name": "St. Central College",
                    "requires_individual_resolution": flag,
                },
            )
        )


def test_reasonless_false_flag_does_not_enable_new_policy() -> None:
    policy = school_policy()
    legacy = replace(
        policy,
        payload={
            "recorded_name": "St. Central College",
            "requires_individual_resolution": False,
        },
    )
    validate_event(legacy)
    assert not requires_individual_resolution(legacy)


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_typed_education_reason_requires_resolution_only(flag: Any) -> None:
    decision = assertion(action="research")
    payload = {**decision.payload, "resolution_reason": "no_suitable_candidate"}
    if flag is not None:
        payload["resolution_only"] = flag
    with pytest.raises(ValueError, match="resolution_only"):
        validate_event(replace(decision, payload=payload))


def test_typed_education_reason_requires_named_research() -> None:
    decision = assertion(action="research")
    payload = {
        **decision.payload,
        "resolution_only": True,
        "resolution_reason": "ambiguous_name",
    }
    validate_event(replace(decision, payload=payload))
    with pytest.raises(ValueError, match="nonempty"):
        validate_event(
            replace(
                decision,
                review_id=education_review_id("one", ""),
                payload={**payload, "recorded_school_name": ""},
            )
        )
    with pytest.raises(ValueError, match="resolution_reason"):
        validate_event(
            replace(
                assertion(),
                payload={**assertion().payload, "resolution_reason": "ambiguous_name"},
            )
        )


@pytest.mark.parametrize(
    "name", ["First High School", "Firzt High School", "St. Central College"]
)
@pytest.mark.parametrize("reason", ["ambiguous_name", "no_suitable_candidate"])
def test_policy_forces_exact_fuzzy_and_alias_matches_unresolved_without_fact_changes(
    policy_matcher: SchoolMatcher, name: str, reason: str
) -> None:
    if name == "St. Central College":
        policy_matcher.aliases[name.lower()] = {
            "canonical_acara_id": "1",
            "source_url": "https://example.org/alias",
        }
    baseline = sources(name)
    original = deepcopy(baseline)
    projected = project(baseline, [school_policy(name, reason)], policy_matcher)
    assert not policy_matcher.review_blocked_keys
    assert policy_matcher.match(name).acara_id == "1"
    institutions = {row["institution_id"]: row for row in projected["institutions"]}
    originals = {row["education_id"]: row for row in original["member_education"]}
    for new in projected["member_education"]:
        old = originals[new["education_id"]]
        assert attendance_facts(old) == attendance_facts(new)
        assert new["institution_resolution"] == "unresolved"
        assert new["resolution_source_url"] is None
        assert new["historical_context_scope"] == "assertion"
        assert institutions[new["institution_id"]]["acara_id"] is None
        assert institutions[new["institution_id"]]["latitude"] is None
    assert baseline == original


def test_policy_preserves_explicit_member_targets_and_other_school_names(
    policy_matcher: SchoolMatcher,
) -> None:
    baseline = sources()
    unrelated = deepcopy(baseline["member_education"][2])
    unrelated.update(
        education_id="unrelated",
        school_name_as_recorded="First High School",
        recorded_school_id=school_review_id("First High School"),
    )
    baseline["member_education"].append(unrelated)
    mapped = replace(
        assertion(),
        payload={
            **assertion().payload,
            "relationship_type": "successor",
            "historical_broad_sector": "Government",
            "historical_broad_sector_source_url": "https://example.org/member-history",
        },
    )
    accepted = assertion("two", "accept")
    # Existing explicit schema-1 accept without relationship_type remains a
    # reviewed individual target and defaults to direct under the new policy.
    accepted = replace(
        accepted,
        payload={
            key: value
            for key, value in accepted.payload.items()
            if key != "relationship_type"
        },
    )
    projected = project(baseline, [school_policy(), mapped, accepted], policy_matcher)
    by_id = {row["education_id"]: row for row in projected["member_education"]}
    assert by_id["source-one"]["institution_id"] == "acara-2"
    assert by_id["source-one"]["historical_broad_sector"] == "Government"
    assert by_id["source-one"]["confidence"] == "verified"
    reviewed = next(
        row
        for row in projected["member_education"]
        if row["evidence_origin"] == "manual"
    )
    assert reviewed["institution_id"] == "acara-2"
    assert reviewed["institution_resolution"] == "direct"
    assert by_id["source-three"]["institution_resolution"] == "unresolved"
    assert by_id["unrelated"] == unrelated


@pytest.mark.parametrize("replacement", [None, "map", "research"])
def test_policy_preserves_no_target_manual_attendance_from_old_default(
    policy_matcher: SchoolMatcher, replacement: str | None
) -> None:
    baseline = sources()
    baseline["member_education"] = []
    accepted = assertion(action="accept")
    accepted = replace(
        accepted,
        payload={
            key: value
            for key, value in accepted.payload.items()
            if key not in {"institution_ref", "relationship_type"}
        },
    )
    old = school_default()
    before = project(baseline, [accepted, old], policy_matcher)["member_education"][0]
    policy = replace(
        school_policy(),
        action="supersede",
        replacement_action="research",
        supersedes=[old.decision_id],
    )
    updated_policy = replace(
        policy,
        decision_id="updated-policy",
        supersedes=[policy.decision_id],
        payload={**policy.payload, "resolution_reason": "no_suitable_candidate"},
    )
    chain = [old, policy, accepted, updated_policy]
    if replacement:
        decision = assertion(action=replacement)
        payload = decision.payload
        if replacement == "research":
            payload = {
                **payload,
                "resolution_only": True,
                "resolution_reason": "no_suitable_candidate",
            }
        chain.append(
            replace(
                decision,
                decision_id="review-manual-resolution",
                action="supersede",
                replacement_action=replacement,
                supersedes=[accepted.decision_id],
                payload=payload,
            )
        )
    expected = project(baseline, chain, policy_matcher)
    after = expected["member_education"][0]
    assert attendance_facts(after) == attendance_facts(before)
    assert after["confidence"] == "verified"
    assert after["institution_resolution"] == (
        "direct" if replacement == "map" else "unresolved"
    )
    assert after["historical_broad_sector"] is None
    assert project(baseline, list(reversed(chain)), policy_matcher) == expected


def test_reviewed_default_can_supersede_policy_without_leaving_rows_unresolved(
    policy_matcher: SchoolMatcher,
) -> None:
    policy = school_policy()
    replacement = replace(
        school_default(),
        action="supersede",
        replacement_action="map",
        supersedes=[policy.decision_id],
    )
    projected = project(sources(), [replacement, policy], policy_matcher)
    assert not policy_matcher.review_blocked_keys
    assert all(
        row["institution_id"] == "acara-1"
        and row["institution_resolution"] == "successor"
        for row in projected["member_education"]
    )


def test_policy_cannot_revive_default_confidence_withdrawn_by_legacy_research(
    policy_matcher: SchoolMatcher,
) -> None:
    baseline = sources()
    accepted = assertion(action="accept")
    accepted = replace(
        accepted,
        payload={
            key: value
            for key, value in accepted.payload.items()
            if key not in {"institution_ref", "relationship_type"}
        },
    )
    old = school_default()
    legacy = replace(
        school_policy(),
        decision_id="legacy-withdrawal",
        payload={"recorded_name": "St. Central College"},
        action="supersede",
        replacement_action="research",
        supersedes=[old.decision_id],
    )
    policy = replace(
        school_policy(),
        action="supersede",
        replacement_action="research",
        supersedes=[legacy.decision_id],
    )
    before = project(baseline, [old, legacy, accepted], policy_matcher)[
        "member_education"
    ][0]
    after = project(baseline, [old, legacy, policy, accepted], policy_matcher)[
        "member_education"
    ][0]
    assert before["confidence"] == after["confidence"] == "unconfirmed"


def test_rejected_attendance_stays_removed_and_legacy_research_stays_withdrawn(
    policy_matcher: SchoolMatcher,
) -> None:
    accepted = assertion(action="accept")
    rejection = assertion(action="reject")
    rejection = replace(
        rejection,
        action="supersede",
        replacement_action="reject",
        supersedes=[accepted.decision_id],
    )
    projected = project(
        sources(), [school_policy(), accepted, rejection], policy_matcher
    )
    assert not any(
        row["member_id"] == "member-one" for row in projected["member_education"]
    )
    legacy = replace(
        assertion(action="research"),
        action="supersede",
        replacement_action="research",
        supersedes=[accepted.decision_id],
    )
    baseline = sources()
    baseline["member_education"] = []
    assert (
        project(baseline, [school_policy(), accepted, legacy], policy_matcher)[
            "member_education"
        ]
        == []
    )


@pytest.mark.parametrize("action", ["research", "reject"])
def test_untagged_school_replay_keeps_legacy_exact_match_and_fuzzy_blocking(
    policy_matcher: SchoolMatcher, action: str
) -> None:
    for name in ("First High School", "Firzt High School"):
        legacy = replace(
            school_policy(name), action=action, payload={"recorded_name": name}
        )
        projected = project(sources(name), [legacy], policy_matcher)
        assert policy_matcher.review_blocked_keys
        row = projected["member_education"][0]
        if name == "First High School":
            assert row == sources(name)["member_education"][0]
        else:
            assert row["institution_resolution"] == "unresolved"
            assert row["confidence"] == "unconfirmed"


def test_resolution_summary_reports_reason_without_overriding_explicit_target() -> None:
    policy = school_policy()
    summary = resolution_summary(
        None, policy, {"institution_ref": "acara:1", "relationship_type": "direct"}
    )
    assert summary["institution_ref"] is None
    assert summary["status"] == "needs_individual_review"
    assert summary["resolution_reason"] == "ambiguous_name"
    mapped = resolution_summary(assertion(), policy, {})
    assert mapped["institution_ref"] == "acara:2"
    assert "resolution_reason" not in mapped
    research = assertion(action="research")
    research = replace(
        research,
        payload={
            **research.payload,
            "resolution_only": True,
            "resolution_reason": "no_suitable_candidate",
        },
    )
    assert (
        resolution_summary(research, policy, {})["resolution_reason"]
        == "no_suitable_candidate"
    )


def test_bootstrap_and_retained_member_refresh_preserve_source_confidence(
    policy_matcher: SchoolMatcher, tmp_path: Path
) -> None:
    raw = historical_member()
    raw["RepresentedParliaments"] = [48]
    raw["SecondarySchool"] = "Firzt High School"
    expected_confidence = policy_matcher.match(raw["SecondarySchool"]).confidence
    assert expected_confidence != "unconfirmed"
    policy = school_policy(raw["SecondarySchool"])
    authority = [policy, service_event("bootstrap-service", 42)]
    log = tmp_path / "decisions.jsonl"
    log.write_bytes(b"".join(encode_event(event) for event in authority))
    with get_connection() as conn:
        for family in ("Original", "Refreshed"):
            raw["FamilyName"] = family
            run_aph_ingestion(
                [42],
                raw_individuals=[raw],
                conn=conn,
                external_dir=tmp_path,
                output_dir=tmp_path,
                decision_log_path=log,
            )
            source = conn.execute(
                "SELECT confidence, source_url, retrieved_at, reviewer_notes FROM review_source_member_education"
            ).fetchone()
            actual = conn.execute(
                "SELECT confidence, source_url, retrieved_at, reviewer_notes FROM member_education"
            ).fetchone()
            assert source is not None and actual == source
            assert source[0] == expected_confidence
            assert conn.execute(
                "SELECT institution_resolution FROM member_education"
            ).fetchone() == ("unresolved",)
            assert conn.execute(
                "SELECT institution_id FROM review_source_member_education"
            ).fetchone() == ("acara-1",)
            assert conn.execute("SELECT family_name FROM members").fetchone() == (
                family,
            )
