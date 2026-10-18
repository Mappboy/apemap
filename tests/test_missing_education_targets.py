"""Missing attendance cannot introduce unsupported historical institution facts."""

from typing import Any

import pytest

from apemap.review.missing_education_results import validate_missing_education_result
from tests.test_missing_education_batches import RID, result


def task() -> dict[str, Any]:
    return {
        "review_id": RID,
        "context": {"member": {"aph_id": "TEST", "display_name": "Test Person"}},
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"relationship_type": "successor"},
        {"relationship_type": "alias"},
        {"relationship_type": "rename"},
        {
            "relationship_type": "successor",
            "attended_institution_ref": "acara:2",
            "attended_identity_source_url": "https://example.org/not-inspected",
        },
        {"historical_scope_confirmed": False},
    ],
)
def test_missing_attendance_rejects_historical_institution_context(
    updates: dict[str, Any],
) -> None:
    proposal = result()
    proposal["education_proposals"][0].update(updates)
    with pytest.raises(ValueError, match="direct institution identity only"):
        validate_missing_education_result(task(), proposal)


def test_direct_institution_identity_remains_available() -> None:
    validate_missing_education_result(task(), result())


def test_unmapped_attendance_needs_no_institution_context() -> None:
    proposal = result()
    payload = proposal["education_proposals"][0]
    payload.pop("institution_ref")
    payload.pop("relationship_type")
    payload["evidence_refs"].pop()
    validate_missing_education_result(task(), proposal)
