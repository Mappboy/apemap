"""Individual review completion is derived from retained attendance and targets."""

from __future__ import annotations

from dataclasses import replace
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apemap.db import get_connection
from apemap.review.candidates import (
    annotate_candidates,
    build_candidates,
    export_candidates,
    load_into_duckdb,
)
from apemap.review.integration import capture_review_sources
from apemap.review.model import ReviewEvent, education_review_id, school_review_id
from apemap.review.progress import (
    annotate_individual_progress,
    school_resolution_progress,
)
from apemap.review.schools import school_view
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError, encode_event
from tests.db_fixtures import DatabaseFactory

NAME = "John Paul College"
SCHOOL_ID = school_review_id(NAME)
SOURCE = "https://example.org/attendance"


def policy() -> ReviewEvent:
    return ReviewEvent(
        decision_id="individual-policy",
        review_id=SCHOOL_ID,
        entity_type="school",
        action="research",
        payload={
            "recorded_name": NAME,
            "resolution_reason": "ambiguous_name",
            "requires_individual_resolution": True,
        },
        notes="Resolve each member separately",
        reviewer="Fixture reviewer",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
    )


def education(
    aph_id: str,
    action: str = "map",
    reference: str | None = "acara:1",
    *,
    decision_id: str | None = None,
) -> ReviewEvent:
    payload: dict[str, Any] = {"aph_id": aph_id, "recorded_school_name": NAME}
    if reference is not None:
        payload.update(institution_ref=reference, relationship_type="direct")
    if action == "accept":
        payload.update(
            attended_status="attended_unspecified",
            confidence="verified",
            retrieved_at="2026-10-09T00:00:00+00:00",
        )
    if action == "research":
        payload.update(resolution_only=True, resolution_reason="no_suitable_candidate")
    return ReviewEvent(
        decision_id=decision_id or f"{aph_id}-{action}",
        review_id=education_review_id(aph_id, NAME),
        entity_type="member_education",
        action=action,
        payload=payload,
        source_url=SOURCE,
        notes="Fixture attendance evidence",
        reviewer="Fixture reviewer",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
    )


def resolve(reference: str) -> dict[str, Any] | None:
    localities = {"acara:1": ("Frankston", "VIC"), "acara:2": ("Kalgoorlie", "WA")}
    if reference not in localities:
        return None
    suburb, state = localities[reference]
    return {
        "institution_ref": reference,
        "school_name": NAME,
        "suburb": suburb,
        "state": state,
    }


def seed(conn: duckdb.DuckDBPyConnection) -> None:
    for number in (1, 2, 3):
        conn.execute(
            "INSERT INTO members (member_id, family_name, given_name, display_name, aph_id) "
            "VALUES (?, 'Person', 'Test', ?, ?)",
            [f"member-{number}", f"Member {number}", f"APH-{number}"],
        )
    for number in (1, 2):
        conn.execute(
            "INSERT INTO institutions (institution_id, acara_id, school_name, sector) "
            "VALUES (?, ?, ?, 'Government')",
            [f"acara-{number}", str(number), NAME],
        )
        conn.execute(
            "INSERT INTO parliament_service (service_id, member_id, parliament_number, chamber, "
            "party, party_abbrev, state_or_territory, service_start) "
            "VALUES (?, ?, 48, 'representatives', 'Labor', 'ALP', 'TAS', '2025-07-22')",
            [f"service-{number}", f"member-{number}"],
        )
        add_source(conn, number)
    conn.execute(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, "
        "attended_status, years_attended, graduation_year, source_url, retrieved_at, "
        "confidence, school_name_as_recorded, evidence_origin, institution_resolution) "
        "SELECT 'duplicate-education', member_id, "
        "'acara-2', level, attended_status, years_attended, graduation_year, source_url, "
        "retrieved_at, confidence, school_name_as_recorded, evidence_origin, institution_resolution "
        "FROM member_education WHERE education_id = 'education-1'"
    )
    capture_review_sources(conn)


def add_source(conn: duckdb.DuckDBPyConnection, number: int) -> None:
    conn.execute(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, "
        "attended_status, source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin) "
        "VALUES (?, ?, 'acara-1', 'secondary', 'attended_unspecified', ?, "
        "'2026-10-09T00:00:00+00:00', 'verified', ?, 'aph')",
        [f"education-{number}", f"member-{number}", SOURCE, NAME],
    )


@pytest.fixture
def progress_conn(db_conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    seed(db_conn)
    return db_conn


@pytest.fixture
def progress_service(
    tmp_path: Path, database_factory: DatabaseFactory
) -> ReviewService:
    path, conn = database_factory(None)
    seed(conn)
    conn.close()
    external = tmp_path / "external"
    external.mkdir()
    (external / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n"
        "1,John Paul College,Government,Secondary,VIC,-38,145\n"
        "2,John Paul College,Government,Secondary,WA,-30,121\n",
        encoding="utf-8",
    )
    return ReviewService(
        log_path=tmp_path / "decisions.jsonl", db_path=path, external_dir=external
    )


def completed() -> list[ReviewEvent]:
    return [policy(), education("APH-1"), education("APH-2", reference="acara:2")]


def test_distinct_members_resolve_individually_and_duplicates_count_once(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    result = school_resolution_progress(progress_conn, completed(), resolve=resolve)[
        SCHOOL_ID
    ]
    assert result["available"] and result["status"] == "resolved_individually"
    assert result["resolved_count"] == 2 and result["unresolved_count"] == 0
    assert {
        row["current_resolution"]["institution_ref"] for row in result["assertions"]
    } == {"acara:1", "acara:2"}
    summaries = {
        row["aph_id"]: row["current_resolution"] for row in result["assertions"]
    }
    assert summaries["aph-1"]["institution_name"] == NAME
    assert (summaries["aph-1"]["suburb"], summaries["aph-1"]["state"]) == (
        "Frankston",
        "VIC",
    )
    assert (summaries["aph-2"]["suburb"], summaries["aph-2"]["state"]) == (
        "Kalgoorlie",
        "WA",
    )
    assert policy().status == "needs_research"


@pytest.mark.parametrize(
    "outcome", ["pending", "research", "conflict", "attendance_only"]
)
def test_automatic_matches_and_unresolved_decisions_do_not_complete(
    progress_conn: duckdb.DuckDBPyConnection, outcome: str
) -> None:
    events = [policy(), education("APH-1")]
    if outcome == "research":
        events.append(education("APH-2", "research", None))
    elif outcome == "conflict":
        events.extend(
            [
                education("APH-2", decision_id="first"),
                education("APH-2", reference="acara:2", decision_id="second"),
            ]
        )
    elif outcome == "attendance_only":
        events.append(education("APH-2", "accept", None))
    result = school_resolution_progress(progress_conn, events, resolve=resolve)[
        SCHOOL_ID
    ]
    assert result["status"] == "needs_individual_review"
    assert result["resolved_count"] == 1 and result["unresolved_count"] == 1


def test_source_and_manual_withdrawals_are_reported_without_blocking_completion(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    claim = education("APH-3", "accept")
    withdrawn = replace(
        education("APH-3", "reject", None),
        action="supersede",
        replacement_action="reject",
        supersedes=[claim.decision_id],
    )
    events = [
        policy(),
        education("APH-1"),
        education("APH-2", "reject", None),
        claim,
        withdrawn,
    ]
    result = school_resolution_progress(progress_conn, events, resolve=resolve)[
        SCHOOL_ID
    ]
    assert result["status"] == "resolved_individually"
    assert (
        result["resolved_count"],
        result["withdrawn_count"],
        result["unresolved_count"],
    ) == (1, 2, 0)
    assert {
        row["aph_id"] for row in result["assertions"] if row["status"] == "withdrawn"
    } == {"aph-2", "aph-3"}


def test_manual_claims_outside_source_members_do_not_affect_current_scope(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    events = completed() + [education("OUTSIDE", "accept")]
    result = school_resolution_progress(progress_conn, events, resolve=resolve)[
        SCHOOL_ID
    ]
    assert result["status"] == "resolved_individually" and result["resolved_count"] == 2


def test_manual_withdrawal_without_source_inventory_cannot_complete(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    for table in (
        "review_source_member_education",
        "review_source_institutions",
        "review_source_members",
    ):
        progress_conn.execute(f'DROP TABLE "{table}"')
    progress_conn.execute("DELETE FROM member_education")
    claim = education("APH-3", "accept")
    withdrawn = replace(
        education("APH-3", "reject", None),
        action="supersede",
        replacement_action="reject",
        supersedes=[claim.decision_id],
    )
    result = school_resolution_progress(
        progress_conn, [policy(), claim, withdrawn], resolve=resolve
    )[SCHOOL_ID]
    assert result["withdrawn_count"] == 1 and result["unresolved_count"] == 0
    assert not result["available"] and result["status"] == "needs_individual_review"


def test_mapping_after_withdrawal_requires_live_attendance(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    claim = education("APH-3", "accept")
    withdrawn = replace(
        education("APH-3", "reject", None),
        action="supersede",
        replacement_action="reject",
        supersedes=[claim.decision_id],
    )
    mapped = replace(
        education("APH-3", reference="acara:2"),
        action="supersede",
        replacement_action="map",
        supersedes=[withdrawn.decision_id],
    )
    result = school_resolution_progress(
        progress_conn, completed() + [claim, withdrawn, mapped], resolve=resolve
    )[SCHOOL_ID]
    assert result["status"] == "needs_individual_review"
    assert result["resolved_count"] == 2 and result["unresolved_count"] == 1
    assert result["withdrawn_count"] == 0
    assertion = next(row for row in result["assertions"] if row["aph_id"] == "aph-3")
    assert assertion["current_resolution"]["status"] == "attendance_unavailable"
    assert assertion["current_resolution"]["institution_ref"] is None


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_empty_inventory_never_completes(
    db_conn: duckdb.DuckDBPyConnection, missing: bool
) -> None:
    result = school_resolution_progress(
        None if missing else db_conn, completed(), resolve=resolve
    )[SCHOOL_ID]
    assert not result["available"] and result["status"] == "needs_individual_review"
    assert result["resolved_count"] == 0 and result["assertions"] == []


def test_unavailable_target_reopens_review(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    result = school_resolution_progress(
        progress_conn,
        completed(),
        resolve=lambda ref: resolve(ref) if ref != "acara:2" else None,
    )[SCHOOL_ID]
    assert (
        result["status"] == "needs_individual_review"
        and result["unresolved_count"] == 1
    )
    unavailable = next(row for row in result["assertions"] if row["aph_id"] == "aph-2")
    assert unavailable["current_resolution"]["unavailable_institution_ref"] == "acara:2"


def test_frozen_inventory_reopens_when_source_build_adds_an_assertion(
    progress_conn: duckdb.DuckDBPyConnection,
) -> None:
    before = school_resolution_progress(progress_conn, completed(), resolve=resolve)[
        SCHOOL_ID
    ]
    add_source(progress_conn, 3)
    unchanged = school_resolution_progress(progress_conn, completed(), resolve=resolve)[
        SCHOOL_ID
    ]
    assert unchanged == before
    progress_conn.execute(
        "INSERT INTO review_source_member_education SELECT * FROM member_education "
        "WHERE education_id = 'education-3'"
    )
    after = school_resolution_progress(progress_conn, completed(), resolve=resolve)[
        SCHOOL_ID
    ]
    assert (
        after["status"] == "needs_individual_review" and after["unresolved_count"] == 1
    )


def test_show_queue_status_and_exports_share_derived_completion(
    progress_service: ReviewService,
    tmp_path: Path,
) -> None:
    service = progress_service
    service.log_path.write_bytes(b"".join(encode_event(event) for event in completed()))
    item = service.show(SCHOOL_ID)
    assert item["status"] == "resolved_individually"
    assert item["context"]["individual_resolution"]["resolved_count"] == 2
    assert school_view(item, service.resolve_institution).status == item["status"]
    rows = service.candidates("school", status="resolved_individually")
    assert rows and all(row["status"] == item["status"] for row in rows)
    assert any(
        row["entity_type"] == "school" and row["status"] == item["status"]
        for row in service.status()["counts"]
    )
    with get_connection(service.db_path) as conn:
        annotated = annotate_individual_progress(
            annotate_candidates(build_candidates(conn), completed()),
            school_resolution_progress(
                conn, completed(), resolve=service.institution_resolver()
            ),
        )
        load_into_duckdb(conn, completed(), annotated)
        assert conn.execute(
            "SELECT COUNT(*) FROM resolved_school_mappings"
        ).fetchone() == (0,)
        export_candidates(conn, annotated, tmp_path / "exports")
    with (tmp_path / "exports" / "review_summary.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        summaries = list(csv.DictReader(stream))
    assert (
        next(row for row in summaries if row["review_id"] == SCHOOL_ID)["status"]
        == item["status"]
    )


def test_preview_normalizes_policy_and_shows_before_after_member_progress(
    progress_service: ReviewService,
) -> None:
    service = progress_service
    preview = service.prepare(
        SCHOOL_ID,
        "research",
        {"recorded_name": NAME, "resolution_reason": "ambiguous_name"},
        reviewer="Fixture reviewer",
        notes="Resolve individually",
    )
    assert preview["event"]["payload"]["requires_individual_resolution"] is True
    assert preview["individual_resolution"]["after"]["unresolved_count"] == 2
    service.save(preview)
    service.save(
        service.prepare(
            education_review_id("APH-1", NAME),
            "map",
            education("APH-1").payload,
            source_url=SOURCE,
            reviewer="Fixture reviewer",
        )
    )
    member = service.prepare(
        education_review_id("APH-2", NAME),
        "map",
        education("APH-2", reference="acara:2").payload,
        source_url=SOURCE,
        reviewer="Fixture reviewer",
    )
    assert (
        member["individual_resolution"]["before"]["status"] == "needs_individual_review"
    )
    assert member["individual_resolution"]["after"]["status"] == "resolved_individually"


@pytest.mark.parametrize("change", ["member", "source"])
def test_school_preview_cannot_save_after_progress_changes(
    progress_service: ReviewService,
    change: str,
) -> None:
    service = progress_service
    events = completed()
    service.log_path.write_bytes(b"".join(encode_event(event) for event in events))
    preview = service.prepare(
        SCHOOL_ID,
        "research",
        policy().payload,
        supersedes=[policy().decision_id],
        reviewer="Fixture reviewer",
        notes="Keep individual review",
    )
    if change == "member":
        research = replace(
            education("APH-2", "research", None),
            action="supersede",
            replacement_action="research",
            supersedes=[events[2].decision_id],
        )
        with service.log_path.open("ab") as stream:
            stream.write(encode_event(research))
    else:
        with get_connection(service.db_path) as conn:
            add_source(conn, 3)
            conn.execute(
                "INSERT INTO review_source_member_education SELECT * FROM member_education "
                "WHERE education_id = 'education-3'"
            )
    before = service.log_path.read_bytes()
    with pytest.raises(StaleReviewError):
        service.save(preview)
    assert service.log_path.read_bytes() == before


def test_build_exports_the_same_completion_as_live_queue(
    progress_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import service as module

    service = progress_service
    service.log_path.write_bytes(b"".join(encode_event(event) for event in completed()))
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "individuals.json").write_text(
        json.dumps([{"PHID": f"APH-{number}"} for number in (1, 2, 3)]),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "RAW_APH_DIR", raw)
    monkeypatch.setattr("apemap.inputs.verify_inputs_manifest", lambda _: (True, []))
    monkeypatch.setattr("apemap.ingest.pipeline.run_aph_ingestion", lambda **_: None)
    monkeypatch.setattr(
        "apemap.review.integration.attach_source_manifest", lambda *_, **__: None
    )
    monkeypatch.setattr(
        "apemap.review.integration.review_snapshot_metadata",
        lambda _: {
            "decision_log_sha256": hashlib.sha256(
                service.log_path.read_bytes()
            ).hexdigest()
        },
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"created_at": "2026-10-09T00:00:00+00:00"}), encoding="utf-8"
    )
    service.build(inputs_manifest=manifest, output_dir=tmp_path / "build")
    with (tmp_path / "build" / "school_candidates.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        rows = [row for row in csv.DictReader(stream) if row["review_id"] == SCHOOL_ID]
    assert rows and all(row["status"] == "resolved_individually" for row in rows)
