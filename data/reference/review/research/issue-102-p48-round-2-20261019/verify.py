"""Verify this unreviewed research delivery without applying any findings."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from apemap.db import get_connection
from apemap.review.candidates import json_value
from apemap.review.integration import project_review_records
from apemap.review.missing_education import _parent_revision, _scope
from apemap.review.research_batches import ResearchBatches, _digest, _write
from apemap.review.service import ReviewService

from assemble import assemble

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[4]


def snapshot(service: ReviewService) -> dict[str, Any]:
    events = service.events()
    with get_connection(service.db_path, read_only=True) as connection:
        records = json_value(
            project_review_records(
                connection,
                events,
                matcher=service.matcher(),
                evidence_records=service.evidence_records(events),
            )
        )
    scope = _scope(records, 48)
    parent = ResearchBatches(service).show(
        "issue-102-p48-missing-schools-followup-20261019"
    )
    parent_root = ResearchBatches(service).path(parent["manifest"]["batch_id"])
    return {
        "source_revision": service.source_revision(include_evidence=False),
        "decision_revision": service.review_revision(),
        "authority_hashes": {
            str(path.relative_to(REPOSITORY)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in (service.db_path, service.log_path, service.evidence_path)
        },
        "parent": _parent_revision(parent),
        "parent_files": {
            str(path.relative_to(REPOSITORY)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(parent_root.rglob("*"))
            if path.is_file()
            and not path.name.endswith(".lock")
            and "__pycache__" not in path.parts
        },
        "projection_digest": _digest(records),
        "counts": {table: len(rows) for table, rows in records.items()},
        "missing_aph_ids": sorted(
            str(row["aph_id"]) for row in scope if row["status"] == "needs_research"
        ),
        "non_institutional_skips": [
            row["aph_id"] for row in scope if row["status"] == "resolved_skip"
        ],
    }


def verify(service: ReviewService) -> dict[str, Any]:
    baseline = json.loads((ROOT / "baseline.json").read_bytes())
    assert snapshot(service) == baseline, (
        "Source, authority, parent or canonical data changed since preparation"
    )
    state = ResearchBatches(service).show(ROOT.name)
    assert state["manifest"]["parent"] == baseline["parent"]
    assert state["manifest"]["decision_revision"] == baseline["decision_revision"]
    assert (
        not state["reviews"]
        and not state["review_history"]
        and not state["applications"]
    )
    assert not state["applied_proposals"]
    assert len(state["tasks"]) == len(state["proposals"]) == 30
    assert len(baseline["missing_aph_ids"]) == 30
    assert len(baseline["non_institutional_skips"]) == 1
    assert baseline["counts"] == {
        "institutions": 11914,
        "member_education": 370,
        "members": 580,
        "parliament_service": 1754,
    }
    observations = json.loads((ROOT / "research.json").read_bytes())
    results = json.loads((ROOT / "results.json").read_bytes())
    assert results == [
        assemble(task, row)
        for task, row in zip(state["tasks"], observations, strict=True)
    ]
    validated = [
        ResearchBatches(service)._validate_result(
            "schools", task, row, origin="agent-session"
        )
        for task, row in zip(state["tasks"], results, strict=True)
    ]
    for row in validated:
        row["eligibility_reasons"] = [
            "School proposals require individual source inspection"
        ]
    imported = {row["review_id"]: row for row in state["proposals"]}
    for row in validated:
        stored = dict(imported[row["review_id"]])
        stored.pop("eligibility_reasons")
        row.pop("eligibility_reasons")
        assert stored == row
    for task, observation in zip(state["tasks"], observations, strict=True):
        operations = observation["operations"]
        for phase, limits in (
            ("initial", {"search": 3, "open": 6}),
            ("targeted_retry", {"search": 2, "open": 4}),
        ):
            counts = Counter(
                operation["kind"]
                for operation in operations
                if operation.get("pass", operation.get("attempt")) == phase
            )
            assert all(counts[kind] <= limit for kind, limit in limits.items())
        assert all(
            operation.get("pass", operation.get("attempt"))
            in {"initial", "targeted_retry"}
            for operation in operations
        )
        prior = task["prior_research"]["proposal"]["identity"]
        if prior["wikidata_status"] == "unavailable":
            assert any(
                operation.get("url")
                == "https://www.wikidata.org/wiki/" + prior["cached_qid_hint"]
                for operation in operations
            )
    positive = [row for row in state["proposals"] if row["education_proposals"]]
    assert len(positive) == 1 and positive[0]["identity"]["aph_id"] == "270198"
    claims = positive[0]["education_proposals"]
    assert [row["institution_ref"] for row in claims] == ["acara:47529", "acara:47887"]
    retained = {record.evidence_id: record for record in service.evidence_records()}
    matcher = service.matcher()
    for row in claims:
        assert row["evidence_refs"][0] in retained
        assert row["institution_ref"].split(":")[1] in matcher.acara_id_map
        assert not row.get("years_attended") and not row.get("graduation_year")
    summary = {
        "research_cases": 30,
        "attendance_supported_members": len(positive),
        "school_assertions": len(claims),
        "institution_link_proposals": sum(
            bool(row.get("institution_ref")) for row in claims
        ),
        "reviewed": 0,
        "applied": 0,
        "unresolved_attendance_members": 29,
        "new_search_queries": sum(row["research_log"]["searches"] for row in results),
        "conservative_source_open_operations": sum(
            row["research_log"]["source_opens"] for row in results
        ),
        "stage_outcomes": dict(
            Counter(row["stages"]["final_broad"]["outcome"] for row in results)
        ),
        "authority_and_parent_unchanged": True,
        "content_sha256": state["content_sha256"],
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-baseline", action="store_true")
    args = parser.parse_args()
    service = ReviewService(
        db_path=REPOSITORY / "data/aped-historical-v0.7.0.duckdb",
        external_dir=REPOSITORY / "data/raw/historical/acara",
    )
    if args.record_baseline:
        baseline = snapshot(service)
        manifest = json.loads((ROOT / "manifest.json").read_bytes())
        assert baseline["source_revision"] == manifest["source_revision"]
        assert baseline["decision_revision"] == manifest["decision_revision"]
        _write(ROOT / "baseline.json", baseline)
        print(
            json.dumps(
                {"baseline_recorded": True, "missing": len(baseline["missing_aph_ids"])}
            )
        )
    else:
        print(json.dumps(verify(service), indent=2))


if __name__ == "__main__":
    main()
