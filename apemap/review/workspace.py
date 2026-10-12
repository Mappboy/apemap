"""Read-only comparison and reusable queue helpers for focused review workspaces."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from apemap.review.member_comparison import normalize_member_value
from apemap.review.model import school_key

SUMMARY_SUPPORTS = "Supports"
SUMMARY_CONFLICTS = "Conflicts"
SUMMARY_UNKNOWN = "Unknown"
PRESETS = (
    "ambiguous_schools",
    "member_identity_conflicts",
    "needs_research",
    "low_evidence",
    "unresolved",
    "member_corrections",
)
EVIDENCE_STANCES = ("supports", "contradicts", "contextual")
_URGENCY = ("conflict", "pending", "needs_research", "rejected", "accepted")


def school_candidate_evidence(
    item: Mapping[str, Any], reference: str
) -> list[dict[str, Any]]:
    """Explain one candidate without attributing another candidate's evidence to it."""
    records = [
        dict(row["evidence"])
        for row in item.get("candidates", [])
        if row.get("payload", {}).get("institution_ref") == reference
        and row.get("evidence", {}).get("stance")
    ] + [
        dict(record)
        for record in item.get("retained_evidence", [])
        if record.get("candidate_institution_ref") in (None, "", reference)
    ]
    candidate = next(
        (
            row
            for row in item.get("ranked_candidates", [])
            if row.get("institution_ref") == reference
        ),
        None,
    )
    if candidate:
        for reason in candidate.get("reasons", []):
            if reason.get("component") == "name_match":
                records.append(
                    {
                        "stance": "contextual",
                        "summary": reason["note"] + "; not attendance evidence",
                        "source_title": "Local register name comparison",
                        "candidate_institution_ref": reference,
                        "generated_by": "deterministic candidate ranking",
                    }
                )
    return records


def _normalize(field: str, value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field in {
        "school",
        "school_name",
        "recorded_name",
        "recorded_school_name",
        "institution_name",
    }:
        return school_key(str(value))
    try:
        return normalize_member_value(field, value)
    except (KeyError, TypeError, ValueError):
        return " ".join(str(value).split()).casefold()


def compare_values(
    field: str,
    source: Any,
    reviewed: Any,
    proposed: Any,
    *,
    provenance: Sequence[Mapping[str, Any] | str] = (),
    comparison: str | None = None,
) -> dict[str, Any]:
    """Compare source/reviewed/proposed values without assigning decision authority."""
    source_norm, reviewed_norm, proposed_norm = (
        _normalize(field, value) for value in (source, reviewed, proposed)
    )
    # A scalar difference describes the proposed change. Only retained source
    # stances can establish support or contradiction.
    summary = (
        SUMMARY_SUPPORTS
        if source_norm is not None and source_norm == proposed_norm
        else SUMMARY_UNKNOWN
    )
    stances = [
        str(item.get("stance", "contextual")).casefold()
        for item in provenance
        if isinstance(item, Mapping)
    ]
    if any(value in {"contradicts", "conflicts"} for value in stances):
        summary = SUMMARY_CONFLICTS
    elif "supports" in stances:
        summary = SUMMARY_SUPPORTS
    comparison_base = reviewed_norm if reviewed_norm is not None else source_norm
    value_comparison = (
        comparison
        if comparison in {"matches", "different", "unknown"}
        else (
            "unknown"
            if comparison_base is None or proposed_norm is None
            else "matches"
            if comparison_base == proposed_norm
            else "different"
        )
    )
    return {
        "field": field,
        "summary": summary,
        "comparison": value_comparison,
        "source": source,
        "reviewed": reviewed,
        "proposed": proposed,
        "provenance": [
            dict(item) if isinstance(item, Mapping) else str(item)
            for item in provenance
        ],
    }


def build_comparisons(fields: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert simple field dictionaries into comparison rows."""
    return [
        compare_values(
            str(row.get("field", "value")),
            row.get("source"),
            row.get("reviewed"),
            row.get("proposed"),
            provenance=row.get("provenance", ()),
            comparison=row.get("comparison"),
        )
        for row in fields
    ]


def summarize_evidence(
    records: Sequence[Mapping[str, Any]],
    expected_dimensions: Sequence[str] = (),
) -> dict[str, Any]:
    """Retain evidence provenance by stance and name dimensions with no records."""
    grouped = {stance: [] for stance in EVIDENCE_STANCES}
    present_dimensions: set[str] = set()
    for record in records:
        stance = str(record.get("stance", "contextual")).casefold()
        if stance not in grouped:
            stance = "contextual"
        grouped[stance].append(dict(record))
        dimension = record.get("dimension")
        claim = record.get("claim_value")
        if not dimension and isinstance(claim, Mapping):
            dimension = claim.get("field")
        if not dimension:
            claim_type = str(record.get("claim_type", "")).casefold()
            if claim_type in {"identity", "member_identity"}:
                dimension = "identity"
            elif record.get("temporal_relevance") is not None:
                dimension = "temporal"
            elif record.get("geographic_relevance") is not None:
                dimension = "geographic"
        if dimension:
            present_dimensions.add(str(dimension))
    return {
        **grouped,
        "unknown_gaps": [
            dimension
            for dimension in expected_dimensions
            if dimension not in present_dimensions
        ],
    }


def _matches_preset(row: Mapping[str, Any], preset: str | None) -> bool:
    if preset is None:
        return True
    entity, status = row.get("entity_type"), row.get("status")
    if preset == "ambiguous_schools":
        return entity == "school" and bool(
            row.get("ambiguous")
            or row.get("identity_conflict")
            or row.get("conflicts")
            or status == "conflict"
        )
    if preset == "member_identity_conflicts":
        return entity == "member" and bool(
            row.get("ambiguous")
            or row.get("identity_conflict")
            or any(
                candidate.get("evidence", {}).get("comparison") == "ambiguous"
                for candidate in row.get("candidates", [])
            )
        )
    if preset == "needs_research":
        return status == "needs_research"
    if preset == "low_evidence":
        return (
            entity == "school"
            and status not in {"accepted", "resolved_individually"}
            and bool(
                row.get("low_evidence")
                or row.get("evidence_band") == "low"
                or row.get("score_band") == "low"
                or any(
                    candidate.get("evidence", {}).get("evidence_band") == "low"
                    or candidate.get("evidence", {}).get("low_evidence") is True
                    for candidate in row.get("candidates", [])
                )
            )
        )
    if preset == "unresolved":
        return status not in {
            "accepted",
            "resolved_individually",
            "rejected",
            "superseded",
        }
    if preset == "member_corrections":
        return entity == "member" and status in {
            "conflict",
            "pending",
            "needs_research",
        }
    raise ValueError(f"Unknown review preset: {preset}")


def extract_queue(
    rows: Sequence[Mapping[str, Any]],
    *,
    view: str = "all",
    status: str | None = None,
    entity: str | None = None,
    unmatched_schools: bool = False,
    parliament: int | None = None,
    q: str = "",
    preset: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Filter and order grouped rows once, before slicing a queue page."""
    if view not in {"all", "members", "schools"}:
        raise ValueError("view must be all, members, or schools")
    if preset is not None and preset not in PRESETS:
        raise ValueError(f"Unknown review preset: {preset}")
    if per_page < 1:
        raise ValueError("per_page must be positive")
    page = max(1, page)
    needle = q.strip().casefold()
    grouped_rows: list[dict[str, Any]] = []
    education_by_id: dict[str, dict[str, Any]] = {}
    for source in rows:
        item = dict(source)
        if item.get("entity_type") != "member_education":
            grouped_rows.append(item)
            continue
        review_id = str(item.get("review_id", ""))
        grouped = education_by_id.get(review_id)
        if grouped is None:
            item["candidate_ids"] = (
                [item.get("candidate_id")] if item.get("candidate_id") else []
            )
            item["alternatives"] = [dict(item)]
            education_by_id[review_id] = item
            grouped_rows.append(item)
            continue
        grouped["alternatives"].append(dict(item))
        if (
            item.get("candidate_id")
            and item["candidate_id"] not in grouped["candidate_ids"]
        ):
            grouped["candidate_ids"].append(item["candidate_id"])
        grouped["parliaments"] = sorted(
            set(grouped.get("parliaments", [])) | set(item.get("parliaments", []))
        )
        if item.get("status") == "conflict":
            grouped["status"] = "conflict"

    filtered = []
    for item in grouped_rows:
        item_entity = item.get("entity_type")
        if view == "members" and (
            item_entity != "member"
            or item.get("status") not in {"conflict", "pending", "needs_research"}
        ):
            continue
        if view == "schools" and (
            item_entity != "school"
            or item.get("status") in {"accepted", "resolved_individually"}
        ):
            continue
        if entity and item_entity != entity:
            continue
        if unmatched_schools and (
            item_entity != "school"
            or item.get("status") in {"accepted", "resolved_individually"}
        ):
            continue
        if status and item.get("status") != status:
            continue
        if parliament is not None and parliament not in item.get("parliaments", []):
            continue
        if not _matches_preset(item, preset):
            continue
        if (
            needle
            and needle
            not in json.dumps(item, ensure_ascii=False, default=str).casefold()
        ):
            continue
        filtered.append(dict(item))

    def order(item: Mapping[str, Any]) -> tuple[int, str, str]:
        value = item.get("status", "pending")
        urgency_order = (
            (
                "conflict",
                "needs_individual_review",
                "pending",
                "needs_research",
                "rejected",
                "superseded",
            )
            if item.get("entity_type") == "school"
            else _URGENCY
        )
        urgency = (
            urgency_order.index(value) if value in urgency_order else len(urgency_order)
        )
        school = item.get("school")
        name = (
            school.name
            if school
            else item.get("name")
            or item.get("display_name")
            or item.get("review_id", "")
        )
        return urgency, str(name).casefold(), str(item.get("review_id", ""))

    filtered.sort(key=order)
    total = len(filtered)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    return {
        "rows": filtered[start : start + per_page],
        "all_rows": filtered,
        "ids": [str(item.get("review_id", "")) for item in filtered],
        "total": total,
        "page": page,
        "pages": pages,
        "filters": {
            "view": view,
            "entity": entity or "",
            "status": status or "",
            "parliament": parliament,
            "q": q,
            "unmatched_schools": unmatched_schools,
            "preset": preset or "",
        },
    }
