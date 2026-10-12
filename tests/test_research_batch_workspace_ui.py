"""Focused batch navigation, shared scope, exact previews and recoverable errors."""

from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("flask")

from apemap.review.gui import create_app
from apemap.review.research_batches import ResearchBatches
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_research_batch_ui import Controls, prepare_ui
from tests.test_research_batches import accept, batches as batches, imported, proposal
from tests.test_review_service import review_service as review_service


def review_data(html: str, item: dict[str, Any], **changes: Any) -> dict[str, Any]:
    controls = Controls(html)
    data = {
        "csrf_token": controls.inputs["csrf_token"]["value"],
        "binding": controls.inputs["binding"]["value"],
        "action": "accept",
        "notes": "Inspected fixture sources",
        "scope": "individual",
        "filter": "all",
    }
    for record in item["evidence"]:
        data["inspected_" + record["evidence_id"]] = "yes"
        data["disposition_" + record["evidence_id"]] = record["stance"]
    return {**data, **changes}


def test_focused_selection_filter_and_save_next(batches: ResearchBatches) -> None:
    state = imported(batches)
    first, second = state["proposals"][:2]
    client = create_app(batches.service).test_client()
    page = client.get("/research-batches/members?filter=unreviewed")
    html = page.get_data(as_text=True)
    assert html.count("data-research-review ") == 1
    assert "1 of 10" in html
    assert "Save and next" in html
    assert 'formaction="/research-batches/members/preview" data-stage-preview' in html
    assert (
        'formaction="/research-batches/members/revalidate" data-stage-revalidate'
        in html
    )
    assert "/review/" + first["proposal_id"] in html
    data = review_data(html, first, advance="next", filter="unreviewed")
    saved = client.post(
        "/research-batches/members/review/" + first["proposal_id"], data=data
    )
    assert saved.status_code == 303
    query = parse_qs(urlsplit(saved.headers["Location"]).query)
    assert query["proposal"] == [second["proposal_id"]]
    assert query["filter"] == ["unreviewed"]
    following = client.get(saved.headers["Location"]).get_data(as_text=True)
    assert "/review/" + second["proposal_id"] in following
    filtered = client.get("/research-batches/members?filter=accept").get_data(
        as_text=True
    )
    assert "1 of 1" in filtered
    assert "/review/" + first["proposal_id"] in filtered
    assert client.get("/research-batches/members?proposal=unknown").status_code == 404


def test_stale_review_retains_draft_and_refreshes_binding(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = prepare_ui(batches, monkeypatch)
    client = create_app(batches.service).test_client()
    original = client.get("/research-batches/ui").get_data(as_text=True)
    data = review_data(
        original, item, action="reject", notes="My preserved review draft"
    )
    batches.record_review(
        "ui",
        item["proposal_id"],
        "research",
        {record["evidence_id"]: record["stance"] for record in item["evidence"]},
        notes="Concurrent reviewer found an identity gap",
    )
    failed = client.post(
        "/research-batches/ui/review/" + item["proposal_id"], data=data
    )
    assert failed.status_code == 409
    html = failed.get_data(as_text=True)
    assert "My preserved review draft" in html
    assert "Concurrent reviewer found an identity gap" in html
    assert "Current saved review: research" in html
    refreshed = Controls(html)
    assert refreshed.inputs["binding"]["value"] != data["binding"]
    assert refreshed.selected["action"] == "reject"
    assert len(batches.show("ui")["review_history"]) == 1
    data["binding"] = refreshed.inputs["binding"]["value"]
    assert (
        client.post(
            "/research-batches/ui/review/" + item["proposal_id"], data=data
        ).status_code
        == 303
    )
    assert batches.show("ui")["reviews"][item["proposal_id"]]["action"] == "reject"


def test_member_service_facts_render_researched_object(
    batches: ResearchBatches,
) -> None:
    task = batches.prepare("service-facts", "members", 1)["tasks"][0]
    raw = proposal(task)
    raw["identity"]["service"] = {
        "chamber": "house",
        "electorate": "Fixture electorate",
        "start": "2021-02-03",
        "end": "2024-05-06",
    }
    batches.import_results("service-facts", [raw])
    client = create_app(batches.service).test_client()
    page = client.get("/research-batches/service-facts")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Recorded service" in html
    assert "Researched service" in html
    assert "Fixture electorate" in html
    assert "2021-02-03 &rarr; 2024-05-06" in html


@pytest.mark.parametrize(
    "operation", ["import", "retain", "preview", "revalidate", "apply"]
)
def test_operation_failures_return_to_workspace_without_authority_changes(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    prepare_ui(batches, monkeypatch)
    client = create_app(batches.service).test_client()
    controls = Controls(client.get("/research-batches/ui").get_data(as_text=True))
    data = {"csrf_token": controls.inputs["csrf_token"]["value"], "scope": "individual"}
    if operation == "import":
        data["results"] = (BytesIO(b"invalid-json"), "results.json")
    if operation == "apply":
        data.update({"preview_name": "../outside.json", "approval": "wrong"})
    failed = client.post("/research-batches/ui/" + operation, data=data)
    assert failed.status_code == 400
    assert b"<h1>ui</h1>" in failed.data
    assert b"Operation was not completed" in failed.data
    assert b"data-research-review" in failed.data
    assert not batches.service.events()
    assert not batches.service.evidence_records()


def test_shared_sampled_scope_survives_retention_and_preview_error(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = imported(batches)
    for item in state["proposals"]:
        if item["proposal_id"] in state["sample"]:
            accept(batches, "members", item)
    client = create_app(batches.service).test_client()
    first = client.get("/research-batches/members").get_data(as_text=True)
    assert 'name="scope" value="individual" checked' in first
    assert first.count('type="radio" name="scope" value="sampled"') == 1
    data = {
        "csrf_token": Controls(first).inputs["csrf_token"]["value"],
        "scope": "sampled",
    }
    saved = client.post("/research-batches/members/retain", data=data)
    assert saved.status_code == 303
    assert parse_qs(urlsplit(saved.headers["Location"]).query)["scope"] == ["sampled"]
    retained = client.get(saved.headers["Location"]).get_data(as_text=True)
    assert 'name="scope" value="sampled" checked' in retained
    assert "Evidence retained." in retained
    assert len(batches.service.evidence_records()) == 20

    def blocked(*args: Any, **kwargs: Any) -> None:
        assert kwargs["approve_sampled"] is True
        raise StaleReviewError("Source context changed; revalidate the research")

    monkeypatch.setattr(ResearchBatches, "preview", blocked)
    failed = client.post("/research-batches/members/preview", data=data)
    assert failed.status_code == 409
    assert b"Source context changed" in failed.data
    assert 'name="scope" value="sampled" checked' in failed.get_data(as_text=True)
    assert not batches.service.events()


@pytest.mark.parametrize("receipt_failure", [False, True])
def test_nojs_exact_preview_apply_and_persistent_measured_receipt(
    review_service: ReviewService,
    monkeypatch: pytest.MonkeyPatch,
    receipt_failure: bool,
) -> None:
    from apemap.review import research_batches as batch_module

    batches = ResearchBatches(review_service)
    task = batches.prepare("complete", "members", 1)["tasks"][0]
    item = batches.import_results("complete", [proposal(task, 12345)])["proposals"][0]
    client = create_app(review_service).test_client()
    initial = client.get("/research-batches/complete").get_data(as_text=True)
    data = review_data(initial, item)
    assert (
        client.post(
            "/research-batches/complete/review/" + item["proposal_id"], data=data
        ).status_code
        == 303
    )
    assert not review_service.events()
    assert (
        client.post("/research-batches/complete/retain", data=data).status_code == 303
    )
    assert not review_service.events()
    prepared = client.post("/research-batches/complete/preview", data=data)
    assert prepared.status_code == 303
    preview_url = prepared.headers["Location"]
    page = client.get(preview_url)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Projected queue changes" in html
    assert "Canonical effects" in html
    assert "Members missing Wikidata IDs" in html
    assert "Q12345" in html
    controls = Controls(html)
    approval = {
        "csrf_token": controls.inputs["csrf_token"]["value"],
        "preview_name": controls.inputs["preview_name"]["value"],
        "approval": controls.inputs["approval"]["value"],
    }
    assert not review_service.events()
    original_write = batch_module._write
    if receipt_failure:

        def fail_receipt(path: Path, value: Any) -> None:
            if path.name.startswith("application-"):
                raise OSError("Fixture receipt write unavailable")
            original_write(path, value)

        monkeypatch.setattr(batch_module, "_write", fail_receipt)
    applied = client.post("/research-batches/complete/apply", data=approval)
    if receipt_failure:
        assert applied.status_code == 200
        assert b"Decisions applied;" in applied.data
        assert b"Recover this application receipt" in applied.data
        assert len(review_service.events()) == 1
        assert not batches.show("complete")["applications"]
        monkeypatch.setattr(batch_module, "_write", original_write)
        applied = client.post("/research-batches/complete/apply", data=approval)
    assert applied.status_code == 303
    receipt = client.get(applied.headers["Location"]).get_data(as_text=True)
    assert "Measured queue changes" in receipt
    assert "Members missing Wikidata IDs" in receipt
    assert "1 decisions applied" in receipt
    assert 'data-review-ready="false"' in receipt
    assert "Application receipts" in client.get("/research-batches/complete").get_data(
        as_text=True
    )
    reopened = client.get(preview_url).get_data(as_text=True)
    assert "This exact preview has been applied" in reopened
    assert "data-apply-preview" not in reopened
    repeated = client.post("/research-batches/complete/apply", data=approval)
    assert repeated.status_code == 303
    assert len(review_service.events()) == 1


def test_revalidation_uses_explicit_original_path_and_preserves_it_on_error(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    item = prepare_ui(batches, monkeypatch)
    accept(batches, "ui", item)
    batches.retain("ui")
    client = create_app(batches.service).test_client()
    page = client.get("/research-batches/ui").get_data(as_text=True)
    original = tmp_path / "original external"
    calls = []

    def blocked(self: ResearchBatches, batch_id: str, **kwargs: Any) -> None:
        calls.append((batch_id, kwargs))
        raise StaleReviewError("Reviewed target changed; a fresh batch is required")

    monkeypatch.setattr(ResearchBatches, "revalidate", blocked)
    failed = client.post(
        "/research-batches/ui/revalidate",
        data={
            "csrf_token": Controls(page).inputs["csrf_token"]["value"],
            "scope": "individual",
            "original_external_dir": str(original),
        },
    )
    assert failed.status_code == 409
    assert calls[0][1]["original_external_dir"] == original
    assert calls[0][1]["approve_sampled"] is False
    assert calls[0][1]["destination"].parent == batches.path("ui") / "previews"
    assert Controls(failed.get_data(as_text=True)).inputs["original_external_dir"][
        "value"
    ] == str(original)
    assert b"Reviewed target changed" in failed.data
    assert not batches.service.events()
