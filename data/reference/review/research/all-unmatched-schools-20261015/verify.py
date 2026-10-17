"""Verify the frozen all-parliament findings without changing authority files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import duckdb

from apemap.review.evidence import EvidenceRecord
from apemap.review.integration import project_review_records
from apemap.review.model import education_review_id
from apemap.review.research_batches import ResearchBatches
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
    expected = {row["review_id"] for row in scope}
    assert (
        len(expected)
        == len(scope)
        == len(state["tasks"])
        == len(state["proposals"])
        == 20
    )
    assert {t["review_id"] for t in state["tasks"]} == expected
    assert {p["review_id"] for p in state["proposals"]} == expected
    baseline = read(path / "baseline.json")
    for saved, digest in baseline["ledger_sha256"].items():
        name = saved.replace("\\", "/").rsplit("/", 1)[-1]
        assert (
            hashlib.sha256((service.log_path.parent / name).read_bytes()).hexdigest()
            == digest
        )
    events = service.events()
    with duckdb.connect(str(service.db_path), read_only=True) as conn:
        projected = project_review_records(
            conn,
            events,
            service.matcher(),
            evidence_records=service.evidence_records(events),
        )
    members = {row["member_id"]: row for row in projected["members"]}
    current = {
        education_review_id(
            members[row["member_id"]]["aph_id"], row["school_name_as_recorded"]
        )
        for row in projected["member_education"]
        if row.get("institution_resolution") == "unresolved"
    }
    assert current == expected, "The all-parliament unmatched scope has changed"
    tasks = {task["review_id"]: task for task in state["tasks"]}
    imported = {result["review_id"]: result for result in read(path / "results.json")}
    assert imported.keys() == expected
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
        queries = log.get("search_queries", log.get("queries", []))
        opens = log.get("opened_sources", log.get("source_opens", []))
        count = log.get("source_open_count", log.get("source_opens"))
        assert log["searches"] == len(queries) <= 3
        assert count == len(opens) <= 6
    for row in read(path / "prior-research.json"):
        for artifact in row["artifacts"]:
            raw = (root / artifact["path"]).read_bytes().replace(b"\r\n", b"\n")
            assert hashlib.sha256(raw).hexdigest() == artifact["sha256_lf"]
    summary: dict[str, Any] = {
        "cases": len(expected),
        "school_descriptions": len({r["school_review_id"] for r in scope}),
        "proposed_mappings": sum(
            bool((p.get("payload") or {}).get("institution_ref"))
            for p in state["proposals"]
        ),
        "evidence_records": sum(len(p["evidence"]) for p in state["proposals"]),
        "reviews": len(state["reviews"]),
        "applied_proposals": len(state["applied_proposals"]),
        "authority_ledgers_unchanged": True,
    }
    if preview_check:
        with tempfile.TemporaryDirectory(
            prefix="school-research-", dir=root / ".cache"
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
                        r["evidence_id"]: r["stance"] for r in proposal["evidence"]
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
                e["entity_type"] == "school" for e in preview["preview"]["events"]
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
