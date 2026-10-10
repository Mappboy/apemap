"""Typed semantic comparison, normalization, and retained baseline resolution for member review."""

from __future__ import annotations

from datetime import date, datetime
from collections.abc import Mapping, Sequence
import re
from typing import Any
from urllib.parse import urlparse

from apemap.review.model import (
    ReviewEvent,
    active_heads,
    member_review_id,
    member_value_provider,
)

MATCHES = "matches"
DIFFERENT = "different"
PROPOSED_MISSING = "proposed_missing"
PROPOSED_INVALID = "proposed_invalid"
AMBIGUOUS = "ambiguous"

COMPARISON_RESULTS = (
    MATCHES,
    DIFFERENT,
    PROPOSED_MISSING,
    PROPOSED_INVALID,
    AMBIGUOUS,
)

GENDER_ALIASES: dict[str, str] = {
    "male": "Male",
    "man": "Male",
    "m": "Male",
    "q6581097": "Male",
    "female": "Female",
    "woman": "Female",
    "f": "Female",
    "q6581072": "Female",
    "other": "Other",
    "non-binary": "Other",
    "nonbinary": "Other",
}


def normalize_gender(value: Any) -> str | None:
    """Normalize supported gender aliases to canonical 'Male', 'Female', 'Other'."""
    if value is None:
        return None
    val = str(value).strip()
    if not val:
        return None
    if val in ("Male", "Female", "Other"):
        return val
    lower = val.lower()
    if lower in GENDER_ALIASES:
        return GENDER_ALIASES[lower]
    # Check URLs ending with Wikidata QID
    if lower.startswith(("http://", "https://")):
        parsed = urlparse(lower)
        token = parsed.path.rstrip("/").split("/")[-1]
        if parsed.hostname not in ("wikidata.org", "www.wikidata.org"):
            raise ValueError(f"Invalid gender: {value}")
        if token in GENDER_ALIASES:
            return GENDER_ALIASES[token]
    raise ValueError(f"Invalid gender: {value}")


def normalize_date_of_birth(value: Any) -> str | None:
    """Normalize valid ISO dates and timestamps to calendar date YYYY-MM-DD.

    Rejects partial dates (e.g. YYYY or YYYY-MM), ambiguous numeric dates
    (e.g. DD/MM/YYYY or MM/DD/YYYY), and malformed dates.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    val = str(value).strip()
    if not val:
        return None
    # Reject partial dates
    if re.fullmatch(r"\d{4}", val) or re.fullmatch(r"\d{4}-\d{2}", val):
        raise ValueError(f"Partial dates are not supported: {value}")
    # Reject slash-separated ambiguous numeric dates
    if "/" in val:
        raise ValueError(f"Ambiguous numeric dates are not supported: {value}")
    # Optional leading '+' from Wikidata SPARQL representation
    cleaned = val.removeprefix("+")
    # Date-only check: YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        try:
            parsed = date.fromisoformat(cleaned)
            return parsed.isoformat()
        except ValueError as exc:
            raise ValueError(f"Invalid date: {value}") from exc
    # ISO timestamp with time: YYYY-MM-DDTHH:MM:SS...
    if "T" in cleaned:
        date_part = cleaned.split("T", 1)[0]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part):
            try:
                # Ensure the full timestamp is parseable
                datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
                parsed = date.fromisoformat(date_part)
                return parsed.isoformat()
            except ValueError as exc:
                raise ValueError(f"Malformed ISO timestamp: {value}") from exc
    raise ValueError(f"Malformed date of birth: {value}")


def normalize_wikidata_id(value: Any) -> str | None:
    """Normalize valid bare QID or Wikidata URL to bare QID Q[1-9][0-9]*."""
    if value is None:
        return None
    val = str(value).strip()
    if not val:
        return None
    if re.fullmatch(r"Q[1-9][0-9]*", val, re.IGNORECASE):
        return val.upper()
    # Check Wikidata URL
    if val.startswith(("http://", "https://")):
        parsed = urlparse(val)
        if (
            parsed.hostname in ("wikidata.org", "www.wikidata.org")
            and not parsed.username
        ):
            qid = parsed.path.rstrip("/").split("/")[-1]
            if re.fullmatch(r"Q[1-9][0-9]*", qid, re.IGNORECASE):
                return qid.upper()
    raise ValueError(f"Invalid Wikidata identifier: {value}")


def normalize_member_value(field: str, value: Any) -> str | None:
    """Normalize a member field value according to its field rules."""
    if value is None:
        return None
    if field == "gender":
        return normalize_gender(value)
    if field == "date_of_birth":
        return normalize_date_of_birth(value)
    if field == "wikidata_id":
        return normalize_wikidata_id(value)
    raise ValueError(f"Unsupported member field: {field}")


def compare_member_proposal(
    field: str,
    effective_value: Any,
    proposed_value: Any,
    *,
    is_ambiguous: bool = False,
    is_explicit_null: bool = False,
) -> tuple[str, str | None, str | None]:
    """Compare a proposed member field value against its effective baseline.

    Returns (comparison_result, normalized_effective, normalized_proposed).
    """
    if is_ambiguous:
        norm_eff = None
        try:
            norm_eff = normalize_member_value(field, effective_value)
        except ValueError:
            pass
        norm_prop = None
        try:
            norm_prop = normalize_member_value(field, proposed_value)
        except ValueError:
            pass
        return AMBIGUOUS, norm_eff, norm_prop

    if proposed_value is None or str(proposed_value).strip() == "":
        norm_eff = None
        try:
            norm_eff = normalize_member_value(field, effective_value)
        except ValueError:
            pass
        return PROPOSED_MISSING, norm_eff, None

    try:
        norm_prop = normalize_member_value(field, proposed_value)
    except ValueError:
        norm_eff = None
        try:
            norm_eff = normalize_member_value(field, effective_value)
        except ValueError:
            pass
        return PROPOSED_INVALID, norm_eff, None

    try:
        norm_eff = normalize_member_value(field, effective_value)
    except ValueError:
        norm_eff = str(effective_value) if effective_value is not None else None

    if norm_eff is None and not is_explicit_null:
        # Baseline is empty/unpopulated, proposed has a valid value
        return DIFFERENT, None, norm_prop

    if norm_eff is None and is_explicit_null:
        # Baseline was explicitly cleared to null
        return DIFFERENT, None, norm_prop

    if norm_eff == norm_prop:
        return MATCHES, norm_eff, norm_prop

    return DIFFERENT, norm_eff, norm_prop


def is_member_candidate_suppressed(
    comparison: str,
    effective_value: Any,
    *,
    is_explicit_null: bool = False,
) -> bool:
    """Determine whether a candidate should be suppressed from the review queue.

    - Suppress matching proposals (`matches`)
    - Suppress missing proposals (`proposed_missing`) against populated baselines
    - Preserve empty-field research tasks (neither current value nor proposal)
    - Preserve discrepancies, invalid proposals, and ambiguity
    """
    if comparison == MATCHES:
        return True
    if comparison == PROPOSED_MISSING:
        # If baseline is populated (or reviewed explicit null), suppress missing proposal.
        # If baseline is unpopulated (None and not explicit null), preserve as research task.
        return bool(
            (effective_value is not None and str(effective_value).strip())
            or is_explicit_null
        )
    return False


def effective_member_value(
    events: list[ReviewEvent],
    aph_id: str,
    field: str,
    source_value: Any,
) -> tuple[Any, ReviewEvent | None, bool]:
    """Resolve the effective value for a member field from the ledger.

    Returns (effective_value, providing_event, is_explicit_null).
    """
    review_id = member_review_id(aph_id, field)
    heads = active_heads(events).get(review_id, [])
    if len(heads) == 0:
        return source_value, None, False
    if len(heads) > 1:
        # Conflicting active heads
        return source_value, None, False

    provider = member_value_provider(heads[0], events)
    if provider is not None:
        value = provider.payload["value"]
        return value, provider, value is None
    return source_value, None, False


def check_identity_ambiguity(
    caches: Sequence[tuple[str | None, str, Mapping[str, Any]]],
    events: list[ReviewEvent],
    aph_id: str,
) -> bool:
    """Assess whether a member's identity is ambiguous across available caches and decision heads."""
    # 1. Conflicting decision heads on any member field
    heads = active_heads(events)
    for field in ("date_of_birth", "gender", "wikidata_id"):
        review_id = member_review_id(aph_id, field)
        if len(heads.get(review_id, [])) > 1:
            return True

    # 2. Cache status conflicts or multiple candidates
    valid_caches = [cached for _, _, cached in caches if cached]
    for cached in valid_caches:
        if cached.get("status") in ("conflict", "ambiguous"):
            return True
        candidates = cached.get("candidates", [])
        if len(candidates) > 1 and cached.get("status") != "matched":
            return True

    # 3. Inter-cache disagreement (e.g. aph_id cache vs name cache)
    if len(valid_caches) > 1:
        for field in ("wikidata_id", "date_of_birth", "gender"):
            values: set[str] = set()
            for cached in valid_caches:
                value = cached.get(field)
                if value is None or value == "":
                    continue
                try:
                    normalized = normalize_member_value(field, value)
                except ValueError:
                    # Invalid facts stay reviewable without inventing a second identity.
                    continue
                if normalized is not None:
                    values.add(normalized)
            if len(values) > 1:
                return True

    return False
