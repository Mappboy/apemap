"""Honest session research uses the normal approval workflow at assertion scope."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from apemap.review.evidence import EvidenceRecord
from apemap.review.model import education_review_id, school_review_id
from apemap.review.manual_institutions import build_manual_institution_candidate
from apemap.review.research_batches import ResearchBatches
from apemap.review.service import ReviewService
from tests.test_research_batches import accept, batches as batches, evidence
from tests.test_review_service import review_service as review_service
from tests.test_manual_research_batches import facts_and_evidence


PROVENANCE = {
    "generated_by": "codex-session",
    "model": None,
    "token_usage": None,
    "availability_note": "Exact model identifier and token usage are unavailable.",
}
NAME = "Test High School"
RID = education_review_id("test", NAME)


def session_proposal(rid: str = RID) -> dict[str, Any]:
    record = evidence(
        rid,
        "https://example.org/attendance",
        {"aph_id": rid.split(":")[1], "recorded_school_name": NAME},
        "attendance",
    )
    school = evidence(
        school_review_id(NAME),
        "https://example.org/school",
        {"name": NAME, "country": "Australia"},
        "identity",
    )
    school = replace(
        EvidenceRecord.from_dict(school),
        candidate_institution_ref="acara:2",
        evidence_id="",
    ).to_dict()
    return {
        "review_id": rid,
        "outcome": "mapping",
        "value": None,
        "reasoning": "Person-specific attendance identifies this institution.",
        "alternatives": [],
        "identity": {},
        "payload": {
            "aph_id": rid.split(":")[1],
            "recorded_school_name": NAME,
            "institution_ref": "acara:2",
            "relationship_type": "direct",
            "evidence_refs": [record["evidence_id"], school["evidence_id"]],
        },
        "manual_institutions": [],
        "evidence": [record, school],
        "attempts": [],
    }


def prepare_session(
    batches: ResearchBatches, batch_id: str = "session"
) -> dict[str, Any]:
    return batches.prepare(
        batch_id,
        "schools",
        review_ids=[RID],
        _origin="agent-session",
        _provenance=PROVENANCE,
    )


@pytest.fixture
def exact_batches(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> ResearchBatches:
    monkeypatch.setattr(
        batches.service,
        "candidates",
        lambda kind: (
            [{"review_id": RID, "evidence": {}}] if kind == "member_education" else []
        ),
    )
    monkeypatch.setattr(
        batches.service,
        "research_context",
        lambda rid: {
            "review_id": rid,
            "member": {"aph_id": "test", "display_name": "Test Person"},
            "assertion": {"recorded_school_name": NAME},
            "candidates": [],
        },
    )
    return batches


def test_session_records_honest_provenance_without_model_attempts(
    exact_batches: ResearchBatches,
) -> None:
    prepared = prepare_session(exact_batches)
    assert prepared["manifest"]["provenance"] == PROVENANCE
    assert prepared["manifest"]["models"] == []
    assert prepared["manifest"]["attempt_budgets"] == []
    assert prepared["tasks"][0]["context"]["member"]["aph_id"] == "test"
    state = exact_batches.import_results("session", [session_proposal()])
    assert state["proposals"][0]["attempts"] == []
    assert state["sample"] == []
    assert not exact_batches.service.events()


@pytest.mark.parametrize("missing", list(PROVENANCE))
def test_session_requires_unavailable_model_provenance(
    exact_batches: ResearchBatches, missing: str
) -> None:
    provenance = {key: value for key, value in PROVENANCE.items() if key != missing}
    with pytest.raises(ValueError, match="provenance"):
        exact_batches.prepare(
            "invalid",
            "schools",
            review_ids=[RID],
            _origin="agent-session",
            _provenance=provenance,
        )
    assert not exact_batches.path("invalid").exists()


@pytest.mark.parametrize(
    "change",
    [
        "member",
        "name",
        "other_evidence",
        "attempts",
        "empty_refs",
        "wrong_target",
        "unresolved_target",
        "missing_target",
        "generic_target",
    ],
)
def test_session_rejects_other_assertions_and_inferred_attempts(
    exact_batches: ResearchBatches, change: str
) -> None:
    prepare_session(exact_batches)
    raw = session_proposal()
    if change == "member":
        raw["payload"]["aph_id"] = "other"
    elif change == "name":
        raw["payload"]["recorded_school_name"] = "Other School"
    elif change == "other_evidence":
        raw["evidence"][0] = replace(
            EvidenceRecord.from_dict(raw["evidence"][0]),
            review_id=education_review_id("other", NAME),
            evidence_id="",
        ).to_dict()
    elif change == "attempts":
        raw["attempts"] = [{"model": "gpt-6-luna"}]
    elif change == "empty_refs":
        raw["payload"]["evidence_refs"] = []
    elif change == "wrong_target":
        wrong = replace(
            EvidenceRecord.from_dict(raw["evidence"][-1]),
            candidate_institution_ref="acara:1",
            evidence_id="",
        ).to_dict()
        raw["evidence"][-1] = wrong
        raw["payload"]["evidence_refs"][-1] = wrong["evidence_id"]
    elif change == "unresolved_target":
        raw["outcome"] = "unresolved"
    elif change == "generic_target":
        generic = replace(
            EvidenceRecord.from_dict(raw["evidence"][-1]),
            candidate_institution_ref=None,
            evidence_id="",
        ).to_dict()
        raw["evidence"][-1] = generic
        raw["payload"]["evidence_refs"][-1] = generic["evidence_id"]
        raw["payload"]["institution_ref"] = "acara:1"
    else:
        raw["payload"] = None
    with pytest.raises(ValueError):
        exact_batches.import_results("session", [raw])


def test_session_preserves_source_aph_id_casing(exact_batches: ResearchBatches) -> None:
    prepare_session(exact_batches)
    raw = session_proposal()
    raw["payload"]["aph_id"] = "TEST"
    item = exact_batches.import_results("session", [raw])["proposals"][0]
    assert item["payload"]["aph_id"] == "TEST"


def test_session_evidence_does_not_weaken_guided_attempt_validation(
    exact_batches: ResearchBatches,
) -> None:
    prepare_session(exact_batches)
    task = exact_batches.show("session")["tasks"][0]
    with pytest.raises(ValueError, match="Luna"):
        exact_batches._validate_result("schools", task, session_proposal())


@pytest.mark.integration
def test_session_historical_definition_and_exact_mapping_share_normal_preview(
    review_service: ReviewService, tmp_path: Path
) -> None:
    batches = ResearchBatches(review_service)
    prepare_session(batches)
    raw = session_proposal()
    facts, records = facts_and_evidence(school_review_id(NAME), name=NAME)
    candidate = build_manual_institution_candidate(facts, records, [])
    raw["manual_institutions"] = [candidate["definition"]]
    raw["payload"]["institution_ref"] = candidate["institution_ref"]
    identity = replace(
        EvidenceRecord.from_dict(raw["evidence"][-1]),
        candidate_institution_ref=candidate["institution_ref"],
        evidence_id="",
    ).to_dict()
    raw["evidence"][-1] = identity
    raw["payload"]["evidence_refs"][-1] = identity["evidence_id"]
    raw["evidence"].extend(records)
    item = batches.import_results("session", [raw])["proposals"][0]
    accept(batches, "session", item)
    batches.retain("session")
    result = batches.preview("session", tmp_path / "historical.json")
    events = result["preview"]["events"]
    assert [event["entity_type"] for event in events] == [
        "manual_institution",
        "member_education",
    ]
    assert events[-1]["action"] == "map"
    assert events[0]["source_url"] == records[0]["source_url"]
    assert events[-1]["payload"]["evidence_refs"] == raw["payload"]["evidence_refs"]
    batches.validate_preview(result["preview"])
    assert not review_service.events()


@pytest.mark.integration
@pytest.mark.parametrize("mapped", [True, False])
def test_session_normal_ui_review_retain_preview_apply(
    review_service: ReviewService, tmp_path: Path, mapped: bool
) -> None:
    pytest.importorskip("flask")
    from apemap.review.gui import create_app

    batches = ResearchBatches(review_service)
    prepare_session(batches)
    raw = session_proposal()
    if not mapped:
        raw["payload"] = None
        raw["outcome"] = "unresolved"
    item = batches.import_results("session", [raw])["proposals"][0]
    with create_app(review_service).test_client() as client:
        listing = client.get("/research-batches")
        assert listing.status_code == 200 and b"session" in listing.data
        response = client.get(
            "/research-batches/session?queue_view=schools&queue_page=1"
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "Test Person" in html and NAME in html
        assert "/items/" + RID in html
        assert "queue_view=schools" in html
        assert "research-packets" not in html
        assert "Record individual review" in html
    assert not review_service.events()
    accept(batches, "session", item)
    assert batches.retain("session")["retained"] == 2
    assert not review_service.events()
    result = batches.preview("session", tmp_path / "preview.json", reviewer="Reviewer")
    event = result["preview"]["events"][0]
    assert event["review_id"] == RID
    assert event["action"] == ("map" if mapped else "research")
    assert event["payload"]["recorded_school_name"] == NAME
    if not mapped:
        assert event["payload"]["resolution_only"] is True
    batches.validate_preview(deepcopy(result["preview"]))
    applied = batches.apply("session", Path(result["preview_path"]), result["sha256"])
    assert applied["new_events"] == 1
    assert len(batches.show("session")["applied_proposals"]) == 1
    assert not any(
        event.review_id.startswith("school:") for event in review_service.events()
    )
