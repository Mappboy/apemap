"""Revalidation reproduces missing-school context and every proposed target."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from apemap.db import get_connection
from apemap.review.research_batches import ResearchBatches, _digest, _encoded
from apemap.review.service import ReviewService, build_review_event
from apemap.review.store import StaleReviewError
from tests.test_missing_education_batches import (
    RID,
    missing_batches as missing_batches,
    result,
)
from tests.test_research_batches import accept
from tests.test_review_service import review_service as review_service


@pytest.fixture
def revalidation_batches(missing_batches: ResearchBatches) -> ResearchBatches:
    service = missing_batches.service
    with get_connection(service.db_path) as conn:
        for table in ("parliament_service", "review_source_parliament_service"):
            conn.execute(f"UPDATE {table} SET is_opening_day_member = true")
    register = service.external_dir / "school-location-2025.csv"
    register.rename(service.external_dir / "school-location-2022.csv")
    state = missing_batches.show("missing")
    task = state["tasks"][0]
    task["context"].update(review_id=RID, parliament=48)
    task["context"] = missing_batches._contexts(service, [task], [])[RID]
    manifest = state["manifest"]
    manifest["source_revision"] = service.source_revision(include_evidence=False)
    manifest["task_sha256"] = _digest([task])
    path = missing_batches.path("missing")
    (path / "tasks.json").write_bytes(_encoded([task]))
    (path / "manifest.json").write_bytes(_encoded(manifest))
    return missing_batches


def current_batches(
    batches: ResearchBatches, tmp_path: Path
) -> tuple[ResearchBatches, Path]:
    external = tmp_path / "current-register"
    external.mkdir()
    register = external / "school-location-2025.csv"
    register.write_bytes(
        (batches.service.external_dir / "school-location-2022.csv").read_bytes()
    )
    return (
        ResearchBatches(
            ReviewService(
                log_path=batches.service.log_path,
                db_path=batches.service.db_path,
                external_dir=external,
                evidence_path=batches.service.evidence_path,
            )
        ),
        register,
    )


def test_missing_context_projects_the_given_events_instead_of_live_member_details(
    revalidation_batches: ResearchBatches,
) -> None:
    batches = revalidation_batches
    tasks = batches.show("missing")["tasks"]
    correction = build_review_event(
        "member:test:date_of_birth",
        "accept",
        {"aph_id": "TEST", "field": "date_of_birth", "value": "1971-02-03"},
        source_url="https://example.org/identity",
        reviewer="Explicit fixture reviewer",
    )
    before = batches._contexts(batches.service, tasks, [])[RID]
    after = batches._contexts(batches.service, tasks, [correction])[RID]
    assert before["member"]["date_of_birth"] == "1970-01-01"
    assert after["member"]["date_of_birth"] == "1971-02-03"
    assert batches._normalized_context(before) != batches._normalized_context(after)
    assert not batches.service.events()


@pytest.mark.parametrize(
    "field", ["party", "electorate", "chamber", "is_opening_day_member"]
)
def test_missing_context_normalizes_export_types_and_keeps_service_facts(
    revalidation_batches: ResearchBatches, field: str
) -> None:
    context = revalidation_batches.show("missing")["tasks"][0]["context"]
    exported = deepcopy(context)
    exported["services"][0]["parliament_number"] = "48"
    exported["services"][0]["is_opening_day_member"] = "True"
    exported["services"][0]["is_current_member"] = str(
        context["services"][0]["is_current_member"]
    )
    assert ResearchBatches._normalized_context(
        context
    ) == ResearchBatches._normalized_context(exported)
    exported["services"][0][field] = (
        False if field == "is_opening_day_member" else "Different fact"
    )
    assert ResearchBatches._normalized_context(
        context
    ) != ResearchBatches._normalized_context(exported)


def test_named_education_context_keeps_top_level_service_facts() -> None:
    context: dict[str, Any] = {
        "review_id": "education:test:0123456789abcdef",
        "member": {"aph_id": "TEST"},
        "assertion": {"recorded_school_name": "Test High School"},
        "services": [{"party": "Labor", "parliament_number": 48}],
    }
    changed = deepcopy(context)
    changed["services"][0]["party"] = "Independent"
    assert ResearchBatches._normalized_context(
        context
    ) != ResearchBatches._normalized_context(changed)


def test_missing_context_rejects_new_attendance_or_lost_opening_membership(
    revalidation_batches: ResearchBatches,
) -> None:
    batches = revalidation_batches
    tasks = batches.show("missing")["tasks"]
    payload = result()["education_proposals"][0]
    payload.pop("evidence_refs")
    attendance = build_review_event(
        RID,
        "accept",
        payload,
        source_url="https://example.org/attendance",
        reviewer="Explicit fixture reviewer",
    )
    with pytest.raises(StaleReviewError, match="no longer has missing"):
        batches._contexts(batches.service, tasks, [attendance])
    with get_connection(batches.service.db_path) as conn:
        conn.execute(
            "UPDATE review_source_parliament_service SET is_opening_day_member = false"
        )
    with pytest.raises(StaleReviewError, match="opening cohort"):
        batches._contexts(batches.service, tasks, [])
    assert not batches.service.events()


def test_missing_revalidation_checks_every_target_without_applying_decisions(
    revalidation_batches: ResearchBatches, tmp_path: Path
) -> None:
    batches = revalidation_batches
    proposal = batches.import_results("missing", [result(two_schools=True)])[
        "proposals"
    ][0]
    accept(batches, "missing", proposal)
    batches.retain("missing")
    continued, register = current_batches(batches, tmp_path)
    preview = continued.revalidate(
        "missing",
        original_external_dir=batches.service.external_dir,
        destination=tmp_path / "revalidated.json",
        reviewer="Explicit fixture reviewer",
    )
    report = preview["preview"]["research_batch"]["revalidation"]
    assert set(report["target_metadata"]) == {"acara:1", "acara:2"}
    assert all(
        target["original"]["institution_status"] == "unknown"
        and target["current"]["institution_status"] == "current"
        for target in report["target_metadata"].values()
    )
    assert len(preview["preview"]["events"]) == 2
    continued.validate_preview(preview["preview"])
    register.write_bytes(
        register.read_bytes().replace(b"Other High School", b"Different School")
    )
    with pytest.raises(StaleReviewError, match="identity or location"):
        continued.revalidate(
            "missing",
            original_external_dir=batches.service.external_dir,
            destination=tmp_path / "changed-target.json",
            reviewer="Explicit fixture reviewer",
        )
    assert not batches.service.events()
