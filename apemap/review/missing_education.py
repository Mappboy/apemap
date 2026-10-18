"""Reproduce a published missing-school cohort and prepare ordinary review tasks."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from apemap.constants import RAW_WIKIMEDIA_DIR
from apemap.db import get_connection
from apemap.review.candidates import json_value
from apemap.review.evidence import evidence_revision
from apemap.review.integration import project_review_records
from apemap.review.model import active_heads
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError

if TYPE_CHECKING:
    from apemap.review.research_batches import ResearchBatches

PURPOSE = "missing-secondary-education"
PUBLISHED_RELEASE: dict[str, Any] = {
    "version": "0.7.0",
    "release_url": "https://github.com/Mappboy/apemap/releases/tag/data-v0.7.0",
    "archive_url": "https://github.com/Mappboy/apemap/releases/download/data-v0.7.0/apemap-release-v0.7.0.tar.gz",
    "archive_sha256": "efff402034ba846001c80b230593cea9b12a444ca79847505b15d3ea5c4dace6",
    "manifest_url": "https://github.com/Mappboy/apemap/releases/download/data-v0.7.0/manifest.json",
    "manifest_sha256": "1b3eecde69ccfb63a0dc2fc206634079745ab0c2b8056264c567d64075cbed58",
    "payload_sha256": {
        "members": "782f12c72a1231cc34f2ed5e228cd127631b43d695123d1badcb9b8083567aff",
        "parliament_service": "41dd70579215b167a93943c9b00314813ca5f64dbd7ed0b36cd1d8502023e950",
        "member_education": "1482ae3b005b86af78c541479fc859dfb1a7db281ba543ba29f46a0ae011d0ea",
    },
}


def _scope(
    records: dict[str, list[dict[str, Any]]], parliament: int
) -> list[dict[str, Any]]:
    services: dict[str, list[dict[str, Any]]] = {}
    for row in records["parliament_service"]:
        if (
            int(row["parliament_number"]) == parliament
            and str(row["is_opening_day_member"]).casefold() == "true"
        ):
            services.setdefault(row["member_id"], []).append(row)
    education: dict[str, list[dict[str, Any]]] = {}
    for row in records["member_education"]:
        if row["level"] == "secondary":
            education.setdefault(row["member_id"], []).append(row)
    result = []
    for member in sorted(records["members"], key=lambda row: row["member_id"]):
        member_id = member["member_id"]
        assertions = education.get(member_id, [])
        if member_id not in services or any(
            row.get("institution_resolution") != "home_educated"
            and (row.get("school_name_as_recorded") or "").strip()
            for row in assertions
        ):
            continue
        home = any(
            row.get("institution_resolution") == "home_educated" for row in assertions
        )
        result.append(
            {
                **member,
                "services": sorted(
                    services[member_id], key=lambda row: row["service_id"]
                ),
                "secondary_education": assertions,
                "status": "resolved_skip" if home else "needs_research",
                "reason": "Reviewed home education has no institutional school identity."
                if home
                else "No institutional secondary-school name is recorded.",
            }
        )
    return json_value(result)


def project_missing_education_scope(
    service: ReviewService, parliament: int
) -> list[dict[str, Any]]:
    """Read the current review projection without updating its database or ledgers."""
    events = service.events()
    with get_connection(service.db_path, read_only=True) as source:
        records = project_review_records(
            source,
            events,
            matcher=service.matcher(),
            evidence_records=service.evidence_records(events),
        )
    return _scope(records, parliament)


def _published_scope(release_dir: Path, parliament: int) -> list[dict[str, Any]]:
    raw = (release_dir / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PUBLISHED_RELEASE["manifest_sha256"]:
        raise ValueError("Release manifest does not match the pinned published dataset")
    manifest = json.loads(raw)
    if (
        manifest["data_release_version"] != PUBLISHED_RELEASE["version"]
        or manifest["cohort_definition"] != "opening_day"
        or parliament not in manifest["parliaments"]
    ):
        raise ValueError(
            "Published release does not contain the requested opening cohort"
        )
    records = {}
    for table, expected in PUBLISHED_RELEASE["payload_sha256"].items():
        relative = f"data/{table}.csv"
        path = release_dir / relative
        content = path.read_bytes()
        entry = manifest["files"][relative]
        if (
            hashlib.sha256(content).hexdigest() != expected
            or entry["sha256"] != expected
            or entry["size_bytes"] != len(content)
        ):
            raise ValueError(f"Published payload checksum mismatch: {relative}")
        with path.open(encoding="utf-8", newline="") as stream:
            records[table] = [
                {key: value or None for key, value in row.items()}
                for row in csv.DictReader(stream)
            ]
    return _scope(records, parliament)


def _identity_hint(aph_id: str) -> dict[str, Any] | None:
    path = RAW_WIKIMEDIA_DIR / "members" / f"{aph_id}.json"
    if not path.exists():
        return None
    raw = path.read_bytes()
    cached = json.loads(raw)
    if (
        str(cached.get("aph_id", "")).casefold() != aph_id.casefold()
        or cached.get("status") != "matched"
    ):
        return None
    return {
        "basis": "unreviewed_cached_identity_hint",
        "cache_sha256": hashlib.sha256(raw).hexdigest(),
        **{
            key: cached.get(key)
            for key in (
                "aph_id",
                "wikidata_id",
                "wikipedia_title",
                "wikipedia_url",
                "source_url",
                "source_query",
                "retrieved_at",
            )
        },
    }


def prepare_missing_education_batch(
    batches: ResearchBatches,
    batch_id: str,
    release_dir: Path,
    parliament: int = 48,
    expected_count: int = 49,
) -> dict[str, Any]:
    """Freeze the audit, resolved skips and research tasks without applying decisions."""
    from apemap.review.research_batches import _digest, _now, _write

    path = batches.path(batch_id)
    if path.exists():
        raise ValueError("Batch already exists; choose a new ID")
    if expected_count < 1:
        raise ValueError("Expected count must be positive")
    scope = _published_scope(Path(release_dir).resolve(), parliament)
    if len(scope) != expected_count:
        raise ValueError(f"Expected {expected_count} audit members, found {len(scope)}")
    service = batches.service
    source = service.source_revision(include_evidence=False)
    ledger = service.review_revision()
    retained = evidence_revision(service.evidence_path)
    current = project_missing_education_scope(service, parliament)
    if {row["member_id"]: row["status"] for row in current} != {
        row["member_id"]: row["status"] for row in scope
    }:
        raise StaleReviewError(
            "Current projected missing-school cohort differs from the published audit"
        )
    heads = active_heads(service.events())
    provenance = {
        "generated_by": "codex-session",
        "model": None,
        "token_usage": None,
        "availability_note": "Preparation runs no research model. Model identity and token usage for subsequent session research must be reported honestly when available.",
    }
    tasks = []
    for row in scope:
        aph_id = row["aph_id"]
        row["identity_hint"] = _identity_hint(aph_id)
        row["decision_ids"] = [
            event.decision_id
            for alternatives in heads.values()
            for event in alternatives
            if event.entity_type == "member_education"
            and str(event.payload.get("aph_id", "")).casefold() == aph_id.casefold()
        ]
        if row["status"] == "resolved_skip":
            continue
        review_id = f"education:{aph_id.casefold()}:missing"
        tasks.append(
            {
                "review_id": review_id,
                "purpose": PURPOSE,
                "context": {
                    "review_id": review_id,
                    "member": {
                        key: value
                        for key, value in row.items()
                        if key
                        not in {
                            "services",
                            "secondary_education",
                            "status",
                            "reason",
                            "identity_hint",
                            "decision_ids",
                        }
                    },
                    "services": row["services"],
                    "assertion": {"recorded_school_name": ""},
                    "projected_education": [],
                    "identity_hint": row["identity_hint"],
                    "published_release_version": PUBLISHED_RELEASE["version"],
                    "parliament": parliament,
                },
                "decision_heads": [
                    event.decision_id for event in heads.get(review_id, [])
                ],
                "research_origin": "agent-session",
                "research_provenance": provenance,
            }
        )
    skipped = [
        {key: row[key] for key in ("member_id", "aph_id", "display_name", "reason")}
        for row in scope
        if row["status"] == "resolved_skip"
    ]
    audit = {
        "schema_version": 1,
        "purpose": PURPOSE,
        "parliament": parliament,
        "cohort": "opening_day",
        "selection": "Opening-day members without any secondary assertion having a nonempty school_name_as_recorded and institution_resolution distinct from home_educated. Named unresolved or unconfirmed schools are excluded.",
        "published_release": PUBLISHED_RELEASE,
        "members": scope,
    }
    manifest = {
        "schema_version": 1,
        "batch_id": batch_id,
        "kind": "schools",
        "purpose": PURPOSE,
        "origin": "agent-session",
        "parliament": parliament,
        "provenance": provenance,
        "created_at": _now(),
        "source_revision": source,
        "decision_revision": ledger,
        "evidence_revision": retained,
        "task_sha256": _digest(tasks),
        "audit_sha256": _digest(audit),
        "case_count": len(tasks),
        "audit_count": len(scope),
        "skipped_count": len(skipped),
        "audit_summary": {
            "audit_count": len(scope),
            "research_count": len(tasks),
            "skipped": skipped,
        },
        "models": [],
        "reasoning_effort": None,
        "max_workers": 2,
        "cases_per_task": 1,
        "attempt_budgets": [],
    }
    if (
        source != service.source_revision(include_evidence=False)
        or ledger != service.review_revision()
        or retained != evidence_revision(service.evidence_path)
    ):
        raise StaleReviewError("Research inputs changed during audit preparation")
    path.mkdir(parents=True)
    _write(path / "audit.json", audit)
    _write(path / "tasks.json", tasks)
    _write(path / "manifest.json", manifest)
    (path / "instructions.md").write_text(
        "# Missing secondary-school research\n\nThe published audit includes resolved non-institutional skips; research only tasks.json. No research has run during preparation. Establish member identity, then inspect and cite actual secondary attendance evidence. Cached QIDs and Wikipedia links are unreviewed hints. Preserve exact school names and source retrieval dates; do not infer a school from party, electorate, tertiary education or geography. Record contradictory evidence and unresolved cases. Save proposals for individual human source inspection in the ordinary Research batches workflow. Never edit decision or evidence ledgers during research.\n",
        encoding="utf-8",
    )
    return {"manifest": manifest, "path": str(path), "tasks": tasks, "audit": audit}
