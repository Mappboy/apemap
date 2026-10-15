"""Check this research packet and replay proposals without writing any authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
from typing import Any

import duckdb

from apemap.review.evidence import EvidenceRecord, validate_evidence_references
from apemap.review.integration import compare_review_records, project_review_records
from apemap.review.model import (
    education_review_id,
    institution_review_id,
    validate_events,
)
from apemap.review.service import ReviewService, build_review_event

BATCH = Path(__file__).resolve().parent
ROOT = BATCH.parents[4]


def read(name: str) -> Any:
    return json.loads((BATCH / name).read_bytes())


def verify_inputs() -> None:
    for item in read("inputs.json")["files"]:
        path = ROOT / item["path"]
        assert path.stat().st_size == item["size_bytes"], item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item[
            "path"
        ]


def unresolved(records: dict[str, Any]) -> dict[str, dict[str, Any]]:
    members = {row["member_id"]: row for row in records["members"]}
    cohort = {
        row["member_id"]
        for row in records["parliament_service"]
        if row["parliament_number"] == 48
    }
    return {
        education_review_id(
            members[row["member_id"]]["aph_id"], row["school_name_as_recorded"]
        ): row
        for row in records["member_education"]
        if row["member_id"] in cohort
        and row.get("institution_resolution") == "unresolved"
    }


def main() -> None:
    verify_inputs()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == read("inputs.json")["package_version"]
    assert (
        next(p["version"] for p in lock["package"] if p["name"] == "apemap")
        == (metadata["project"]["version"])
    )
    scope = {row["review_id"]: row for row in read("scope.json")}
    results = read("results.json")
    assert len(results) == len(scope) == 5
    assert {row["review_id"] for row in results} == scope.keys()
    for history in read("prior-research.json"):
        source = json.loads((ROOT / history["source_path"]).read_bytes())
        values = source["proposals"] if isinstance(source, dict) else source
        assert history["previous_result"] in values
    for case in read("research-log.json")["cases"]:
        assert case["searches"] == len(case["search_queries"]) <= 3
        assert case["source_opens"] == len(case["opened_sources"]) <= 6
    service = ReviewService(
        db_path=ROOT / "data/aped-p48-research.duckdb",
        external_dir=ROOT / "data/raw/historical/acara",
    )
    existing = service.events()
    records = service.evidence_records(existing)
    proposed = []
    for result in results:
        records.extend(EvidenceRecord.from_dict(item) for item in result["evidence"])
        own = scope[result["review_id"]]
        assert result["recorded_school_name"] == own["school"]
        for item in result["evidence"]:
            assert item["review_id"] in {own["review_id"], own["school_review_id"]}
            if "aph_id" in item["claim_value"]:
                assert item["claim_value"]["aph_id"] == own["aph_id"]
                assert item["review_id"] == own["review_id"]
        mapping = result["mapping"]
        if mapping is None:
            assert not result["manual_institutions"]
            continue
        assert mapping["review_id"] == own["review_id"]
        assert mapping["payload"]["aph_id"] == own["aph_id"]
        assert not own["current_decision_heads"][own["review_id"]]
        for definition in result["manual_institutions"]:
            proposed.append(
                build_review_event(
                    institution_review_id(definition["institution_ref"]),
                    "accept",
                    definition,
                    source_url=result["evidence"][-1]["source_url"],
                    reviewer="research-validation-only",
                )
            )
        proposed.append(
            build_review_event(
                mapping["review_id"],
                mapping["action"],
                mapping["payload"],
                source_url=mapping["source_url"],
                reviewer="research-validation-only",
            )
        )
    validate_events(existing + proposed)
    validate_evidence_references(existing + proposed, records)
    with duckdb.connect(str(service.db_path), read_only=True) as conn:
        before = project_review_records(
            conn, existing, service.matcher(), evidence_records=records
        )
        after = project_review_records(
            conn, existing + proposed, service.matcher(), evidence_records=records
        )
    assert unresolved(before).keys() == scope.keys()
    mapped = {row["review_id"] for row in results if row["mapping"]}
    assert unresolved(after).keys() == scope.keys() - mapped
    changes = compare_review_records(before, after)
    assert set(changes) == {"institutions", "member_education"}
    assert changes["member_education"]["updated_count"] == 2
    assert changes["member_education"]["added_count"] == 0
    assert changes["member_education"]["removed_count"] == 0
    assert changes["institutions"]["added_count"] == 1
    assert changes["institutions"]["updated_count"] == 0
    verify_inputs()
    print(
        "PASS: five exact assertions; immutable prior research; evidence and event "
        "validation; read-only replay changes only two assertions and adds one "
        "historical institution; unresolved 5 -> 3; all input hashes unchanged. "
        "No decisions or evidence retained or applied."
    )


if __name__ == "__main__":
    main()
