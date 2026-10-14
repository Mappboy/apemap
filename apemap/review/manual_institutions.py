"""Build reviewable manual definitions from explicitly linked institution facts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
import re
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.model import (
    ReviewEvent,
    entity_for_review_id,
    institution_reference,
    institution_review_id,
    school_key,
    validate_event,
)

FIELDS = {
    "name": "school_name",
    "country": "country",
    "status": "institution_status",
    "sector": "sector",
    "school_type": "school_type",
    "suburb": "suburb",
    "state": "state",
    "latitude": "latitude",
    "longitude": "longitude",
}
IDENTITY_FIELDS = ("name", "country", "state", "suburb", "latitude", "longitude")


def _records(
    evidence: Iterable[EvidenceRecord | Mapping[str, Any]],
) -> dict[str, EvidenceRecord]:
    return {
        record.evidence_id: record
        for value in evidence
        for record in [
            value
            if isinstance(value, EvidenceRecord)
            else EvidenceRecord.from_dict(dict(value))
        ]
    }


def _references(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) - (FIELDS.keys() | {"reuse_ref"}):
        raise ValueError(
            "Institution evidence_refs must map known facts to evidence IDs"
        )
    for refs in value.values():
        if (
            not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or not ref for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError("Institution facts need unique nonempty evidence IDs")
    return {field: sorted(refs) for field, refs in value.items()}


def _claim(record: EvidenceRecord, field: str) -> Any:
    claim = record.claim_value
    if not isinstance(claim, Mapping) or "aph_id" in claim:
        return None
    if field in {"latitude", "longitude"}:
        if (
            record.claim_type == "location"
            and claim.get("latitude") is not None
            and claim.get("longitude") is not None
        ):
            return claim.get(field)
        return None
    if record.claim_type == "institution_fact" and claim.get("field") == field:
        return claim.get("value")
    if record.claim_type == "identity" and field in {"name", "country", "reuse_ref"}:
        return claim.get("institution_ref" if field == "reuse_ref" else field)
    return None


def _same(value: Any, other: Any) -> bool:
    if isinstance(value, str) and isinstance(other, str):
        return value.strip() == other.strip()
    return type(value) is not bool and type(other) is not bool and value == other


def _matches(
    record: EvidenceRecord, field: str, value: Any, values: Mapping[str, Any]
) -> bool:
    if _claim(record, field) is None or not _same(value, _claim(record, field)):
        return False
    if field in {"latitude", "longitude"}:
        return all(
            _same(values.get(coordinate), _claim(record, coordinate))
            for coordinate in ("latitude", "longitude")
        )
    return True


def _normalized(field: str, value: Any) -> Any:
    if field == "name":
        return school_key(str(value))
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    if field in {"latitude", "longitude"} and value is not None:
        return float(value)
    return value


def _distinguished(facts: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
    return any(
        field in facts
        and existing.get(FIELDS[field]) is not None
        and _normalized(field, facts[field])
        != _normalized(field, existing[FIELDS[field]])
        for field in IDENTITY_FIELDS
    )


def _compatible_candidate(
    records: Mapping[str, EvidenceRecord],
    references: Mapping[str, list[str]],
    reference: str,
) -> bool:
    return all(
        records[ref].candidate_institution_ref in (None, reference)
        for refs in references.values()
        for ref in refs
    )


def _disputed_fields(
    facts: Mapping[str, Any],
    records: Mapping[str, EvidenceRecord],
    case: str,
    reference: str,
) -> list[str]:
    return [
        f"Conflicting institution evidence for {field}"
        for field, value in facts.items()
        if any(
            record.review_id == case
            and record.candidate_institution_ref in (None, reference)
            and _claim(record, field) is not None
            and (
                record.stance == "contradicts"
                or (
                    record.stance == "supports"
                    and not _matches(record, field, value, facts)
                )
            )
            for record in records.values()
        )
    ]


def build_manual_institution_candidate(
    facts: dict[str, Any],
    evidence: Iterable[EvidenceRecord | Mapping[str, Any]],
    existing_institutions: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Propose a definition or exact reuse; never append authority or merge names."""
    if not isinstance(facts, dict) or set(facts) - (
        FIELDS.keys() | {"reuse_ref", "evidence_refs"}
    ):
        raise ValueError("Institution candidate must contain known structured facts")
    records = _records(evidence)
    references = _references(facts.get("evidence_refs", {}))
    if any(ref not in records for refs in references.values() for ref in refs):
        raise ValueError("Unknown institution fact evidence reference")
    supported: dict[str, Any] = {}
    linked: dict[str, list[str]] = {}
    problems = []
    for field in (*FIELDS, "reuse_ref"):
        value = facts.get(field)
        if value is None or value == "":
            continue
        refs = references.get(field, [])
        for ref in refs:
            record = records[ref]
            claimed = _claim(record, field)
            if claimed is not None and (
                record.stance == "contradicts"
                or (
                    record.stance == "supports"
                    and not _matches(record, field, value, facts)
                )
            ):
                problems.append(f"Conflicting institution evidence for {field}")
        matching = [
            ref
            for ref in refs
            if records[ref].stance == "supports"
            and _matches(records[ref], field, value, facts)
        ]
        if matching:
            supported[field] = value.strip() if isinstance(value, str) else value
            linked[field] = matching
    for required in ("name", "country"):
        if required not in supported:
            problems.append(f"Supporting inspected evidence is required for {required}")
    if facts.get("reuse_ref") not in (None, "") and "reuse_ref" not in supported:
        problems.append(
            "Evidence-confirmed reuse_ref is required to reuse an institution"
        )
    if not {"latitude", "longitude"} <= supported.keys():
        for field in ("latitude", "longitude"):
            supported.pop(field, None)
            linked.pop(field, None)
    cases = {records[ref].review_id for refs in linked.values() for ref in refs}
    if len(cases) != 1 or any(
        entity_for_review_id(case) not in {"school", "member_education"}
        for case in cases
    ):
        problems.append(
            "Institution facts must belong to one school or attendance case"
        )
    result: dict[str, Any] = {
        "state": "needs_research",
        "institution_ref": None,
        "definition": None,
        "facts": supported,
        "evidence_refs": linked,
        "problems": sorted(set(problems)),
    }
    if problems:
        return result
    existing = {
        str(row.get("institution_ref") or row.get("institution_id")): row
        for row in existing_institutions
        if str(row.get("institution_ref") or row.get("institution_id", "")).startswith(
            "manual:"
        )
    }
    reuse = supported.get("reuse_ref")
    if reuse:
        institution_reference(reuse)
        target = existing.get(reuse)
        if (
            target is None
            or any(
                _normalized(field, supported[field])
                != _normalized(field, target.get(FIELDS[field]))
                for field in ("name", "country")
            )
            or _distinguished(supported, target)
        ):
            result["problems"] = [
                "Reviewed reuse identity does not match an existing manual institution"
            ]
            return result
        if any(
            field in supported
            and target.get(column) is not None
            and not (field == "status" and target[column] in {"manual", "unknown"})
            and _normalized(field, supported[field])
            != _normalized(field, target[column])
            for field, column in FIELDS.items()
        ):
            result["problems"] = [
                "Established institution facts differ; explicit supersession is required"
            ]
            return result
        if not _compatible_candidate(records, linked, reuse):
            result["problems"] = [
                "Institution fact evidence belongs to a different candidate"
            ]
            return result
        result["problems"] = _disputed_fields(
            supported, records, next(iter(cases)), reuse
        )
        if result["problems"]:
            return result
        result.update(state="reuse", institution_ref=reuse)
        return result
    slug = re.sub(r"[^a-z0-9]+", "-", school_key(supported["name"])).strip("-")
    slug = slug[:48].rstrip("-") or "school"
    reference = "manual:" + slug
    for row in existing.values():
        if (
            _normalized("name", supported["name"])
            == _normalized("name", row.get("school_name"))
            and _normalized("country", supported["country"])
            == _normalized("country", row.get("country"))
            and not _distinguished(supported, row)
        ):
            result["problems"] = [
                "Existing institution identity is ambiguous; evidence-confirmed reuse is required"
            ]
            return result
    if reference in existing:
        if not _distinguished(supported, existing[reference]):
            result["problems"] = [
                "Institution slug collision lacks distinguishing evidence"
            ]
            return result
        identity = {
            field: _normalized(field, supported[field])
            for field in IDENTITY_FIELDS
            if field in supported
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest()[:12]
        reference += "-" + digest
        if reference in existing:
            result["problems"] = [
                "Institution reference already exists; evidence-confirmed reuse is required"
            ]
            return result
    if not _compatible_candidate(records, linked, reference):
        result["problems"] = [
            "Institution fact evidence belongs to a different candidate"
        ]
        return result
    result["problems"] = _disputed_fields(
        supported, records, next(iter(cases)), reference
    )
    if result["problems"]:
        return result
    definition = {
        "institution_ref": reference,
        "sector": None,
        "institution_status": "unknown",
        **{
            FIELDS[field]: value
            for field, value in supported.items()
            if field in FIELDS
        },
        "institution_fact_evidence_refs": linked,
        "institution_evidence_review_id": next(iter(cases)),
    }
    if "latitude" in supported:
        definition["location_source_url"] = records[linked["latitude"][0]].source_url
    first = records[linked["name"][0]]
    try:
        validate_event(
            ReviewEvent(
                decision_id="candidate-validation",
                review_id=institution_review_id(reference),
                entity_type="manual_institution",
                action="accept",
                payload=definition,
                reviewer="candidate-validation",
                source_url=first.source_url,
                reviewed_at=first.retrieved_at[:10],
                recorded_at=first.retrieved_at,
            )
        )
    except ValueError as exc:
        result["problems"] = [str(exc)]
        return result
    result.update(state="create", institution_ref=reference, definition=definition)
    return result


def validate_institution_fact_references(
    payload: Mapping[str, Any],
    evidence: Iterable[EvidenceRecord | Mapping[str, Any]],
) -> set[str]:
    """Validate generated facts against their exact retained originating case."""
    if "institution_fact_evidence_refs" not in payload:
        return set()
    references = _references(payload["institution_fact_evidence_refs"])
    reference = institution_reference(payload.get("institution_ref"))
    if not reference.startswith("manual:"):
        raise ValueError(
            "Generated institution facts require a manual institution reference"
        )
    if set(references) - FIELDS.keys() or not {"name", "country"} <= references.keys():
        raise ValueError(
            "Generated institution definitions need name and country fact references"
        )
    case = payload.get("institution_evidence_review_id")
    if not isinstance(case, str) or entity_for_review_id(case) not in {
        "school",
        "member_education",
    }:
        raise ValueError(
            "Institution evidence must identify its originating school or attendance case"
        )
    records = _records(evidence)
    for field, column in FIELDS.items():
        if (
            payload.get(column) is not None
            and field not in references
            and not (field == "status" and payload[column] == "unknown")
        ):
            raise ValueError(
                f"Institution {field} requires its own fact evidence references"
            )
    result: set[str] = set()
    for field, refs in references.items():
        value = payload.get(FIELDS[field])
        for ref in refs:
            record = records.get(ref)
            if record is None:
                raise ValueError(f"Unknown institution fact evidence reference: {ref}")
            if record.review_id != case:
                raise ValueError(
                    "Institution fact evidence belongs to a different case"
                )
            if record.candidate_institution_ref not in (None, reference):
                raise ValueError(
                    "Institution fact evidence belongs to a different candidate"
                )
            if (
                value is None
                or record.stance != "supports"
                or not _matches(record, field, value, payload)
            ):
                raise ValueError(
                    f"Institution {field} differs from supporting evidence"
                )
            result.add(ref)
    if "latitude" in references and payload.get("location_source_url") not in {
        records[ref].source_url for ref in references["latitude"]
    }:
        raise ValueError(
            "Institution coordinates require their cited location source URL"
        )
    return result
