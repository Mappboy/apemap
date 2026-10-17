"""Verify all 27 queue names and their unapplied native research proposals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.research_batches import ResearchBatches
from apemap.review.schools import group_school_rows
from apemap.review.service import ReviewService


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def verify(path: Path, *, preview_check: bool = False) -> dict[str, Any]:
    root = path.parents[4]
    service = ReviewService(
        log_path=root / "data/reference/review/decisions.jsonl",
        evidence_path=root / "data/reference/review/evidence.jsonl",
        db_path=root / "data/aped.duckdb",
        external_dir=root / "data/raw/historical/acara",
    )
    batches = ResearchBatches(service)
    state = batches.show(path.name)
    scope = read(path / "scope.json")
    cases = [case for school in scope for case in school["cases"]]
    expected = {case["review_id"] for case in cases}
    assert len(scope) == 27
    assert len(expected) == len(cases) == len(state["tasks"]) == 31
    assert len(state["proposals"]) == 31
    assert {task["review_id"] for task in state["tasks"]} == expected
    assert {proposal["review_id"] for proposal in state["proposals"]} == expected
    supplementary = {
        case["review_id"]
        for case in cases
        if case["research_scope"] == "verification_of_applied_mapping"
    }
    assert len(supplementary) == 3
    assert len(expected - supplementary) == 28
    assert supplementary == set(
        state["manifest"]["provenance"]["supplemental_verification_ids"]
    )
    baseline = read(path / "baseline.json")
    for name, digest in baseline["authority_hashes"].items():
        assert (
            hashlib.sha256((service.log_path.parent / name).read_bytes()).hexdigest()
            == digest
        ), f"Authority changed: {name}; freeze a new snapshot before further research"
    assert (
        hashlib.sha256(service.db_path.read_bytes()).hexdigest()
        == baseline["database_sha256"]
    )
    assert (
        service.source_revision(include_evidence=False) == baseline["source_revision"]
    )
    assert service.review_revision() == baseline["decision_revision"]
    current = {
        row["review_id"]
        for row in group_school_rows(service.candidates("school"))
        if row["status"] not in {"accepted", "resolved_individually"}
    }
    assert current == {school["school_review_id"] for school in scope}
    tasks = {task["review_id"]: task for task in state["tasks"]}
    imported = {result["review_id"]: result for result in read(path / "results.json")}
    assert imported.keys() == expected
    searches = opens = 0
    for proposal in state["proposals"]:
        validated = batches._validate_result(
            "schools",
            tasks[proposal["review_id"]],
            imported[proposal["review_id"]],
            origin="agent-session",
        )
        assert validated["proposal_id"] == proposal["proposal_id"]
        for record in proposal["evidence"]:
            EvidenceRecord.from_dict(record)
        log = proposal["research_log"]
        assert log["searches"] == len(log["search_queries"]) <= 3
        assert log["source_opens"] == len(log["opened_sources"]) <= 6
        searches += log["searches"]
        opens += log["source_opens"]
    for row in read(path / "prior-research.json"):
        for artifact in row["artifacts"]:
            raw = (root / artifact["path"]).read_bytes().replace(b"\r\n", b"\n")
            assert hashlib.sha256(raw).hexdigest() == artifact["sha256_lf"]
    assert not state["reviews"] and not state["applied_proposals"]
    summary: dict[str, Any] = {
        "school_names": len(scope),
        "outstanding_member_records": len(expected - supplementary),
        "supplemental_verifications": len(supplementary),
        "proposals": len(expected),
        "proposed_mappings": sum(
            bool((proposal.get("payload") or {}).get("institution_ref"))
            for proposal in state["proposals"]
        ),
        "manual_definitions": sum(
            len(proposal["manual_institutions"]) for proposal in state["proposals"]
        ),
        "evidence_records": sum(
            len(proposal["evidence"]) for proposal in state["proposals"]
        ),
        "fresh_search_queries": searches,
        "fresh_source_open_attempts": opens,
        "authority_and_database_unchanged": True,
        "reviews": 0,
        "applied_proposals": 0,
    }
    if preview_check:
        with tempfile.TemporaryDirectory(
            prefix="remaining-schools-", dir=root / ".cache"
        ) as directory:
            isolated = Path(directory)
            for source in (service.log_path, service.evidence_path):
                shutil.copyfile(source, isolated / source.name)
            copied = isolated / "research" / path.name
            shutil.copytree(path, copied)
            scratch = ReviewService(
                log_path=isolated / service.log_path.name,
                evidence_path=isolated / service.evidence_path.name,
                db_path=service.db_path,
                external_dir=service.external_dir,
            )
            checks = ResearchBatches(scratch)
            for proposal in state["proposals"]:
                checks.record_review(
                    path.name,
                    proposal["proposal_id"],
                    "accept",
                    inspected={
                        record["evidence_id"]: record["stance"]
                        for record in proposal["evidence"]
                    },
                    reviewer="offline-verification",
                    notes="Synthetic approval in an isolated copy for validation only.",
                    supersedes=tasks[proposal["review_id"]]["decision_heads"],
                )
            checks.retain(path.name)
            preview = checks.preview(
                path.name, isolated / "preview.json", reviewer="offline-verification"
            )
            checks.validate_preview(preview["preview"])
            assert not any(
                event["entity_type"] == "school"
                for event in preview["preview"]["events"]
            )
            receipt = checks.apply(
                path.name, Path(preview["preview_path"]), preview["sha256"]
            )
            assert len(checks.show(path.name)["applied_proposals"]) == len(expected)
            summary["isolated_preview_and_apply"] = {
                "events": receipt["new_events"],
                "queue": preview["preview"]["research_queue_expectation"],
            }
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview-check", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            verify(
                Path(__file__).resolve().parent, preview_check=arguments.preview_check
            ),
            indent=2,
        )
    )
