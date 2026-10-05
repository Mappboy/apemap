"""Offline replay, transaction integrity and consumed-provenance regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

from apemap.ingest.matching import SchoolMatcher
from apemap.review.integration import (
    apply_review_events,
    attach_source_manifest,
    capture_review_snapshot,
    capture_review_sources,
    compare_review_records,
    detached_member_children,
    preview_review_events,
    project_review_records,
    restore_review_sources,
    review_snapshot_metadata,
)
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    institution_review_id,
    member_review_id,
    school_digest,
    school_review_id,
)
from apemap.review.store import StaleReviewError, encode_event

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection


def event(
    entity: str, payload: dict[str, Any], decision_id: str = "accepted"
) -> ReviewEvent:
    identifiers = {
        "member": lambda: member_review_id(payload["aph_id"], payload["field"]),
        "member_education": lambda: education_review_id(
            payload["aph_id"], payload["recorded_school_name"]
        ),
        "manual_institution": lambda: institution_review_id(payload["institution_ref"]),
        "school": lambda: school_review_id(payload["recorded_name"]),
    }
    return ReviewEvent(
        decision_id=decision_id,
        review_id=identifiers[entity](),
        entity_type=entity,
        action="map" if entity == "school" else "accept",
        payload=payload,
        source_url="https://example.org/reviewed-source",
        reviewed_at="2026-10-05",
        recorded_at="2026-10-05T00:00:00+00:00",
        reviewer="Fixture reviewer",
    )


@pytest.fixture
def seeded_review(
    db_conn: DuckDBPyConnection, tmp_path: Path
) -> tuple[DuckDBPyConnection, SchoolMatcher]:
    db_conn.execute(
        "INSERT INTO members VALUES ('member-one', 'One', 'Person', 'Person One', 'Male', '1970-01-01', 'APH-ONE', 'Q1')"
    )
    db_conn.execute(
        """INSERT INTO parliament_service
        (service_id, member_id, parliament_number, chamber, party, party_abbrev,
         state_or_territory, service_start, service_end, is_opening_day_member)
        VALUES ('service-one', 'member-one', 47, 'senate', 'Party', 'P', 'TAS',
                '2022-07-26', '2025-07-21', TRUE)"""
    )
    db_conn.execute(
        "INSERT INTO institutions (institution_id, acara_id, school_name, sector) VALUES ('acara-1', '1', 'First High School', 'Government')"
    )
    db_conn.execute(
        """INSERT INTO member_education
        (education_id, member_id, institution_id, level, attended_status, source_url,
         retrieved_at, confidence, school_name_as_recorded, evidence_origin)
        VALUES ('source-education', 'member-one', 'acara-1', 'secondary',
                'attended_unspecified', 'https://example.org/aph',
                '2026-10-01T00:00:00+00:00', 'verified', 'First High School', 'aph')"""
    )
    capture_review_sources(db_conn)
    external = tmp_path / "external"
    external.mkdir()
    (external / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n"
        "1,First High School,Government,Secondary,TAS,-42,147\n"
        "2,Second High School,Independent,Secondary,TAS,-42.1,147.1\n",
        encoding="utf-8",
    )
    return db_conn, SchoolMatcher(external_dir=external)


def test_indexed_qid_and_source_restoration_preserve_children_and_indexes(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    conn.execute("CREATE INDEX review_fixture_member ON member_education(member_id)")
    accepted = [
        event(
            "member",
            {"aph_id": "APH-ONE", "field": "date_of_birth", "value": "1971-02-03"},
            "dob",
        ),
        event(
            "member",
            {"aph_id": "APH-ONE", "field": "wikidata_id", "value": None},
            "qid",
        ),
    ]
    conn.execute("BEGIN")
    apply_review_events(conn, events=accepted, matcher=matcher)
    conn.execute("COMMIT")
    assert conn.execute(
        "SELECT date_of_birth, wikidata_id FROM members"
    ).fetchone() == (date(1971, 2, 3), None)
    assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM parliament_service").fetchone() == (1,)
    assert conn.execute(
        "SELECT count(*) FROM duckdb_indexes() WHERE index_name = 'review_fixture_member'"
    ).fetchone() == (1,)
    conn.execute("BEGIN")
    restore_review_sources(conn)
    conn.execute(
        "UPDATE members SET date_of_birth = '1972-03-04' WHERE member_id = 'member-one'"
    )
    conn.execute("COMMIT")
    assert conn.execute(
        "SELECT date_of_birth, wikidata_id FROM members"
    ).fetchone() == (date(1972, 3, 4), "Q1")
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            "UPDATE member_education SET member_id = 'missing' WHERE education_id = 'source-education'"
        )


def test_detached_child_catalog_and_parent_update_roll_back_together(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, _ = seeded_review
    before = conn.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = 'member_education'"
    ).fetchone()
    conn.execute("BEGIN")
    with pytest.raises(RuntimeError, match="rollback"):
        with detached_member_children(conn):
            conn.execute(
                "UPDATE members SET wikidata_id = 'Q2' WHERE member_id = 'member-one'"
            )
            raise RuntimeError("rollback")
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT wikidata_id FROM members").fetchone() == ("Q1",)
    assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (1,)
    assert (
        conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE table_name = 'member_education'"
        ).fetchone()
        == before
    )


def test_reviewed_qid_swap_preserves_uniqueness_and_child_links(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    conn.execute(
        "INSERT INTO members VALUES ('member-two', 'Two', 'Person', 'Person Two', NULL, NULL, 'APH-TWO', 'Q2')"
    )
    capture_review_sources(conn)
    reviewed = [
        event(
            "member",
            {"aph_id": "APH-ONE", "field": "wikidata_id", "value": "Q2"},
            "first-qid",
        ),
        event(
            "member",
            {"aph_id": "APH-TWO", "field": "wikidata_id", "value": "Q1"},
            "second-qid",
        ),
    ]
    conn.execute("BEGIN")
    apply_review_events(conn, events=reviewed, matcher=matcher)
    conn.execute("COMMIT")
    assert conn.execute(
        "SELECT aph_id, wikidata_id FROM members ORDER BY aph_id"
    ).fetchall() == [("APH-ONE", "Q2"), ("APH-TWO", "Q1")]
    assert conn.execute("SELECT member_id FROM member_education").fetchall() == [
        ("member-one",)
    ]


def test_manual_optional_fields_and_education_accept_reject_share_replay(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    manual = event(
        "manual_institution",
        {
            "institution_ref": "manual:overseas-school",
            "school_name": "Overseas School",
            "sector": "Other",
            "country": "New Zealand",
        },
        "registry",
    )
    education = event(
        "member_education",
        {
            "aph_id": "APH-ONE",
            "recorded_school_name": "Overseas School",
            "institution_ref": "manual:overseas-school",
            "attended_status": "attended_unspecified",
            "confidence": "provisional",
            "retrieved_at": "2026-10-01T00:00:00+00:00",
        },
        "education",
    )
    preview = preview_review_events(conn, [manual, education], matcher)
    assert preview["member_education"]["before_count"] == 1
    assert preview["member_education"]["after_count"] == 2
    assert preview["member_education"]["before"] == []
    row = preview["member_education"]["after"][0]
    assert row["education_id"] == f"edu-aph-one-{school_digest('Overseas School')}"
    assert row["resolution_source_url"] is None
    assert row["confidence"] == "provisional"
    assert preview["institutions"]["after"][0]["school_type"] is None
    assert preview["institutions"]["after"][0]["institution_status"] == "manual"
    closed = replace(manual, payload={**manual.payload, "institution_status": "closed"})
    assert (
        preview_review_events(conn, [closed], matcher)["institutions"]["after"][0][
            "institution_status"
        ]
        == "closed"
    )
    assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (1,)
    conn.execute("BEGIN")
    apply_review_events(conn, events=[manual, education], matcher=matcher)
    conn.execute("COMMIT")
    rejected = replace(
        education,
        decision_id="rejected",
        action="supersede",
        replacement_action="reject",
        supersedes=[education.decision_id],
        notes="Attendance disproved",
    )
    conn.execute("BEGIN")
    apply_review_events(conn, events=[manual, education, rejected], matcher=matcher)
    conn.execute("COMMIT")
    assert conn.execute("SELECT education_id FROM member_education").fetchall() == [
        ("source-education",)
    ]


def test_compact_projection_ignores_unchanged_typed_values(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    same = event(
        "member", {"aph_id": "APH-ONE", "field": "date_of_birth", "value": "1970-01-01"}
    )
    before = project_review_records(conn, [], matcher)
    after = project_review_records(conn, [same], matcher)
    assert compare_review_records(before, after) == {}
    assert preview_review_events(conn, [same], matcher) == {}
    mapping = event(
        "school",
        {
            "recorded_name": "First High School",
            "institution_ref": "acara:2",
            "relationship_type": "successor",
        },
        "mapping",
    )
    impact = preview_review_events(conn, [mapping], matcher)
    assert impact["member_education"]["updated_count"] == 1
    assert len(impact["member_education"]["before"]) == 1
    assert impact["institutions"]["after"][0]["institution_status"] == "unknown"
    assert (
        impact["member_education"]["after"][0]["resolution_source_url"]
        == mapping.source_url
    )


def test_consumed_ledger_and_input_manifest_are_archived_exactly(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
    tmp_path: Path,
) -> None:
    conn, _ = seeded_review
    first = event("member", {"aph_id": "APH-ONE", "field": "gender", "value": "Other"})
    raw = (json.dumps(first.to_dict(), ensure_ascii=False) + "\r\n").encode()
    log = tmp_path / "decisions.jsonl"
    log.write_bytes(raw)
    snapshot = capture_review_snapshot(
        conn, [first], decision_log_path=log, source_provenance={"source": "fixture"}
    )
    assert snapshot["decision_log_sha256"] == hashlib.sha256(raw).hexdigest()
    assert conn.execute(
        "SELECT decision_log_jsonl FROM review_build_snapshot"
    ).fetchone() == (raw,)
    second = event(
        "member",
        {"aph_id": "APH-ONE", "field": "date_of_birth", "value": None},
        "later",
    )
    log.write_bytes(raw + encode_event(second))
    assert review_snapshot_metadata(conn) == snapshot
    with pytest.raises(StaleReviewError):
        capture_review_snapshot(conn, [first], decision_log_path=log)
    assert review_snapshot_metadata(conn) == snapshot
    manifest = tmp_path / "inputs.json"
    consumed_manifest = b'{ "created_at": "2026-10-01T00:00:00+00:00", "files": {} }\n'
    manifest.write_text('{"changed":true}', encoding="utf-8")
    attached = attach_source_manifest(conn, manifest, manifest_bytes=consumed_manifest)
    assert attached["decision_log_sha256"] == snapshot["decision_log_sha256"]
    assert (
        attached["source_manifest_sha256"]
        == hashlib.sha256(consumed_manifest).hexdigest()
    )
    assert conn.execute(
        "SELECT manifest_json FROM review_build_input_manifests"
    ).fetchone() == (consumed_manifest,)
    assert attached["source_provenance"]["source"] == "fixture"
    rebuilt = capture_review_snapshot(
        conn,
        [first, second],
        decision_log_path=log,
        source_provenance={
            **attached["source_provenance"],
            "wikimedia": {"source": "fixture cache"},
        },
    )
    assert rebuilt["source_manifest_sha256"] == attached["source_manifest_sha256"]
    assert (
        rebuilt["decision_log_sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
    )


def test_unrelated_registered_reference_does_not_require_fixture_register(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    unrelated = event(
        "school",
        {
            "recorded_name": "Outside Cohort School",
            "institution_ref": "acara:999",
            "relationship_type": "direct",
        },
    )
    assert preview_review_events(conn, [unrelated], matcher) == {}
    applied = replace(
        unrelated,
        review_id=school_review_id("First High School"),
        payload={**unrelated.payload, "recorded_name": "First High School"},
    )
    with pytest.raises(ValueError, match="Unknown ACARA"):
        preview_review_events(conn, [applied], matcher)


def test_research_removes_prior_fuzzy_identity_but_preserves_source_attendance(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    recorded = "First High School Old Campus"
    conn.execute(
        "UPDATE member_education SET school_name_as_recorded = ?, confidence = 'provisional'",
        [recorded],
    )
    capture_review_sources(conn)
    research = replace(
        event("school", {"recorded_name": recorded}),
        action="research",
        source_url="",
        notes="No evidence for the fuzzy identity",
    )
    change = preview_review_events(conn, [research], matcher)["member_education"]
    assert change["before_count"] == change["after_count"] == 1
    projected = change["after"][0]
    assert projected["institution_id"].startswith("inst-unmatched-")
    assert projected["confidence"] == "unconfirmed"
    assert projected["school_name_as_recorded"] == recorded
    assert projected["source_url"] == "https://example.org/aph"
    assert projected["attended_status"] == "attended_unspecified"
