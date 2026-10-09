"""Explain assertion precedence without changing review authority or source facts."""

from __future__ import annotations

from typing import Any

from apemap.review.model import ReviewEvent, active_heads, school_review_id


def relationship_conflicts(events: list[ReviewEvent]) -> list[dict[str, Any]]:
    """Report intentional overrides separately from conflicting event heads.

    Different institutions for the same recorded name are permitted. These
    diagnostics tell reviewers which school-wide defaults need reconsideration;
    they never choose a winner or prevent an assertion-specific decision.
    """
    effective = {
        key: heads[0] for key, heads in active_heads(events).items() if len(heads) == 1
    }
    result: list[dict[str, Any]] = []
    for assertion in sorted(effective.values(), key=lambda event: event.review_id):
        if assertion.entity_type != "member_education":
            continue
        name = assertion.payload.get("recorded_school_name", "")
        if not name:
            continue
        if (
            assertion.effective_action == "research"
            and assertion.payload.get("resolution_only") is not True
        ):
            continue
        school_id = school_review_id(name)
        default = effective.get(school_id)
        if default is None or default.effective_action not in {"accept", "map"}:
            continue
        reference = assertion.payload.get("institution_ref")
        relation = assertion.payload.get("relationship_type") or (
            default.payload["relationship_type"]
            if assertion.effective_action == "accept"
            and reference == default.payload["institution_ref"]
            else "direct"
        )
        kind = None
        if assertion.effective_action in {"research", "reject"}:
            kind = (
                "assertion_unresolved"
                if assertion.effective_action == "research"
                else "attendance_rejected"
            )
        elif assertion.effective_action in {"accept", "map"} and reference:
            if reference != default.payload["institution_ref"]:
                kind = "target_disagreement"
            elif relation != default.payload["relationship_type"]:
                kind = "relationship_disagreement"
        if kind is None:
            continue
        result.append(
            {
                "kind": kind,
                "review_id": assertion.review_id,
                "school_review_id": school_id,
                "recorded_name": name,
                "institution_ref": reference
                if assertion.status == "accepted"
                else None,
                "default_institution_ref": default.payload["institution_ref"],
                "relationship_type": relation
                if assertion.status == "accepted"
                else None,
                "default_relationship_type": default.payload["relationship_type"],
                "decision_id": assertion.decision_id,
                "default_decision_id": default.decision_id,
                "message": f"{assertion.review_id} overrides the school-wide relationship ({kind.replace('_', ' ')}).",
            }
        )
    return result


def resolution_summary(
    assertion: ReviewEvent | None,
    default: ReviewEvent | None,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Describe the reviewed resolution independently of attendance confidence."""
    reference = source.get("institution_ref")
    relationship = source.get("relationship_type", "unresolved")
    scope, status = "source", "pending"
    if default and default.effective_action in {"accept", "map"}:
        reference = default.payload["institution_ref"]
        relationship = default.payload["relationship_type"]
        scope, status = "school_default", "default_available"
    elif default and default.effective_action in {"reject", "research"}:
        reference, relationship = None, "unresolved"
        scope, status = "school_default", default.status
    if assertion:
        status = assertion.status
        if assertion.effective_action == "reject" or (
            assertion.effective_action == "research"
            and assertion.payload.get("resolution_only") is True
        ):
            reference, relationship, scope = None, "unresolved", "assertion"
        elif assertion.effective_action == "research":
            pass  # Legacy withdrawal leaves the original source/default resolution.
        elif assertion.payload.get("institution_ref"):
            reference = assertion.payload["institution_ref"]
            inherits = (
                assertion.effective_action == "accept"
                and not assertion.payload.get("relationship_type")
                and default is not None
                and default.effective_action in {"accept", "map"}
                and reference == default.payload["institution_ref"]
            )
            if not inherits:
                relationship = assertion.payload.get("relationship_type", "direct")
                scope = "assertion"
        elif default is None or default.effective_action not in {"accept", "map"}:
            # A legacy attendance acceptance can still obtain a matcher/profile
            # target, but without a reviewed relationship replay leaves school
            # identity unresolved. Do not present its old source match as reviewed.
            reference, relationship, scope = None, "unresolved", "assertion"
    return {
        "institution_ref": reference,
        "relationship_type": relationship,
        "resolution_scope": scope,
        "status": status,
    }
