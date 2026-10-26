"""New attendance retains the exact supporting source retrieval instant."""

from typing import Any

import pytest

from apemap.review.missing_education_results import validate_missing_education_result
from tests.test_missing_education_batches import RID, result


def task() -> dict[str, Any]:
    return {
        "review_id": RID,
        "context": {"member": {"aph_id": "TEST", "display_name": "Test Person"}},
    }


def test_attendance_cannot_replace_evidence_retrieval_time() -> None:
    proposal = result()
    proposal["education_proposals"][0]["retrieved_at"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="retrieval time must match"):
        validate_missing_education_result(task(), proposal)


def test_attendance_allows_the_same_retrieval_instant_in_another_timezone() -> None:
    proposal = result()
    proposal["education_proposals"][0]["retrieved_at"] = "2026-10-18T12:00:00+11:00"
    validate_missing_education_result(task(), proposal)
