"""Manual review persistence, candidate evaluation, and reconciliation helpers.

This module provides schema definitions, candidate sanity filtering, and merge
preservation logic for Wikimedia member and school review artifacts. Manual review
decisions stored in CSV review files are preserved across enrichment pipeline reruns.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from apemap.constants import REFERENCE_DIR
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
]

MEMBER_REVIEW_MANUAL_COLUMNS = [
    "historical_value",
    "historical_source",
    "review_status",
    "resolved_value",
    "manual_source_url",
    "review_notes",
]

MEMBER_REVIEW_COLUMNS = MEMBER_REVIEW_GENERATED_COLUMNS + MEMBER_REVIEW_MANUAL_COLUMNS

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

SCHOOL_REVIEW_COLUMNS = SCHOOL_REVIEW_GENERATED_COLUMNS + SCHOOL_REVIEW_MANUAL_COLUMNS

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
    ref_path = Path(path or (REFERENCE_DIR / "school_aliases.json"))
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
    """Merge newly generated review evidence rows with existing manual review decisions.

    Guarantees:
    - Retains existing manual decisions across reruns.
    - Updates generated evidence columns with the latest pipeline run.
    - Preserves previously reviewed rows (review_status != 'pending' or non-empty manual fields)
      even if no longer generated in the current run.
    - Drops unreviewed obsolete rows.
    - Sets missing review_status to 'pending'.
    - Produces a deterministically sorted CSV artifact.
    """
    path = Path(existing_path)
    old_rows: dict[tuple[str, ...], dict[str, Any]] = {}

    if path.exists():
        try:
            with open(path, mode="r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    key = tuple(str(row.get(k, "")).strip() for k in key_columns)
                    if any(key):
                        old_rows[key] = dict(row)
        except Exception as exc:
            logger.warning("Failed to load existing review CSV %s: %s", path, exc)

    merged: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()

    for gen_row in generated_rows:
        key = tuple(str(gen_row.get(k, "")).strip() for k in key_columns)
        seen_keys.add(key)
        old_row = old_rows.get(key)

        merged_row: dict[str, Any] = {}

        # 1. Populate generated columns from latest run
        for col in generated_columns:
            merged_row[col] = gen_row.get(col, "")

        # 2. Populate manual columns, preferring existing user edits if present
        for col in manual_columns:
            if (
                old_row
                and col in old_row
                and old_row[col] is not None
                and str(old_row[col]).strip() != ""
            ):
                merged_row[col] = old_row[col]
            else:
                # Default to generated historical evidence if available, otherwise default
                val = gen_row.get(col, "")
                if col == "review_status" and not val:
                    val = "pending"
                merged_row[col] = val

        # Ensure review_status is non-empty and valid
        curr_status = str(merged_row.get("review_status", "")).strip().lower()
        if curr_status not in VALID_REVIEW_STATUSES:
            merged_row["review_status"] = "pending"
        else:
            merged_row["review_status"] = curr_status

        merged.append(merged_row)

    # 3. Preserve previously reviewed rows that are no longer generated
    for old_key, old_row in old_rows.items():
        if old_key not in seen_keys:
            old_status = str(old_row.get("review_status", "")).strip().lower()
            has_manual_notes = any(
                str(old_row.get(c, "")).strip() != ""
                for c in manual_columns
                if c
                not in (
                    "review_status",
                    "historical_source",
                    "historical_match_name",
                    "historical_acara_id",
                    "historical_value",
                )
            )
            # If reviewer accepted, rejected, flagged needs_research, or added manual notes
            if (
                old_status in ("accepted", "rejected", "needs_research")
                or has_manual_notes
            ):
                preserved_row: dict[str, Any] = {}
                for col in generated_columns + manual_columns:
                    preserved_row[col] = old_row.get(col, "")
                if preserved_row.get("review_status") not in VALID_REVIEW_STATUSES:
                    preserved_row["review_status"] = (
                        old_status if old_status in VALID_REVIEW_STATUSES else "pending"
                    )
                merged.append(preserved_row)

    # 4. Deterministic sorting by key columns
    merged.sort(
        key=lambda r: tuple(str(r.get(k, "")).strip().lower() for k in key_columns)
    )

    # 5. Write out merged CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    all_columns = generated_columns + manual_columns
    with open(path, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_columns)
        writer.writeheader()
        writer.writerows(merged)

    return merged
