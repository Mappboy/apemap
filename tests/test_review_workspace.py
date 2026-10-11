"""Offline contracts for focused workspace comparison and navigation helpers."""

import pytest

from apemap.review.navigation import (
    choose_successor,
    normalize_queue_context,
    queue_position,
    successor_ids,
)
from apemap.review.workspace import (
    SUMMARY_CONFLICTS,
    SUMMARY_SUPPORTS,
    SUMMARY_UNKNOWN,
    compare_values,
    extract_queue,
    summarize_evidence,
)


def test_comparison_uses_normalized_values_and_does_not_infer_contradiction() -> None:
    same = compare_values(
        "recorded_school_name", "St Clare's College", None, "st clares college"
    )
    assert same["summary"] == SUMMARY_SUPPORTS
    assert (
        compare_values("gender", "Female", "Female", "Male")["summary"]
        == SUMMARY_UNKNOWN
    )
    assert (
        compare_values("gender", "Male", "Female", "Female")["comparison"] == "matches"
    )
    assert (
        compare_values("gender", "Female", "Male", "Female")["comparison"]
        == "different"
    )
    assert (
        compare_values("gender", "Male", "Female", "Male", comparison="different")[
            "comparison"
        ]
        == "different"
    )
    contradicted = compare_values(
        "gender",
        "Female",
        None,
        "Male",
        provenance=[{"stance": "contradicts", "source_url": "https://example.test"}],
    )
    assert contradicted["summary"] == SUMMARY_CONFLICTS
    assert contradicted["provenance"][0]["source_url"] == "https://example.test"
    assert compare_values("gender", None, None, "Male")["summary"] == SUMMARY_UNKNOWN


def test_evidence_summary_keeps_stances_and_marks_missing_dimensions() -> None:
    result = summarize_evidence(
        [
            {
                "stance": "supports",
                "dimension": "identity",
                "source_url": "https://example.test",
            }
        ],
        ["identity", "temporal"],
    )
    assert result["supports"][0]["source_url"] == "https://example.test"
    assert result["contradicts"] == []
    assert result["contextual"] == []
    assert result["unknown_gaps"] == ["temporal"]


def test_queue_filters_grouped_rows_before_pagination_and_exposes_all_ids() -> None:
    rows = [
        {
            "review_id": "m1",
            "entity_type": "member",
            "status": "pending",
            "name": "Zed",
            "parliaments": [48],
        },
        {
            "review_id": "s1",
            "entity_type": "school",
            "status": "needs_research",
            "name": "Alpha",
            "parliaments": [48],
        },
        {
            "review_id": "m2",
            "entity_type": "member",
            "status": "conflict",
            "name": "Ada",
            "ambiguous": True,
            "parliaments": [47],
        },
    ]
    queue = extract_queue(
        rows, view="members", preset="member_identity_conflicts", per_page=1
    )
    assert queue["ids"] == ["m2"]
    assert [row["review_id"] for row in queue["all_rows"]] == ["m2"]
    assert queue["rows"][0]["review_id"] == "m2"
    assert queue["total"] == 1
    assert extract_queue(rows, view="schools", status="accepted")["total"] == 0


def test_duplicate_education_candidates_share_one_queue_position_and_keep_search_context() -> (
    None
):
    queue = extract_queue(
        [
            {
                "review_id": "edu-1",
                "entity_type": "member_education",
                "candidate_id": "a",
                "status": "pending",
                "parliaments": [47],
                "payload": {"recorded_school_name": "Old High"},
            },
            {
                "review_id": "edu-1",
                "entity_type": "member_education",
                "candidate_id": "b",
                "status": "pending",
                "parliaments": [48],
                "payload": {"recorded_school_name": "New College"},
            },
            {
                "review_id": "edu-2",
                "entity_type": "member_education",
                "candidate_id": "c",
                "status": "pending",
                "parliaments": [47],
            },
        ],
        q="New College",
    )
    assert queue["ids"] == ["edu-1"]
    assert queue["total"] == 1
    assert queue["all_rows"][0]["candidate_ids"] == ["a", "b"]
    assert queue["all_rows"][0]["parliaments"] == [47, 48]
    assert len(queue["all_rows"][0]["alternatives"]) == 2


def test_queue_navigation_whitelists_context_and_never_wraps() -> None:
    assert normalize_queue_context(
        {"view": "members", "q": "Ada", "irrelevant": "secret", "page": "2"}
    ) == {"view": "members", "q": "Ada", "page": "2"}
    with pytest.raises(ValueError, match="view"):
        normalize_queue_context({"view": "everything"})
    assert successor_ids(["a", "b", "c"], "b") == ["c"]
    assert successor_ids(["a", "b"], "outside") == []
    assert choose_successor(["a", "b", "c"], ["b", "c"], "a") == "b"
    assert choose_successor(["a", "b"], ["a"], "a") is None
    assert queue_position(["a", "b"], "b") == {
        "position": 2,
        "total": 2,
        "previous_id": "a",
        "next_id": None,
        "in_queue": True,
    }
