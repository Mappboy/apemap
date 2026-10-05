"""Disposable review evidence exports and Wikimedia candidate quality filters.

Authoritative human decisions live in the tracked append-only review log.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz


from apemap.ingest.matching import (
    is_international_text,
    normalize_school_key,
    normalize_text,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Review Schemas & Status Constants
# --------------------------------------------------------------------------

VALID_REVIEW_STATUSES = ("pending", "accepted", "rejected", "needs_research")

MEMBER_REVIEW_GENERATED_COLUMNS = [
    "member_id",
    "aph_id",
    "display_name",
    "wikidata_id",
    "field",
    "aph_value",
    "wikidata_value",
    "wikipedia_title",
    "source_url",
    "status",
    "notes",
    "retrieved_at",
    "source_query",
    "candidates",
]

MEMBER_REVIEW_MANUAL_COLUMNS = [
    "historical_value",
    "historical_source",
    "review_status",
    "resolved_value",
    "manual_source_url",
    "review_notes",
]

MEMBER_REVIEW_COLUMNS = MEMBER_REVIEW_GENERATED_COLUMNS

SCHOOL_REVIEW_GENERATED_COLUMNS = [
    "institution_id",
    "raw_school_text",
    "suggested_institution_name",
    "wikidata_id",
    "wikipedia_url",
    "country",
    "locality",
    "latitude",
    "longitude",
    "institution_type",
    "confidence",
    "notes",
    "retrieved_at",
    "source_url",
    "source_query",
]

SCHOOL_REVIEW_MANUAL_COLUMNS = [
    "historical_match_name",
    "historical_acara_id",
    "historical_source",
    "review_status",
    "resolved_school_name",
    "resolved_acara_id",
    "resolved_wikidata_id",
    "resolved_country",
    "resolved_state",
    "resolved_suburb",
    "resolved_postcode",
    "resolved_address",
    "resolved_latitude",
    "resolved_longitude",
    "manual_source_url",
    "address_source_url",
    "review_notes",
]

SCHOOL_REVIEW_COLUMNS = SCHOOL_REVIEW_GENERATED_COLUMNS

# --------------------------------------------------------------------------
# Disallowed Types for School Filtering
# --------------------------------------------------------------------------

DISALLOWED_SCHOOL_TYPES = {
    "human",
    "person",
    "town",
    "city",
    "suburb",
    "village",
    "locality",
    "administrative territorial entity",
    "local government area",
    "country",
    "sovereign state",
    "wikimedia disambiguation page",
    "disambiguation page",
    "wikimedia list article",
    "list article",
    "religious order",
    "company",
    "business",
    "film",
    "album",
    "book",
    "musical group",
    "band",
}


# --------------------------------------------------------------------------
# Historical School Aliases Loader
# --------------------------------------------------------------------------


def load_historical_school_aliases(
    path: Path | str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load historical school aliases from school_aliases.json for supporting evidence."""
    if path is None:
        return {}
    ref_path = Path(path)
    if not ref_path.exists():
        return {}

    try:
        data = json.loads(ref_path.read_text(encoding="utf-8"))
        aliases: dict[str, Any] = data.get("aliases", {})
        indexed: dict[str, dict[str, Any]] = {}
        for raw_k, val in aliases.items():
            norm_k = normalize_school_key(raw_k)
            indexed[norm_k] = val
            indexed[normalize_text(raw_k).lower()] = val
        return indexed
    except Exception as exc:
        logger.warning(
            "Failed to load historical school aliases from %s: %s", ref_path, exc
        )
        return {}


# --------------------------------------------------------------------------
# Candidate Quality Evaluation
# --------------------------------------------------------------------------


def evaluate_school_candidate(
    raw_school_text: str,
    suggestion: dict[str, Any],
    is_international: bool | None = None,
    historical_evidence: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Evaluate whether a Wikimedia school suggestion is worth human review.

    Filters out obvious false positives (humans, towns, suburbs, list articles,
    disambiguation pages, out-of-bounds coordinates, wrong countries, low similarity).

    Returns:
        (accepted_for_review, reason)
    """
    if not raw_school_text:
        return False, "Missing raw school text"

    cand_name = suggestion.get("suggested_institution_name")
    if not cand_name:
        return False, "Missing suggested institution name"

    clean_cand_name = str(cand_name).strip()
    cand_lower = clean_cand_name.lower()

    # 1. Check title indicators for disambiguation or list pages
    if "(disambiguation)" in cand_lower or cand_lower.startswith("list of "):
        return False, f"Rejected disambiguation/list title: {clean_cand_name}"

    # 2. Check institution type against disallowed categories
    inst_type = (suggestion.get("institution_type") or "").strip().lower()
    if inst_type:
        for dis in DISALLOWED_SCHOOL_TYPES:
            if dis == inst_type or dis in inst_type:
                return (
                    False,
                    f"Rejected non-school institution type '{inst_type}' for {clean_cand_name}",
                )

    # 3. Geographic sanity checks
    if is_international is None:
        is_intl = is_international_text(raw_school_text)
    else:
        is_intl = is_international
    cand_country = (suggestion.get("country") or "").strip()
    cand_country_low = cand_country.lower()

    if not is_intl:
        # Expected Australian school: reject obvious overseas countries
        if cand_country_low and cand_country_low not in (
            "australia",
            "commonwealth of australia",
        ):
            return (
                False,
                f"Rejected non-Australian candidate country '{cand_country}' for domestic school",
            )

        # Check broad bounding box if coordinates are present: lat [-44.5, -10], lon [112, 154]
        lat = suggestion.get("latitude")
        lon = suggestion.get("longitude")
        if lat is not None and lon is not None:
            try:
                f_lat = float(lat)
                f_lon = float(lon)
                if not (-44.5 <= f_lat <= -10.0 and 112.0 <= f_lon <= 154.0):
                    return (
                        False,
                        f"Rejected candidate coordinates ({f_lat}, {f_lon}) outside Australian bounds",
                    )
            except (ValueError, TypeError):
                pass

    # 4. Name similarity checking
    norm_raw = normalize_school_key(raw_school_text)
    norm_cand = normalize_school_key(clean_cand_name)
    sim_score = fuzz.token_set_ratio(norm_raw, norm_cand)

    if sim_score >= 70:
        return True, f"Accepted candidate with name similarity {sim_score:.1f}"

    # Similarity < 70: Allow retention if corroborated by historical evidence
    if historical_evidence and historical_evidence.get("historical_match_name"):
        hist_name = str(historical_evidence["historical_match_name"])
        hist_sim = fuzz.token_set_ratio(normalize_school_key(hist_name), norm_cand)
        if hist_sim >= 70:
            return (
                True,
                f"Accepted candidate supported by historical evidence matching '{hist_name}' (similarity {hist_sim:.1f})",
            )
        return (
            True,
            f"Accepted candidate supported by historical evidence (similarity {sim_score:.1f})",
        )

    return (
        False,
        f"Rejected candidate due to low name similarity ({sim_score:.1f} < 70) between '{raw_school_text}' and '{clean_cand_name}'",
    )


# --------------------------------------------------------------------------
# Review Merging and Manual Column Preservation
# --------------------------------------------------------------------------


def merge_review_rows(
    generated_rows: list[dict[str, Any]],
    existing_path: Path | str,
    key_columns: list[str] | tuple[str, ...],
    generated_columns: list[str],
    manual_columns: list[str],
) -> list[dict[str, Any]]:
    """Regenerate a disposable CSV without importing edited exports as decisions.

    The legacy name/signature remains for callers; manual_columns are omitted.
    """
    path = Path(existing_path)
    rows = [
        {column: row.get(column, "") for column in generated_columns}
        for row in generated_rows
    ]
    rows.sort(
        key=lambda row: tuple(str(row.get(key, "")).lower() for key in key_columns)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=generated_columns)
        writer.writeheader()
        writer.writerows(rows)
    return rows
