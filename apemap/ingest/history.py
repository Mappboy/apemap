"""Explicit legacy migration readers and disposable historical evidence exports."""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from apemap.constants import (
    ATTENDED_STATUSES,
    CONFIDENCE_LEVELS,
    PARLIAMENT_METADATA,
    CANONICAL_CHAMBERS,
    current_parliament,
)
from apemap.ingest.aph import ServiceStint
from apemap.ingest.review import merge_review_rows

MANUAL_EDUCATION_COLUMNS = [
    "aph_id",
    "school_name",
    "source_url",
    "retrieved_at",
    "confidence",
    "reviewer_notes",
    "attended_status",
]
HISTORICAL_REVIEW_COLUMNS = [
    "parliament_number",
    "member_id",
    "member_display_name",
    "raw_school_text",
    "source_url",
    "is_international",
    "suggested_action",
    "notes",
]


def load_manual_education(path: Path | None = None) -> dict[str, list[dict[str, str]]]:
    """Read accepted, sourced education evidence; do not infer by name."""
    if path is None:
        return {}
    source = path
    records: dict[str, list[dict[str, str]]] = {}
    if not source.exists():
        return records
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if any(
                not row.get(k)
                for k in MANUAL_EDUCATION_COLUMNS
                if k != "reviewer_notes"
            ):
                raise ValueError(f"Incomplete manual education evidence in {source}")
            if (
                row["confidence"] not in CONFIDENCE_LEVELS
                or row["attended_status"] not in ATTENDED_STATUSES
            ):
                raise ValueError(
                    f"Invalid manual education confidence/attendance in {source}"
                )
            records.setdefault(row["aph_id"].lower(), []).append(row)
    return records


def load_service_overrides(
    path: Path | None = None,
) -> dict[tuple[str, int], list[ServiceStint]]:
    """Read sourced replacement intervals for an entire member/term, never guesses."""
    if path is None:
        return {}
    source = path
    records: dict[tuple[str, int], list[ServiceStint]] = {}
    if not source.exists():
        return records
    with source.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            required = (
                "aph_id",
                "parliament_number",
                "service_start",
                "chamber",
                "party",
                "state_or_territory",
                "source_url",
                "retrieved_at",
                "reviewer_notes",
            )
            if any(not row.get(k) for k in required):
                raise ValueError(f"Incomplete service override in {source}")
            p = int(row["parliament_number"])
            info = PARLIAMENT_METADATA[p]
            start = date.fromisoformat(row["service_start"])
            end = (
                date.fromisoformat(row["service_end"])
                if row.get("service_end")
                else None
            )
            if (
                row["chamber"] not in CANONICAL_CHAMBERS
                or start.isoformat() < info["opening_date"]
                or (end and end < start)
                or (
                    info["end_date"]
                    and (end is None or end.isoformat() > info["end_date"])
                )
            ):
                raise ValueError(f"Invalid service override interval in {source}")
            phid = row["aph_id"].lower()
            identity = [
                phid,
                p,
                row["service_start"],
                row["chamber"],
                row["party"],
                row.get("electorate"),
                row["state_or_territory"],
            ]
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
            records.setdefault((phid, p), []).append(
                ServiceStint(
                    service_id=f"srv-{phid}-{p}-reviewed-{digest}",
                    member_id=f"aph-{phid}",
                    parliament_number=p,
                    chamber=row["chamber"],
                    party=row["party"],
                    party_abbrev=row.get("party_abbrev") or row["party"],
                    electorate=row.get("electorate") or None,
                    state_or_territory=row["state_or_territory"],
                    service_start=start.isoformat(),
                    service_end=end.isoformat() if end else None,
                    is_opening_day_member=start.isoformat() == info["opening_date"],
                    is_current_member=p == current_parliament() and end is None,
                    source_url=row["source_url"],
                    source_service_start=start.isoformat(),
                    source_service_end=end.isoformat() if end else None,
                    retrieved_at=row["retrieved_at"],
                )
            )
    for stints in records.values():
        stints.sort(key=lambda stint: stint.service_start or "")
        for left, right in zip(stints, stints[1:]):
            if left.service_end is None or (
                right.service_start and left.service_end >= right.service_start
            ):
                raise ValueError(f"Overlapping reviewed service intervals in {source}")
    return records


def write_review_queue(
    path: Path, rows: list[dict[str, Any]], key_columns: list[str], columns: list[str]
) -> None:
    """Regenerate an evidence export without reading any prior CSV edits."""
    manual = {"review_status", "resolved_value", "manual_source_url", "review_notes"}
    generated_columns = [column for column in columns if column not in manual]
    merge_review_rows(rows, path, key_columns, generated_columns, [])
