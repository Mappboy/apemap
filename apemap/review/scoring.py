"""Deterministic advisory rankings; never attendance or decision authority."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from apemap.review.evidence import EvidenceRecord, evidence_applies
from apemap.review.model import school_key

SCORING_VERSION = 1
COMPONENT_WEIGHTS = {
    "identity": 24.0,
    "source_quality": 12.0,
    "temporal": 12.0,
    "geographic": 12.0,
}
EVIDENCE_DIMENSIONS = {
    "identity": "match_strength",
    "source_quality": "source_quality",
    "temporal": "temporal_relevance",
    "geographic": "geographic_relevance",
}


def _source_key(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, "")
    )


def _band(score: float, directional_evidence: bool) -> str:
    if not directional_evidence:
        return "name_only" if score else "insufficient"
    if score >= 80:
        return "strong"
    if score >= 60:
        return "moderate"
    return "low"


def score_candidates(
    review_id: str,
    candidates: list[dict[str, Any]],
    records: list[EvidenceRecord],
) -> list[dict[str, Any]]:
    """Rank copied candidate metadata using name and explicit evidence dimensions.

    A normalized exact name contributes 40 points. Explicit retained evidence
    contributes at most 60; missing dimensions contribute zero. Each source URL
    contributes once per dimension, with its strongest support minus strongest
    contradiction. Independent sources sum within bounded component weights.
    Contextual and candidate-unspecified evidence is displayed without points.
    """
    applicable = {
        record.evidence_id: record
        for record in records
        if evidence_applies(record, review_id)
    }
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        row = deepcopy(candidate)
        reference = str(candidate.get("institution_ref") or "")
        name = str(
            candidate.get("institution_name") or candidate.get("school_name") or ""
        )
        recorded = str(
            candidate.get("recorded_name")
            or candidate.get("recorded_school_name")
            or ""
        )
        ratio = (
            SequenceMatcher(None, school_key(recorded), school_key(name)).ratio()
            if recorded and name
            else 0.0
        )
        components = {
            "name_match": round(40 * ratio, 2),
            **dict.fromkeys(COMPONENT_WEIGHTS, 0.0),
        }
        matched = sorted(
            (
                record
                for record in applicable.values()
                if record.candidate_institution_ref in (None, reference)
            ),
            key=lambda record: record.evidence_id,
        )
        sources: dict[str, list[EvidenceRecord]] = {}
        for record in matched:
            sources.setdefault(_source_key(record.source_url), []).append(record)
        directional = False
        for source_records in sources.values():
            for component, field in EVIDENCE_DIMENSIONS.items():
                support = max(
                    (
                        getattr(record, field) or 0.0
                        for record in source_records
                        if record.stance == "supports"
                        and record.candidate_institution_ref == reference
                    ),
                    default=0.0,
                )
                opposition = max(
                    (
                        getattr(record, field) or 0.0
                        for record in source_records
                        if record.stance == "contradicts"
                        and record.candidate_institution_ref == reference
                    ),
                    default=0.0,
                )
                directional = directional or support > 0 or opposition > 0
                components[component] += (support - opposition) * COMPONENT_WEIGHTS[
                    component
                ]
        for component, weight in COMPONENT_WEIGHTS.items():
            components[component] = round(
                max(-weight, min(weight, components[component])), 2
            )
        score = round(max(0.0, min(100.0, sum(components.values()))), 2)
        reasons: list[dict[str, Any]] = [
            {
                "component": "name_match",
                "points": components["name_match"],
                "note": "Exact normalized name; same-name institutions remain tied without evidence"
                if ratio == 1
                else "Normalized name similarity only"
                if recorded and name
                else "Recorded or candidate school name unavailable",
            }
        ]
        for record in matched:
            reasons.append(
                {
                    "evidence_id": record.evidence_id,
                    "stance": record.stance,
                    "source_url": record.source_url,
                    "source_group": _source_key(record.source_url),
                    "dimensions": {
                        component: getattr(record, field)
                        for component, field in EVIDENCE_DIMENSIONS.items()
                    },
                    "note": "Context only; no candidate-specific directional points"
                    if record.stance == "contextual"
                    or record.candidate_institution_ref is None
                    else "Explicit evidence dimensions; counted once per source URL",
                }
            )
        row.update(
            institution_ref=reference,
            institution_name=name,
            state=candidate.get("state"),
            suburb=candidate.get("suburb"),
            score=score,
            score_band=_band(score, directional),
            scoring_version=SCORING_VERSION,
            components=components,
            reasons=reasons,
            evidence_ids=[record.evidence_id for record in matched],
            supporting_ids=[
                record.evidence_id for record in matched if record.stance == "supports"
            ],
            opposing_ids=[
                record.evidence_id
                for record in matched
                if record.stance == "contradicts"
            ],
            contextual_ids=[
                record.evidence_id
                for record in matched
                if record.stance == "contextual"
            ],
            independent_source_count=len(sources),
            advisory=True,
        )
        ranked.append(row)
    ranked.sort(key=lambda row: (-row["score"], row["institution_ref"]))
    counts = Counter(row["score"] for row in ranked)
    for position, row in enumerate(ranked, 1):
        row["rank"] = next(
            index
            for index, other in enumerate(ranked, 1)
            if other["score"] == row["score"]
        )
        row["tied"] = counts[row["score"]] > 1
        row["display_order"] = position
    return ranked
