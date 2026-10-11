"""Read-only presentation of school evidence, never an identity matcher."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from apemap.review.model import school_key

RELATIONSHIPS = {
    "direct": "Same school",
    "alias": "Alternate name",
    "rename": "Renamed school",
    "successor": "Successor institution",
}


@dataclass
class SchoolTarget:
    reference: str
    name: str
    metadata: dict[str, Any]
    sources: list[str] = field(default_factory=list)
    legacy: bool = False


@dataclass
class SchoolLead:
    name: str
    sources: list[str]
    reference: str = ""


@dataclass
class SchoolView:
    name: str
    status: str
    parliaments: list[int]
    targets: list[SchoolTarget]
    leads: list[SchoolLead]
    source_records: list[dict[str, Any]]
    saved_reference: str
    saved_relationship: str
    original_reference: str = ""
    original_metadata: dict[str, Any] = field(default_factory=dict)


def evidence_links(value: Any) -> list[str]:
    """Retain only ordinary source links, with no requests or HTML interpretation."""
    found: set[str] = set()

    def collect(part: Any) -> None:
        if isinstance(part, str):
            try:
                parsed = urlsplit(part)
            except ValueError:
                return
            if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                found.add(part)
        elif isinstance(part, dict):
            for child in part.values():
                collect(child)
        elif isinstance(part, (list, tuple)):
            for child in part:
                collect(child)

    collect(value)
    return sorted(found)


def target_reference(payload: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Only explicit references establish a target, never matching display names."""
    ref = payload.get("institution_ref") or evidence.get("institution_ref")
    if isinstance(ref, str) and ref.startswith(("acara:", "manual:")):
        return ref
    aid = str(evidence.get("acara_id") or "").strip()
    return f"acara:{aid}" if aid.isascii() and aid.isdecimal() else ""


def school_view(
    item: dict[str, Any],
    resolve: Callable[[str], dict[str, Any] | None],
) -> SchoolView:
    """Group explicit targets while retaining unresolved leads and source records."""
    candidates = item.get("candidates", [])
    decision = item.get("decision") or {}
    payload = decision.get("payload", {})
    name = next(
        (
            row["payload"]["recorded_name"]
            for row in candidates
            if row.get("payload", {}).get("recorded_name")
        ),
        payload.get(
            "recorded_name",
            item.get("context", {}).get("recorded_name", item["review_id"]),
        ),
    )

    targets: dict[str, SchoolTarget] = {}
    leads: list[SchoolLead] = []
    records: list[dict[str, Any]] = []

    def add_target(
        ref: str, label: str, sources: list[str], legacy: bool = False
    ) -> None:
        metadata = resolve(ref)
        if metadata is None:
            existing = next((lead for lead in leads if lead.reference == ref), None)
            if existing:
                existing.sources = sorted(set(existing.sources + sources))
            else:
                leads.append(SchoolLead(label, sources, ref))
            return
        if ref not in targets:
            targets[ref] = SchoolTarget(ref, metadata["school_name"], metadata)
        target = targets[ref]
        target.sources = sorted(set(target.sources + sources))
        target.legacy |= legacy

    for row in candidates:
        evidence = row.get("evidence", {})
        ref = target_reference(row.get("payload", {}), evidence)
        label = (
            evidence.get("suggested_institution_name")
            or evidence.get("school_name")
            or name
        )
        if ref:
            add_target(ref, label, evidence_links(evidence))
        elif evidence.get("suggested_institution_name"):
            leads.append(SchoolLead(label, evidence_links(evidence)))
        elif not evidence.get("decision_only"):
            records.append(evidence)

    heads = item.get("conflicts") or ([decision] if decision else [])
    for event in heads:
        proposed = event.get("payload", {})
        ref = target_reference(proposed, {})
        if ref:
            add_target(ref, name, evidence_links(event.get("source_url")))
        legacy = proposed.get("legacy") or (event.get("legacy") or {}).get("record", {})
        if not isinstance(legacy, dict):
            legacy = {}
        aid = str(legacy.get("canonical_acara_id") or "")
        if aid.isascii() and aid.isdecimal():
            add_target(f"acara:{aid}", legacy.get("canonical_name") or name, [], True)

    action = decision.get("replacement_action") or decision.get("action")
    accepted = action in {"accept", "map"}
    status = (
        "conflict"
        if item.get("conflicts")
        else {
            "accept": "accepted",
            "map": "accepted",
            "research": "needs_research",
            "reject": "rejected",
        }.get(action, "pending")
    )
    progress = item.get("context", {}).get("individual_resolution") or item.get(
        "individual_resolution"
    )
    if progress and status != "conflict":
        status = progress["status"]
    return SchoolView(
        name=name,
        status=status,
        parliaments=sorted(
            {p for row in candidates for p in row.get("parliaments", [])}
        ),
        targets=list(targets.values()),
        leads=leads,
        source_records=records,
        saved_reference=payload.get("institution_ref", "") if accepted else "",
        saved_relationship=payload.get("relationship_type", "") if accepted else "",
        original_reference=payload.get("attended_institution_ref", "")
        if accepted
        else "",
        original_metadata=(resolve(payload["attended_institution_ref"]) or {})
        if accepted and payload.get("attended_institution_ref")
        else {},
    )


def school_comparison(
    school: dict[str, Any], selected: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Compare the effective mapping with a selected candidate without inferring identity.

    A suggested candidate is never treated as the current mapping. Only a saved
    school-wide decision supplies current institution metadata; original
    attended-institution metadata remains a separate historical reference.
    """
    current_ref = school.get("saved_reference") or ""
    current = next(
        (
            target.get("metadata", {})
            for target in school.get("targets", [])
            if target.get("reference") == current_ref
        ),
        {},
    )
    proposed = selected or {}
    fields = (
        ("name", "school_name", ("school_name", "recorded_name"), True),
        ("locality", "suburb", ("suburb", "location", "locality"), False),
        ("state", "state", ("state",), False),
        ("ACARA identifier", "acara_id", ("acara_id", "canonical_acara_id"), False),
        ("My School identifier", "myschool_id", ("myschool_id", "my_school_id"), False),
        ("sector", "sector", ("sector", "school_sector"), False),
        ("type", "school_type", ("school_type",), False),
        ("coordinates", "coordinates", ("latitude", "longitude"), False),
        (
            "historical names",
            "historical_names",
            ("historical_names", "alternative_names"),
            False,
        ),
    )
    rows: list[dict[str, str]] = []
    source_records = school.get("source_records", [])
    current_target = next(
        (
            target
            for target in school.get("targets", [])
            if target.get("reference") == current_ref
        ),
        {},
    )
    for label, key, source_keys, normalized_name in fields:
        current_value = current.get(key)
        proposed_value = proposed.get(key)
        source_value = _source_value(source_records, source_keys)
        if label == "name" and not source_value:
            source_value = school.get("name", "")
        if key == "acara_id":
            current_value = current.get("acara_id") or _acara_id(
                current_target.get("reference", "")
            )
            proposed_value = proposed.get("acara_id") or _acara_id(
                proposed.get("institution_ref", "")
            )
        elif key == "myschool_id":
            current_value = current.get("myschool_id") or current.get("my_school_id")
            proposed_value = proposed.get("myschool_id") or proposed.get("my_school_id")
        if key == "coordinates":
            current_value = _coordinate_value(current)
            proposed_value = _coordinate_value(proposed)
            source_value = _source_coordinates(source_records)
        elif key == "historical_names":
            current_value = current.get(key) or current.get("alternative_names")
            proposed_value = proposed.get(key) or proposed.get("alternative_names")
        reviewed = _display_comparison_value(current_value)
        recorded = _display_comparison_value(source_value)
        left = reviewed or (recorded if normalized_name else "")
        right = _display_comparison_value(proposed_value)
        if not right or not left:
            comparison = "unknown"
        elif normalized_name:
            comparison = (
                "matches"
                if school_key(str(left)) == school_key(str(right))
                else "different"
            )
        else:
            comparison = "matches" if left == right else "different"
        rows.append(
            {
                "field": label,
                "source": recorded,
                "reviewed": reviewed,
                "proposed": right,
                "comparison": comparison,
                "summary": (
                    "Names normalize to the same school key"
                    if normalized_name and comparison == "matches"
                    else "Material difference; review evidence"
                    if comparison == "different"
                    else "No current or proposed value available"
                    if comparison == "unknown"
                    else "Values match"
                ),
            }
        )
    return rows


def _source_value(records: list[dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for record in records:
        for key in keys:
            if record.get(key) not in (None, ""):
                return record[key]
    return None


def _coordinate_value(metadata: dict[str, Any]) -> str:
    latitude = metadata.get("latitude")
    longitude = metadata.get("longitude")
    if latitude is None:
        latitude = metadata.get("lat")
    if longitude is None:
        longitude = metadata.get("lon")
    return (
        f"{latitude}, {longitude}"
        if latitude is not None and longitude is not None
        else ""
    )


def _source_coordinates(records: list[dict[str, Any]]) -> str:
    for record in records:
        value = _coordinate_value(record)
        if value:
            return value
    return ""


def _acara_id(reference: str) -> str:
    return reference.removeprefix("acara:") if reference.startswith("acara:") else ""


def _display_comparison_value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(part) for part in value)
    return str(value)


def group_school_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate schools before search and pagination; other rows keep their shape."""
    result: list[dict[str, Any]] = []
    schools: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["entity_type"] != "school":
            result.append(row)
            continue
        key = row["review_id"]
        if key not in schools:
            group = {**row, "candidates": [], "parliaments": []}
            schools[key] = group
            result.append(group)
        group = schools[key]
        group["candidates"].append(row)
        group["parliaments"] = sorted(
            set(group["parliaments"] + row.get("parliaments", []))
        )
    return result
