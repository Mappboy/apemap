"""Read-only verification of the individually approved attendance application."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from apemap.db import get_connection
from apemap.review.candidates import json_value
from apemap.review.integration import project_review_records
from apemap.review.missing_education import _scope
from apemap.review.model import parse_events
from apemap.review.research_batches import ResearchBatches, _digest
from apemap.review.service import ReviewService

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[4]


def verify() -> dict[str, Any]:
    audit = json.loads((ROOT / "applied-attendance-audit.json").read_bytes())
    preview_raw = (ROOT / audit["preview_name"]).read_bytes()
    assert hashlib.sha256(preview_raw).hexdigest() == audit["approval_sha256"]
    preview = json.loads(preview_raw)
    service = ReviewService(
        db_path=REPOSITORY / "data/aped-historical-v0.7.0.duckdb",
        external_dir=REPOSITORY / "data/raw/historical/acara",
    )
    raw = service.log_path.read_bytes()
    prefix = raw[: audit["decision_bytes_before"]]
    assert hashlib.sha256(prefix).hexdigest() == audit["decision_revision_before"]
    assert hashlib.sha256(raw).hexdigest() == audit["decision_revision_after"]
    assert [json.loads(line) for line in raw[len(prefix) :].splitlines()] == preview[
        "events"
    ]
    for path, expected in (
        (service.evidence_path, audit["evidence_sha256"]),
        (service.db_path, audit["db_sha256"]),
        (ROOT / "reviews.jsonl", audit["reviews_sha256"]),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    assert service.source_revision() == audit["source_revision"]
    state = ResearchBatches(service).show(ROOT.name)
    assert state["content_sha256"] == audit["batch_content_sha256"]
    assert state["review_revision"] == audit["review_revision"]
    assert dict(Counter(row["action"] for row in state["reviews"].values())) == {
        "accept": 18,
        "research": 30,
    }
    accepted = {
        proposal_id
        for proposal_id, review in state["reviews"].items()
        if review["action"] == "accept"
    }
    assert (
        accepted == set(audit["applied_proposals"]) == set(state["applied_proposals"])
    )
    assert len(preview["events"]) == len(audit["event_ids"]) == 18
    assert [event["decision_id"] for event in preview["events"]] == audit["event_ids"]
    assert all(
        event["action"] == "accept"
        and event["entity_type"] == "member_education"
        and event["payload"]["purpose"] == "missing-secondary-education"
        and not event["payload"].get("institution_ref")
        and not event["supersedes"]
        for event in preview["events"]
    )
    receipt = json.loads((ROOT / audit["receipt_name"]).read_bytes())
    assert state["applications"] == [receipt]
    assert receipt["approval_sha256"] == audit["approval_sha256"]
    assert receipt["events"] == audit["event_ids"]
    assert receipt["queue_changes"] == preview["research_queue_expectation"]
    events = service.events()
    with get_connection(service.db_path, read_only=True) as connection:
        before = json_value(
            project_review_records(
                connection,
                parse_events(prefix),
                matcher=service.matcher(),
                evidence_records=service.evidence_records(parse_events(prefix)),
            )
        )
        after = json_value(
            project_review_records(
                connection,
                events,
                matcher=service.matcher(),
                evidence_records=service.evidence_records(events),
            )
        )
    assert _digest(before) == audit["projection_before_sha256"]
    assert _digest(after) == audit["projection_after_sha256"]
    assert {table: len(rows) for table, rows in before.items()} == audit[
        "counts_before"
    ]
    assert {table: len(rows) for table, rows in after.items()} == audit["counts_after"]
    for table in ("members", "parliament_service"):
        assert before[table] == after[table]
    for table, identity in (
        ("member_education", "education_id"),
        ("institutions", "institution_id"),
    ):
        previous = {row[identity]: row for row in before[table]}
        current = {row[identity]: row for row in after[table]}
        assert len(previous) == len(before[table])
        assert len(current) == len(after[table]) == len(previous) + 18
        assert all(current[key] == row for key, row in previous.items())
        for key in current.keys() - previous.keys():
            row = current[key]
            if table == "member_education":
                assert row["level"] == "secondary" and row["confidence"] == "verified"
                assert row["institution_resolution"] == "unresolved"
                assert row["institution_id"].startswith("inst-unmatched-reviewed-")
            else:
                assert row["acara_id"] is None and row["sector"] == "Other"
                assert row["latitude"] is None and row["longitude"] is None
    scope_before = dict(Counter(row["status"] for row in _scope(before, 48)))
    scope_after = dict(Counter(row["status"] for row in _scope(after, 48)))
    assert (
        scope_before
        == audit["missing_scope_before"]
        == {
            "needs_research": 48,
            "resolved_skip": 1,
        }
    )
    assert (
        scope_after
        == audit["missing_scope_after"]
        == {
            "needs_research": 30,
            "resolved_skip": 1,
        }
    )
    opening_members = {
        row["member_id"]
        for row in after["parliament_service"]
        if int(row["parliament_number"]) == 48
        and str(row["is_opening_day_member"]).casefold() == "true"
    }
    assert len(opening_members) == 226
    return {
        "attendance_decisions_applied": 18,
        "mapping_decisions_applied": 0,
        "missing_members_before": 48,
        "missing_members_after": 30,
        "home_education_skip": 1,
        "named_secondary_members_before": len(opening_members)
        - sum(scope_before.values()),
        "named_secondary_members_after": len(opening_members)
        - sum(scope_after.values()),
        "counts_after": audit["counts_after"],
        "user_reviews_and_evidence_unchanged": True,
        "database_unchanged": True,
        "receipt_matches_exact_preview": True,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
