"""Evidence retention and candidate scores cannot establish review authority."""

from pathlib import Path
from typing import Any
import hashlib
import json

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.review.model import education_review_id, school_review_id
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_service import review_service as review_service

pytestmark = pytest.mark.integration


def evidence_payload(
    *, stance: str = "supports", target: str = "acara:2"
) -> dict[str, Any]:
    return {
        "candidate_institution_ref": target,
        "source_url": "https://example.org/biography-" + stance,
        "source_type": "biography",
        "source_title": "School and locality evidence",
        "retrieved_at": "2026-10-09T00:00:00+00:00",
        "claim_type": "identity",
        "claim_value": "Test High School in its locality",
        "stance": stance,
        "source_quality": 1.0,
        "match_strength": 1.0,
        "temporal_relevance": 1.0,
        "geographic_relevance": 1.0,
        "excerpt_or_note": "Public source directly identifies the school",
        "generated_by": "manual",
    }


def test_retained_evidence_is_separate_from_authority_and_attendance(
    review_service: ReviewService,
) -> None:
    service = review_service
    case = education_review_id("TEST", "Test High School")
    before = hashlib.sha256(service.db_path.read_bytes()).hexdigest()
    register = service.external_dir / "school-location-2025.csv"
    register.write_bytes(
        register.read_bytes().replace(b"Other High School", b"Test High School")
    )
    initial = service.show(case)["ranked_candidates"]
    assert len(initial) == 2
    assert all(row["tied"] for row in initial)
    preview = service.prepare_evidence(case, evidence_payload())
    assert not service.evidence_path.exists() and not service.log_path.exists()
    record = service.save_evidence(preview)
    ranked = service.show(case)["ranked_candidates"]
    assert ranked[0]["institution_ref"] == "acara:2"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert record.evidence_id in ranked[0]["evidence_ids"]
    assert not service.log_path.exists()
    assert service.show(case)["decision"] is None
    assert hashlib.sha256(service.db_path.read_bytes()).hexdigest() == before


def test_mapping_can_reference_supporting_and_opposing_retained_evidence(
    review_service: ReviewService,
) -> None:
    service = review_service
    case = education_review_id("TEST", "Test High School")
    records = [
        service.save_evidence(
            service.prepare_evidence(case, evidence_payload(stance=stance))
        )
        for stance in ("supports", "contradicts")
    ]
    proposal = service.prepare(
        case,
        "map",
        {
            "institution_ref": "acara:2",
            "relationship_type": "direct",
            "evidence_refs": [record.evidence_id for record in records],
        },
        source_url="https://example.org/reviewed-conclusion",
        reviewer="Fixture reviewer",
    )
    saved = service.save(proposal)
    assert saved.payload["evidence_refs"] == [record.evidence_id for record in records]
    assert service.check()["valid"] is True


def test_new_evidence_invalidates_prior_decision_preview(
    review_service: ReviewService,
) -> None:
    service = review_service
    decision = service.prepare(
        school_review_id("Test High School"),
        "map",
        {
            "institution_ref": "acara:1",
            "relationship_type": "direct",
        },
        reviewer="Fixture reviewer",
    )
    case = education_review_id("TEST", "Test High School")
    service.save_evidence(service.prepare_evidence(case, evidence_payload()))
    with pytest.raises(StaleReviewError, match="Source/candidate"):
        service.save(decision)
    assert not service.log_path.exists()


def test_cli_evidence_dry_run_retains_only_after_explicit_save(
    review_service: ReviewService,
    tmp_path: Path,
) -> None:
    service = review_service
    case = education_review_id("TEST", "Test High School")
    payload = tmp_path / "evidence.json"
    payload.write_text(json.dumps(evidence_payload()), encoding="utf-8")
    command = [
        "review",
        "--log-path",
        str(service.log_path),
        "--db-path",
        str(service.db_path),
        "--external-dir",
        str(service.external_dir),
        "retain-evidence",
        case,
        "--payload",
        str(payload),
    ]
    runner = CliRunner()
    dry = runner.invoke(app, [*command, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert not service.evidence_path.exists() and not service.log_path.exists()
    retained = runner.invoke(app, command)
    assert retained.exit_code == 0, retained.output
    assert len(service.retained_evidence(case)) == 1
    assert not service.log_path.exists()


@pytest.mark.parametrize("explicit_evidence_path", [False, True])
def test_check_command_log_override_uses_the_matching_evidence_store(
    review_service: ReviewService,
    tmp_path: Path,
    explicit_evidence_path: bool,
) -> None:
    alternate_log = tmp_path / "alternate" / "decisions.jsonl"
    explicit = tmp_path / "separate-evidence" / "retained.jsonl"
    alternate = ReviewService(
        log_path=alternate_log,
        db_path=review_service.db_path,
        external_dir=review_service.external_dir,
        evidence_path=explicit if explicit_evidence_path else None,
    )
    case = education_review_id("TEST", "Test High School")
    retained = alternate.save_evidence(
        alternate.prepare_evidence(case, evidence_payload())
    )
    alternate.save(
        alternate.prepare(
            case,
            "map",
            {
                "institution_ref": "acara:2",
                "relationship_type": "direct",
                "evidence_refs": [retained.evidence_id],
            },
            source_url="https://example.org/reviewed-conclusion",
            reviewer="Fixture reviewer",
        )
    )
    command = [
        "review",
        "--log-path",
        str(review_service.log_path),
        "--db-path",
        str(review_service.db_path),
        "--external-dir",
        str(review_service.external_dir),
    ]
    if explicit_evidence_path:
        command.extend(["--evidence-path", str(explicit)])
    command.extend(["check", "--log-path", str(alternate_log)])
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["valid"] is True
    assert not review_service.evidence_path.exists()
    assert alternate.evidence_path == (
        explicit
        if explicit_evidence_path
        else alternate_log.with_name("evidence.jsonl")
    )
