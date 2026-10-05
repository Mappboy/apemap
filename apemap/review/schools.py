"""Read-only presentation of school evidence, never an identity matcher."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

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
    )


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
