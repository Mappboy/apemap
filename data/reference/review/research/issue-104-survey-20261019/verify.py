"""Verify the frozen survey comparison without changing evidence or decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from reconcile import (
    ROOT,
    REPOSITORY,
    read,
    reconcile,
    projected,
    prior_research,
    markdown_report,
)

from apemap.review.evidence import EvidenceRecord, load_evidence
from apemap.review.model import education_review_id
from apemap.review.research_batches import ResearchBatches, _digest
from apemap.review.service import ReviewService


def verify(service: ReviewService) -> dict[str, Any]:
    baseline = read("baseline.json")
    assert service.review_revision() == baseline["decision_revision"]
    assert (
        hashlib.sha256(service.db_path.read_bytes()).hexdigest()
        == baseline["db_sha256"]
    )
    state = ResearchBatches(service).show(ROOT.name)
    assert state["manifest"]["purpose"] == "survey-evidence-reconciliation"
    tasks = {task["review_id"]: task for task in state["tasks"]}
    assert len(tasks) == len(state["proposals"]) == 27
    assert state["manifest"]["task_sha256"] == _digest(state["tasks"])
    assert not state["applications"] and not state["applied_proposals"]
    report, _, results = reconcile(service)
    assert report == read("reconciliation.json")
    assert (ROOT / "comparison.md").read_text(encoding="utf-8") == markdown_report(
        report
    )
    assert results == read("results.json")
    assert report["summary"]["respondents"] == 18
    assert report["summary"]["projection_counts"] == {
        "members": 580,
        "institutions": 11896,
        "parliament_service": 1754,
        "member_education": 352,
    }
    assert _digest(projected(service)) == report["summary"]["projection_sha256"]
    records = []
    imported = {row["review_id"]: row for row in state["proposals"]}
    for result in results:
        assert all(
            imported[result["review_id"]][key] == value for key, value in result.items()
        )
    for proposal in state["proposals"]:
        assert proposal["outcome"] == "unresolved" and proposal["payload"] is None
        assert not proposal["manual_institutions"] and not proposal["attempts"]
        ResearchBatches(service)._validate_result(
            "schools", tasks[proposal["review_id"]], proposal, origin="agent-session"
        )
        for evidence in proposal["evidence"]:
            record = EvidenceRecord.from_dict(evidence)
            assert record.candidate_institution_ref is None
            assert record.claim_value["source_publication_date"] == "2025-06-28"
            assert record.review_id == education_review_id(
                record.claim_value["aph_id"], record.claim_value["recorded_school_name"]
            )
            records.append(record)
    attendance = read("attendance-proposals.json")
    assert prior_research(attendance) == read("prior-research.json")
    assert len(read("prior-research.json")["overlapping_attendance_proposals"]) == 2
    assert (
        len(read("prior-research.json")["possible_overlapping_attendance_proposals"])
        == 1
    )
    assert len(attendance) == 5
    assert {row["aph_id"] for row in attendance} == {
        "312823",
        "270198",
        "315154",
        "88411",
    }
    for row in attendance:
        assert (
            row["level"] == "secondary"
            and row["attended_status"] == "attended_unspecified"
        )
        assert not any(
            field in row
            for field in ("institution_ref", "graduation_year", "years_attended")
        )
    assert (
        sum(
            len(row["other_current_assertions_preserved"])
            for row in report["respondents"]
        )
        == 4
    )
    history_records = []
    for item in read("history-evidence.json"):
        record = EvidenceRecord.from_dict(item)
        assert record.review_id == education_review_id(
            "312871", "Sacred Heart High School"
        )
        assert (
            record.stance == "contextual" and record.candidate_institution_ref is None
        )
        history_records.append(record)
    receipt_path = ROOT / "retention.json"
    if receipt_path.exists():
        receipt = read("retention.json")
        raw = service.evidence_path.read_bytes()
        assert (
            hashlib.sha256(raw[: receipt["before_bytes"]]).hexdigest()
            == baseline["evidence_revision"]
        )
        assert hashlib.sha256(raw).hexdigest() == receipt["after_sha256"]
        approved = read("evidence-preview.json")
        assert (
            hashlib.sha256((ROOT / "evidence-preview.json").read_bytes()).hexdigest()
            == receipt["approval_sha256"]
        )
        approval = receipt["user_approval"]
        assert approval["approved_preview_sha256"] == receipt["approval_sha256"]
        assert approval["response"] == "Approve these 29 evidence records"
        assert approval["scope"] == (
            "Retain inspected evidence only; no attendance or mapping decisions"
        )
        expected_ids = {record.evidence_id for record in records + history_records}
        assert len(expected_ids) == 29
        appended = [
            json.loads(line) for line in raw[receipt["before_bytes"] :].splitlines()
        ]
        assert len(appended) == 29
        assert {record["evidence_id"] for record in appended} == expected_ids
        assert set(receipt["retained_evidence_ids"]) == expected_ids
        assert {record["evidence_id"] for record in approved["records"]} == expected_ids
        assert receipt["new_evidence_records"] == 29
        inspections = receipt["separate_history_source_inspections"]
        assert {item["evidence_id"] for item in inspections} == {
            record.evidence_id for record in history_records
        }
        assert all(
            item["reviewer"].strip() and item["disposition"] == "contextual"
            for item in inspections
        )
        retained = {
            record.evidence_id for record in load_evidence(service.evidence_path)
        }
        assert set(receipt["retained_evidence_ids"]) <= retained
        assert {record.evidence_id for record in records} <= retained
        assert len(state["reviews"]) == 27
        for proposal in state["proposals"]:
            review = state["reviews"][proposal["proposal_id"]]
            assert review["action"] == "accept" and review["reviewer"].strip()
            assert review["inspected"] == {
                record["evidence_id"]: record["stance"]
                for record in proposal["evidence"]
            }
    else:
        assert not state["reviews"]
        assert (
            hashlib.sha256(service.evidence_path.read_bytes()).hexdigest()
            == baseline["evidence_revision"]
        )
    return {
        **report["summary"],
        "reviewed_source_proposals": len(state["reviews"]),
        "evidence_retained": receipt_path.exists(),
        "attendance_decisions_applied": 0,
        "mapping_decisions_applied": 0,
        "database_unchanged": True,
        "decision_ledger_unchanged": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=REPOSITORY / "data/aped-historical-v0.7.0.duckdb",
    )
    arguments = parser.parse_args()
    service = ReviewService(
        db_path=arguments.db_path, external_dir=REPOSITORY / "data/raw/historical/acara"
    )
    print(json.dumps(verify(service), indent=2))
