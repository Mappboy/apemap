"""Verify this unreviewed delivery without appending evidence or decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from assemble import ROOT, assemble

from apemap.review.missing_education import (
    PUBLISHED_RELEASE,
    _published_scope,
    project_missing_education_scope,
)
from apemap.review.missing_education_results import validate_missing_education_result
from apemap.review.research_batches import ResearchBatches, _digest
from apemap.review.service import ReviewService


def read(name: str) -> Any:
    return json.loads((ROOT / name).read_bytes())


def verify(
    release_dir: Path | None, db_path: Path | None, external_dir: Path
) -> dict[str, Any]:
    manifest, tasks, audit = (
        read("manifest.json"),
        read("tasks.json"),
        read("audit.json"),
    )
    results, snapshot = read("results.json"), read("proposals-0001.json")
    assert manifest["task_sha256"] == _digest(tasks)
    assert manifest["audit_sha256"] == _digest(audit)
    assert audit["published_release"] == PUBLISHED_RELEASE
    assert manifest["audit_count"] == len(audit["members"]) == 49
    assert manifest["case_count"] == len(tasks) == len(results) == 48
    skips = [row for row in audit["members"] if row["status"] == "resolved_skip"]
    assert [row["aph_id"] for row in skips] == ["250156"]
    assert manifest["skipped_count"] == len(skips) == 1
    assert results == assemble(), "Normalized results differ from retained raw research"
    by_id = {task["review_id"]: task for task in tasks}
    assert {row["review_id"] for row in results} == set(by_id)
    for row in results:
        validate_missing_education_result(by_id[row["review_id"]], row)
    assert snapshot["content_sha256"] == _digest(snapshot["proposals"])
    assert len(snapshot["proposals"]) == len(results)
    proposals = {row["review_id"]: row for row in snapshot["proposals"]}
    for row in results:
        assert all(
            proposals[row["review_id"]][key] == value for key, value in row.items()
        )
    assert not snapshot["sample"]
    assert not list(ROOT.glob("reviews*.jsonl"))
    assert not list(ROOT.glob("application-*.json"))
    positive = [row for row in results if row["education_proposals"]]
    assert len(positive) == 17
    assert sum(len(row["education_proposals"]) for row in positive) == 17
    assert not any(row["manual_institutions"] for row in results)
    assert not any(
        payload.get("institution_ref")
        for row in positive
        for payload in row["education_proposals"]
    )
    searches = sum(row["research_log"]["searches"] for row in results)
    assert searches == 213
    ledgers = ROOT.parent.parent
    for filename, key in (
        ("decisions.jsonl", "decision_revision"),
        ("evidence.jsonl", "evidence_revision"),
    ):
        assert (
            hashlib.sha256((ledgers / filename).read_bytes()).hexdigest()
            == manifest[key]
        )
    if release_dir is not None:
        published = _published_scope(release_dir.resolve(), 48)
        assert {(row["member_id"], row["status"]) for row in published} == {
            (row["member_id"], row["status"]) for row in audit["members"]
        }
    if db_path is not None:
        service = ReviewService(db_path=db_path, external_dir=external_dir)
        state = ResearchBatches(service).show(manifest["batch_id"])
        assert not state["reviews"] and not state["applications"]
        assert not state["applied_proposals"]
        current = project_missing_education_scope(service, 48)
        assert {(row["member_id"], row["status"]) for row in current} == {
            (row["member_id"], row["status"]) for row in audit["members"]
        }
    return {
        "audit_members": 49,
        "home_education_skips": 1,
        "research_results": 48,
        "proposed_attendance": 17,
        "unresolved_research": 31,
        "actual_search_queries": searches,
        "reviews": 0,
        "applied": 0,
        "published_baseline_checked": release_dir is not None,
        "current_projection_checked": db_path is not None,
        "conditional_name_coverage_if_all_17_approved": {
            "before": 177,
            "after": 194,
            "cohort": 226,
            "remaining_without_school": 32,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument(
        "--external-dir", type=Path, default=ROOT.parents[3] / "raw/historical/acara"
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            verify(arguments.release_dir, arguments.db_path, arguments.external_dir),
            indent=2,
        )
    )
