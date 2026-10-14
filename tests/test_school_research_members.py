"""School research keeps every member and separates attendance from identity."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Event
from typing import Any

import pytest

from apemap.db import get_connection
from apemap.review.evidence import (
    EvidenceRecord,
    evidence_applies,
    validate_evidence_references,
)
from apemap.review.model import ReviewEvent, education_review_id, school_review_id
from apemap.review.research import (
    RESEARCH_RESULT_SCHEMA,
    ResearchJobs,
    _case_prompt,
    validate_result,
)
from apemap.review.scoring import score_candidates
from apemap.review.service import ReviewService, school_research_members
from tests.test_review_service import review_service as review_service


NAME = "McCarthy Catholic College"
CASE = school_review_id(NAME)
URL = "https://example.org/biography"


def context(count: int = 1) -> dict[str, Any]:
    return {
        "review_id": CASE,
        "assertion": {"recorded_school_name": NAME},
        "members": [
            {
                "aph_id": f"M{index}",
                "display_name": f"Member {index}",
                "date_of_birth": "1970-01-02",
                "biography_url": f"https://handbook.aph.gov.au/individual/M{index}",
                "services": [{"state": "TAS", "electorate": "Test"}],
                "education": [
                    {
                        "review_id": education_review_id(f"M{index}", NAME),
                        "recorded_name": NAME,
                        "source_url": URL,
                        "years_attended": "1982–1987",
                        "graduation_year": 1987,
                        "attendance_period": {"basis": "recorded"},
                    }
                ],
            }
            for index in range(count)
        ],
        "candidates": [
            {"institution_ref": "acara:1", "school_name": NAME, "suburb": "Tamworth"},
            {"institution_ref": "acara:2", "school_name": NAME, "suburb": "Penrith"},
        ],
    }


def result(
    claim_type: str = "attendance",
    claim_value: Any = None,
    reference: str | None = None,
) -> dict[str, Any]:
    return {
        "sources": [{"url": URL, "title": "Biography"}],
        "suggestions": [
            {
                "candidate_institution_ref": reference,
                "source_url": URL,
                "claim_type": claim_type,
                "claim_value": claim_value
                or {"aph_id": "M0", "recorded_school_name": NAME, "claim": "Attended"},
                "stance": "supports",
                "excerpt_or_note": "The source names the member and school.",
            }
        ],
    }


@pytest.mark.integration
def test_school_context_includes_only_linked_members_and_assertions(
    review_service: ReviewService,
) -> None:
    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "INSERT INTO review_source_member_education (education_id, member_id, institution_id, level, attended_status, years_attended, graduation_year, source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin, institution_resolution) SELECT 'unrelated', member_id, institution_id, level, attended_status, years_attended, graduation_year, source_url, retrieved_at, confidence, 'Other School', evidence_origin, institution_resolution FROM review_source_member_education"
        )
    data = review_service.research_context(school_review_id("Test High School"))
    assert len(data["members"]) == 1
    member = data["members"][0]
    assert member["aph_id"] == "TEST"
    assert member["display_name"]
    assert member["biography_url"].endswith("/TEST")
    assert member["date_of_birth"] and member["services"]
    assert len(member["education"]) == 1
    assert member["education"][0]["recorded_name"] == "Test High School"
    assert member["education"][0]["attendance_period"]["basis"] == "estimated"


def test_prompt_names_each_member_school_locality_and_official_biography() -> None:
    prompt = _case_prompt(context(2))
    assert "Member 0" in prompt and "Member 1" in prompt
    assert NAME in prompt and "Tamworth" in prompt and "Penrith" in prompt
    assert "https://handbook.aph.gov.au/individual/M0" in prompt
    assert "this member" not in prompt
    assert "separately" in prompt and "Do not infer" in prompt


def test_no_member_prompt_researches_institution_without_inventing_person() -> None:
    prompt = _case_prompt(context(0))
    assert "this member" not in prompt
    assert "institution" in prompt and "Do not infer" in prompt


def test_member_projection_is_deterministic_and_keeps_sourced_attendance_precedence() -> (
    None
):
    members = context(3)["members"]
    members[1]["education"][0]["years_attended"] = None
    members[2]["education"][0]["years_attended"] = None
    members[2]["education"][0]["graduation_year"] = None
    original = deepcopy(members)
    projected = school_research_members(CASE, members)
    assert [row["education"][0]["attendance_period"]["basis"] for row in projected] == [
        "recorded",
        "recorded_endpoint",
        "estimated",
    ]
    assert projected == school_research_members(CASE, list(reversed(members)))
    assert members == original


@pytest.mark.parametrize("reference", ["acara:1", "acara:2"])
def test_generic_attendance_cannot_support_same_name_candidate(reference: str) -> None:
    with pytest.raises(ValueError, match="attendance|distinguish"):
        validate_result(context(), result(reference=reference))
    with pytest.raises(ValueError, match="distinguish"):
        validate_result(
            context(), result("identity", "Member 0 attended " + NAME, reference)
        )


def test_candidate_identity_requires_sourced_distinguishing_detail() -> None:
    claim = {
        "aph_id": "M0",
        "recorded_school_name": NAME,
        "claim": "The biography names Tamworth.",
        "identity_detail": {"field": "suburb", "value": "Tamworth"},
    }
    validated = validate_result(context(), result("identity", claim, "acara:1"))
    assert validated["suggestions"][0]["claim_value"]["aph_id"] == "M0"
    claim["identity_detail"]["value"] = "Penrith"
    with pytest.raises(ValueError, match="distinguish"):
        validate_result(context(), result("identity", claim, "acara:1"))
    ambiguous = context()
    ambiguous["candidates"][1]["suburb"] = None
    claim["identity_detail"]["value"] = "Tamworth"
    with pytest.raises(ValueError, match="distinguish"):
        validate_result(ambiguous, result("identity", claim, "acara:1"))


def test_strict_provider_schema_allows_reusable_campus_identity_without_member() -> (
    None
):
    branches = RESEARCH_RESULT_SCHEMA["properties"]["suggestions"]["items"][
        "properties"
    ]["claim_value"]["anyOf"]
    campus = next(
        branch
        for branch in branches
        if set(branch.get("properties", {})) == {"claim", "identity_detail"}
    )
    assert set(campus["required"]) == {"claim", "identity_detail"}
    assert campus["additionalProperties"] is False
    detail = campus["properties"]["identity_detail"]
    assert set(detail["required"]) == {"field", "value"}
    assert detail["additionalProperties"] is False
    claim = {
        "claim": "The school register identifies the Tamworth campus.",
        "identity_detail": {"field": "suburb", "value": "Tamworth"},
    }
    validated = validate_result(context(2), result("identity", claim, "acara:1"))
    record = EvidenceRecord.from_dict(validated["suggestions"][0])
    assert "aph_id" not in record.claim_value
    assert evidence_applies(record, education_review_id("M0", NAME))
    assert evidence_applies(record, education_review_id("M1", NAME))
    assert record.candidate_institution_ref == "acara:1"


def test_unsupplied_member_and_unattributed_attendance_are_rejected() -> None:
    with pytest.raises(ValueError, match="supplied"):
        validate_result(
            context(), result(claim_value={"aph_id": "OTHER", "claim": "Attended"})
        )
    with pytest.raises(ValueError, match="aph_id|member"):
        validate_result(context(), result(claim_value="A member attended this school"))


def test_member_claim_applies_only_to_its_exact_education_assertion() -> None:
    record = EvidenceRecord.from_dict(
        validate_result(context(), result())["suggestions"][0]
    )
    assert evidence_applies(record, education_review_id("M0", NAME))
    assert not evidence_applies(record, education_review_id("M1", NAME))
    assert not evidence_applies(record, education_review_id("M0", "Other School"))


def test_member_claim_cannot_change_school_default_ranking() -> None:
    payload = validate_result(
        context(),
        result(
            "identity",
            {
                "aph_id": "M0",
                "recorded_school_name": NAME,
                "claim": "Tamworth campus",
                "identity_detail": {"field": "suburb", "value": "Tamworth"},
            },
            "acara:1",
        ),
    )["suggestions"][0]
    record = EvidenceRecord.from_dict(
        {**payload, "match_strength": 1.0, "evidence_id": ""}
    )
    candidates = [{**row, "recorded_name": NAME} for row in context()["candidates"]]
    ranked = score_candidates(CASE, candidates, [record])
    assert [row["score"] for row in ranked] == [40, 40]
    assert ranked[0]["reasons"][-1]["note"].startswith("Member-specific")
    individual = score_candidates(education_review_id("M0", NAME), candidates, [record])
    assert individual[0]["institution_ref"] == "acara:1"
    assert individual[0]["score"] == 64


def test_parent_job_requests_every_member_deterministically_without_result_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    calls: list[dict[str, Any]] = []

    class Provider:
        def search(self, context: dict[str, Any]) -> dict[str, Any]:
            calls.append(deepcopy(context))
            return result(
                claim_value={
                    "aph_id": context["member"]["aph_id"],
                    "recorded_school_name": NAME,
                    "claim": "Attended",
                }
            )

    data = context(23)
    data["members"].reverse()
    jobs = ResearchJobs({"fixture": Provider()})
    try:
        identifier = jobs.start("fixture", data)
        job = jobs.get(identifier, CASE)
        job.future.result(timeout=5)
        output = jobs.result(identifier, CASE, {})
        assert output["status"] == "complete"
        assert output["completed_requests"] == output["total_requests"] == 23
        assert len(output["suggestions"]) == 23
        assert [call["member"]["aph_id"] for call in calls] == sorted(
            member["aph_id"] for member in data["members"]
        )
        assert all(
            len(call["members"]) == len(call["assertions"]) == 1 for call in calls
        )
        assert not any("institution_candidate" in row for row in output["suggestions"])
        assert data["members"][0]["aph_id"] == "M22"
    finally:
        jobs.close()


def test_partial_job_preserves_successes_and_reports_failed_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)

    class Provider:
        def search(self, context: dict[str, Any]) -> dict[str, Any]:
            if context["member"]["aph_id"] == "M1":
                raise ValueError("private-secret")
            return result()

    jobs = ResearchJobs({"fixture": Provider()})
    try:
        identifier = jobs.start("fixture", context(2))
        jobs.get(identifier, CASE).future.result(timeout=5)
        output = jobs.result(identifier, CASE, {})
        assert output["status"] == "partial"
        assert len(output["suggestions"]) == 1
        assert output["request_results"][1]["aph_id"] == "M1"
        assert output["request_results"][1]["status"] == "failed"
        assert "private-secret" not in str(output)
    finally:
        jobs.close()


def test_all_failed_members_raise_a_redacted_failure_after_processing_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    calls = []

    class Provider:
        def search(self, context: dict[str, Any]) -> dict[str, Any]:
            calls.append(context["member"]["aph_id"])
            raise RuntimeError("private-test-secret")

    jobs = ResearchJobs({"fixture": Provider()})
    try:
        identifier = jobs.start("fixture", context(2))
        with pytest.raises(RuntimeError, match="every requested") as failed:
            jobs.get(identifier, CASE).future.result(timeout=5)
        assert "private-test-secret" not in str(failed.value)
        output = jobs.result(identifier, CASE, {})
        assert output["status"] == "failed"
        assert output["completed_requests"] == output["total_requests"] == 2
        assert calls == ["M0", "M1"]
        assert all(row["status"] == "failed" for row in output["request_results"])
        assert "private-test-secret" not in str(output)
    finally:
        jobs.close()


def test_running_job_progress_and_expiry_start_at_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    entered, release = Event(), Event()

    class Provider:
        def search(self, context: dict[str, Any]) -> dict[str, Any]:
            entered.set()
            assert release.wait(5)
            return result()

    jobs = ResearchJobs({"fixture": Provider()})
    try:
        identifier = jobs.start("fixture", context())
        assert entered.wait(5)
        job = jobs.get(identifier, CASE)
        job.created_at -= 2000
        assert jobs.result(identifier, CASE, {})["status"] == "running"
        assert jobs.result(identifier, CASE, {})["total_requests"] == 1
        release.set()
        job.future.result(timeout=5)
        assert jobs.result(identifier, CASE, {})["status"] == "complete"
        assert job.completed_at is not None
        job.completed_at -= 1801
        with pytest.raises(ValueError, match="expired"):
            jobs.get(identifier, CASE)
    finally:
        release.set()
        jobs.close()


def test_candidate_fact_indexes_normalize_to_local_immutable_evidence_ids() -> None:
    raw = result("institution_fact", {"field": "name", "value": "Historical School"})
    raw["suggestions"].append(
        {
            **raw["suggestions"][0],
            "claim_value": {"field": "country", "value": "Australia"},
        }
    )
    raw["institution_candidate"] = {
        "name": "Historical School",
        "country": "Australia",
        "sector": None,
        "evidence_refs": {"name": [0], "country": [1]},
    }
    validated = validate_result(
        context(), raw, retrieved_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    facts = validated["institution_candidate"]
    for field, index in [("name", 0), ("country", 1)]:
        assert facts["evidence_refs"][field] == [
            EvidenceRecord.from_dict(validated["suggestions"][index]).evidence_id
        ]
    with pytest.raises(ValueError, match="evidence"):
        raw["institution_candidate"]["evidence_refs"]["name"] = [20]
        validate_result(context(), raw)


@pytest.mark.integration
def test_retained_member_identity_cannot_copy_or_support_school_default(
    review_service: ReviewService,
) -> None:
    case = school_review_id("Test High School")
    payload = result(
        "identity",
        {
            "aph_id": "TEST",
            "recorded_school_name": "Test High School",
            "claim": "The source identifies ACARA 1.",
            "identity_detail": {"field": "acara_id", "value": "1"},
        },
        "acara:1",
    )["suggestions"][0]
    payload.update(
        source_title="School source", source_type="biography", generated_by="manual"
    )
    record = review_service.save_evidence(
        review_service.prepare_evidence(case, payload)
    )
    with pytest.raises(ValueError, match="individual"):
        review_service.copy_school_evidence_value(case, record.evidence_id)
    assert (
        review_service.copy_school_evidence_value(
            education_review_id("TEST", "Test High School"), record.evidence_id
        )
        == "acara:1"
    )
    event = ReviewEvent(
        decision_id="default",
        review_id=case,
        entity_type="school",
        action="map",
        payload={
            "recorded_name": "Test High School",
            "institution_ref": "acara:1",
            "relationship_type": "direct",
            "evidence_refs": [record.evidence_id],
        },
        reviewer="Reviewer",
        source_url=URL,
        reviewed_at="2026-10-14",
        recorded_at="2026-10-14T00:00:00+00:00",
    )
    with pytest.raises(ValueError, match="school-wide default"):
        validate_evidence_references([event], [record])
    assert not review_service.log_path.exists()
    payload["claim_value"]["aph_id"] = "UNSUPPLIED"
    with pytest.raises(ValueError, match="supplied"):
        review_service.prepare_evidence(case, payload)


def test_revalidated_candidate_fact_ids_follow_the_local_retrieval_timestamp() -> None:
    raw = result("institution_fact", {"field": "name", "value": "Historical School"})
    raw["institution_candidate"] = {
        "name": "Historical School",
        "evidence_refs": {"name": [0]},
    }
    first = validate_result(
        context(), raw, retrieved_at=datetime(2026, 10, 13, tzinfo=timezone.utc)
    )
    second = validate_result(
        context(), first, retrieved_at=datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    assert (
        first["suggestions"][0]["evidence_id"]
        != second["suggestions"][0]["evidence_id"]
    )
    assert second["institution_candidate"]["evidence_refs"]["name"] == [
        second["suggestions"][0]["evidence_id"]
    ]


def test_parent_candidate_retains_only_its_child_evidence_and_trusted_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)

    class Provider:
        provider = "fixture-provider"
        model = "fixture-model"

        def search(self, context: dict[str, Any]) -> dict[str, Any]:
            raw = result(
                "institution_fact",
                {
                    "field": "name",
                    "value": context["member"]["display_name"] + " School",
                },
            )
            raw["institution_candidate"] = {
                "name": raw["suggestions"][0]["claim_value"]["value"],
                "evidence_refs": {"name": [0]},
            }
            return raw

    provider = Provider()
    jobs = ResearchJobs({"fixture": provider})
    try:
        identifier = jobs.start("fixture", context(2))
        job = jobs.get(identifier, CASE)
        job.future.result(timeout=5)
        output = jobs.result(identifier, CASE, {})
        assert len(output["institution_candidates"]) == 2
        for wrapper in output["institution_candidates"]:
            assert len(wrapper["evidence_ids"]) == 1
            assert wrapper["facts"]["evidence_refs"]["name"] == wrapper["evidence_ids"]
        assert (
            output["institution_candidates"][0]["evidence_ids"]
            != output["institution_candidates"][1]["evidence_ids"]
        )
        provider.model = "changed-model"
        assert job.provenance == {
            "provider_id": "fixture",
            "provider": "fixture-provider",
            "model": "fixture-model",
        }
    finally:
        jobs.close()
