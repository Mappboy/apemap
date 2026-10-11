"""Guided proposals, source inspection, frozen samples and atomic application."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection
from apemap.review.evidence import EvidenceRecord
from apemap.review.model import school_review_id
from apemap.review.preview import apply_preview
from apemap.review.research_batches import ResearchBatches, _digest, member_eligibility
from apemap.review.service import ReviewService, build_review_event
from apemap.review.store import StaleReviewError, append_events
from tests.test_review_service import review_service as review_service


def evidence(
    rid: str, url: str, value: Any = "Identity evidence", claim: str = "member_identity"
) -> dict[str, Any]:
    return EvidenceRecord(
        review_id=rid,
        candidate_institution_ref=None,
        source_title="Source",
        source_type="guided-research",
        source_url=url,
        retrieved_at="2026-10-12T00:00:00+00:00",
        claim_type=claim,
        claim_value=value,
        stance="supports",
        excerpt_or_note="Sourced conclusion",
        generated_by="agent-search",
    ).to_dict()


def proposal(task: dict[str, Any], number: int = 1) -> dict[str, Any]:
    rid, member = task["review_id"], task["context"]["member"]
    official = evidence(
        rid,
        "https://www.aph.gov.au/member/" + member["aph_id"],
        {"aph_id": member["aph_id"]},
    )
    wiki = evidence(
        rid,
        "https://www.wikidata.org/wiki/Q" + str(number),
        {"aph_id": member["aph_id"], "wikidata_id": "Q" + str(number)},
    )
    return {
        "review_id": rid,
        "outcome": "matched",
        "value": "Q" + str(number),
        "reasoning": "The official member and exact Wikidata APH identity agree.",
        "alternatives": [],
        "identity": {
            "name": member["display_name"],
            "aph_id": member["aph_id"],
            "evidence_refs": {"aph_id": [official["evidence_id"], wiki["evidence_id"]]},
        },
        "payload": None,
        "manual_institutions": [],
        "evidence": [official, wiki],
        "attempts": [
            {
                "model": "gpt-6-luna",
                "status": "resolved",
                "searches": 1,
                "source_opens": 2,
                "token_usage": None,
                "gaps": [],
            }
        ],
    }


@pytest.fixture
def batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ResearchBatches:
    service = ReviewService(
        log_path=tmp_path / "decisions.jsonl",
        db_path=tmp_path / "absent.duckdb",
        external_dir=tmp_path / "external",
    )
    monkeypatch.setattr(service, "source_revision", lambda **kwargs: "source")
    monkeypatch.setattr(
        service,
        "candidates",
        lambda kind: [
            {
                "review_id": f"member:m{i}:wikidata_id",
                "entity_type": "member",
                "evidence": {"effective_value": None},
            }
            for i in range(10)
        ],
    )
    monkeypatch.setattr(
        service,
        "research_context",
        lambda rid: {
            "review_id": rid,
            "member": {
                "aph_id": rid.split(":")[1],
                "display_name": "Member " + rid.split(":")[1],
                "date_of_birth": "1970-01-01",
                "aliases": [],
            },
            "services": [
                {
                    "chamber": "representatives",
                    "electorate": "Seat",
                    "service_start": "2020-01-01",
                    "service_end": None,
                    "party": "Labor",
                }
            ],
            "conflicts": [],
        },
    )
    return ResearchBatches(service)


def imported(batches: ResearchBatches) -> dict[str, Any]:
    prepared = batches.prepare("members", "members", 10)
    return batches.import_results(
        "members", [proposal(task, i + 1) for i, task in enumerate(prepared["tasks"])]
    )


def accept(
    batches: ResearchBatches, batch_id: str, item: dict[str, Any], **kwargs: Any
) -> None:
    batches.record_review(
        batch_id,
        item["proposal_id"],
        "accept",
        {e["evidence_id"]: e["stance"] for e in item["evidence"]},
        **kwargs,
    )


def test_reproducible_sample_requires_every_sample(batches: ResearchBatches) -> None:
    state = imported(batches)
    assert len(state["sample"]) == 5
    expected = sorted(
        [p["proposal_id"] for p in state["proposals"]],
        key=lambda pid: _digest([state["content_sha256"], pid]),
    )[:5]
    assert state["sample"] == expected == batches.show("members")["sample"]
    with pytest.raises(ValueError, match="every sample"):
        batches.retain("members", approve_sampled=True)
    for item in state["proposals"]:
        if item["proposal_id"] in state["sample"]:
            accept(batches, "members", item)
    assert batches.show("members")["sample_passed"]
    selected = batches._approved(batches.show("members"), True)
    assert {scope for _, scope in selected} == {"sample_inspected", "sample_unsampled"}
    assert batches.retain("members", approve_sampled=True)["retained"] == 20
    assert not batches.service.events()


def test_sample_identity_error_permanently_blocks_remainder(
    batches: ResearchBatches,
) -> None:
    state = imported(batches)
    items = {p["proposal_id"]: p for p in state["proposals"]}
    bad = items[state["sample"][0]]
    inspected = {e["evidence_id"]: e["stance"] for e in bad["evidence"]}
    batches.record_review(
        "members",
        bad["proposal_id"],
        "reject",
        inspected,
        identity_error=True,
        notes="Wrong person",
    )
    for pid in state["sample"]:
        accept(batches, "members", items[pid])
    assert batches.show("members")["sample_failed"]
    with pytest.raises(ValueError, match="identity errors"):
        batches.retain("members", approve_sampled=True)


def test_frozen_contents_review_binding_and_sample_tampering(
    batches: ResearchBatches,
) -> None:
    state = imported(batches)
    item = state["proposals"][0]
    with pytest.raises(StaleReviewError):
        accept(batches, "members", item, expected_revision="stale")
    accept(batches, "members", item)
    changed = deepcopy(item)
    changed["value"] = "Q999"
    with pytest.raises(ValueError):
        batches.import_results("members", [changed])
    snapshot = batches.path("members") / "proposals-0001.json"
    value = json.loads(snapshot.read_bytes())
    value["sample"] = value["sample"][:1]
    snapshot.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="sample changed"):
        batches.show("members")


def test_namesakes_and_claim_linked_non_name_facts(batches: ResearchBatches) -> None:
    task = batches.prepare("members", "members", 1)["tasks"][0]
    raw = proposal(task)
    raw["identity"]["name"] = "Another person"
    state = batches.import_results("members", [raw])
    assert not state["sample"]
    raw = proposal(task)
    raw["identity"].pop("aph_id")
    validated = batches._validate_result("members", task, raw)
    assert member_eligibility(task, validated)
    validated["identity"].update(
        date_of_birth="1970-01-01",
        party="Labor",
        evidence_refs={
            "date_of_birth": [validated["evidence"][0]["evidence_id"]],
            "party": [validated["evidence"][1]["evidence_id"]],
        },
    )
    assert member_eligibility(task, validated)
    validated["evidence"][0]["claim_value"]["date_of_birth"] = "1970-01-01"
    validated["evidence"][1]["claim_value"] = {"party": "Labor"}
    assert not member_eligibility(task, validated)
    validated["alternatives"] = ["Q2"]
    assert member_eligibility(task, validated)


def test_duplicates_and_prior_decisions_require_individual_review(
    batches: ResearchBatches,
) -> None:
    tasks = batches.prepare("members", "members", 2)["tasks"]
    state = batches.import_results("members", [proposal(task, 1) for task in tasks])
    assert not state["sample"]
    assert all(
        any("multiple members" in r for r in p["eligibility_reasons"])
        for p in state["proposals"]
    )
    task = deepcopy(tasks[0])
    task["decision_heads"] = ["prior-manual"]
    assert any(
        "Prior decisions" in r
        for r in member_eligibility(
            task, batches._validate_result("members", task, proposal(task))
        )
    )


def test_selection_preserves_explicit_nulls_and_deduplicates(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = build_review_event(
        "member:m0:wikidata_id",
        "accept",
        {"aph_id": "m0", "field": "wikidata_id", "value": None},
        source_url="https://example.org/decision",
        notes="Reviewed absence",
    )
    append_events(
        batches.service.log_path,
        [event],
        expected_revision=batches.service.review_revision(),
    )
    original = batches.service.log_path.read_bytes()
    rows = batches.service.candidates("member")
    rows[1]["evidence"]["effective_value"] = "Q2"
    monkeypatch.setattr(batches.service, "candidates", lambda kind: rows + rows)
    state = batches.prepare("selection", "members", 10)
    assert len(state["tasks"]) == 8
    assert not {"member:m0:wikidata_id", "member:m1:wikidata_id"} & {
        t["review_id"] for t in state["tasks"]
    }
    assert batches.service.log_path.read_bytes() == original


def test_partial_import_cannot_freeze_a_review(batches: ResearchBatches) -> None:
    tasks = batches.prepare("partial", "members", 2)["tasks"]
    item = batches.import_results("partial", [proposal(tasks[0])])["proposals"][0]
    with pytest.raises(ValueError, match="every batch case"):
        accept(batches, "partial", item)


def test_sample_rounds_up_above_minimum(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        batches.service,
        "candidates",
        lambda kind: [
            {
                "review_id": f"member:m{i}:wikidata_id",
                "entity_type": "member",
                "evidence": {"effective_value": None},
            }
            for i in range(26)
        ],
    )
    tasks = batches.prepare("large", "members", 26)["tasks"]
    state = batches.import_results(
        "large", [proposal(task, i + 1) for i, task in enumerate(tasks)]
    )
    assert len(state["sample"]) == 6


def test_failed_attempt_cannot_be_a_matched_proposal(batches: ResearchBatches) -> None:
    task = batches.prepare("failed", "members", 1)["tasks"][0]
    raw = proposal(task)
    raw["attempts"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="remain unresolved"):
        batches.import_results("failed", [raw])


@pytest.mark.parametrize(
    "field,value",
    [
        ("review_id", "member:other:wikidata_id"),
        ("value", "Q0"),
        ("attempts", []),
        ("evidence", []),
        ("identity", []),
        ("evidence", [None]),
    ],
)
def test_malformed_import_preserves_artifacts(
    batches: ResearchBatches, field: str, value: Any
) -> None:
    task = batches.prepare("members", "members", 1)["tasks"][0]
    raw = proposal(task)
    raw[field] = value
    with pytest.raises(ValueError):
        batches.import_results("members", [raw])
    assert not list(batches.path("members").glob("proposals-*.json"))


def test_one_sol_retry_retains_evidence_and_budget(batches: ResearchBatches) -> None:
    task = batches.prepare("members", "members", 1)["tasks"][0]
    raw = proposal(task)
    raw.update(outcome="unresolved", value=None)
    raw["attempts"][0]["status"] = "unresolved"
    batches.import_results("members", [raw])
    packet = batches.retry_tasks("members")
    assert packet["model"] == "gpt-6.1-sol"
    assert packet["cases"][0]["prior_proposal"]["evidence"] == raw["evidence"]
    retry = deepcopy(raw)
    retry.update(outcome="matched", value="Q1")
    retry["attempts"].append(
        {
            "model": "gpt-6.1-sol",
            "status": "resolved",
            "searches": 2,
            "source_opens": 4,
            "token_usage": {"input": 10, "output": 20},
        }
    )
    over = deepcopy(retry)
    over["attempts"][1]["source_opens"] = 5
    with pytest.raises(ValueError, match="budget"):
        batches.import_results("members", [over])
    dropped = deepcopy(retry)
    dropped["evidence"] = []
    with pytest.raises(ValueError):
        batches.import_results("members", [dropped])
    state = batches.import_results("members", [retry])
    assert len(state["proposals"][0]["attempts"]) == 2
    assert len(list(batches.path("members").glob("proposals-*.json"))) == 2
    with pytest.raises(ValueError, match="No unresolved"):
        batches.retry_tasks("members")


def school_proposal(task: dict[str, Any]) -> dict[str, Any]:
    rid = task["review_id"]
    return {
        "review_id": rid,
        "outcome": "closure",
        "value": None,
        "reasoning": "Historical school closed without a documented successor.",
        "alternatives": [],
        "identity": {},
        "payload": {
            "recorded_name": task["context"]["assertion"]["recorded_school_name"],
            "resolution_reason": "no_suitable_candidate",
            "requires_individual_resolution": True,
        },
        "manual_institutions": [],
        "evidence": [
            evidence(
                rid,
                "https://education.example.org/history",
                "Closed",
                "historical_context",
            ),
            evidence(
                rid,
                "https://education.example.org/closure",
                "Closure documented",
                "operating_dates",
            ),
        ],
        "attempts": [
            {
                "model": "gpt-6-luna",
                "status": "resolved",
                "searches": 1,
                "source_opens": 2,
                "token_usage": None,
            }
        ],
    }


def test_school_requires_every_evidence_disposition_and_no_sampling(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = school_review_id("Historical School")
    monkeypatch.setattr(
        batches.service,
        "candidates",
        lambda kind: [{"review_id": rid, "evidence": {}, "entity_type": "school"}],
    )
    monkeypatch.setattr(
        batches.service,
        "research_context",
        lambda key: {
            "assertion": {"recorded_school_name": "Historical School"},
            "candidates": [],
            "selected_candidate": None,
        },
    )
    monkeypatch.setattr(
        batches.service, "show", lambda key: {"context": {"members": []}}
    )
    task = batches.prepare("schools", "schools")["tasks"][0]
    raw = school_proposal(task)
    item = batches.import_results("schools", [raw])["proposals"][0]
    with pytest.raises(ValueError, match="every attached"):
        batches.record_review(
            "schools",
            item["proposal_id"],
            "accept",
            {item["evidence"][0]["evidence_id"]: "supports"},
        )
    inspected = {e["evidence_id"]: "supports" for e in item["evidence"]}
    inspected[item["evidence"][0]["evidence_id"]] = "unavailable"
    with pytest.raises(ValueError, match="available inspected"):
        batches.record_review("schools", item["proposal_id"], "accept", inspected)
    accept(batches, "schools", item)
    with pytest.raises(ValueError, match="member batch"):
        batches.retain("schools", approve_sampled=True)
    raw["payload"] = {
        "recorded_name": "Historical School",
        "institution_ref": "acara:1",
        "relationship_type": "direct",
    }
    with pytest.raises(ValueError, match="current ACARA"):
        batches._validate_result("schools", task, raw)


def test_school_manual_closure_end_to_end(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "UPDATE institutions SET acara_id = NULL, school_name = 'Historical School'"
        )
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded = 'Historical School'"
        )
        from apemap.review.integration import capture_review_sources

        capture_review_sources(conn)
    batches = ResearchBatches(review_service)
    rows = [
        row
        for row in review_service.candidates("school")
        if row["review_id"] == school_review_id("Historical School")
    ]
    monkeypatch.setattr(review_service, "candidates", lambda kind: rows)
    task = batches.prepare("closure", "schools", 1)["tasks"][0]
    raw = school_proposal(task)
    raw["payload"] = {
        "recorded_name": "Historical School",
        "institution_ref": "manual:historical-school",
        "relationship_type": "direct",
    }
    raw["manual_institutions"] = [
        {
            "institution_ref": "manual:historical-school",
            "school_name": "Historical School",
            "country": "Australia",
            "sector": "Government",
            "institution_status": "closed",
        }
    ]
    item = batches.import_results("closure", [raw])["proposals"][0]
    accept(batches, "closure", item)
    batches.retain("closure")
    path = tmp_path / "school-preview.json"
    result = batches.preview("closure", path)
    assert len(result["preview"]["events"]) == 2
    tampered = deepcopy(result["preview"])
    tampered["events"][0]["payload"]["institution_status"] = "current"
    with pytest.raises(ValueError, match="manual institutions"):
        batches.validate_preview(tampered)
    batches.apply("closure", path, result["sha256"])
    definitions = [
        e for e in review_service.events() if e.entity_type == "manual_institution"
    ]
    assert definitions[0].payload["institution_status"] == "closed"
    assert not definitions[0].payload.get("acara_id")


def test_real_preview_apply_and_generic_stale_guards(
    review_service: ReviewService, tmp_path: Path
) -> None:
    batches = ResearchBatches(review_service)
    task = batches.prepare("test-member", "members", 1)["tasks"][0]
    item = batches.import_results("test-member", [proposal(task, 12345)])["proposals"][
        0
    ]
    accept(batches, "test-member", item)
    batches.retain("test-member")
    path = tmp_path / "preview.json"
    result = batches.preview("test-member", path)
    assert not review_service.events()
    assert result["preview"]["changes"]["canonical"] is not None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result["sha256"]
    changed = deepcopy(result["preview"])
    changed.pop("research_batch")
    unbound = tmp_path / "unbound.json"
    unbound.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="bound batch approval"):
        apply_preview(
            review_service, unbound, hashlib.sha256(unbound.read_bytes()).hexdigest()
        )
    accept(batches, "test-member", item, notes="Second inspection")
    with pytest.raises(StaleReviewError, match="reviews changed"):
        batches.apply("test-member", path, result["sha256"])
    with pytest.raises(StaleReviewError, match="reviews changed"):
        apply_preview(review_service, path, result["sha256"])
    path = tmp_path / "fresh.json"
    result = batches.preview("test-member", path)
    changed = deepcopy(result["preview"])
    changed["events"][0]["payload"]["value"] = "Q99999"
    with pytest.raises(ValueError, match="differs from"):
        batches.validate_preview(changed)
    applied = batches.apply("test-member", path, result["sha256"])
    assert applied["new_events"] == 1
    assert applied["queue_changes"] == result["preview"]["research_queue_expectation"]
    assert review_service.events()[0].payload["value"] == "Q12345"
    assert review_service.events()[0].payload["research_approval_scope"] == "individual"
    assert (
        applied["queue_changes"]["pending_after"]
        < applied["queue_changes"]["pending_before"]
    )


def test_cli_and_ui_use_shared_batches(review_service: ReviewService) -> None:
    from apemap.review.gui import create_app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "--log-path",
            str(review_service.log_path),
            "--db-path",
            str(review_service.db_path),
            "--external-dir",
            str(review_service.external_dir),
            "research-batch",
            "prepare",
            "cli-pilot",
            "--kind",
            "members",
            "--limit",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    batches = ResearchBatches(review_service)
    task = batches.show("cli-pilot")["tasks"][0]
    item = batches.import_results("cli-pilot", [proposal(task)])["proposals"][0]
    with create_app(review_service).test_client() as client:
        listing = client.get("/research-batches")
        assert listing.status_code == 200 and b"cli-pilot" in listing.data
        detail = client.get("/research-batches/cli-pilot")
        assert detail.status_code == 200 and b"Sample item" in detail.data
        assert (
            client.post(
                "/research-batches/cli-pilot/review/" + item["proposal_id"],
                data={"action": "accept"},
            ).status_code
            == 403
        )
