"""Offline legacy parity and assertion-scoped historical evidence regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apemap.db import init_schema
from apemap.education_context import CONTEXT_FIELDS
from apemap.ingest.matching import SchoolMatcher
from apemap.review.integration import _project, configure_review_matcher
from apemap.review.model import ReviewEvent, education_review_id, school_review_id
from apemap.review.service import relationship_conflicts
from apemap.validate import validate_database


def decision(
    entity: str, action: str, payload: dict[str, Any], identifier: str
) -> ReviewEvent:
    return ReviewEvent(
        decision_id=identifier,
        review_id=school_review_id(payload["recorded_name"])
        if entity == "school"
        else education_review_id(payload["aph_id"], payload["recorded_school_name"]),
        entity_type=entity,
        action=action,
        payload=payload,
        source_url="https://example.org/relationship",
        notes="Fixture reviewed evidence",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
        reviewer="Fixture reviewer",
    )


@pytest.fixture
def legacy_source(
    tmp_path: Path,
) -> tuple[dict[str, list[dict[str, Any]]], SchoolMatcher]:
    register = tmp_path / "register"
    register.mkdir()
    (register / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n"
        "1,Original School,Government,Secondary,TAS,-42,147\n"
        "2,Successor School,Independent,Secondary,VIC,-37,145\n",
        encoding="utf-8",
    )
    source = {
        "members": [
            {"member_id": "person", "aph_id": "PERSON", "wikidata_id": None},
        ],
        "parliament_service": [],
        "institutions": [
            {
                "institution_id": "acara-1",
                "school_name": "Original School",
                "sector": "Government",
                "acara_id": "1",
            },
        ],
        "member_education": [
            {
                "education_id": "source-education",
                "member_id": "person",
                "institution_id": "acara-1",
                "level": "secondary",
                "school_name_as_recorded": "Old School",
                "attended_status": "graduated",
                "years_attended": "1980–1985",
                "graduation_year": 1985,
                "confidence": "provisional",
                "source_url": "https://example.org/attendance",
                "retrieved_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
                "reviewer_notes": "Original attendance evidence",
                "evidence_origin": "aph",
                "institution_resolution": "direct",
                "resolution_source_url": None,
                **dict.fromkeys(CONTEXT_FIELDS),
                "recorded_school_id": school_review_id("Old School"),
            },
        ],
    }
    return source, SchoolMatcher(external_dir=register)


def replay(
    fixture: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
    events: list[ReviewEvent],
) -> dict[str, list[dict[str, Any]]]:
    source, matcher = fixture
    configure_review_matcher(matcher, events)
    return _project(source, events, matcher, set(), {})


def school_mapping(relationship: str = "successor") -> ReviewEvent:
    payload: dict[str, Any] = {
        "recorded_name": "Old School",
        "institution_ref": "acara:2",
        "relationship_type": relationship,
    }
    if relationship == "successor":
        payload.update(
            historical_scope_confirmed=True,
            historical_broad_sector="Government",
            historical_broad_sector_source_url="https://example.org/historical-sector",
        )
    return decision("school", "map", payload, "school-default")


def legacy_accept(reference: str | None = None) -> ReviewEvent:
    payload = {
        "aph_id": "PERSON",
        "recorded_school_name": "Old School",
        "attended_status": "attended_unspecified",
        "confidence": "verified",
        "retrieved_at": "2026-10-02T00:00:00+00:00",
    }
    if reference:
        payload["institution_ref"] = reference
    return decision("member_education", "accept", payload, "legacy-attendance")


@pytest.mark.unit
@pytest.mark.parametrize("relationship", ["direct", "alias", "rename", "successor"])
def test_existing_school_mapping_preserves_attendance_fact_parity(
    legacy_source: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
    relationship: str,
) -> None:
    source, _ = legacy_source
    before = dict(source["member_education"][0])
    school = school_mapping(relationship)
    result = replay(legacy_source, [school])
    actual = result["member_education"][0]
    for field in (
        "education_id",
        "member_id",
        "level",
        "school_name_as_recorded",
        "attended_status",
        "years_attended",
        "graduation_year",
        "source_url",
        "retrieved_at",
        "reviewer_notes",
        "evidence_origin",
        "recorded_school_id",
    ):
        assert actual[field] == before[field]
    assert actual["institution_id"] == "acara-2"
    assert actual["institution_resolution"] == relationship
    assert actual["resolution_source_url"] == school.source_url
    assert source["member_education"][0] == before


@pytest.mark.unit
@pytest.mark.parametrize("reference", [None, "acara:2"])
def test_legacy_accept_without_relationship_keeps_successor_default(
    legacy_source: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
    reference: str | None,
) -> None:
    school = school_mapping()
    attendance = legacy_accept(reference)
    result = replay(legacy_source, [school, attendance])
    actual = result["member_education"][0]
    assert len(result["member_education"]) == 1
    assert actual["institution_id"] == "acara-2"
    assert actual["institution_resolution"] == "successor"
    assert actual["resolution_source_url"] == school.source_url
    assert actual["historical_broad_sector"] == "Government"
    assert actual["source_url"] == attendance.source_url
    assert actual["attended_status"] == "attended_unspecified"
    assert actual["evidence_origin"] == "manual"


@pytest.mark.unit
def test_legacy_different_target_accept_remains_direct_and_isolated(
    legacy_source: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
) -> None:
    result = replay(legacy_source, [school_mapping(), legacy_accept("acara:1")])
    actual = result["member_education"][0]
    assert actual["institution_id"] == "acara-1"
    assert actual["institution_resolution"] == "direct"
    assert actual["resolution_source_url"] is None
    assert actual["historical_broad_sector"] is None
    assert actual["historical_context_scope"] == "assertion"
    # Legacy replay retained the default institution even when acceptance later
    # replaced its assignment. A new scoped map must not silently clean it up.
    assert {row["institution_id"] for row in result["institutions"]} == {
        "acara-1",
        "acara-2",
    }


@pytest.mark.unit
def test_explicit_new_accept_relationship_overrides_same_target_successor_context(
    legacy_source: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
) -> None:
    attendance = legacy_accept("acara:2")
    attendance = replace(
        attendance, payload={**attendance.payload, "relationship_type": "direct"}
    )
    actual = replay(legacy_source, [school_mapping(), attendance])["member_education"][
        0
    ]
    assert actual["institution_id"] == "acara-2"
    assert actual["institution_resolution"] == "direct"
    assert actual["historical_context_scope"] == "assertion"
    assert actual["historical_broad_sector"] is None
    assert actual["historical_scope_confirmed"] is None


@pytest.mark.unit
def test_named_assertion_research_preserves_source_attendance_and_rejection_removes_it(
    legacy_source: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
) -> None:
    research = decision(
        "member_education",
        "research",
        {
            "aph_id": "PERSON",
            "recorded_school_name": "Old School",
            "resolution_only": True,
        },
        "assertion-research",
    )
    actual = replay(legacy_source, [school_mapping(), research])["member_education"][0]
    source = legacy_source[0]["member_education"][0]
    for field in (
        "education_id",
        "attended_status",
        "years_attended",
        "graduation_year",
        "confidence",
        "source_url",
        "retrieved_at",
        "reviewer_notes",
        "evidence_origin",
    ):
        assert actual[field] == source[field]
    assert actual["institution_resolution"] == "unresolved"
    assert actual["historical_broad_sector"] is None
    assert actual["historical_context_scope"] == "assertion"
    rejected = replace(
        research,
        action="reject",
        decision_id="assertion-rejected",
        payload={
            key: value
            for key, value in research.payload.items()
            if key != "resolution_only"
        },
    )
    assert replay(legacy_source, [school_mapping(), rejected])["member_education"] == []


@pytest.mark.unit
def test_untagged_legacy_research_withdraws_manual_acceptance_and_restores_source(
    legacy_source: tuple[dict[str, list[dict[str, Any]]], SchoolMatcher],
) -> None:
    school, attendance = school_mapping(), legacy_accept("acara:2")
    withdrawn = decision(
        "member_education",
        "research",
        {"aph_id": "PERSON", "recorded_school_name": "Old School"},
        "legacy-withdrawal",
    )
    withdrawn = replace(
        withdrawn,
        action="supersede",
        replacement_action="research",
        supersedes=[attendance.decision_id],
    )
    # Before resolution-only research existed, withdrawing an acceptance
    # restored source attendance and its ordinary school-wide fallback.
    assert replay(legacy_source, [school, attendance, withdrawn]) == replay(
        legacy_source, [school]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "action,reference,relationship,kind",
    [
        ("map", "acara:1", "direct", "target_disagreement"),
        ("map", "acara:2", "direct", "relationship_disagreement"),
        ("research", None, None, "assertion_unresolved"),
        ("reject", None, None, "attendance_rejected"),
    ],
)
def test_split_diagnostics_are_deterministic_and_reference_both_review_items(
    action: str, reference: str | None, relationship: str | None, kind: str
) -> None:
    default = school_mapping()
    payload: dict[str, Any] = {
        "aph_id": "PERSON",
        "recorded_school_name": "Old School",
    }
    if reference:
        payload.update(institution_ref=reference, relationship_type=relationship)
    if action == "research":
        payload["resolution_only"] = True
    assertion = decision("member_education", action, payload, "scoped-relationship")
    conflicts = relationship_conflicts([default, assertion])
    assert conflicts == relationship_conflicts([assertion, default])
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["kind"] == kind
    assert conflict["review_id"] == assertion.review_id
    assert conflict["school_review_id"] == default.review_id
    assert conflict["institution_ref"] == reference
    assert conflict["default_institution_ref"] == "acara:2"
    assert conflict["relationship_type"] == relationship
    assert conflict["default_relationship_type"] == "successor"


@pytest.mark.unit
def test_compatible_legacy_fallback_does_not_report_a_relationship_split() -> None:
    assert relationship_conflicts([school_mapping(), legacy_accept("acara:2")]) == []


@pytest.fixture
def historical_db(db_conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    db_conn.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name) "
        "VALUES ('person', 'Person', 'One', 'One Person')"
    )
    db_conn.executemany(
        "INSERT INTO institutions (institution_id, school_name, sector, longitude, latitude) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("original", "Original School", "Other", 130, -20),
            ("successor", "Successor School", "Independent", 140, -30),
            ("other-successor", "Another Successor", "Government", 141, -31),
        ],
    )
    return db_conn


def add_historical_assertion(
    conn: duckdb.DuckDBPyConnection, identifier: str, **extra: Any
) -> None:
    fields = {
        "education_id": identifier,
        "member_id": "person",
        "institution_id": "successor",
        "level": "secondary",
        "attended_status": "attended_unspecified",
        "confidence": "verified",
        "source_url": "https://example.org/attendance",
        "retrieved_at": "2026-10-01T00:00:00+00:00",
        "school_name_as_recorded": "Old School",
        "recorded_school_id": school_review_id("Old School"),
        "institution_resolution": "successor",
        **extra,
    }
    conn.execute(
        f"INSERT INTO member_education ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
        list(fields.values()),
    )


def context(conn: duckdb.DuckDBPyConnection, identifier: str) -> dict[str, Any]:
    result = conn.execute(
        "SELECT * FROM v_education_attendance_context WHERE education_id = ?",
        [identifier],
    )
    values = result.fetchone()
    assert values is not None
    return dict(zip([column[0] for column in result.description], values))


@pytest.mark.integration
@pytest.mark.parametrize("reviewed_scope", [None, "school", "assertion"])
def test_assertion_context_cannot_receive_or_supply_school_wide_consensus(
    historical_db: duckdb.DuckDBPyConnection, reviewed_scope: str | None
) -> None:
    original = {
        "attended_institution_id": "original",
        "attended_identity_source_url": "https://example.org/original",
        "historical_scope_confirmed": True,
    }
    add_historical_assertion(
        historical_db,
        "reviewed",
        **original,
        historical_context_scope=reviewed_scope,
        historical_broad_sector="Government",
        historical_broad_sector_source_url="https://example.org/sector",
        historical_longitude=135,
        historical_latitude=-25,
        historical_location_source_url="https://example.org/location",
    )
    add_historical_assertion(
        historical_db,
        "other",
        **original,
        historical_context_scope="assertion"
        if reviewed_scope != "assertion"
        else "school",
    )
    reviewed, other = (
        context(historical_db, "reviewed"),
        context(historical_db, "other"),
    )
    assert reviewed["attended_school_id"] == other["attended_school_id"] == "original"
    assert reviewed["location_basis"] == "original_verified"
    assert reviewed["broad_sector"] == "Government"
    assert other["location_basis"] == "successor_unverified"
    assert other["attendance_longitude"] is None
    assert other["broad_sector"] == "Non-government"
    assert other["sector_basis"] == "successor_assumption"


@pytest.mark.integration
@pytest.mark.parametrize("relationship", ["successor", "unresolved"])
def test_scoped_same_name_successors_keep_separate_provisional_identities(
    historical_db: duckdb.DuckDBPyConnection,
    relationship: str,
) -> None:
    for identifier, target in (("one", "successor"), ("two", "other-successor")):
        add_historical_assertion(
            historical_db,
            identifier,
            institution_id=target,
            institution_resolution=relationship,
            historical_context_scope="assertion",
        )
    one, two = context(historical_db, "one"), context(historical_db, "two")
    assert one["attended_school_id"] != two["attended_school_id"]
    assert one["identity_basis"] == two["identity_basis"] == "recorded_name_provisional"
    assert one["broad_sector"] == (
        "Non-government" if relationship == "successor" else None
    )
    assert two["broad_sector"] == (
        "Government" if relationship == "successor" else None
    )
    assert one["sector_conflict"] is two["sector_conflict"] is False


@pytest.mark.integration
def test_scope_column_upgrade_preserves_source_rows_and_legacy_null_scope(
    historical_db: duckdb.DuckDBPyConnection,
) -> None:
    add_historical_assertion(historical_db, "legacy")
    historical_db.execute(
        "CREATE TABLE review_source_member_education AS "
        "SELECT * EXCLUDE (historical_context_scope) FROM member_education"
    )
    before = historical_db.execute(
        "SELECT education_id, institution_id, source_url FROM review_source_member_education"
    ).fetchall()
    init_schema(historical_db)
    assert (
        historical_db.execute(
            "SELECT education_id, institution_id, source_url FROM review_source_member_education"
        ).fetchall()
        == before
    )
    assert historical_db.execute(
        "SELECT historical_context_scope FROM review_source_member_education"
    ).fetchall() == [(None,)]


@pytest.mark.integration
def test_database_validation_rejects_unknown_context_scope(
    historical_db: duckdb.DuckDBPyConnection,
) -> None:
    add_historical_assertion(
        historical_db, "invalid", historical_context_scope="everyone"
    )
    report = validate_database(historical_db, parliaments=[47])
    assert any(
        "historical evidence scope and identity" in failure
        for failure in report.failures
    )


@pytest.mark.integration
def test_coverage_counts_unresolved_identity_even_with_verified_attendance(
    historical_db: duckdb.DuckDBPyConnection,
) -> None:
    historical_db.execute(
        "INSERT INTO parliament_service "
        "(service_id, member_id, parliament_number, chamber, party, party_abbrev, state_or_territory) "
        "VALUES ('service', 'person', 47, 'senate', 'Party', 'P', 'TAS')"
    )
    add_historical_assertion(historical_db, "pending", institution_resolution="direct")
    add_historical_assertion(
        historical_db, "unchanged", institution_resolution="direct"
    )
    query = (
        "SELECT matched_education_records, unmatched_education_records "
        "FROM v_coverage_metrics WHERE parliament_number = 47"
    )
    assert historical_db.execute(query).fetchone() == (2, 0)
    historical_db.execute(
        "UPDATE member_education SET institution_resolution = 'unresolved', "
        "historical_context_scope = 'assertion' WHERE education_id = 'pending'"
    )
    assert historical_db.execute(query).fetchone() == (1, 1)
    assert historical_db.execute(
        "SELECT confidence FROM member_education WHERE education_id = 'pending'"
    ).fetchone() == ("verified",)
