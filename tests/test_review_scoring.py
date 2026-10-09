"""Advisory scores are stable, source-deduplicated, and explicit about ties."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from apemap.review.model import education_review_id
from apemap.review.scoring import score_candidates
from tests.test_review_evidence import record

REVIEW_ID = education_review_id("ONE", "John Paul College")


def candidates() -> list[dict[str, Any]]:
    return [
        {
            "institution_ref": f"acara:{aid}",
            "school_name": "John Paul College",
            "recorded_name": "John Paul College",
            "state": state,
            "suburb": suburb,
            # A political service state conveys no school locality evidence.
            "represented_state": "WA",
        }
        for aid, state, suburb in (
            ("45994", "VIC", "Frankston"),
            ("48982", "WA", "Kalgoorlie"),
            ("48060", "QLD", "Daisy Hill"),
        )
    ]


def test_exact_same_name_candidates_tie_without_location_inference() -> None:
    result = score_candidates(REVIEW_ID, candidates(), [])
    assert {row["score"] for row in result} == {40}
    assert {row["rank"] for row in result} == {1}
    assert all(row["tied"] and row["advisory"] for row in result)
    assert all(row["score_band"] == "name_only" for row in result)
    assert all(row["components"]["geographic"] == 0 for row in result)
    assert {row["institution_name"] for row in result} == {"John Paul College"}


def test_explicit_support_and_opposition_have_explainable_bounded_components() -> None:
    support = record(
        source_quality=1, match_strength=1, temporal_relevance=1, geographic_relevance=1
    )
    opposing = record(
        candidate_institution_ref="acara:45994",
        stance="contradicts",
        source_quality=1,
        match_strength=1,
        temporal_relevance=1,
        geographic_relevance=1,
    )
    result = score_candidates(REVIEW_ID, candidates(), [opposing, support])
    assert result[0]["institution_ref"] == "acara:48982"
    assert result[0]["score"] == 100
    assert result[0]["score_band"] == "strong"
    assert result[0]["components"] == {
        "name_match": 40,
        "identity": 24,
        "source_quality": 12,
        "temporal": 12,
        "geographic": 12,
    }
    assert result[0]["supporting_ids"] == [support.evidence_id]
    assert result[-1]["score"] == 0
    assert result[-1]["opposing_ids"] == [opposing.evidence_id]
    assert result[-1]["reasons"][1]["stance"] == "contradicts"


def test_same_source_multiple_claims_do_not_multiply_scores() -> None:
    first = record(match_strength=0.5, geographic_relevance=0.5)
    repeat = record(
        claim_type="locality",
        claim_value="Kalgoorlie",
        match_strength=0.5,
        geographic_relevance=0.5,
    )
    anchor = record(
        source_url="https://example.org/history#school",
        claim_type="temporal",
        match_strength=0.5,
        geographic_relevance=0.5,
    )
    one = score_candidates(REVIEW_ID, candidates(), [first])[0]
    many = score_candidates(REVIEW_ID, candidates(), [first, repeat, anchor, first])[0]
    assert one["score"] == many["score"] == 58
    assert many["independent_source_count"] == 1
    assert len(many["evidence_ids"]) == 3


def test_opposing_and_supporting_same_source_are_retained_and_cancel() -> None:
    support = record(match_strength=1)
    opposite = record(stance="contradicts", match_strength=1)
    target = next(
        row
        for row in score_candidates(REVIEW_ID, candidates(), [support, opposite])
        if row["institution_ref"] == "acara:48982"
    )
    assert target["components"]["identity"] == 0
    assert target["score"] == 40
    assert target["supporting_ids"] == [support.evidence_id]
    assert target["opposing_ids"] == [opposite.evidence_id]


def test_context_and_other_member_sources_do_not_promote_candidate() -> None:
    contextual = record(stance="contextual", match_strength=1, geographic_relevance=1)
    other_member = record(
        review_id=education_review_id("OTHER", "John Paul College"), match_strength=1
    )
    general_attendance = record(
        candidate_institution_ref=None, claim_type="attendance", match_strength=1
    )
    result = score_candidates(
        REVIEW_ID, candidates(), [contextual, other_member, general_attendance]
    )
    assert {row["score"] for row in result} == {40}
    assert all(other_member.evidence_id not in row["evidence_ids"] for row in result)
    target = next(row for row in result if row["institution_ref"] == "acara:48982")
    assert contextual.evidence_id in target["contextual_ids"]


def test_shuffling_inputs_changes_neither_scores_nor_inputs() -> None:
    items = candidates()
    before = deepcopy(items)
    support = record(match_strength=0.7, source_quality=0.8)
    opposing = record(
        candidate_institution_ref="acara:48060",
        stance="contradicts",
        geographic_relevance=0.9,
    )
    expected = score_candidates(REVIEW_ID, items, [support, opposing])
    assert (
        score_candidates(REVIEW_ID, list(reversed(items)), [opposing, support])
        == expected
    )
    assert items == before
    assert all("confidence" not in row for row in expected)


def test_missing_names_and_missing_dimensions_have_no_imputed_points() -> None:
    result = score_candidates(
        REVIEW_ID,
        [{"institution_ref": "acara:48982", "school_name": "John Paul College"}],
        [record()],
    )[0]
    assert result["score"] == 0
    assert result["score_band"] == "insufficient"
    assert all(value == 0 for value in result["components"].values())


def test_name_similarity_is_explained_separately_from_retained_evidence() -> None:
    items = candidates()[:1]
    items[0]["school_name"] = "St John Paul College"
    result = score_candidates(REVIEW_ID, items, [])[0]
    assert 0 < result["score"] < 40
    assert result["components"]["name_match"] == result["score"]
    assert result["score_band"] == "name_only"
