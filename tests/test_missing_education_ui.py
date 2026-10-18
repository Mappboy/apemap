"""Missing attendance findings remain individually reviewable in normal batches."""

import json
from typing import Any

import pytest

pytest.importorskip("flask", reason="Install the review-ui extra for GUI tests")

from apemap.review.gui import create_app
from apemap.review.research_batches import ResearchBatches
from tests.test_missing_education_batches import (
    missing_batches as missing_batches,
    result,
)
from tests.test_research_batch_ui import Controls
from tests.test_review_service import review_service as review_service


def prepare_ui(batches: ResearchBatches, *, positive: bool) -> dict[str, Any]:
    path = batches.path("missing") / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest.update(
        parliament=48,
        created_at="2026-10-18T01:00:00+00:00",
        provenance={"availability_note": "Exact model identity and usage unavailable."},
        audit_summary={
            "audit_count": 2,
            "research_count": 1,
            "skipped": [
                {"display_name": "Home Person", "reason": "Resolved home education"}
            ],
        },
    )
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return batches.import_results("missing", [result(positive=positive)])["proposals"][
        0
    ]


@pytest.mark.parametrize("positive", [False, True])
def test_missing_school_stages_and_skip_survive_reload_without_reviews(
    missing_batches: ResearchBatches, positive: bool
) -> None:
    prepare_ui(missing_batches, positive=positive)
    client = create_app(missing_batches.service).test_client()
    for _ in range(2):
        response = client.get("/research-batches/missing")
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert "Test Person" in page
        assert "Wikipedia and cited sources" in page
        assert "Final general web pass" in page
        assert "Home Person: Resolved home education" in page
        assert "Search and source-access log" in page
        assert 'name="reviewer"' in page
        assert ("Test High School" in page) is positive
        if not positive:
            assert "No attendance assertion proposed" in page
            assert 'data-accept-ready="false"' in page
            assert Controls(page).selected["action"] == "research"
            assert "No verified secondary attendance is proposed" in page
    assert not missing_batches.show("missing")["review_history"]
    assert not missing_batches.service.events()
    assert not missing_batches.service.evidence_records()


def test_missing_school_gui_requires_and_preserves_explicit_reviewer(
    missing_batches: ResearchBatches,
) -> None:
    proposal = prepare_ui(missing_batches, positive=False)
    client = create_app(missing_batches.service).test_client()
    page = client.get("/research-batches/missing").get_data(as_text=True)
    controls = Controls(page)
    data = {
        "csrf_token": controls.inputs["csrf_token"]["value"],
        "binding": controls.inputs["binding"]["value"],
        "action": "research",
        "notes": "The final search is complete; no sourced attendance was found.",
    }
    for evidence in proposal["evidence"]:
        data["inspected_" + evidence["evidence_id"]] = "yes"
        data["disposition_" + evidence["evidence_id"]] = evidence["stance"]
    location = "/research-batches/missing/review/" + proposal["proposal_id"]
    response = client.post(location, data=data)
    assert response.status_code == 400
    assert b"Enter the reviewer" in response.data
    assert not missing_batches.show("missing")["review_history"]
    data["reviewer"] = "Explicit GUI reviewer"
    response = client.post(location, data=data)
    assert response.status_code == 303, response.get_data(as_text=True)
    state = missing_batches.show("missing")
    assert (
        state["reviews"][proposal["proposal_id"]]["reviewer"] == "Explicit GUI reviewer"
    )
    assert not missing_batches.service.events()
    assert not missing_batches.service.evidence_records()
