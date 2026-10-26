"""Survey source review can retain evidence but cannot change resolutions."""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from apemap.review.research_batches import ResearchBatches
from tests.test_agent_session_research_batches import (
    exact_batches as exact_batches,
    prepare_session,
    session_proposal,
)
from tests.test_research_batches import batches as batches
from tests.test_review_service import review_service as review_service


@pytest.fixture
def survey_batches(exact_batches: ResearchBatches) -> ResearchBatches:
    prepare_session(exact_batches)
    path = exact_batches.path("session") / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["purpose"] = "survey-evidence-reconciliation"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal = session_proposal()
    proposal.update(outcome="unresolved", payload=None)
    exact_batches.import_results("session", [proposal])
    return exact_batches


@pytest.mark.parametrize(
    "operation", ["preview", "revalidate", "apply", "validate_preview"]
)
def test_evidence_only_scope_blocks_every_decision_entry_point(
    survey_batches: ResearchBatches, tmp_path: Path, operation: str
) -> None:
    destination = tmp_path / "decision.json"
    with pytest.raises(ValueError, match="Evidence-only survey"):
        if operation == "preview":
            survey_batches.preview("session", destination)
        elif operation == "revalidate":
            survey_batches.revalidate(
                "session", original_external_dir=tmp_path, destination=destination
            )
        elif operation == "apply":
            survey_batches.apply("session", destination, "untrusted")
        else:
            survey_batches.validate_preview(
                {"research_batch": {"batch_id": "session"}, "events": []}
            )
    assert not destination.exists()
    assert not survey_batches.service.events()
    assert not survey_batches.show("session")["applications"]


def test_explicit_inspector_and_retention_do_not_create_decisions(
    survey_batches: ResearchBatches,
) -> None:
    proposal = survey_batches.show("session")["proposals"][0]
    inspected = {
        record["evidence_id"]: record["stance"] for record in proposal["evidence"]
    }
    with pytest.raises(ValueError, match="reviewer"):
        survey_batches.record_review(
            "session", proposal["proposal_id"], "accept", inspected
        )
    survey_batches.record_review(
        "session",
        proposal["proposal_id"],
        "accept",
        inspected,
        reviewer="Explicit source inspector",
    )
    assert survey_batches.retain("session")["retained"] == 2
    assert len(survey_batches.service.evidence_records()) == 2
    assert not survey_batches.service.events()


def test_evidence_only_ui_exposes_inspection_and_retention_not_decisions(
    survey_batches: ResearchBatches,
) -> None:
    pytest.importorskip("flask")
    from apemap.review.gui import create_app

    page = (
        create_app(survey_batches.service)
        .test_client()
        .get("/research-batches/session")
        .get_data(as_text=True)
    )
    assert "Evidence-only survey" in page
    assert "source approvals" in page
    assert "across all parliaments" not in page
    assert 'name="reviewer"' in page
    assert "Retain approved evidence" in page
    assert "Approve source evidence" in page
    assert re.search(r"<button[^>]*data-stage-preview[^>]*hidden", page)
    assert re.search(r"<details[^>]*research-revalidation[^>]*hidden", page)
