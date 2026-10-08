"""Offline regression coverage for review authority at ingestion boundaries."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from apemap.db import get_connection
from apemap.ingest.history import load_manual_education, load_service_overrides
from apemap.ingest.matching import SchoolMatcher
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.ingest.review import merge_review_rows
from apemap.review.integration import (
    KEYS,
    SOURCE_TABLES,
    attach_source_manifest,
    capture_review_snapshot,
    preview_review_events,
    project_review_records,
    review_snapshot_metadata,
)
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    member_review_id,
    service_review_id,
    school_review_id,
    validate_events,
)
from tests.test_historical_coverage import historical_member


def write_log(path: Path, events: list[ReviewEvent]) -> None:
    path.write_text("".join(json.dumps(event.to_dict()) + "\n" for event in events))


def event(
    identifier: str,
    review_id: str,
    entity: str,
    payload: dict[str, object],
    **changes: object,
) -> ReviewEvent:
    values = {
        "decision_id": identifier,
        "review_id": review_id,
        "entity_type": entity,
        "action": "accept",
        "payload": payload,
        "source_url": "https://example.org/review",
        "reviewed_at": "2026-10-05",
        "recorded_at": "2026-10-05T00:00:00+00:00",
        "reviewer": "Researcher",
        "notes": "Evidence checked",
    }
    values.update(changes)
    return ReviewEvent.from_dict(values)


@pytest.mark.parametrize("nonempty_working_log", [False, True])
def test_fixture_ingestion_never_reads_working_review_authority(
    tmp_path: Path,
    empty_review_log: Path,
    monkeypatch: pytest.MonkeyPatch,
    nonempty_working_log: bool,
) -> None:
    """An unrelated valid working mapping cannot affect a small pipeline fixture."""
    from apemap.review import integration

    working = tmp_path / "working-decisions.jsonl"
    mapping = event(
        "working-mapping",
        school_review_id("Historic High School"),
        "school",
        {
            "recorded_name": "Historic High School",
            "institution_ref": "acara:999999",
            "relationship_type": "direct",
        },
        action="map",
    )
    write_log(working, [mapping] if nonempty_working_log else [])
    # The reference exists in the working register, but not in the pipeline fixture.
    validate_events([mapping], acara_ids={"999999"})
    assert integration.read_review_events(working) == (
        [mapping] if nonempty_working_log else []
    )
    monkeypatch.setattr(integration, "DEFAULT_LOG_PATH", working)
    loaded_paths: list[Path] = []
    original_load = integration.load_events

    def tracked_load(path: Path) -> list[ReviewEvent]:
        loaded_paths.append(path)
        return original_load(path)

    monkeypatch.setattr(integration, "load_events", tracked_load)
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            conn=conn,
            raw_individuals=[historical_member()],
            external_dir=tmp_path / "fixture-register",
            output_dir=tmp_path / "output",
            decision_log_path=empty_review_log,
        )
        assert loaded_paths == [empty_review_log]
        assert conn.execute("SELECT count(*) FROM members").fetchone() == (1,)
        assert conn.execute("SELECT acara_id FROM institutions").fetchall() == [(None,)]
        snapshot = review_snapshot_metadata(conn)
        assert snapshot["event_count"] == 0
        assert snapshot["decision_log_sha256"] == hashlib.sha256(b"").hexdigest()


def test_aph_review_supersession_restores_updated_source(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    raw["DateOfBirth"] = "1970-01-01"
    override = event(
        "reviewed-dob",
        member_review_id("HIST", "date_of_birth"),
        "member",
        {"aph_id": "HIST", "field": "date_of_birth", "value": "1960-01-01"},
    )
    write_log(path, [override])
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        effective = conn.execute("SELECT date_of_birth FROM members").fetchone()
        assert effective is not None
        assert str(effective[0]) == "1960-01-01"
        raw["DateOfBirth"] = "1975-01-01"
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        effective = conn.execute("SELECT date_of_birth FROM members").fetchone()
        assert effective is not None
        assert str(effective[0]) == "1960-01-01"
        source = conn.execute(
            "SELECT date_of_birth FROM review_source_members"
        ).fetchone()
        assert source is not None
        original = source[0]
        assert str(original) == "1975-01-01"
        withdrawn = event(
            "withdrawn-dob",
            override.review_id,
            "member",
            {"aph_id": "HIST", "field": "date_of_birth"},
            action="supersede",
            supersedes=[override.decision_id],
            replacement_action="research",
            source_url="",
        )
        write_log(path, [override, withdrawn])
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        effective = conn.execute("SELECT date_of_birth FROM members").fetchone()
        assert effective is not None
        assert effective[0] == original


def test_aph_education_rejection_and_withdrawal_replay(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    raw["SecondarySchool"] = "Unresolved Academy"
    rejection = event(
        "false-education",
        education_review_id("HIST", "Unresolved Academy"),
        "member_education",
        {"aph_id": "HIST", "recorded_school_name": "Unresolved Academy"},
        action="reject",
    )
    write_log(path, [rejection])
    with get_connection() as conn:
        for _ in range(2):
            run_aph_ingestion(
                [42],
                raw_individuals=[raw],
                conn=conn,
                external_dir=tmp_path,
                output_dir=tmp_path,
                decision_log_path=path,
            )
            assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (
                0,
            )
            assert conn.execute(
                "SELECT count(*) FROM review_source_member_education"
            ).fetchone() == (1,)
        withdrawn = event(
            "research-education",
            rejection.review_id,
            "member_education",
            rejection.payload,
            action="supersede",
            supersedes=[rejection.decision_id],
            replacement_action="research",
        )
        write_log(path, [rejection, withdrawn])
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (1,)


def test_invalid_log_cannot_partially_refresh_canonical_sources(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_log(path, [])
    raw = historical_member()
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        before = conn.execute("SELECT * FROM member_education").fetchall()
        path.write_text('{"schema_version": 999}\n')
        raw["SecondarySchool"] = ""
        with pytest.raises(ValueError):
            run_aph_ingestion(
                [42],
                raw_individuals=[raw],
                conn=conn,
                external_dir=tmp_path,
                output_dir=tmp_path,
                decision_log_path=path,
            )
        assert conn.execute("SELECT * FROM member_education").fetchall() == before


def test_disposable_csv_edits_do_not_become_authority(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    path.write_text("id,value,review_status\nold,false,accepted\n")
    rows = merge_review_rows(
        [{"id": "new", "value": "fresh"}],
        path,
        ["id"],
        ["id", "value"],
        ["review_status"],
    )
    assert rows == [{"id": "new", "value": "fresh"}]
    with path.open(newline="") as handle:
        assert list(csv.DictReader(handle)) == rows
    assert "review_status" not in path.read_text()


def test_default_legacy_readers_are_disabled() -> None:
    assert load_manual_education() == {}
    assert load_service_overrides() == {}


def test_aph_snapshot_fingerprints_consumed_json_register(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_log(path, [])
    register = tmp_path / "acara_school_results.json"
    register.write_text("[]")
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[historical_member()],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        provenance = review_snapshot_metadata(conn)["source_provenance"]
        assert (
            provenance["acara_files"][register.name]
            == hashlib.sha256(register.read_bytes()).hexdigest()
        )


def service_event(
    identifier: str, parliament: int, aph_id: str = "HIST"
) -> ReviewEvent:
    return event(
        identifier,
        service_review_id(aph_id, parliament),
        "service",
        {
            "aph_id": aph_id,
            "parliament_number": parliament,
            "intervals": [
                {
                    "service_start": "2008-02-12" if parliament == 42 else "2025-07-22",
                    "service_end": "2010-07-19" if parliament == 42 else None,
                    "chamber": "representatives",
                    "party": "Reviewed Party",
                    "electorate": "Reviewed Seat",
                    "state_or_territory": "VIC",
                    "source_url": "https://example.org/service",
                    "retrieved_at": "2026-10-05T00:00:00+00:00",
                }
            ],
        },
    )


def test_reviewed_service_repairs_missing_narrow_cohort_membership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    raw["RepresentedParliaments"] = [48]
    replacement = service_event("missing-membership", 42)
    write_log(path, [replacement])
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute("SELECT count(*) FROM members").fetchone() == (1,)
        assert conn.execute(
            "SELECT parliament_number, party FROM parliament_service"
        ).fetchall() == [(42, "Reviewed Party")]
        assert conn.execute(
            "SELECT count(*) FROM review_source_parliament_service"
        ).fetchone() == (0,)
        assert review_snapshot_metadata(conn)["source_parliaments"] == [42]
        withdrawn = event(
            "withdraw-membership",
            replacement.review_id,
            "service",
            {"aph_id": "HIST", "parliament_number": 42},
            action="supersede",
            supersedes=[replacement.decision_id],
            replacement_action="research",
        )
        write_log(path, [replacement, withdrawn])
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute("SELECT count(*) FROM parliament_service").fetchone() == (
            0,
        )


def test_reviewed_service_respects_persisted_requested_cohorts(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    replacement = service_event("outside-cohort", 48)
    write_log(path, [replacement])
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute(
            "SELECT DISTINCT parliament_number FROM parliament_service"
        ).fetchall() == [(42,)]
        assert "parliament_service" not in preview_review_events(conn, [replacement])
        run_aph_ingestion(
            [48],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute(
            "SELECT DISTINCT parliament_number FROM parliament_service ORDER BY 1"
        ).fetchall() == [(42,), (48,)]
        assert review_snapshot_metadata(conn)["source_parliaments"] == [42, 48]


def test_missing_member_preview_matches_source_facts_and_rebuild(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    raw["PHID"] = "PHID-OUTSIDE-COHORT"
    raw["RepresentedParliaments"] = [48]
    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    replacement = service_event("preview-missing-membership", 42, raw["PHID"])
    write_log(path, [])
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
            retrieved_at=stamp,
        )
        assert conn.execute("SELECT count(*) FROM members").fetchone() == (0,)
        assert conn.execute(
            "SELECT aph_id FROM review_source_individuals"
        ).fetchall() == [(raw["PHID"].lower(),)]
        matcher = SchoolMatcher(external_dir=tmp_path)
        projected = project_review_records(conn, [replacement], matcher)
        preview = preview_review_events(conn, [replacement], matcher)
        for table in SOURCE_TABLES:
            assert preview[table]["added_count"] == 1
        assert projected["member_education"][0]["evidence_origin"] == "aph"
        assert projected["member_education"][0]["retrieved_at"] == stamp
        assert conn.execute("SELECT count(*) FROM members").fetchone() == (0,)
        write_log(path, [replacement])
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        for table in SOURCE_TABLES:
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {KEYS[table]}")
            columns = [column[0] for column in cursor.description]
            assert [dict(zip(columns, row)) for row in cursor.fetchall()] == projected[
                table
            ]
        assert conn.execute(
            "SELECT count(*) FROM review_source_parliament_service"
        ).fetchone() == (0,)
        withdrawn = event(
            "withdraw-preview-membership",
            replacement.review_id,
            "service",
            {"aph_id": raw["PHID"], "parliament_number": 42},
            action="supersede",
            supersedes=[replacement.decision_id],
            replacement_action="research",
        )
        restored = project_review_records(conn, [replacement, withdrawn], matcher)
        assert restored["parliament_service"] == []
        assert restored["members"] == projected["members"]
        assert restored["member_education"] == projected["member_education"]
        write_log(path, [replacement, withdrawn])
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute("SELECT count(*) FROM parliament_service").fetchone() == (
            0,
        )
        assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (1,)


def test_aph_refresh_retains_consumed_stage_and_manifest_provenance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    write_log(path, [])
    consumed_manifest = b'{"files":{"acara":{"sha256":"consumed"}}}\n'
    manifest = tmp_path / "inputs.json"
    manifest.write_bytes(consumed_manifest)
    stages = {
        "acara": {"files": {"source.csv": "original-acara-hash"}},
        "wikimedia": {"member_caches": {"hist.json": "original-wikimedia-hash"}},
    }
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[historical_member()],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        capture_review_snapshot(
            conn,
            [],
            decision_log_path=path,
            source_provenance={
                **review_snapshot_metadata(conn)["source_provenance"],
                **stages,
            },
        )
        before = attach_source_manifest(conn, manifest)
        manifest.write_text('{"files":{"acara":{"sha256":"newer-unconsumed"}}}')
        run_aph_ingestion(
            [42],
            raw_individuals=[historical_member()],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        after = review_snapshot_metadata(conn)
        assert after["source_manifest_sha256"] == before["source_manifest_sha256"]
        for stage, facts in stages.items():
            assert after["source_provenance"][stage] == facts
        assert (
            after["source_provenance"]["input_manifests"]
            == before["source_provenance"]["input_manifests"]
        )
        assert conn.execute(
            "SELECT manifest_json FROM review_build_input_manifests"
        ).fetchone() == (consumed_manifest,)


def test_retained_reviewed_person_refreshes_source_before_withdrawal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    raw["RepresentedParliaments"] = [48]
    raw["DateOfBirth"] = "1970-01-01"
    service = service_event("retained-member-service", 42)
    dob = event(
        "retained-member-dob",
        member_review_id("HIST", "date_of_birth"),
        "member",
        {"aph_id": "HIST", "field": "date_of_birth", "value": "1960-01-01"},
    )
    education = event(
        "retained-member-education",
        education_review_id("HIST", "Historic High School"),
        "member_education",
        {
            "aph_id": "HIST",
            "recorded_school_name": "Historic High School",
            "attended_status": "graduated",
            "confidence": "verified",
            "retrieved_at": "2026-10-05T00:00:00+00:00",
        },
    )
    accepted = [service, dob, education]
    write_log(path, accepted)
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        # Simulate independently acquired automatic QID/education source facts.
        conn.execute("UPDATE review_source_members SET wikidata_id = 'Q12345'")
        conn.execute(
            """INSERT INTO review_source_member_education
            SELECT * REPLACE ('wm-retained-hist' AS education_id,
                'Wikimedia Academy' AS school_name_as_recorded,
                'wikimedia' AS evidence_origin)
            FROM review_source_member_education"""
        )
        raw["DateOfBirth"] = "1975-01-01"
        raw["SecondarySchool"] = "Updated APH Academy"
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        effective = conn.execute(
            "SELECT date_of_birth, wikidata_id FROM members"
        ).fetchone()
        source = conn.execute(
            "SELECT date_of_birth, wikidata_id FROM review_source_members"
        ).fetchone()
        assert effective is not None and source is not None
        assert str(effective[0]) == "1960-01-01"
        assert str(source[0]) == "1975-01-01"
        assert effective[1] == source[1] == "Q12345"
        assert conn.execute(
            """SELECT school_name_as_recorded, evidence_origin
            FROM review_source_member_education ORDER BY school_name_as_recorded"""
        ).fetchall() == [
            ("Updated APH Academy", "aph"),
            ("Wikimedia Academy", "wikimedia"),
        ]
        withdrawals = [
            event(
                f"withdraw-{decision.decision_id}",
                decision.review_id,
                decision.entity_type,
                {
                    key: value
                    for key, value in decision.payload.items()
                    if key
                    in {"aph_id", "field", "recorded_school_name", "parliament_number"}
                },
                action="supersede",
                supersedes=[decision.decision_id],
                replacement_action="research",
            )
            for decision in accepted
        ]
        write_log(path, accepted + withdrawals)
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        restored = conn.execute(
            "SELECT date_of_birth, wikidata_id FROM members"
        ).fetchone()
        assert restored == source
        assert conn.execute("SELECT count(*) FROM parliament_service").fetchone() == (
            0,
        )
        assert conn.execute(
            """SELECT school_name_as_recorded, evidence_origin
            FROM member_education ORDER BY school_name_as_recorded"""
        ).fetchall() == [
            ("Updated APH Academy", "aph"),
            ("Wikimedia Academy", "wikimedia"),
        ]


@pytest.mark.parametrize(
    "action,intervals", [("accept", []), ("research", None), ("reject", None)]
)
def test_nonadding_service_decisions_do_not_create_phantom_members(
    tmp_path: Path,
    action: str,
    intervals: list[object] | None,
) -> None:
    path = tmp_path / "decisions.jsonl"
    raw = historical_member()
    raw["RepresentedParliaments"] = [48]
    payload: dict[str, object] = {"aph_id": "HIST", "parliament_number": 42}
    if intervals is not None:
        payload["intervals"] = intervals
    write_log(
        path,
        [
            event(
                "nonadding-service",
                service_review_id("HIST", 42),
                "service",
                payload,
                action=action,
            )
        ],
    )
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            decision_log_path=path,
        )
        assert conn.execute("SELECT count(*) FROM members").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM parliament_service").fetchone() == (
            0,
        )
