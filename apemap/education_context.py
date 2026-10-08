"""Optional reviewed school-wide historical facts, separate from registry facts."""

from __future__ import annotations

CONTEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("recorded_school_id", "VARCHAR"),
    ("attended_institution_id", "VARCHAR"),
    ("attended_identity_source_url", "VARCHAR"),
    ("historical_scope_confirmed", "BOOLEAN"),
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
        if name not in {"attended_institution_id", "recorded_school_id"}
    ),
)
