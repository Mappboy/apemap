"""Evidence defaults and recoverable research review validation."""

from dataclasses import replace
from html.parser import HTMLParser
from typing import Any

import pytest

pytest.importorskip("flask", reason="Install the review-ui extra for GUI tests")

from apemap.review.evidence import EvidenceRecord
from apemap.review.gui import create_app
from apemap.review.model import school_review_id
from apemap.review.research_batches import ResearchBatches
from tests.test_research_batches import batches as batches
from tests.test_research_batches import proposal, school_proposal


class Controls(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.inputs: dict[str, dict[str, str | None]] = {}
        self.selected: dict[str, str] = {}
        self.select = ""
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("name"):
            self.inputs[str(values["name"])] = values
        elif tag == "select":
            self.select = values.get("name") or ""
        elif tag == "option" and "selected" in values:
            self.selected[self.select] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = ""


def prepare_ui(
    batches: ResearchBatches,
    monkeypatch: pytest.MonkeyPatch,
    kind: str = "members",
    stance: str = "contextual",
) -> dict[str, Any]:
    if kind == "schools":
        rid = school_review_id("Historical School")
        monkeypatch.setattr(
            batches.service,
            "candidates",
            lambda entity: [
                {"review_id": rid, "evidence": {}, "entity_type": "school"}
            ],
        )
        monkeypatch.setattr(
            batches.service,
            "research_context",
            lambda key: {
                "assertion": {"recorded_school_name": "Historical School"},
                "candidates": [],
                "selected_candidate": None,
            },
        )
        monkeypatch.setattr(
            batches.service, "show", lambda key: {"context": {"members": []}}
        )
    task = batches.prepare("ui", kind, 1)["tasks"][0]
    raw = school_proposal(task) if kind == "schools" else proposal(task)
    prior_id = raw["evidence"][-1]["evidence_id"]
    raw["evidence"][-1] = replace(
        EvidenceRecord.from_dict(raw["evidence"][-1]), stance=stance, evidence_id=""
    ).to_dict()
    for fact, refs in raw["identity"].get("evidence_refs", {}).items():
        raw["identity"]["evidence_refs"][fact] = [
            raw["evidence"][-1]["evidence_id"] if ref == prior_id else ref
            for ref in refs
        ]
    return batches.import_results("ui", [raw])["proposals"][0]


@pytest.mark.parametrize("kind", ["members", "schools"])
@pytest.mark.parametrize("stance", ["contextual", "contradicts"])
def test_dispositions_default_to_saved_stance_without_claiming_inspection(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch, kind: str, stance: str
) -> None:
    item = prepare_ui(batches, monkeypatch, kind, stance)
    with create_app(batches.service).test_client() as client:
        page = client.get("/research-batches/ui")
    assert page.status_code == 200
    controls = Controls(page.get_data(as_text=True))
    for record in item["evidence"]:
        eid = record["evidence_id"]
        assert controls.selected["disposition_" + eid] == record["stance"]
        assert "checked" not in controls.inputs["inspected_" + eid]
    assert controls.selected["action"] == "accept"
    assert b"Saved evidence conclusion" in page.data
    assert b"research-batch.js" in page.data
    assert b"data-review-warning" in page.data
    assert not batches.show("ui")["review_history"]


def test_saved_review_dispositions_and_action_take_precedence(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = prepare_ui(batches, monkeypatch)
    inspected = {e["evidence_id"]: "contradicts" for e in item["evidence"]}
    batches.record_review(
        "ui",
        item["proposal_id"],
        "research",
        inspected,
        notes="Identity needs research",
    )
    with create_app(batches.service).test_client() as client:
        page = client.get("/research-batches/ui")
    controls = Controls(page.get_data(as_text=True))
    assert controls.selected["action"] == "research"
    for eid, stance in inspected.items():
        assert controls.selected["disposition_" + eid] == stance
        assert "checked" in controls.inputs["inspected_" + eid]


def test_mismatched_acceptance_preserves_draft_and_can_record_research(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = prepare_ui(batches, monkeypatch)
    with create_app(batches.service).test_client() as client:
        page = client.get("/research-batches/ui")
        controls = Controls(page.get_data(as_text=True))
        data = {
            "csrf_token": controls.inputs["csrf_token"]["value"],
            "binding": controls.inputs["binding"]["value"],
            "action": "accept",
            "notes": "This source provides context, not identity evidence.",
        }
        for record in item["evidence"]:
            eid = record["evidence_id"]
            data["inspected_" + eid] = "yes"
            data["disposition_" + eid] = record["stance"]
        changed = item["evidence"][0]["evidence_id"]
        data["disposition_" + changed] = "contextual"
        route = "/research-batches/ui/review/" + item["proposal_id"]
        failed = client.post(route, data=data)
        assert failed.status_code == 400
        assert (
            b"Changed evidence conclusions require a corrected research batch"
            in failed.data
        )
        assert b"Review was not saved" in failed.data
        assert b"<h1>ui</h1>" in failed.data
        assert b"Individually reviewed" not in failed.data
        assert data["notes"].encode() in failed.data
        draft = Controls(failed.get_data(as_text=True))
        assert draft.selected["disposition_" + changed] == "contextual"
        assert "checked" in draft.inputs["inspected_" + changed]
        assert not batches.show("ui")["review_history"]
        data["action"] = "research"
        saved = client.post(route, data=data)
        assert saved.status_code == 303
    review = batches.show("ui")["reviews"][item["proposal_id"]]
    assert review["action"] == "research"
    assert review["inspected"][changed] == "contextual"
    assert not batches.service.events()


def test_accepting_default_dispositions_still_requires_explicit_inspection(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = prepare_ui(batches, monkeypatch)
    with create_app(batches.service).test_client() as client:
        controls = Controls(client.get("/research-batches/ui").get_data(as_text=True))
        data = {
            "csrf_token": controls.inputs["csrf_token"]["value"],
            "binding": controls.inputs["binding"]["value"],
            "action": "accept",
        }
        for record in item["evidence"]:
            eid = record["evidence_id"]
            data["disposition_" + eid] = record["stance"]
        route = "/research-batches/ui/review/" + item["proposal_id"]
        assert client.post(route, data=data).status_code == 400
        assert not batches.show("ui")["review_history"]
        for record in item["evidence"]:
            data["inspected_" + record["evidence_id"]] = "yes"
        assert client.post(route, data=data).status_code == 303
    assert batches.show("ui")["reviews"][item["proposal_id"]]["action"] == "accept"
    assert not batches.service.events()
