"""Reviewer-confirmed interpretations of retained institution evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import math
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.manual_institutions import FIELDS, manual_institution_reference
from apemap.review.model import active_heads, institution_reference, school_key


def eligible_sources(
    review_id: str, evidence: Iterable[EvidenceRecord | Mapping[str, Any]]
) -> dict[str, EvidenceRecord]:
    records = [
        value
        if isinstance(value, EvidenceRecord)
        else EvidenceRecord.from_dict(dict(value))
        for value in evidence
    ]
    return {
        record.evidence_id: record
        for record in records
        if record.review_id == review_id
        and not (
            isinstance(record.claim_value, Mapping) and "aph_id" in record.claim_value
        )
        and record.claim_type
        not in {
            "attendance",
            "member_attendance",
            "attendance_context",
            "parliamentary_context",
            "member_value",
            "member_identity",
            "member_context",
        }
        and record.source_type != "legacy-decision"
    }


def validate_field_sources(
    field: str, refs: Any, records: Mapping[str, EvidenceRecord], reference: str | None
) -> list[str]:
    if (
        field not in FIELDS
        or not isinstance(refs, list)
        or not refs
        or any(not isinstance(ref, str) for ref in refs)
        or len(refs) != len(set(refs))
    ):
        raise ValueError("Each institution field needs unique retained evidence IDs")
    for ref in refs:
        record = records.get(ref) if isinstance(ref, str) else None
        if record is None:
            raise ValueError(
                "Institution citation is unknown, member-attributed, or belongs to another case"
            )
        if record.stance != "supports":
            raise ValueError(
                "Institution field citations must support the reviewed fact"
            )
        if record.candidate_institution_ref not in (None, reference):
            raise ValueError("Institution citation belongs to a different candidate")
    return sorted(refs)


def validate_interpretation(
    context: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Check citations locally; interpretation of source text remains a human decision."""
    if not isinstance(result, dict) or set(result) != {"fields", "gaps", "conflicts"}:
        raise ValueError("Institution interpretation needs fields, gaps and conflicts")
    fields = result["fields"]
    if not isinstance(fields, dict) or set(fields) - FIELDS.keys():
        raise ValueError("Unknown interpreted institution field")
    for key in ("gaps", "conflicts"):
        if not isinstance(result[key], list) or any(
            not isinstance(note, str) for note in result[key]
        ):
            raise ValueError("Institution gaps and conflicts must be text lists")
    records = eligible_sources(context["review_id"], context["retained_evidence"])
    values, citations = {}, {}
    for field, proposed in fields.items():
        if not isinstance(proposed, dict) or set(proposed) != {"value", "evidence_ids"}:
            raise ValueError(
                "Institution fields need a value and retained evidence IDs"
            )
        value = proposed["value"]
        if value is None or value == "":
            if proposed["evidence_ids"]:
                raise ValueError("Unknown institution fields cannot cite a value")
            continue
        if field in {"latitude", "longitude"}:
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or abs(value) > (90 if field == "latitude" else 180)
            ):
                raise ValueError("Institution coordinates must be numeric")
        elif not isinstance(value, str):
            raise ValueError("Institution facts must be text")
        if field == "sector" and value not in {
            "Government",
            "Catholic",
            "Independent",
            "Other",
        }:
            raise ValueError("Invalid interpreted institution sector")
        if field == "status" and value not in {
            "current",
            "unknown",
            "historical_only",
            "closed",
            "merged",
        }:
            raise ValueError("Invalid interpreted institution status")
        values[field] = value.strip() if isinstance(value, str) else value
        citations[field] = proposed["evidence_ids"]
    reference = None
    problems = list(result["gaps"])
    if values.get("name"):
        try:
            reference = manual_institution_reference(
                values, context.get("manual_institutions", [])
            )
        except ValueError as exc:
            problems.append(str(exc))
    for field, refs in citations.items():
        validate_field_sources(field, refs, records, reference)
    if ("latitude" in values) != ("longitude" in values):
        raise ValueError("Supply both institution coordinates or neither")
    if "latitude" in values and set(citations["latitude"]) != set(
        citations["longitude"]
    ):
        raise ValueError("Coordinate pairs must cite the same location evidence")
    payload = {FIELDS[field]: value for field, value in values.items()}
    payload.setdefault("sector", None)
    payload.setdefault("institution_status", "unknown")
    payload["institution_ref"] = reference or ""
    if "latitude" in values:
        payload["location_source_url"] = records[citations["latitude"][0]].source_url
    return {
        "payload": payload,
        "field_sources": citations,
        "gaps": problems,
        "conflicts": [
            *result["conflicts"],
            *[
                record.excerpt_or_note
                for record in records.values()
                if record.stance == "contradicts"
            ],
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_draft_provenance(
    payload: Mapping[str, Any], evidence: Iterable[EvidenceRecord | Mapping[str, Any]]
) -> set[str]:
    """Replay the reviewed citation scope without treating model text as proof."""
    provenance = payload.get("institution_draft_provenance")
    if provenance is None:
        return set()
    if not isinstance(provenance, dict) or set(provenance) != {
        "review_id",
        "provider",
        "model",
        "generated_at",
        "fields",
    }:
        raise ValueError("Invalid institution draft provenance")
    case = provenance["review_id"]
    if not isinstance(case, str) or not case.startswith("school:"):
        raise ValueError(
            "Institution draft evidence requires its originating school case"
        )
    for key in ("provider", "model", "generated_at"):
        if not isinstance(provenance[key], str) or not provenance[key].strip():
            raise ValueError(
                "Institution draft provenance needs provider, model and generation time"
            )
    try:
        timestamp = datetime.fromisoformat(
            provenance["generated_at"].replace("Z", "+00:00")
        )
        if timestamp.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Institution generation time must include a timezone") from exc
    fields = provenance["fields"]
    if (
        not isinstance(fields, dict)
        or set(fields) - FIELDS.keys()
        or not {"name", "country"} <= fields.keys()
    ):
        raise ValueError("Reviewed institution drafts require cited name and country")
    reference = institution_reference(payload.get("institution_ref"))
    if not reference.startswith("manual:"):
        raise ValueError("Institution drafts require a manual reference")
    records = eligible_sources(case, evidence)
    used: set[str] = set()
    for field, column in FIELDS.items():
        value = payload.get(column)
        fact = fields.get(field)
        if value is None or (field == "status" and value == "unknown" and fact is None):
            if fact is not None:
                raise ValueError("Institution citation has no reviewed value")
            continue
        if (
            not isinstance(fact, dict)
            or set(fact) != {"value", "evidence_ids"}
            or fact["value"] != value
        ):
            raise ValueError(
                f"Reviewed institution {field} differs from its citation provenance"
            )
        used.update(
            validate_field_sources(field, fact["evidence_ids"], records, reference)
        )
    if "latitude" in fields:
        refs = fields["latitude"]["evidence_ids"]
        if refs != fields["longitude"]["evidence_ids"] or payload.get(
            "location_source_url"
        ) not in {records[ref].source_url for ref in refs}:
            raise ValueError(
                "Institution coordinates require the cited location source"
            )
    return used


def matching_manual_institutions(service: Any, name: str) -> list[dict[str, Any]]:
    return sorted(
        [
            dict(heads[0].payload)
            for heads in active_heads(service.events(allow_conflicts=True)).values()
            if len(heads) == 1
            and heads[0].entity_type == "manual_institution"
            and heads[0].effective_action == "accept"
            and school_key(heads[0].payload["school_name"]) == school_key(name)
        ],
        key=lambda row: row["institution_ref"],
    )
