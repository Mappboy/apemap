"""Portable, explicitly approved CLI previews; imports commit as one batch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from apemap.review.model import ReviewEvent
from apemap.review.candidates import json_value
from apemap.review.store import StaleReviewError, append_events, log_revision

if TYPE_CHECKING:
    from apemap.review.service import ReviewService


def requires_approval(events: list[ReviewEvent]) -> bool:
    """Every school-wide change needs explicit review, including withdrawals."""
    return any(event.entity_type == "school" for event in events)


def prepare_batch(service: ReviewService, events: list[ReviewEvent]) -> dict[str, Any]:
    revision, source = log_revision(service.log_path), service.source_revision()
    current = service.events(allow_conflicts=True)
    service.check(current + events)
    changes = service.semantic_diff(current, current + events)
    # Withdrawals have no new default, but their source assertions still matter.
    inventories = {
        event.review_id: service.show(event.review_id)["context"].get("members", [])
        for event in events
        if event.entity_type == "school"
    }
    if (
        revision != log_revision(service.log_path)
        or source != service.source_revision()
    ):
        raise StaleReviewError("Review inputs changed while previewing; retry")
    return {
        "schema_version": 1,
        "kind": "decision_batch",
        "events": [event.to_dict() for event in events],
        "revision": revision,
        "source_revision": source,
        "changes": changes,
        "school_assertions": json_value(inventories),
    }


def write_preview(
    service: ReviewService, events: list[ReviewEvent], path: Path
) -> dict[str, Any]:
    preview = prepare_batch(service, events)
    raw = (
        json.dumps(
            preview, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")
    path = path.resolve()
    # Never turn a preview destination into an accidental authority overwrite.
    protected = {
        service.log_path.resolve(),
        service.evidence_path.resolve(),
        service.db_path.resolve(),
    }
    if path in protected or path.suffix.lower() != ".json":
        raise ValueError("Choose a new .json preview file outside the authority stores")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return {
        "preview_path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "preview": preview,
    }


def apply_preview(service: ReviewService, path: Path, approval: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != approval:
        raise ValueError("Approval SHA-256 does not match the reviewed preview")
    preview = json.loads(raw)
    if (
        not isinstance(preview, dict)
        or preview.get("schema_version") != 1
        or preview.get("kind") != "decision_batch"
    ):
        raise ValueError("Unsupported decision preview")
    values = preview.get("events")
    if not isinstance(values, list):
        raise ValueError("Preview events must be a list")
    events = [ReviewEvent.from_dict(value) for value in values]

    def check_sources() -> None:
        if preview.get("source_revision") != service.source_revision():
            raise StaleReviewError(
                "Source/candidate/evidence data changed; preview again"
            )
        # Called within the append lock. Never regenerate event IDs or conclusions.
        current = service.events(allow_conflicts=True)
        if preview.get("revision") != log_revision(service.log_path):
            raise StaleReviewError("Decision history changed; preview again")
        changes = service.semantic_diff(current, current + events)
        if changes != preview.get("changes"):
            raise StaleReviewError("Mapping effects changed; preview again")
        inventories = {
            event.review_id: service.show(event.review_id)["context"].get("members", [])
            for event in events
            if event.entity_type == "school"
        }
        if json_value(inventories) != preview.get("school_assertions"):
            raise StaleReviewError(
                "Affected assertion inventory changed; preview again"
            )
        if requires_approval(events) and changes.get("canonical") is None:
            raise ValueError(
                "Run review build and preview the affected assertions before saving"
            )
        for application in changes.get("default_applications", []):
            if application.get("available") is not True:
                raise ValueError("Affected assertion inventory is unavailable")

    append_events(
        service.log_path,
        events,
        expected_revision=preview["revision"],
        validator=lambda proposed: service.check(proposed),
        source_check=check_sources,
    )
    return {
        "applied": True,
        "new_events": len(events),
        "events": [event.to_dict() for event in events],
    }


def finish_preview(
    service: ReviewService,
    preview: dict[str, Any],
    *,
    dry_run: bool,
    preview_out: Path | None = None,
) -> dict[str, Any]:
    event = ReviewEvent.from_dict(preview["event"])
    if preview_out is not None:
        return write_preview(service, [event], preview_out)
    if dry_run:
        return preview
    if requires_approval([event]):
        raise ValueError(
            "School-wide changes require --preview-out FILE, then review apply-preview FILE --approve SHA256"
        )
    return service.save(preview).to_dict()
