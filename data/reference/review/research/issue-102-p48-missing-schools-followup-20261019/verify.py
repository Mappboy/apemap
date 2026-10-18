"""Verify this unreviewed delivery without appending evidence or decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from assemble import BASELINE, ROOT, assemble

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
    previous = json.loads((BASELINE / "results.json").read_bytes())
    original = {row["review_id"]: row for row in previous}
    updated_ids = {
        "education:263528:missing",
        "education:281558:missing",
        "education:290544:missing",
        "education:301128:missing",
    }
    assert {
        row["review_id"] for row in results if row != original[row["review_id"]]
    } == updated_ids
    archive_hashes = read("archive-hashes.json")
    assert set(archive_hashes) == {
        path.name for path in BASELINE.iterdir() if path.is_file()
    }
    for name, digest in archive_hashes.items():
        assert hashlib.sha256((BASELINE / name).read_bytes()).hexdigest() == digest
    old_manifest = json.loads((BASELINE / "manifest.json").read_bytes())
    for field in ("task_sha256", "audit_sha256"):
        assert manifest[field] == old_manifest[field]
    for row in results:
        old_log = original[row["review_id"]]["research_log"]
        assert {key: row["research_log"][key] for key in old_log} == old_log
        old_evidence = original[row["review_id"]]["evidence"]
        assert row["evidence"][: len(old_evidence)] == old_evidence
    by_member = {row["identity"]["aph_id"]: row for row in results}
    price = by_member["263528"]["education_proposals"]
    assert len(price) == 1
    assert price[0]["recorded_school_name"] == "Centralian Senior College"
    assert price[0]["attended_status"] == "attended_unspecified"
    assert "graduation_year" not in price[0] and "years_attended" not in price[0]
    for aph_id in ("281558", "290544", "301128"):
        assert not by_member[aph_id]["education_proposals"]
    assert by_member["281558"]["stages"]["final_broad"]["outcome"] == "uncertain"
    assert by_member["290544"]["stages"]["final_broad"]["outcome"] == "uncertain"
    assert by_member["301128"]["stages"]["final_broad"]["outcome"] == "not_found"
    assert (
        sum(
            row["stages"]["final_broad"]["outcome"] == "uncertain"
            for row in results
            if not row["education_proposals"]
        )
        == 21
    )
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
    assert len(positive) == 18
    assert sum(len(row["education_proposals"]) for row in positive) == 18
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
        assert hashlib.sha256(db_path.read_bytes()).hexdigest() == (
            "9568bbc44e929ce836784efd5fa220f3e4beac64fdec03b7c5073a827be91f82"
        )
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
        "proposed_attendance": 18,
        "unresolved_research": 30,
        "not_found": 9,
        "uncertain": 21,
        "original_search_queries": searches,
        "followup_search_queries_this_update": 0,
        "prior_user_lead_query_log_complete": False,
        "updated_members": 4,
        "unchanged_members": 44,
        "reviews": 0,
        "applied": 0,
        "published_baseline_checked": release_dir is not None,
        "current_projection_checked": db_path is not None,
        "conditional_name_coverage_if_all_18_approved": {
            "before": 177,
            "after": 195,
            "cohort": 226,
            "remaining_without_school": 31,
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
