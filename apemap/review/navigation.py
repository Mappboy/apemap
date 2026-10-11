"""Whitelisted queue navigation context and stable save-next selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

QUEUE_CONTEXT_KEYS = (
    "view",
    "entity",
    "status",
    "parliament",
    "q",
    "unmatched_schools",
    "page",
    "preset",
)
QUEUE_VIEWS = {"all", "members", "schools"}


def normalize_queue_context(
    args: Mapping[str, Any], *, statuses: Sequence[str] | None = None
) -> dict[str, str]:
    """Return only supported queue parameters, rejecting malformed navigation state."""
    result: dict[str, str] = {}
    for key in QUEUE_CONTEXT_KEYS:
        value = args.get(key, "")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if value is None:
            value = ""
        text = str(value).strip()
        if text:
            result[key] = text
    if result.get("view") and result["view"] not in QUEUE_VIEWS:
        raise ValueError("Unknown queue view")
    if (
        result.get("status")
        and statuses is not None
        and result["status"] not in statuses
    ):
        raise ValueError("Unknown queue status")
    if result.get("entity") and result["entity"] not in {
        "school",
        "member",
        "member_education",
        "service",
        "manual_institution",
    }:
        raise ValueError("Unknown review entity")
    if result.get("page"):
        try:
            page = int(result["page"])
        except ValueError as exc:
            raise ValueError("Queue page must be a positive integer") from exc
        if page < 1:
            raise ValueError("Queue page must be a positive integer")
        result["page"] = str(page)
    if result.get("parliament"):
        try:
            int(result["parliament"])
        except ValueError as exc:
            raise ValueError("Parliament must be an integer") from exc
    unmatched = result.get("unmatched_schools")
    if unmatched and unmatched not in {"0", "1"}:
        raise ValueError("unmatched_schools must be 0 or 1")
    return result


def successor_ids(queue_ids: Sequence[str], current_id: str) -> list[str]:
    """List queue IDs after the current item; never wrap to the queue beginning."""
    ids = list(queue_ids)
    try:
        index = ids.index(current_id)
    except ValueError:
        return []
    return ids[index + 1 :]


def choose_successor(
    pre_save_ids: Sequence[str], post_save_ids: Sequence[str], current_id: str
) -> str | None:
    """Choose the first post-save item surviving from the old queue's successors."""
    surviving = set(post_save_ids)
    return next(
        (item for item in successor_ids(pre_save_ids, current_id) if item in surviving),
        None,
    )


def queue_position(queue_ids: Sequence[str], current_id: str) -> dict[str, Any]:
    """Return one-based queue position and adjacent IDs across the full filtered queue."""
    ids = list(queue_ids)
    try:
        index = ids.index(current_id)
    except ValueError:
        return {
            "position": None,
            "total": len(ids),
            "previous_id": None,
            "next_id": None,
            "in_queue": False,
        }
    return {
        "position": index + 1,
        "total": len(ids),
        "previous_id": ids[index - 1] if index else None,
        "next_id": ids[index + 1] if index + 1 < len(ids) else None,
        "in_queue": True,
    }
