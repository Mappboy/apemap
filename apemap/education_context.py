"""Optional reviewed school-wide historical facts, separate from registry facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


LOCATION_BASIS_RANK = {
    "original_verified": 4,
    "successor_verified_same_campus": 3,
    "original_reference": 2,
    "successor_unverified": 1,
    "unresolved": 0,
}


def resolve_school_display_locations(
    contexts: Sequence[Mapping[str, Any]],
    *,
    longitude_key: str = "longitude",
    latitude_key: str = "latitude",
) -> tuple[list[Mapping[str, Any]], bool]:
    """Select the strongest school display evidence and flag conflicting points."""
    if not contexts:
        return [], False
    best = max(LOCATION_BASIS_RANK[context["location_basis"]] for context in contexts)
    selected = [
        context
        for context in contexts
        if LOCATION_BASIS_RANK[context["location_basis"]] == best
    ]
    points = {(context[longitude_key], context[latitude_key]) for context in selected}
    return selected, len(points) > 1


CONTEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("recorded_school_id", "VARCHAR"),
    ("attended_institution_id", "VARCHAR"),
    ("attended_identity_source_url", "VARCHAR"),
    ("historical_scope_confirmed", "BOOLEAN"),
    ("historical_context_scope", "VARCHAR"),
    ("historical_latitude", "DOUBLE"),
    ("historical_longitude", "DOUBLE"),
    ("historical_location_source_url", "VARCHAR"),
    ("campus_continuity", "VARCHAR"),
    ("campus_continuity_source_url", "VARCHAR"),
    ("historical_broad_sector", "VARCHAR"),
    ("historical_broad_sector_source_url", "VARCHAR"),
    ("historical_detailed_sector", "VARCHAR"),
    ("historical_detailed_sector_source_url", "VARCHAR"),
)
CONTEXT_FIELDS = tuple(name for name, _ in CONTEXT_COLUMNS)
SCHOOL_CONTEXT_FIELDS = (
    "attended_institution_ref",
    *tuple(
        name
        for name in CONTEXT_FIELDS
        if name
        not in {
            "attended_institution_id",
            "recorded_school_id",
            "historical_context_scope",
        }
    ),
)
