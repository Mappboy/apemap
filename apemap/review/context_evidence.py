"""Reviewed evidence roles adapt to the existing historical-context contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
import re
from typing import TYPE_CHECKING, Any

from apemap.review.model import ReviewEvent, validate_event

if TYPE_CHECKING:
    from apemap.review.evidence import EvidenceRecord

ROLE_TYPES = {
    "original_identity": "original_identity",
    "location": "location",
    "campus": "campus_continuity",
    "broad_sector": "sector",
    "detailed_sector": "sector",
    "relationship_timing": "relationship_timing",
    "profile_proxy": "profile_proxy",
    "finance_proxy": "finance_proxy",
}


def context_references(payload: Mapping[str, Any]) -> list[str]:
    roles = payload.get("context_evidence_refs", {})
    if not isinstance(roles, dict) or set(roles) - ROLE_TYPES.keys():
        raise ValueError(
            "context_evidence_refs must map known context roles to evidence IDs"
        )
    refs: list[str] = []
    for values in roles.values():
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
        ):
            raise ValueError("Each context role needs a nonempty list of evidence IDs")
        refs.extend(values)
    return refs


def _claim(role: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{role} needs a structured claim value")
    if role == "original_identity":
        return {"attended_institution_ref": value.get("institution_ref")}
    if role == "location":
        return {
            "historical_latitude": value.get("latitude"),
            "historical_longitude": value.get("longitude"),
        }
    if role == "campus":
        return {"campus_continuity": value.get("value")}
    if role in {"broad_sector", "detailed_sector"}:
        return {"historical_" + role: value.get(role)}
    if role == "relationship_timing":
        start, end = value.get("start_year"), value.get("end_year")
        if start is None and end is None:
            raise ValueError("Relationship timing needs at least one sourced year")
        if any(
            year is not None and (type(year) is not int or not 1700 <= year <= 2200)
            for year in (start, end)
        ) or (start is not None and end is not None and start > end):
            raise ValueError("Invalid relationship timing years")
        return {"start_year": start, "end_year": end}
    status = value.get("status")
    if status not in {"approved", "unsuitable"}:
        raise ValueError("Proxy claim status must be approved or unsuitable")
    year = value.get("reporting_year")
    if year is not None and (type(year) is not int or not 1700 <= year <= 2200):
        raise ValueError("Invalid proxy reporting year")
    return {"status": status, "reporting_year": year}


def resolve_context(
    event: ReviewEvent, records: list[EvidenceRecord]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Only explicitly selected, compatible reviewed facts enter replay."""
    from apemap.review.evidence import evidence_applies

    refs = context_references(event.payload)
    payload = dict(event.payload)
    summary: dict[str, Any] = {}
    if not refs:
        return payload, summary
    if (
        event.effective_action not in {"accept", "map"}
        or payload.get("relationship_type") != "successor"
    ):
        raise ValueError(
            "Reviewed context roles require an accepted successor relationship"
        )
    by_id = {record.evidence_id: record for record in records}
    sources = {
        "original_identity": "attended_identity_source_url",
        "location": "historical_location_source_url",
        "campus": "campus_continuity_source_url",
        "broad_sector": "historical_broad_sector_source_url",
        "detailed_sector": "historical_detailed_sector_source_url",
    }
    for role, ids in payload["context_evidence_refs"].items():
        selected = []
        for identifier in sorted(set(ids)):
            record = by_id.get(identifier)
            if (
                record is None
                or not evidence_applies(record, event.review_id)
                or record.candidate_institution_ref != payload.get("institution_ref")
            ):
                raise ValueError(
                    f"{role} evidence has an unavailable or incompatible scope/candidate"
                )
            if record.claim_type != ROLE_TYPES[role] or record.stance != "supports":
                raise ValueError(
                    f"{role} requires supporting {ROLE_TYPES[role]} evidence"
                )
            selected.append(record)
        claims = [_claim(role, record.claim_value) for record in selected]
        if any(claim != claims[0] for claim in claims[1:]):
            raise ValueError(
                f"Conflicting reviewed {role} claims; resolve them before mapping"
            )
        claim = claims[0]
        if any(value is None for value in claim.values()) and role in sources:
            raise ValueError(f"Incomplete reviewed {role} claim")
        summary[role] = {
            "value": claim,
            "evidence_refs": sorted(set(ids)),
            "source_urls": sorted({record.source_url for record in selected}),
        }
        if role in sources:
            for name, value in claim.items():
                if payload.get(name) is not None and payload[name] != value:
                    raise ValueError(
                        f"Explicit {name} disagrees with selected evidence"
                    )
                payload[name] = value
            source = sources[role]
            if (
                payload.get(source)
                and payload[source] not in summary[role]["source_urls"]
            ):
                raise ValueError(f"Explicit {source} disagrees with selected evidence")
            payload[source] = payload.get(source) or summary[role]["source_urls"][0]
    if (
        event.entity_type == "school"
        and payload.get("historical_scope_confirmed") is not True
    ):
        raise ValueError(
            "Confirm reviewed context covers all affected school assertions"
        )
    validate_event(replace(event, payload=payload))
    return payload, summary


def expand_context_events(
    events: list[ReviewEvent], records: list[EvidenceRecord]
) -> list[ReviewEvent]:
    """Adapt copies for replay; preserve serialized event/evidence bytes and IDs."""
    return [
        replace(event, payload=resolve_context(event, records)[0])
        if event.payload.get("context_evidence_refs")
        else event
        for event in events
    ]


def attendance_period(
    member: Mapping[str, Any], assertion: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe sourced dates first; DOB estimates remain research-only context."""
    years = str(assertion.get("years_attended") or "")
    found = [int(value) for value in re.findall(r"\b(?:18|19|20)\d{2}\b", years)]
    if found:
        return {
            "basis": "recorded",
            "start_year": min(found),
            "end_year": max(found),
            "label": years,
        }
    graduation = assertion.get("graduation_year")
    if graduation is not None:
        return {
            "basis": "recorded_endpoint",
            "start_year": None,
            "end_year": graduation,
            "label": f"Graduation recorded: {graduation}; start unknown",
        }
    born = member.get("date_of_birth")
    if born:
        try:
            year = date.fromisoformat(str(born)[:10]).year
        except ValueError:
            year = None
        if year is not None:
            return {
                "basis": "estimated",
                "start_year": year + 12,
                "end_year": year + 18,
                "label": f"Estimated secondary attendance: {year + 12}–{year + 18} (birth year +12 to +18; research only)",
            }
    return {
        "basis": "unknown",
        "start_year": None,
        "end_year": None,
        "label": "Attendance period unknown",
    }


def context_report(
    events: list[ReviewEvent], records: list[EvidenceRecord]
) -> dict[str, Any]:
    from apemap.review.model import resolve_events

    return {
        "schema_version": 1,
        "cases": [
            {
                "review_id": event.review_id,
                "decision_id": event.decision_id,
                "claims": resolve_context(event, records)[1],
                "profile_interpretation": "Contemporary reporting institution; historical proxy suitability is independent",
                "finance_interpretation": "Contemporary reporting institution and stated reporting year",
            }
            for event in sorted(
                resolve_events(events).values(), key=lambda item: item.review_id
            )
            if event.payload.get("relationship_type") == "successor"
            and event.effective_action in {"accept", "map"}
        ],
        "policy": "Only explicitly reviewed retained claims enter context; estimates never verify attendance.",
    }
