"""Advisory scenarios preserve grains and frozen release provenance."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection
from apemap.release import build_release, verify_release
from apemap.review.integration import capture_review_snapshot
from apemap.review.readiness import (
    cohort_readiness,
    consumed_review_inputs,
    readiness_report,
)
from apemap.review.service import ReviewService
from tests.test_release import create_release_db
from tests.test_review_service import review_service as review_service


def member(identifier: str) -> dict[str, Any]:
    return {
        "member_id": identifier,
        "display_name": identifier,
        "party": "Party",
        "party_abbrev": "P",
        "chamber": "representatives",
    }


def row(
    person: str, identifier: str, school: str, name: str = "Ambiguous"
) -> dict[str, Any]:
    return {
        "education_id": identifier,
        "member_id": person,
        "attended_school_id": school,
        "attended_school_name": school,
        "school_name_as_recorded": name,
        "resolved_institution_id": school,
        "resolved_institution_name": school,
        "resolved_state": "TAS",
        "is_successor": False,
        "identity_basis": "original_reference",
        "location_basis": "original_reference",
        "broad_sector": "Government",
        "detailed_sector": "Government",
        "sector_basis": "original_reference",
        "broad_sector_basis": "original_reference",
        "detailed_sector_basis": "original_reference",
    }


def test_sector_shared_changes_unknown_coverage_and_no_mutation() -> None:
    members = [member("one"), member("two")]
    rows = [row("one", "e1", "acara-1"), row("two", "e2", "acara-2")]
    institutions = [
        {
            "institution_id": "acara-1",
            "school_name": "Ambiguous Government",
            "sector": "Government",
        },
        {
            "institution_id": "acara-2",
            "school_name": "Ambiguous Independent",
            "sector": "Independent",
        },
    ]
    before = deepcopy(rows)
    report = cohort_readiness(
        members, rows, institutions, [], [], 48, {"one": "ONE", "two": "TWO"}
    )
    assert rows == before
    assert report["baseline"]["total_parliamentarians"] == 2
    assert report["baseline"]["total_attendance_instances"] == 2
    assert report["joint_sector_bounds"]["unresolved_people"] == 2
    assert len(report["cases"]) == 2
    assert any(
        scenario["sector_impact"]
        for case in report["cases"]
        for scenario in case["scenarios"]
    )
    assert any(
        scenario["shared_top_ten_impact"]
        for case in report["cases"]
        for scenario in case["scenarios"]
    )
    assert all(
        case["candidate_coverage"] == "local_search_only" for case in report["cases"]
    )
    missing = cohort_readiness(
        members, rows, [], [], [], 48, {"one": "ONE", "two": "TWO"}
    )
    assert all(
        case["impact"] == "unknown" and not case["scenarios"]
        for case in missing["cases"]
    )
    gaps = cohort_readiness(
        members,
        [{**rows[0], "school_name_as_recorded": None}],
        [],
        [],
        [],
        48,
        {"one": "ONE", "two": "TWO"},
    )
    assert gaps["context_gaps"][0]["impact"] == "unknown"
    assert gaps["missing_attendance_members"]["member_ids"] == ["two"]


def test_joint_bounds_count_people_once_and_detect_threshold_ties() -> None:
    members = [member(str(i)) for i in range(35)]
    rows = [
        row(str(i), "e" + str(i), "existing-" + str(i // 3), "Known " + str(i // 3))
        for i in range(30)
    ]
    rows += [row(str(i), "e" + str(i), "unresolved-" + str(i)) for i in range(30, 35)]
    rows.append(row("30", "duplicate", "unresolved-30"))
    institutions = [
        {
            "institution_id": "acara-target",
            "school_name": "Ambiguous Target",
            "sector": "Government",
        }
    ]
    report = cohort_readiness(
        members, rows, institutions, [], [], 48, {str(i): str(i) for i in range(35)}
    )
    bound = report["joint_shared_school_bounds"][0]
    assert bound["upper_member_count"] == 5
    assert report["shared_top_ten_tie_threshold"] == 3
    assert bound["can_reach_top_ten_threshold_or_tie"]
    assert all(
        not scenario["shared_top_ten_impact"]
        for case in report["cases"]
        for scenario in case["scenarios"]
    )
    assert report == cohort_readiness(
        members, rows, institutions, [], [], 48, {str(i): str(i) for i in range(35)}
    )


def test_working_cli_report_does_not_change_database(
    review_service: ReviewService,
) -> None:
    before = hashlib.sha256(review_service.db_path.read_bytes()).hexdigest()
    report = review_service.readiness([48])
    assert report["advisory"] and report["schema_version"] == 1
    assert hashlib.sha256(review_service.db_path.read_bytes()).hexdigest() == before
    response = CliRunner().invoke(
        app,
        [
            "review",
            "--db-path",
            str(review_service.db_path),
            "--external-dir",
            str(review_service.external_dir),
            "--log-path",
            str(review_service.log_path),
            "readiness",
            "--parliament",
            "48",
        ],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output) == report


def test_release_reports_use_consumed_snapshot_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = create_release_db(tmp_path / "db.duckdb")
    log = tmp_path / "decisions.jsonl"
    with get_connection(db) as conn:
        capture_review_snapshot(conn, [], decision_log_path=log)
        events, evidence, provenance = consumed_review_inputs(conn)
        baseline = readiness_report(conn, events, evidence, [47], provenance)
    # A newer working log is deliberately invalid and must never be consumed.
    log.write_text("not a decision\n", encoding="utf-8")
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    output = tmp_path / "release"
    build_release(
        db_path=db,
        output_dir=output,
        version="0.7.0-rc.1",
        parliaments=[47],
        strict=False,
    )
    assert json.loads((output / "review/readiness.json").read_text()) == baseline
    assert (output / "review/successor-context.json").is_file()
    for filename in ("readiness.json", "successor-context.json"):
        assert b"\r\n" not in (output / "review" / filename).read_bytes()
    assert verify_release(output)["valid"]
    report = json.loads((output / "review/readiness.json").read_text())
    report["provenance"]["decision_log_sha256"] = "changed"
    (output / "review/readiness.json").write_text(json.dumps(report))
    assert not verify_release(output)["valid"]
