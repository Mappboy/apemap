"""Member presentation model, queue grouping, and member batch drafts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from apemap.review.member_comparison import (
    AMBIGUOUS,
    DIFFERENT,
    MATCHES,
    PROPOSED_INVALID,
    PROPOSED_MISSING,
)

STATUS_URGENCY = ("conflict", "pending", "needs_research", "rejected", "accepted")


@dataclass
class MemberFieldDraft:
    field: str
    action: str  # "accept", "reject", "research", or "unchanged"
    value: Any = None
    source_url: str = ""
    notes: str = ""
    reviewer: str | None = None
    supersedes: list[str] = field(default_factory=list)
    retained_decision_id: str | None = None
    proposal_only: bool = True
    candidate_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemberFieldDraft:
        if not isinstance(data.get("field"), str) or not isinstance(
            data.get("action", "unchanged"), str
        ):
            raise ValueError("Draft field and action must be text")
        for key in (
            "source_url",
            "notes",
            "reviewer",
            "retained_decision_id",
            "candidate_id",
        ):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise ValueError(f"Draft {key} must be text")
        parents = data.get("supersedes", [])
        if not isinstance(parents, list) or any(
            not isinstance(parent, str) for parent in parents
        ):
            raise ValueError("Draft supersedes must be a list of decision IDs")
        if not isinstance(data.get("proposal_only", True), bool):
            raise ValueError("Draft proposal_only must be boolean")
        return cls(
            field=data["field"],
            action=str(data.get("action", "unchanged")),
            value=data.get("value"),
            source_url=str(data.get("source_url", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
            reviewer=str(data["reviewer"]).strip() if data.get("reviewer") else None,
            supersedes=list(data.get("supersedes", [])),
            retained_decision_id=str(data["retained_decision_id"]).strip()
            if data.get("retained_decision_id")
            else None,
            proposal_only=data.get("proposal_only", True),
            candidate_id=data.get("candidate_id"),
        )


@dataclass
class MemberView:
    aph_id: str
    name: str
    status: str
    actionable_count: int
    parliaments: list[int]
    ambiguous: bool
    field_statuses: dict[str, str]
    fields: list[dict[str, Any]]
    education: list[dict[str, Any]] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)


def aggregate_member_status(statuses: list[str], *, is_ambiguous: bool = False) -> str:
    """Aggregate member field statuses in strict urgency order:

    conflict > pending > needs_research > rejected > accepted.
    """
    if is_ambiguous or "conflict" in statuses:
        return "conflict"
    for urgency in STATUS_URGENCY[1:]:
        if urgency in statuses:
            return urgency
    return "accepted" if statuses else "pending"


def group_member_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate member field candidates into a member presentation model keyed by normalized aph_id."""
    result: list[dict[str, Any]] = []
    members: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("entity_type") != "member":
            result.append(row)
            continue
        aph_id = str(row.get("payload", {}).get("aph_id", "")).strip().lower()
        if not aph_id:
            result.append(row)
            continue
        if aph_id not in members:
            display_name = row.get("evidence", {}).get("display_name") or aph_id
            group = {
                "review_id": f"member:{aph_id}",
                "candidate_id": f"member:{aph_id}",
                "entity_type": "member",
                "aph_id": row["payload"]["aph_id"],
                "display_name": display_name,
                "name": display_name,
                "parliaments": [],
                "candidates": [],
                "field_statuses": {},
                "ambiguous": False,
            }
            members[aph_id] = group
            result.append(group)
        group = members[aph_id]
        group["candidates"].append(row)
        field_name = row["payload"].get("field", "")
        if field_name:
            previous = group["field_statuses"].get(field_name)
            group["field_statuses"][field_name] = aggregate_member_status(
                [row.get("status", "pending")] + ([previous] if previous else [])
            )
        group["parliaments"] = sorted(
            set(group["parliaments"] + row.get("parliaments", []))
        )
        if (
            row.get("status") == "conflict"
            or row.get("evidence", {}).get("comparison") == AMBIGUOUS
        ):
            group["ambiguous"] = True

    for group in members.values():
        field_statuses = list(group["field_statuses"].values())
        group["status"] = aggregate_member_status(
            field_statuses, is_ambiguous=group["ambiguous"]
        )
        group["actionable_count"] = len(group["field_statuses"])
    return result


def field_presentation_order(comparison: str) -> int:
    """Order ambiguity first, discrepancies second, invalid/missing research third, matching fourth."""
    if comparison == AMBIGUOUS:
        return 0
    if comparison == DIFFERENT:
        return 1
    if comparison in (PROPOSED_INVALID, PROPOSED_MISSING):
        return 2
    if comparison == MATCHES:
        return 3
    return 4
