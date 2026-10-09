"""Evidence previews bind their selected institution definition."""

import pytest

from apemap.review.model import education_review_id, institution_review_id
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_evidence_service import evidence_payload
from tests.test_review_service import review_service as review_service

pytestmark = pytest.mark.integration


def test_manual_definition_change_invalidates_retained_evidence_preview(
    review_service: ReviewService,
) -> None:
    service = review_service
    reference = "manual:fixture-college"
    case = institution_review_id(reference)
    definition = {
        "institution_ref": reference,
        "school_name": "Fixture College",
        "country": "Australia",
        "sector": "Other",
    }
    original = service.save(
        service.prepare(
            case,
            "accept",
            definition,
            source_url="https://example.org/college",
            reviewer="Fixture reviewer",
        )
    )
    preview = service.prepare_evidence(
        education_review_id("TEST", "Test High School"),
        evidence_payload(target=reference),
    )
    service.save(
        service.prepare(
            case,
            "supersede",
            {**definition, "school_name": "Another Fixture College"},
            supersedes=[original.decision_id],
            replacement_action="accept",
            source_url="https://example.org/correction",
            reviewer="Fixture reviewer",
        )
    )
    with pytest.raises(StaleReviewError, match="candidate definition changed"):
        service.save_evidence(preview)
    assert not service.evidence_path.exists()
