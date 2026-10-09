"""Independent successor claims adapt copies without rewriting authority."""

from dataclasses import replace
from typing import Any

import pytest

from apemap.review.context_evidence import (
    ROLE_TYPES,
    attendance_period,
    context_report,
    expand_context_events,
    resolve_context,
)
from apemap.review.evidence import EvidenceRecord
from apemap.review.model import ReviewEvent, education_review_id
from apemap.review.service import ReviewService
from tests.test_review_service import review_service as review_service

CASE = education_review_id("TEST", "Test High School")
CLAIMS = {
    "original_identity": {"institution_ref": "acara:1"},
    "location": {"latitude": -42, "longitude": 147},
    "campus": {"value": "different_campus"},
    "broad_sector": {"broad_sector": "Non-government"},
    "detailed_sector": {"detailed_sector": "Catholic"},
    "relationship_timing": {"start_year": 1990, "end_year": None},
    "profile_proxy": {"status": "unsuitable", "reporting_year": 2025},
    "finance_proxy": {"status": "approved", "reporting_year": 2023},
}


def records() -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            review_id=CASE,
            candidate_institution_ref="acara:2",
            source_title=role,
            source_type="history",
            source_url="https://example.org/" + role,
            retrieved_at="2026-10-10T00:00:00+00:00",
            claim_type=ROLE_TYPES[role],
            claim_value=value,
            stance="supports",
            excerpt_or_note="Reviewed public history",
            generated_by="manual",
        )
        for role, value in CLAIMS.items()
    ]


def event(retained: list[EvidenceRecord]) -> ReviewEvent:
    return ReviewEvent(
        decision_id="context-fixture",
        review_id=CASE,
        entity_type="member_education",
        action="map",
        payload={
            "aph_id": "TEST",
            "recorded_school_name": "Test High School",
            "institution_ref": "acara:2",
            "relationship_type": "successor",
            "context_evidence_refs": {
                role: [record.evidence_id]
                for role, record in zip(CLAIMS, retained, strict=True)
            },
        },
        source_url="https://example.org/conclusion",
        reviewer="Reviewer",
        recorded_at="2026-10-10T00:00:00+00:00",
        reviewed_at="2026-10-10",
    )


def test_roles_are_independent_and_serialized_events_unchanged() -> None:
    retained = records()
    selected = event(retained)
    before = selected.to_dict()
    expanded = expand_context_events([selected], retained)[0]
    assert selected.to_dict() == before
    assert expanded.payload["attended_institution_ref"] == "acara:1"
    assert expanded.payload["historical_broad_sector"] == "Non-government"
    assert (
        "years_attended" not in expanded.payload
        and "reporting_year" not in expanded.payload
    )
    report = context_report([selected], retained)
    assert (
        report["cases"][0]["claims"]["profile_proxy"]["value"]["status"] == "unsuitable"
    )
    assert (
        report["cases"][0]["claims"]["finance_proxy"]["value"]["reporting_year"] == 2023
    )


@pytest.mark.parametrize("change", ["target", "stance", "type", "conflict", "scope"])
def test_incompatible_context_cannot_enter_replay(change: str) -> None:
    retained = records()
    selected = event(retained)
    original = retained[0]
    replacement = replace(
        original,
        evidence_id="",
        **{
            "target": {"candidate_institution_ref": "acara:999"},
            "stance": {"stance": "contradicts"},
            "type": {"claim_type": "attendance"},
            "conflict": {"claim_value": {"institution_ref": "acara:2"}},
            "scope": {"review_id": education_review_id("OTHER", "Test High School")},
        }[change],
    )
    if change == "conflict":
        retained.append(replacement)
        selected.payload["context_evidence_refs"]["original_identity"].append(
            replacement.evidence_id
        )
    else:
        retained[0] = replacement
        selected.payload["context_evidence_refs"]["original_identity"] = [
            replacement.evidence_id
        ]
    with pytest.raises(ValueError):
        resolve_context(selected, retained)


def test_selected_context_projects_through_service(
    review_service: ReviewService,
) -> None:
    service = review_service
    retained = [
        service.save_evidence(service.prepare_evidence(CASE, record.to_dict()))
        for record in records()
    ]
    selected = event(retained)
    preview = service.prepare(
        CASE,
        "map",
        selected.payload,
        source_url=selected.source_url,
        reviewer="Reviewer",
    )
    assert (
        preview["context_claims"]["campus"]["value"]["campus_continuity"]
        == "different_campus"
    )
    service.save(preview)
    assert service.check()["valid"]
    assert service.show(CASE)["context"]["reviewed_context_claims"][
        "relationship_timing"
    ]


def test_retained_roles_replay_offline_into_existing_contract(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.db import get_connection
    from apemap.review.integration import (
        apply_review_events,
        capture_review_snapshot,
        read_review_events,
    )
    from apemap.review.readiness import consumed_review_inputs

    service = review_service
    retained = [
        service.save_evidence(service.prepare_evidence(CASE, record.to_dict()))
        for record in records()
    ]
    selected = event(retained)
    service.save(
        service.prepare(
            CASE,
            "map",
            selected.payload,
            source_url=selected.source_url,
            reviewer="Reviewer",
        )
    )
    decision_bytes = service.log_path.read_bytes()
    evidence_bytes = service.evidence_path.read_bytes()
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    consumed = read_review_events(service.log_path)
    with get_connection(service.db_path) as conn:
        apply_review_events(conn, events=consumed, matcher=service.matcher())
        actual = conn.execute(
            "SELECT attended_school_id, broad_sector, detailed_sector, sector_basis FROM v_education_attendance_context WHERE education_id='edu-test'"
        ).fetchone()
        assert actual == (
            "acara-1",
            "Non-government",
            "Catholic",
            "historical_verified",
        )
        capture_review_snapshot(
            conn,
            consumed,
            decision_log_path=service.log_path,
            evidence_log_path=service.evidence_path,
        )
        events, frozen, _provenance = consumed_review_inputs(conn)
        assert (
            context_report(events, frozen)["cases"][0]["claims"]["finance_proxy"][
                "value"
            ]["status"]
            == "approved"
        )
    assert service.log_path.read_bytes() == decision_bytes
    assert service.evidence_path.read_bytes() == evidence_bytes


@pytest.mark.parametrize(
    "assertion,basis,start,end",
    [
        ({"years_attended": "1980–1986"}, "recorded", 1980, 1986),
        ({"graduation_year": 1986}, "recorded_endpoint", None, 1986),
        ({}, "estimated", 1982, 1988),
    ],
)
def test_sourced_periods_take_precedence_over_research_estimates(
    assertion: dict[str, Any], basis: str, start: int | None, end: int
) -> None:
    value = attendance_period({"date_of_birth": "1970-01-01"}, assertion)
    assert (value["basis"], value["start_year"], value["end_year"]) == (
        basis,
        start,
        end,
    )
