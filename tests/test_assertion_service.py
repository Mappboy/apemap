"""Assertion previews, comparison context and school-default concurrency guards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection
from apemap.review.integration import capture_review_sources
from apemap.review.model import education_review_id, school_review_id
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_service import review_service as _review_service

pytestmark = pytest.mark.integration
assertion_service = _review_service


def _map(service: ReviewService, aph_id: str, target: str) -> dict[str, Any]:
    return service.prepare(
        education_review_id(aph_id, "Test High School"),
        "map",
        {
            "aph_id": aph_id,
            "recorded_school_name": "Test High School",
            "institution_ref": target,
            "relationship_type": "direct",
        },
        source_url="https://example.org/attendance",
        reviewer="Fixture reviewer",
    )


def _add_member(service: ReviewService) -> None:
    with get_connection(service.db_path) as conn:
        conn.execute(
            "INSERT INTO members (member_id, aph_id, family_name, given_name, display_name) VALUES ('aph-other', 'OTHER', 'Other', 'Test', 'Test Other')"
        )
        conn.execute(
            "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded) SELECT 'edu-other', 'aph-other', institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded FROM member_education WHERE education_id = 'edu-test'"
        )
        capture_review_sources(conn)


def _default(service: ReviewService) -> dict[str, Any]:
    return service.prepare(
        school_review_id("Test High School"),
        "map",
        {
            "recorded_name": "Test High School",
            "institution_ref": "acara:1",
            "relationship_type": "alias",
        },
        reviewer="Fixture reviewer",
    )


def test_default_preview_lists_protected_assertions_and_warns_about_split(
    assertion_service: ReviewService,
) -> None:
    service = assertion_service
    _add_member(service)
    service.save(_map(service, "TEST", "acara:2"))
    preview = _default(service)
    affected = {
        row["aph_id"]: row["applies"]
        for row in preview["default_application"]["assertions"]
    }
    assert affected == {"OTHER": True, "TEST": False}
    warnings = preview["relationship_conflicts"]
    assert len(warnings) == 1
    assert warnings[0]["institution_ref"] == "acara:2"
    assert warnings[0]["default_institution_ref"] == "acara:1"
    service.save(preview)
    assert service.check()["relationship_conflicts"] == warnings
    for review_id in (
        school_review_id("Test High School"),
        education_review_id("TEST", "Test High School"),
    ):
        context = service.show(review_id)["context"]
        resolutions = {
            member["aph_id"]: member["education"][0]["current_resolution"]
            for member in context["members"]
        }
        assert resolutions["TEST"]["institution_ref"] == "acara:2"
        assert resolutions["TEST"]["resolution_scope"] == "assertion"
        assert resolutions["OTHER"]["resolution_scope"] == "school_default"
        assert resolutions["TEST"]["institution_name"] == "Other High School"
        assert context["resolution_warnings"] == warnings


def test_same_school_assertion_change_invalidates_default_preview(
    assertion_service: ReviewService,
) -> None:
    service = assertion_service
    default = _default(service)
    service.save(_map(service, "TEST", "acara:1"))
    # Identity stayed the same, but the affected-assertion list changed scope.
    with pytest.raises(StaleReviewError):
        service.save(default)


def test_cli_resolution_preview_and_save_preserve_source_attendance(
    assertion_service: ReviewService,
) -> None:
    service = assertion_service
    command = [
        "review",
        "--db-path",
        str(service.db_path),
        "--log-path",
        str(service.log_path),
        "--external-dir",
        str(service.external_dir),
        "map-education",
        education_review_id("TEST", "Test High School"),
        "--institution-ref",
        "acara:2",
        "--relationship-type",
        "direct",
        "--source",
        "https://example.org/attendance",
        "--reviewer",
        "Fixture reviewer",
    ]
    runner = CliRunner()
    preview = runner.invoke(app, [*command, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert not service.log_path.exists()
    assert json.loads(preview.output)["event"]["action"] == "map"
    saved = runner.invoke(app, command)
    assert saved.exit_code == 0, saved.output
    event = json.loads(saved.output)
    assert event["payload"]["institution_ref"] == "acara:2"
    with get_connection(service.db_path, read_only=True) as conn:
        from apemap.review.integration import project_review_records

        projected = project_review_records(
            conn, service.events(), matcher=service.matcher()
        )
        row = projected["member_education"][0]
        assert row["education_id"] == "edu-test"
        assert row["source_url"] == "https://example.org/bio"
        assert row["resolution_source_url"] == "https://example.org/attendance"


def test_manual_attendance_survives_resolution_in_comparison_context(
    assertion_service: ReviewService,
) -> None:
    service = assertion_service
    name = "Manually Reported School"
    review_id = education_review_id("TEST", name)
    accepted = service.save(
        service.prepare(
            review_id,
            "accept",
            {
                "aph_id": "TEST",
                "recorded_school_name": name,
                "attended_status": "graduated",
                "graduation_year": 1985,
                "confidence": "provisional",
                "retrieved_at": "2026-10-02T00:00:00+00:00",
                "institution_ref": "acara:1",
            },
            source_url="https://example.org/manual-attendance",
            reviewer="Fixture reviewer",
        )
    )
    mapped = service.prepare(
        review_id,
        "map",
        {
            "aph_id": "TEST",
            "recorded_school_name": name,
            "institution_ref": "acara:2",
            "relationship_type": "alias",
        },
        source_url="https://example.org/manual-resolution",
        reviewer="Fixture reviewer",
        supersedes=[accepted.decision_id],
    )
    service.save(mapped)
    context = service.show(school_review_id(name))["context"]
    assert len(context["members"]) == 1
    row = context["members"][0]["education"][0]
    assert row["review_id"] == review_id
    assert row["source_url"] == "https://example.org/manual-attendance"
    assert row["attended_status"] == "graduated"
    assert row["current_resolution"]["institution_ref"] == "acara:2"
    preview = service.prepare(
        school_review_id(name),
        "map",
        {
            "recorded_name": name,
            "institution_ref": "acara:1",
            "relationship_type": "alias",
        },
        reviewer="Fixture reviewer",
    )
    assert preview["default_application"]["assertions"][0]["applies"] is False


def test_researched_attendance_is_unresolved_in_coverage_and_retained_exports(
    assertion_service: ReviewService,
    tmp_path: Path,
) -> None:
    from apemap.coverage import compute_parliament_coverage
    from apemap.export import export_canonical_parquet
    from apemap.review.integration import apply_review_events

    service = assertion_service
    with get_connection(service.db_path) as conn:
        conn.execute("UPDATE parliament_service SET is_opening_day_member = TRUE")
        capture_review_sources(conn)
    preview = service.prepare(
        education_review_id("TEST", "Test High School"),
        "research",
        {},
        notes="Same-name institutions need evidence",
        reviewer="Fixture reviewer",
    )
    service.save(preview)
    with get_connection(service.db_path) as conn:
        conn.execute("BEGIN")
        apply_review_events(conn, events=service.events(), matcher=service.matcher())
        conn.execute("COMMIT")
        coverage = compute_parliament_coverage(conn, [48])[0]
        assert coverage["secondary_education_assertions"] == 1
        assert coverage["unresolved_school_records"] == 1
        assert coverage["members_with_secondary_school"] == 0
        assert coverage["members_without_secondary_school"] == 1
        assert coverage["domestic_schools_matched_acara"] == 0
        paths = export_canonical_parquet(conn, tmp_path / "exports")
        row = conn.execute(
            "SELECT confidence, institution_resolution, historical_context_scope "
            "FROM read_parquet(?)",
            [str(paths["member_education"])],
        ).fetchone()
        assert row == ("verified", "unresolved", "assertion")
