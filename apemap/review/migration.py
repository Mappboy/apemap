"""Deterministic legacy proposals, local alias audits and independent parity."""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from apemap.constants import DATA_DIR, PROJECT_ROOT
from apemap.ingest.matching import normalize_school_key
from apemap.review.model import (
    ReviewEvent,
    DEFAULT_LOG_PATH,
    education_review_id,
    institution_review_id,
    member_review_id,
    school_review_id,
    service_review_id,
    validate_event,
)

LEGACY_FILES = (
    "manual_member_education.csv",
    "school_aliases.json",
    "historical_service_overrides.csv",
)
DEFAULT_LEGACY_DIR = PROJECT_ROOT / "archive/reference/2026-10-05"
DEFAULT_AUDIT_DIR = DATA_DIR / "raw/historical/acara"
TYPE_EQUIVALENTS = {
    "Sec": "Secondary",
    "Pri/Sec": "Combined",
    "Prim": "Primary",
}


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _register_path(external_dir: Path) -> Path:
    """Select a recipe's location register without mixing vintages."""
    for name in ("school-location-2025.csv", "school-location-2022.csv"):
        path = external_dir / name
        if path.exists():
            return path
    raise FileNotFoundError(f"No ACARA location register in {external_dir}")


def _register_rows(external_dir: Path) -> list[dict[str, str]]:
    return _csv_rows(_register_path(external_dir))


def audit_aliases(
    aliases_path: Path | str, external_dir: Path | str = DEFAULT_AUDIT_DIR
) -> list[dict[str, Any]]:
    """Audit every target against pinned rows without accepting research leads.

    Agreement of target metadata does not establish identity or succession.
    Existing source attestations are retained, never treated as fresh retrievals.
    """
    source = Path(aliases_path)
    aliases = json.loads(source.read_text(encoding="utf-8"))["aliases"]
    register = _register_path(Path(external_dir)).resolve()
    reference_source = {
        "path": register.relative_to(PROJECT_ROOT).as_posix()
        if register.is_relative_to(PROJECT_ROOT)
        else register.name,
        "sha256": _digest(register),
    }
    rows_by_id: dict[str, list[dict[str, str]]] = {}
    for row in _csv_rows(register):
        rows_by_id.setdefault(row["ACARA SML ID"], []).append(row)
    audit: list[dict[str, Any]] = []
    for recorded_name, value in sorted(aliases.items()):
        targets = rows_by_id.get(str(value["canonical_acara_id"]), [])
        checks = {
            "name": ("canonical_name", "School Name"),
            "state": ("state", "State"),
            "sector": ("sector", "School Sector"),
            "school_type": ("school_type", "School Type"),
        }
        disagreements = [] if targets else ["missing_target_id"]
        for label, (legacy_key, register_key) in checks.items():
            expected = value.get(legacy_key)
            if label == "school_type":
                expected = TYPE_EQUIVALENTS.get(expected, expected)
            if targets and expected not in {row.get(register_key) for row in targets}:
                disagreements.append(label)
        sourced = bool(
            value.get("source_url")
            and value.get("reviewed_at")
            and value.get("relationship_type")
            in {"direct", "alias", "rename", "successor"}
        )
        audit.append(
            {
                "recorded_name": recorded_name,
                "register_source": reference_source,
                "legacy": value,
                "target_rows": [
                    {
                        key: row.get(key)
                        for key in (
                            "ACARA SML ID",
                            "School Name",
                            "State",
                            "Suburb",
                            "School Sector",
                            "School Type",
                            "Campus Type",
                        )
                    }
                    for row in targets
                ],
                "metadata_disagreements": disagreements,
                "disposition": "map" if sourced and not disagreements else "research",
                "reason": (
                    "Existing sourced relationship; target metadata agrees"
                    if sourced and not disagreements
                    else "Legacy relationship needs evidence; target agreement alone is insufficient"
                ),
            }
        )
    return audit


def _migration_event(
    *,
    source: Path,
    original: Any,
    review_id: str,
    entity_type: str,
    action: str,
    payload: dict[str, Any],
    source_url: str,
    reviewer: str,
    reviewed_at: str,
    recorded_at: str,
    notes: str,
) -> ReviewEvent:
    identity = json.dumps(
        {"source_file": source.name, "record": original},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ReviewEvent(
        schema_version=1,
        decision_id=str(uuid5(NAMESPACE_URL, "apemap:legacy-review:" + identity)),
        review_id=review_id,
        entity_type=entity_type,
        action=action,
        payload=payload,
        source_url=source_url,
        reviewed_at=reviewed_at,
        reviewer=reviewer,
        notes="Legacy import attestation; original facts and dates retained in provenance. "
        + notes,
        supersedes=[],
        recorded_at=recorded_at,
        legacy={
            "source_file": source.name,
            "source_sha256": _digest(source),
            "record": original,
            "original_reviewer": (
                original.get("reviewer") if isinstance(original, dict) else None
            ),
            "original_reviewed_at": (
                original.get("reviewed_at") if isinstance(original, dict) else None
            ),
        },
    )


def propose_legacy_migration(
    source_dir: Path | str = DEFAULT_LEGACY_DIR,
    external_dir: Path | str = DEFAULT_AUDIT_DIR,
    *,
    reviewer: str,
    reviewed_at: str | None = None,
    recorded_at: str | None = None,
) -> tuple[list[ReviewEvent], list[dict[str, Any]]]:
    """Propose imports without touching the ledger or altering legacy artifacts."""
    root = Path(source_dir)
    now = datetime.now(timezone.utc)
    attestation_date = reviewed_at or now.date().isoformat()
    timestamp = recorded_at or now.isoformat()
    date.fromisoformat(attestation_date)
    datetime.fromisoformat(timestamp)
    if not reviewer.strip():
        raise ValueError("An explicit migration reviewer is required")
    for name in LEGACY_FILES:
        if not (root / name).is_file():
            raise FileNotFoundError(root / name)
    audit = audit_aliases(root / "school_aliases.json", external_dir)
    events: list[ReviewEvent] = []
    accepted_aliases: dict[str, str] = {}
    for row in audit:
        name, legacy = row["recorded_name"], row["legacy"]
        accepted = row["disposition"] == "map"
        payload = (
            {
                "recorded_name": name,
                "institution_ref": f"acara:{legacy['canonical_acara_id']}",
                "relationship_type": legacy["relationship_type"],
            }
            if accepted
            else {"recorded_name": name, "legacy": legacy}
        )
        if accepted:
            accepted_aliases[normalize_school_key(name)] = payload["institution_ref"]
        events.append(
            _migration_event(
                source=root / "school_aliases.json",
                original={"recorded_name": name, **legacy},
                review_id=school_review_id(name),
                entity_type="school",
                action=row["disposition"],
                payload=payload,
                source_url=legacy.get("source_url", "") if accepted else "",
                reviewer=reviewer,
                reviewed_at=attestation_date,
                recorded_at=timestamp,
                notes=legacy.get("notes", "") if accepted else row["reason"],
            )
        )
    direct_ids: dict[str, set[str]] = {}
    for row in _register_rows(Path(external_dir)):
        direct_ids.setdefault(normalize_school_key(row["School Name"]), set()).add(
            row["ACARA SML ID"]
        )
    for original in _csv_rows(root / "manual_member_education.csv"):
        name, aph_id = original["school_name"], original["aph_id"].lower()
        payload = {
            "aph_id": aph_id,
            "recorded_school_name": name,
            **{
                field: original[field]
                for field in ("attended_status", "confidence", "retrieved_at")
            },
        }
        normalized = normalize_school_key(name)
        target = accepted_aliases.get(normalized)
        ids = direct_ids.get(normalized, set())
        if target is None and len(ids) == 1:
            target = f"acara:{next(iter(ids))}"
        if target:
            payload["institution_ref"] = target
        events.append(
            _migration_event(
                source=root / "manual_member_education.csv",
                original=original,
                review_id=education_review_id(aph_id, name),
                entity_type="member_education",
                action="accept",
                payload=payload,
                source_url=original["source_url"],
                reviewer=reviewer,
                reviewed_at=attestation_date,
                recorded_at=timestamp,
                notes=original.get("reviewer_notes", ""),
            )
        )
    services: dict[tuple[str, int], list[dict[str, str]]] = {}
    for row in _csv_rows(root / "historical_service_overrides.csv"):
        key = row["aph_id"].lower(), int(row["parliament_number"])
        services.setdefault(key, []).append(row)
    for (aph_id, parliament), originals in sorted(services.items()):
        sources = {row["source_url"] for row in originals}
        if len(sources) != 1:
            raise ValueError(
                "Legacy service group has multiple sources; review explicitly"
            )
        intervals = [
            {
                key: row.get(key) or None
                for key in (
                    "service_start",
                    "service_end",
                    "chamber",
                    "party",
                    "party_abbrev",
                    "electorate",
                    "state_or_territory",
                    "retrieved_at",
                    "source_url",
                )
            }
            for row in sorted(originals, key=lambda item: item["service_start"])
        ]
        events.append(
            _migration_event(
                source=root / "historical_service_overrides.csv",
                original=originals,
                review_id=service_review_id(aph_id, parliament),
                entity_type="service",
                action="accept",
                payload={
                    "aph_id": aph_id,
                    "parliament_number": parliament,
                    "intervals": intervals,
                },
                source_url=next(iter(sources)),
                reviewer=reviewer,
                reviewed_at=attestation_date,
                recorded_at=timestamp,
                notes="; ".join(row["reviewer_notes"] for row in originals),
            )
        )
    return events, audit


def migration_report(
    events: list[ReviewEvent], audit: list[dict[str, Any]], source_dir: Path | str
) -> dict[str, Any]:
    """Return a reviewable report with exact original lineage and all target checks."""
    root = Path(source_dir)
    return {
        "schema_version": 1,
        "source_files": {name: _digest(root / name) for name in LEGACY_FILES},
        "proposed_event_count": len(events),
        "accepted_education": sum(
            event.entity_type == "member_education" and event.action == "accept"
            for event in events
        ),
        "mapped_aliases": sum(row["disposition"] == "map" for row in audit),
        "research_aliases": sum(row["disposition"] == "research" for row in audit),
        "audit_reference": audit[0]["register_source"] if audit else None,
        "alias_audit": [
            {key: value for key, value in row.items() if key != "register_source"}
            for row in audit
        ],
        "proposals": [asdict(event) for event in events],
    }


def _csv_identity(row: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    """Identify old queues from explicit IDs; never resolve people by name."""
    aph_id = row.get("aph_id") or row.get("member_id", "").removeprefix("aph-")
    if row.get("field"):
        if not aph_id:
            raise ValueError("Member review requires aph_id/member_id")
        payload: dict[str, Any] = {"aph_id": aph_id.lower(), "field": row["field"]}
        return "member", member_review_id(aph_id, row["field"]), payload
    if "institution_id" in row and "raw_school_text" in row:
        name = row["raw_school_text"]
        if not name:
            raise ValueError("School review requires raw_school_text")
        return "school", school_review_id(name), {"recorded_name": name}
    if "raw_school_text" in row:
        if not aph_id:
            raise ValueError("Education review requires aph_id/member_id")
        name = row["raw_school_text"]
        return (
            "member_education",
            education_review_id(aph_id, name),
            {
                "aph_id": aph_id.lower(),
                "recorded_school_name": name,
            },
        )
    if row.get("parliament_number"):
        if not aph_id:
            raise ValueError("Service review requires aph_id/member_id")
        parliament = int(row["parliament_number"])
        return (
            "service",
            service_review_id(aph_id, parliament),
            {
                "aph_id": aph_id.lower(),
                "parliament_number": parliament,
            },
        )
    raise ValueError("Unsupported review CSV schema")


def _resolved_csv_payload(
    entity: str, payload: dict[str, Any], row: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Use only explicit resolved values, never generated suggestions."""
    result = dict(payload)
    if entity == "member":
        value: Any = row.get("resolved_value")
        if not value:
            raise ValueError("Accepted member correction requires resolved_value")
        if value == "null":
            value = None
        elif result["field"] == "date_of_birth":
            try:
                value = date.fromisoformat(value).isoformat()
            except ValueError:
                value = datetime.strptime(value, "%d/%m/%Y").date().isoformat()
        result["value"] = value
    elif entity == "school":
        target = row.get("resolved_acara_id")
        manual: dict[str, Any] | None = None
        if target:
            result["institution_ref"] = f"acara:{target}"
        else:
            name, country = row.get("resolved_school_name"), row.get("resolved_country")
            if not name or not country:
                raise ValueError(
                    "Manual school requires resolved_school_name and resolved_country"
                )
            slug = re.sub(r"[^a-z0-9]+", "-", normalize_school_key(name)).strip("-")
            slug = slug[:48].rstrip("-") or "school"
            digest = hashlib.sha256(payload["recorded_name"].encode()).hexdigest()[:12]
            reference = row.get("resolved_institution_ref") or f"manual:{slug}-{digest}"
            result["institution_ref"] = reference
            manual = {
                "institution_ref": reference,
                "school_name": name,
                "country": country,
                "sector": row.get("resolved_sector") or "Other",
            }
            for field in ("state", "suburb", "postcode", "school_type"):
                if row.get(f"resolved_{field}"):
                    manual[field] = row[f"resolved_{field}"]
            for field in ("latitude", "longitude"):
                if row.get(f"resolved_{field}"):
                    manual[field] = float(row[f"resolved_{field}"])
            if "latitude" in manual or "longitude" in manual:
                manual["location_source_url"] = row.get("address_source_url", "")
        result["relationship_type"] = row.get("relationship_type") or "direct"
        return result, manual
    elif entity == "member_education":
        if not row.get("resolved_value"):
            raise ValueError(
                "Accepted education requires an explicit resolved school name"
            )
        if (
            normalize_school_key(row["resolved_value"])
            != normalize_school_key(payload["recorded_school_name"])
            and not row.get("resolved_acara_id")
            and not row.get("resolved_institution_ref")
        ):
            raise ValueError(
                "A changed school label requires an explicit institution mapping; "
                "new attendance assertions must be recorded explicitly"
            )
        for field in ("attended_status", "confidence", "retrieved_at"):
            if not row.get(field):
                raise ValueError(f"Accepted education requires {field}")
            result[field] = row[field]
        if row.get("resolved_acara_id"):
            result["institution_ref"] = f"acara:{row['resolved_acara_id']}"
        elif row.get("resolved_institution_ref"):
            result["institution_ref"] = row["resolved_institution_ref"]
        if row.get("years_attended"):
            result["years_attended"] = row["years_attended"]
        if row.get("graduation_year"):
            result["graduation_year"] = int(row["graduation_year"])
    elif entity == "service":
        if not row.get("resolved_value"):
            raise ValueError(
                "Accepted service correction requires resolved_value JSON intervals"
            )
        value = json.loads(row["resolved_value"])
        result["intervals"] = (
            value.get("intervals") if isinstance(value, dict) else value
        )
    return result, None


def propose_review_csv_import(
    path: Path | str,
    *,
    reviewer: str,
    incomplete_as_research: bool = False,
) -> tuple[list[ReviewEvent], dict[str, Any]]:
    """Dry-run old CSV annotations; never infer missing evidence or mutate them."""
    source = Path(path)
    rows = _csv_rows(source)
    now = datetime.now(timezone.utc)
    events: list[ReviewEvent] = []
    blocked: list[dict[str, Any]] = []
    skipped = 0
    for index, row in enumerate(rows, start=2):
        status = row.get("review_status", "")
        if status not in {"accepted", "rejected", "needs_research"}:
            skipped += 1
            continue
        try:
            entity, identifier, identity = _csv_identity(row)
            action = {
                "accepted": "accept",
                "rejected": "reject",
                "needs_research": "research",
            }[status]
            payload = identity
            source_url = row.get("manual_source_url", "")
            notes = (
                row.get("review_notes", "")
                or "Explicit legacy CSV annotation imported; original review rationale unrecorded"
            )
            manual = None
            incomplete = None
            if action == "accept":
                try:
                    payload, manual = _resolved_csv_payload(entity, identity, row)
                    if not source_url:
                        raise ValueError(
                            "Accepted correction requires manual_source_url evidence"
                        )
                except (ValueError, TypeError) as error:
                    if not incomplete_as_research:
                        raise
                    incomplete = str(error)
                    action, payload, manual = "research", identity, None
                    source_url = ""
                    notes = f"Legacy accepted annotation retained as research: {error}"
            event = _migration_event(
                source=source,
                original=row,
                review_id=identifier,
                entity_type=entity,
                action=action,
                payload=payload,
                source_url=source_url,
                reviewer=reviewer,
                reviewed_at=now.date().isoformat(),
                recorded_at=now.isoformat(),
                notes=notes,
            )
            batch = [event]
            if manual:
                institution = _migration_event(
                    source=source,
                    original={"manual_institution": row},
                    review_id=institution_review_id(manual["institution_ref"]),
                    entity_type="manual_institution",
                    action="accept",
                    payload=manual,
                    source_url=source_url,
                    reviewer=reviewer,
                    reviewed_at=now.date().isoformat(),
                    recorded_at=now.isoformat(),
                    notes=notes,
                )
                batch.insert(0, institution)
            try:
                for proposed in batch:
                    validate_event(proposed)
            except (ValueError, TypeError) as error:
                if not incomplete_as_research or action != "accept":
                    raise
                incomplete = str(error)
                event = _migration_event(
                    source=source,
                    original=row,
                    review_id=identifier,
                    entity_type=entity,
                    action="research",
                    payload=identity,
                    source_url="",
                    reviewer=reviewer,
                    reviewed_at=now.date().isoformat(),
                    recorded_at=now.isoformat(),
                    notes=f"Legacy accepted annotation retained as research: {error}",
                )
                validate_event(event)
                batch = [event]
            events.extend(batch)
            if incomplete:
                blocked.append(
                    {"row": index, "reason": incomplete, "retained_as_research": True}
                )
        except (ValueError, TypeError, KeyError) as error:
            blocked.append(
                {"row": index, "reason": str(error), "retained_as_research": False}
            )
    return events, {
        "source_file": source.name,
        "source_sha256": _digest(source),
        "rows": len(rows),
        "skipped_rows": skipped,
        "proposed_events": len(events),
        "blocked": blocked,
        "errors": [item for item in blocked if not item["retained_as_research"]],
        "dry_run": True,
    }


def compare_migration_records(
    before: dict[str, list[dict[str, Any]]],
    after: dict[str, list[dict[str, Any]]],
    research_names: set[str],
    *,
    source_institutions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fail on undeclared factual changes; report explicit legacy-policy effects."""
    from apemap.review.model import school_key

    names = {school_key(name) for name in research_names}
    changes: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    counts = {
        table: {"before": len(before[table]), "after": len(after[table])}
        for table in before
    }
    linked_before = {row["institution_id"] for row in before["member_education"]}
    linked_after = {row["institution_id"] for row in after["member_education"]}
    source_rows = {row["institution_id"]: row for row in source_institutions or []}
    changed_research_institutions: set[str] = set()
    keys = {
        "members": "member_id",
        "parliament_service": "service_id",
        "member_education": "education_id",
        "institutions": "institution_id",
    }
    for table, key in keys.items():
        left, right = (
            {row[key]: row for row in records[table]} for records in (before, after)
        )
        if len(left) != len(before[table]) or len(right) != len(after[table]):
            unexpected.append(
                {
                    "table": table,
                    "reason": "Duplicate canonical identity in parity input",
                    "key": key,
                }
            )
        for identifier in sorted(set(left) | set(right)):
            original, current = left.get(identifier), right.get(identifier)
            fields = sorted(
                field
                for field in set(original or {}) | set(current or {})
                if field != "reviewer_notes"
                and (original or {}).get(field) != (current or {}).get(field)
            )
            if not fields and original is not None and current is not None:
                continue
            policy = None
            if table == "member_education" and original and current:
                recorded = str(original.get("school_name_as_recorded") or "")
                unresolved_id = (
                    "inst-unmatched-"
                    + normalize_school_key(recorded).replace(" ", "-")[:40]
                )
                if (
                    school_key(recorded) in names
                    and set(fields)
                    <= {
                        "institution_id",
                        "confidence",
                        "institution_resolution",
                        "resolution_source_url",
                    }
                    and current.get("institution_id") == unresolved_id
                    and current.get("confidence") == "unconfirmed"
                    and current.get("institution_resolution") == "unresolved"
                    and current.get("resolution_source_url") is None
                ):
                    policy = "unsourced_alias_requires_research"
                    changed_research_institutions.update(
                        [original["institution_id"], current["institution_id"]]
                    )
            elif table == "institutions":
                if identifier in changed_research_institutions and (
                    (current is None and identifier not in linked_after)
                    or (original is None and current == source_rows.get(identifier))
                ):
                    policy = "institution_change_for_unsourced_alias"
                elif (
                    original is None
                    and current is not None
                    and identifier not in linked_before | linked_after
                    and current == source_rows.get(identifier)
                ):
                    policy = "unlinked_source_institution_retained"
            item = {
                "table": table,
                "id": identifier,
                "changed_fields": fields,
                "policy": policy,
                "before": original,
                "after": current,
            }
            (changes if policy else unexpected).append(item)
    return {
        "passed": not unexpected,
        "counts": counts,
        "declared_policy_changes": changes,
        "unexpected_changes": unexpected,
        "ignored_fields": ["reviewer_notes"],
    }


def verify_migration_parity(
    *,
    log_path: Path | str = DEFAULT_LOG_PATH,
    legacy_dir: Path | str = DEFAULT_LEGACY_DIR,
    raw_path: Path | str = DATA_DIR / "raw/aph/individuals.json",
    recipes: dict[str, tuple[Path, list[int], Path]] | None = None,
) -> dict[str, Any]:
    """Run archived legacy code independently beside the new ingestion projection.

    Work occurs in separate temporary databases and report directories. No checked-in
    research output is refreshed; pinned cached APH records are supplied explicitly.
    """
    import importlib.util
    import sys
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from apemap.db import get_connection
    from apemap.coverage import compute_parliament_coverage
    from apemap.ingest.pipeline import run_aph_ingestion
    from apemap.inputs import verify_inputs_manifest
    from apemap.review.model import load_events, resolve_events

    archive = PROJECT_ROOT / "archive/legacy-review/2026-10-05"

    def load_archived(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(
            f"apemap_legacy_{name}", archive / f"{name}.py"
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"Cannot load independent legacy oracle {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    matching = load_archived("matching")
    with patch.dict(sys.modules, {"apemap.ingest.matching": matching}):
        review = load_archived("review")
        with patch.dict(sys.modules, {"apemap.ingest.review": review}):
            history = load_archived("history")
            with patch.dict(sys.modules, {"apemap.ingest.history": history}):
                legacy_pipeline = load_archived("pipeline")
    original_matcher = matching.SchoolMatcher
    legacy_pipeline.SchoolMatcher = lambda **kwargs: original_matcher(
        **kwargs, aliases_file=Path(legacy_dir) / "school_aliases.json"
    )
    selected = recipes or {
        "standard": (
            DATA_DIR / "external",
            list(range(42, 49)),
            DATA_DIR / "inputs-manifest.json",
        ),
        "historical": (
            DEFAULT_AUDIT_DIR,
            list(range(42, 49)),
            DATA_DIR / "historical-inputs-manifest.json",
        ),
        "contemporary_only": (
            DATA_DIR / "external",
            [46, 47, 48],
            DATA_DIR / "inputs-manifest.json",
        ),
    }
    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    individuals = raw["value"] if isinstance(raw, dict) else raw
    research_names = {
        event.payload["recorded_name"]
        for event in resolve_events(load_events(Path(log_path))).values()
        if event.entity_type == "school" and event.effective_action == "research"
    }
    outputs: dict[str, Any] = {}

    def records(connection: Any) -> dict[str, list[dict[str, Any]]]:
        result = {}
        for table in (
            "members",
            "institutions",
            "parliament_service",
            "member_education",
        ):
            cursor = connection.execute(f"SELECT * FROM {table}")
            columns = [description[0] for description in cursor.description]
            result[table] = [dict(zip(columns, row)) for row in cursor.fetchall()]
        # Normalize database timestamps identically, rather than comparing Python types.
        return json.loads(json.dumps(result, default=str))

    with TemporaryDirectory(prefix="apemap-review-parity-") as temporary:
        for name, (external, parliaments, manifest_path) in selected.items():
            valid, errors = verify_inputs_manifest(manifest_path)
            if not valid:
                raise ValueError(
                    "Cannot compare unpinned source inputs: " + "; ".join(errors)
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            timestamp = datetime.fromisoformat(manifest["created_at"])
            with get_connection(None) as old:
                legacy_pipeline.run_aph_ingestion(
                    conn=old,
                    output_dir=Path(temporary) / name / "legacy",
                    raw_individuals=individuals,
                    external_dir=external,
                    parliaments=parliaments,
                    retrieved_at=timestamp,
                    manual_education_path=Path(legacy_dir)
                    / "manual_member_education.csv",
                    service_overrides_path=Path(legacy_dir)
                    / "historical_service_overrides.csv",
                )
                before = records(old)
                before_coverage = compute_parliament_coverage(old, parliaments)

            # An independent source-only projection proves each retained row's
            # exact identity and metadata. Keep legacy research blocks, but remove
            # accepted aliases and manual attendance seeds from this baseline.
            def source_matcher(**kwargs: Any) -> Any:
                kwargs["require_alias_sources"] = True
                matcher = original_matcher(
                    **kwargs, aliases_file=Path(legacy_dir) / "school_aliases.json"
                )
                matcher.aliases = {
                    key: value
                    for key, value in matcher.aliases.items()
                    if not value.get("source_url")
                }
                return matcher

            with patch.object(legacy_pipeline, "SchoolMatcher", source_matcher):
                with get_connection(None) as source_only:
                    legacy_pipeline.run_aph_ingestion(
                        conn=source_only,
                        output_dir=Path(temporary) / name / "source-only",
                        raw_individuals=individuals,
                        external_dir=external,
                        parliaments=parliaments,
                        retrieved_at=timestamp,
                        manual_education_path=Path(temporary) / "no-manual.csv",
                        service_overrides_path=Path(temporary) / "no-service.csv",
                    )
                    source_institutions = records(source_only)["institutions"]
            with get_connection(None) as new:
                run_aph_ingestion(
                    conn=new,
                    output_dir=Path(temporary) / name / "review",
                    raw_individuals=individuals,
                    external_dir=external,
                    parliaments=parliaments,
                    retrieved_at=timestamp,
                    decision_log_path=Path(log_path),
                )
                after = records(new)
                after_coverage = compute_parliament_coverage(new, parliaments)
            outputs[name] = {
                "external_dir": external.relative_to(PROJECT_ROOT).as_posix()
                if external.is_relative_to(PROJECT_ROOT)
                else str(external),
                "parliaments": parliaments,
                "source_manifest_sha256": _digest(manifest_path),
                "coverage": {
                    "before": [
                        {
                            key: value
                            for key, value in row.items()
                            if "finance" not in key and "funding" not in key
                        }
                        for row in before_coverage
                    ],
                    "after": [
                        {
                            key: value
                            for key, value in row.items()
                            if "finance" not in key and "funding" not in key
                        }
                        for row in after_coverage
                    ],
                },
                **compare_migration_records(
                    before,
                    after,
                    research_names,
                    source_institutions=source_institutions,
                ),
            }
    return {
        "passed": all(report["passed"] for report in outputs.values()),
        "ledger_sha256": _digest(Path(log_path)),
        "legacy_source_hashes": {
            name: _digest(Path(legacy_dir) / name) for name in LEGACY_FILES
        },
        "legacy_oracle_hashes": {
            name: _digest(archive / f"{name}.py")
            for name in ("pipeline", "matching", "history", "review")
        },
        "recipes": outputs,
        "policy": "89 unsourced aliases become research; preserve supported assertions and source facts. Extra unlinked source institutions do not alter published coverage.",
    }
