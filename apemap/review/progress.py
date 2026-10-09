"""Disposable progress for names that require individual school resolutions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import duckdb

from apemap.review.candidates import _rows
from apemap.review.model import (
    ReviewEvent,
    accepted_education_ancestor,
    active_heads,
    education_review_id,
    school_review_id,
)
from apemap.review.resolution import (
    requires_individual_resolution,
    resolution_summary,
)


def individual_school_ids(events: list[ReviewEvent]) -> set[str]:
    """Include conflicting policy heads so they cannot appear completed."""
    return {
        review_id
        for review_id, alternatives in active_heads(events).items()
        if any(requires_individual_resolution(event) for event in alternatives)
    }


def _attendance_inventory(
    conn: duckdb.DuckDBPyConnection | None,
    events: list[ReviewEvent],
    school_ids: set[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Keep source claims and manual attendance history, keyed by assertion."""
    inventory: dict[str, dict[str, dict[str, Any]]] = {key: {} for key in school_ids}
    members = _rows(conn, "members") if conn is not None else []
    by_member = {row["member_id"]: row for row in members}
    by_aph = {str(row["aph_id"]).lower(): row for row in members if row.get("aph_id")}
    if conn is not None:
        institutions = {
            row["institution_id"]: row for row in _rows(conn, "institutions")
        }
        for source in _rows(conn, "member_education"):
            institution = institutions.get(source["institution_id"], {})
            name = source.get("school_name_as_recorded") or institution.get(
                "school_name", ""
            )
            if not name or school_review_id(name) not in school_ids:
                continue
            school_id = school_review_id(name)
            member = by_member.get(source["member_id"], {})
            aph_id = str(member.get("aph_id") or "").lower()
            review_id = education_review_id(aph_id, name) if aph_id else None
            key = review_id or f"unknown:{source['member_id']}:{school_id}"
            inventory[school_id][key] = {
                "review_id": review_id,
                "aph_id": aph_id or None,
                "display_name": member.get("display_name") or "Unknown member",
                "recorded_name": name,
                "has_source": True,
            }
    # Historical acceptance establishes that a manual claim existed. The
    # current head determines whether attendance survives or was withdrawn.
    for event in events:
        if (
            event.entity_type != "member_education"
            or event.effective_action != "accept"
        ):
            continue
        name = event.payload.get("recorded_school_name", "")
        if not name or school_review_id(name) not in school_ids:
            continue
        aph_id = str(event.payload["aph_id"]).lower()
        member = by_aph.get(aph_id)
        if member is None:
            continue
        inventory[school_review_id(name)].setdefault(
            event.review_id,
            {
                "review_id": event.review_id,
                "aph_id": aph_id,
                "display_name": member.get("display_name") or aph_id,
                "recorded_name": name,
                "has_source": False,
            },
        )
    return inventory


def _assertion_progress(
    attendance: dict[str, Any],
    events: list[ReviewEvent],
    heads: dict[str, list[ReviewEvent]],
    default: ReviewEvent | None,
    resolve: Callable[[str], dict[str, Any] | None] | None,
) -> dict[str, Any]:
    alternatives = heads.get(attendance["review_id"], [])
    head = alternatives[0] if len(alternatives) == 1 else None
    summary = resolution_summary(head, default, {})
    if len(alternatives) > 1:
        state = "unresolved"
        summary.update(
            status="conflict",
            institution_ref=None,
            relationship_type="unresolved",
            resolution_scope="assertion",
        )
    elif head and (
        head.effective_action == "reject"
        or (
            not attendance["has_source"]
            and head.effective_action == "research"
            and accepted_education_ancestor(head, events) is None
        )
    ):
        state = "withdrawn"
        summary.update(
            institution_ref=None,
            relationship_type="unresolved",
            resolution_scope="assertion",
        )
    elif (
        head
        and head.effective_action == "map"
        and not attendance["has_source"]
        and accepted_education_ancestor(head, events) is None
    ):
        state = "unresolved"
        summary.update(
            status="attendance_unavailable",
            institution_ref=None,
            relationship_type="unresolved",
            resolution_scope="assertion",
        )
    elif (
        attendance["review_id"]
        and head
        and head.effective_action in {"accept", "map"}
        and head.payload.get("institution_ref")
    ):
        reference = head.payload["institution_ref"]
        target = resolve(reference) if resolve is not None else None
        if target is not None:
            state = "resolved"
            summary.update(
                institution_name=target.get("school_name"),
                suburb=target.get("suburb"),
                state=target.get("state"),
            )
        else:
            state = "unresolved"
            summary.update(
                status="target_unavailable",
                unavailable_institution_ref=reference,
                institution_ref=None,
                relationship_type="unresolved",
            )
    else:
        state = "unresolved"
    return {key: value for key, value in attendance.items() if key != "has_source"} | {
        "status": state,
        "current_resolution": summary,
    }


def school_resolution_progress(
    conn: duckdb.DuckDBPyConnection | None,
    events: list[ReviewEvent],
    school_ids: set[str] | None = None,
    *,
    resolve: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project current progress without changing the event's action or status.

    Missing source inventory cannot establish completion, including when all
    retained manual claims happen to have explicit targets. Source duplicates
    count once and removed attendance remains visible as a separate count.
    """
    selected = individual_school_ids(events) if school_ids is None else school_ids
    if not selected:
        return {}
    heads = active_heads(events)
    inventory = _attendance_inventory(conn, events, selected)
    snapshot_available = False
    if conn is not None:
        found = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name IN "
            "('review_source_members', 'review_source_institutions', "
            "'review_source_member_education')"
        ).fetchone()
        snapshot_available = bool(found and found[0] == 3)
    progress: dict[str, dict[str, Any]] = {}
    for school_id in sorted(selected):
        alternatives = heads.get(school_id, [])
        default = alternatives[0] if len(alternatives) == 1 else None
        assertions = sorted(
            (
                _assertion_progress(attendance, events, heads, default, resolve)
                for attendance in inventory[school_id].values()
            ),
            key=lambda row: (row["display_name"], row["review_id"] or ""),
        )
        counts = {
            state: sum(row["status"] == state for row in assertions)
            for state in ("resolved", "unresolved", "withdrawn")
        }
        available = conn is not None and (
            snapshot_available
            or any(row["has_source"] for row in inventory[school_id].values())
        )
        status = default.status if default else "pending"
        if len(alternatives) > 1:
            status = "conflict"
        elif requires_individual_resolution(default):
            status = (
                "resolved_individually"
                if available and assertions and not counts["unresolved"]
                else "needs_individual_review"
            )
        progress[school_id] = {
            "available": available,
            "status": status,
            "reason": default.payload.get("resolution_reason", "") if default else "",
            **{f"{state}_count": count for state, count in counts.items()},
            "assertions": assertions,
        }
    return progress


def annotate_individual_progress(
    items: list[dict[str, Any]], progress: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply the same case disposition to every candidate and export row."""
    return [
        {
            **item,
            "status": progress[item["review_id"]]["status"],
            "individual_resolution": progress[item["review_id"]],
        }
        if item["entity_type"] == "school" and item["review_id"] in progress
        else item
        for item in items
    ]
