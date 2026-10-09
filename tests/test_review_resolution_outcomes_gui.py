"""Typed unresolved outcomes, per-member progress and strict JSON GUI behavior."""

from __future__ import annotations

from html.parser import HTMLParser
from html import unescape
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.education_context import SCHOOL_CONTEXT_FIELDS
from apemap.review.gui import RESOLUTION_REASONS, create_app
from apemap.review.model import education_review_id, school_review_id
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_evidence_gui import EDUCATION_ID, EvidenceFixtureService
from tests.test_review_gui import Inputs, SCHOOL_ID, SOURCE, save_form
from tests.test_review_service import review_service as review_service


class Controls(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.selected: dict[str, str] = {}
        self.disabled: set[str] = set()
        self.options: dict[str, list[str]] = {}
        self.select = ""
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        name = attributes.get("name") or ""
        if "disabled" in attributes:
            self.disabled.add(name)
        if tag == "select":
            self.select = name
            self.options[name] = []
        elif tag == "option" and self.select in self.options:
            value = attributes.get("value") or ""
            self.options[self.select].append(value)
            if "selected" in attributes:
                self.selected[self.select] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = ""


class OutcomeService(EvidenceFixtureService):
    def progress(self) -> dict[str, Any]:
        return {
            "available": True,
            "status": "needs_individual_review",
            "reason": "ambiguous_name",
            "resolved_count": 0,
            "unresolved_count": 1,
            "withdrawn_count": 0,
            "assertions": [
                {
                    "review_id": EDUCATION_ID,
                    "aph_id": "abc",
                    "display_name": "Fixture Member",
                    "recorded_name": "Fixture School",
                    "status": "unresolved",
                    "current_resolution": {},
                }
            ],
        }

    def show(self, review_id: str) -> dict[str, Any]:
        item = super().show(review_id)
        item["context"]["individual_resolution"] = self.progress()
        return item

    def prepare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        preview = super().prepare(*args, **kwargs)
        preview["individual_resolution"] = {
            "before": self.progress(),
            "after": self.progress(),
        }
        preview["individual_resolution"]["before"]["assertions"][0][
            "current_resolution"
        ] = {"institution_ref": "acara:123"}
        return preview


@pytest.fixture
def outcomes_gui() -> tuple[Any, OutcomeService]:
    service = OutcomeService()
    app = create_app(service)
    app.config["TESTING"] = True
    return app.test_client(), service


def guided(client: Any, review_id: str, **overrides: Any) -> dict[str, Any]:
    values = Inputs(client.get(f"/items/{review_id}").get_data(as_text=True)).values
    education = review_id.startswith("education:")
    payload: dict[str, Any] = (
        {
            "aph_id": "abc",
            "recorded_school_name": "Fixture School",
            "attended_status": "graduated",
            "confidence": "verified",
            "retrieved_at": "2026-10-09T00:00:00+00:00",
        }
        if education
        else {"recorded_name": "Fixture School"}
    )
    payload.update(
        institution_ref="acara:123",
        relationship_type="successor",
        attended_institution_ref="acara:456",
        attended_identity_source_url=SOURCE,
        historical_scope_confirmed=True,
        resolution_reason="no_suitable_candidate",
        requires_individual_resolution=False,
        resolution_only=False,
    )
    return {
        "csrf_token": values["csrf_token"],
        **(
            {"school_workflow": "1", "form_token": values["form_token"]}
            if not education
            else {}
        ),
        "action": "ambiguous_name",
        "payload_mode": "guided",
        "payload": json.dumps(payload),
        "field_aph_id": "abc",
        "field_recorded_school_name": "Fixture School",
        "field_recorded_name": "Fixture School",
        "field_institution_ref": "acara:123",
        "field_relationship_type": "successor",
        "field_attended_institution_ref": "acara:456",
        "field_attended_identity_source_url": SOURCE,
        "field_historical_scope_confirmed": "1",
        "field_attended_status": "graduated",
        "field_confidence": "verified",
        "field_retrieved_at": "2026-10-09T00:00:00+00:00",
        "source_url": SOURCE,
        "reviewer": "Fixture reviewer",
        "notes": "Identical recorded names need separate member evidence",
        **overrides,
    }


@pytest.mark.parametrize("review_id", [SCHOOL_ID, EDUCATION_ID])
@pytest.mark.parametrize("reason", ["ambiguous_name", "no_suitable_candidate"])
def test_guided_reason_clears_stale_targets_preserves_attendance_and_reloads(
    outcomes_gui: tuple[Any, OutcomeService], review_id: str, reason: str
) -> None:
    client, service = outcomes_gui
    page = client.get(f"/items/{review_id}").get_data(as_text=True)
    controls = Controls(page)
    assert set(controls.options["action"]) >= {"map", "research", *[reason]}
    if review_id == SCHOOL_ID:
        assert "reject" not in controls.options["action"]
        assert "reject" in controls.options["advanced_action"]
    else:
        assert "Reject attendance claim" in page
        assert controls.selected["action"] == "map"
    response = client.post(
        f"/items/{review_id}/preview", data=guided(client, review_id, action=reason)
    )
    assert response.status_code == 200
    event = service.prepared[-1]["event"]
    payload = event["payload"]
    assert event["action"] == "research"
    assert payload["resolution_reason"] == reason
    assert all(
        key not in payload
        for key in ("institution_ref", "relationship_type", *SCHOOL_CONTEXT_FIELDS)
    )
    marker = (
        "requires_individual_resolution"
        if review_id == SCHOOL_ID
        else "resolution_only"
    )
    assert payload[marker] is True
    assert (
        "resolution_only"
        if review_id == SCHOOL_ID
        else "requires_individual_resolution"
    ) not in payload
    if review_id == EDUCATION_ID:
        assert payload["attended_status"] == "graduated"
        assert payload["confidence"] == "verified"
    html = response.get_data(as_text=True)
    if review_id == SCHOOL_ID:
        assert f"{RESOLUTION_REASONS[reason]} · {event['notes']}" in html
        assert f"Needs research · {event['notes']}" not in html
    assert "Individual resolution before saving" in html
    assert "Individual resolution after saving" in html
    assert "0 resolved · 1 unresolved · 0 withdrawn" in html
    assert "<td>acara:123</td>" in html
    assert "acara:123 · acara:123" not in html
    assert service.saved == []
    assert Controls(html).selected["action"] == reason
    assert "field_relationship_type" in Controls(html).disabled
    saved = client.post(f"/items/{review_id}/save", data=save_form(response))
    assert saved.status_code == 303
    reopened = client.get(saved.headers["Location"]).get_data(as_text=True)
    assert Controls(reopened).selected["action"] == reason
    assert "field_institution_ref" in Controls(reopened).disabled


@pytest.mark.parametrize("review_id", [SCHOOL_ID, EDUCATION_ID])
@pytest.mark.parametrize("action", ["map", "research", "reject"])
def test_guided_switch_removes_stale_typed_policy(
    outcomes_gui: tuple[Any, OutcomeService], review_id: str, action: str
) -> None:
    client, service = outcomes_gui
    payload: dict[str, Any] = (
        {"aph_id": "abc", "recorded_school_name": "Fixture School"}
        if review_id == EDUCATION_ID
        else {"recorded_name": "Fixture School"}
    )
    payload.update(
        resolution_only=False,
        resolution_reason="ambiguous_name",
        requires_individual_resolution=True,
    )
    response = client.post(
        f"/items/{review_id}/preview",
        data=guided(
            client,
            review_id,
            action=action,
            field_relationship_type="direct",
            field_attended_institution_ref="",
            field_attended_identity_source_url="",
            field_historical_scope_confirmed="",
            payload=json.dumps(payload),
        ),
    )
    assert response.status_code == 200
    payload = service.prepared[-1]["event"]["payload"]
    assert "resolution_reason" not in payload
    assert "requires_individual_resolution" not in payload
    assert (
        payload.get("resolution_only") is True
        if action == "research" and review_id == EDUCATION_ID
        else "resolution_only" not in payload
    )
    if action == "map":
        assert payload["relationship_type"] == "direct"
        if review_id == EDUCATION_ID:
            assert "attended_status" not in payload
    else:
        assert "institution_ref" not in payload and "relationship_type" not in payload


@pytest.mark.parametrize("review_id", [SCHOOL_ID, EDUCATION_ID])
def test_complete_json_research_payload_is_preserved(
    outcomes_gui: tuple[Any, OutcomeService], review_id: str
) -> None:
    client, service = outcomes_gui
    payload = (
        {"recorded_name": "Fixture School"}
        if review_id == SCHOOL_ID
        else {"aph_id": "abc", "recorded_school_name": "Fixture School"}
    )
    payload.update(
        institution_ref="acara:123",
        relationship_type="direct",
        custom_provenance="retained",
    )
    response = client.post(
        f"/items/{review_id}/preview",
        data=guided(
            client,
            review_id,
            action="research",
            advanced_action="research",
            payload_mode="json",
            payload=json.dumps(payload),
        ),
    )
    assert response.status_code == 200
    assert service.prepared[-1]["event"]["payload"] == payload
    assert (
        "field_relationship_type"
        not in Controls(response.get_data(as_text=True)).disabled
    )


def test_invalid_reason_draft_and_stale_save_retain_selected_outcome(
    outcomes_gui: tuple[Any, OutcomeService], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = outcomes_gui
    bad = client.post(
        f"/items/{SCHOOL_ID}/preview", data=guided(client, SCHOOL_ID, notes="")
    )
    assert bad.status_code == 400
    assert Controls(bad.get_data(as_text=True)).selected["action"] == "ambiguous_name"
    assert "field_relationship_type" in Controls(bad.get_data(as_text=True)).disabled
    response = client.post(
        f"/items/{SCHOOL_ID}/preview", data=guided(client, SCHOOL_ID)
    )

    def stale(_preview: dict[str, Any]) -> None:
        raise StaleReviewError("Fixture context changed")

    monkeypatch.setattr(service, "save", stale)
    retry = client.post(f"/items/{SCHOOL_ID}/save", data=save_form(response))
    assert retry.status_code == 409
    assert Controls(retry.get_data(as_text=True)).selected["action"] == "ambiguous_name"
    assert service.saved == []


@pytest.mark.parametrize("review_id", [SCHOOL_ID, EDUCATION_ID])
def test_complete_json_typed_reason_does_not_coerce_explicit_false(
    outcomes_gui: tuple[Any, OutcomeService], review_id: str
) -> None:
    client, service = outcomes_gui
    payload: dict[str, Any] = (
        {"recorded_name": "Fixture School"}
        if review_id == SCHOOL_ID
        else {"aph_id": "abc", "recorded_school_name": "Fixture School"}
    )
    marker = (
        "requires_individual_resolution"
        if review_id == SCHOOL_ID
        else "resolution_only"
    )
    payload.update(resolution_reason="ambiguous_name")
    payload[marker] = False
    response = client.post(
        f"/items/{review_id}/preview",
        data=guided(
            client,
            review_id,
            action="research",
            advanced_action="research",
            payload_mode="json",
            payload=json.dumps(payload),
        ),
    )
    assert response.status_code == 400
    assert f'"{marker}": false' in unescape(response.get_data(as_text=True))
    assert service.prepared == [] and service.saved == []


def test_replacement_reason_is_normalized_without_rejecting_attendance(
    outcomes_gui: tuple[Any, OutcomeService],
) -> None:
    client, service = outcomes_gui
    first = client.post(
        f"/items/{EDUCATION_ID}/preview", data=guided(client, EDUCATION_ID)
    )
    assert (
        client.post(f"/items/{EDUCATION_ID}/save", data=save_form(first)).status_code
        == 303
    )
    response = client.post(
        f"/items/{EDUCATION_ID}/preview",
        data=guided(
            client,
            EDUCATION_ID,
            action="supersede",
            replacement_action="no_suitable_candidate",
            supersedes=service.saved[-1].decision_id,
        ),
    )
    assert response.status_code == 200
    event = service.prepared[-1]["event"]
    assert event["action"] == "supersede" and event["replacement_action"] == "research"
    assert event["payload"]["resolution_reason"] == "no_suitable_candidate"
    assert event["payload"]["resolution_only"] is True


def test_real_signed_school_policy_and_assertion_resolution_update_progress(
    review_service: ReviewService,
) -> None:
    service = review_service
    school_id = school_review_id("Test High School")
    education_id = education_review_id("TEST", "Test High School")
    app = create_app(service)
    app.config["TESTING"] = True
    client = app.test_client()
    values = Inputs(client.get(f"/items/{school_id}").get_data(as_text=True)).values
    response = client.post(
        f"/items/{school_id}/preview",
        data={
            "csrf_token": values["csrf_token"],
            "form_token": values["form_token"],
            "school_workflow": "1",
            "action": "ambiguous_name",
            "payload_mode": "guided",
            "payload": json.dumps({"recorded_name": "Test High School"}),
            "field_recorded_name": "Test High School",
            "reviewer": "Fixture reviewer",
            "notes": "Resolve each member separately",
        },
    )
    assert response.status_code == 200
    assert "Individual resolution after saving" in response.get_data(as_text=True)
    assert (
        client.post(f"/items/{school_id}/save", data=save_form(response)).status_code
        == 303
    )
    html = client.get(f"/items/{school_id}").get_data(as_text=True)
    assert "needs individual review" in html
    assert "0 resolved · 1 unresolved · 0 withdrawn" in html
    assert (
        client.get("/?entity_type=school&status=needs_individual_review").status_code
        == 200
    )
    service.save(
        service.prepare(
            education_id,
            "map",
            {
                "aph_id": "TEST",
                "recorded_school_name": "Test High School",
                "institution_ref": "acara:2",
                "relationship_type": "direct",
            },
            reviewer="Fixture reviewer",
            source_url=SOURCE,
        )
    )
    completed = client.get(f"/items/{school_id}").get_data(as_text=True)
    assert "resolved individually" in completed
    assert "1 resolved · 0 unresolved · 0 withdrawn" in completed
    filtered = client.get("/?entity_type=school&status=resolved_individually").get_data(
        as_text=True
    )
    assert "Test High School" in filtered


def test_unavailable_inventory_is_displayed_without_completion(
    outcomes_gui: tuple[Any, OutcomeService], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = outcomes_gui
    monkeypatch.setattr(
        service,
        "progress",
        lambda: {
            "available": False,
            "status": "needs_individual_review",
            "reason": "ambiguous_name",
        },
    )
    html = client.get(f"/items/{SCHOOL_ID}").get_data(as_text=True)
    assert "Assertion inventory unavailable" in html
    assert "0 resolved · 0 unresolved" not in html


def test_browser_unresolved_choices_clear_mapping_fields_without_editing_json() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to exercise offline browser interactions")
    script = (
        Path(__file__).resolve().parents[1] / "apemap/review/static/school-mapping.js"
    )
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const action = {value:'map'};
const replacement = {value:'accept'};
const mode = {value:'guided'};
const reference = {value:'acara:123', disabled:false, type:'text'};
const relationship = {value:'successor', disabled:false, type:'select-one'};
const context = {value:'acara:456', disabled:false, type:'text'};
const confirmation = {value:'1', checked:true, disabled:false, type:'checkbox'};
const payload = {value:'{"institution_ref":"acara:123","historical_scope_confirmed":true}'};
const originalJson = payload.value;
let change;
const fields = [reference,relationship,context,confirmation];
const form = {
  elements:{namedItem(name){return {action,replacement_action:replacement,payload_mode:mode}[name];}},
  querySelectorAll(selector){assert.equal(selector,'[data-resolution-field]'); return fields;},
  querySelector(){return null;},
  addEventListener(name,handler){assert.equal(name,'change'); change=handler;},
};
global.document = {querySelector(selector){return selector==='[data-resolution-outcomes]'?form:null;}};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
assert.equal(relationship.disabled,false);
for (const outcome of ['ambiguous_name','no_suitable_candidate','research','reject']) {
  reference.value='acara:123'; relationship.value='direct'; context.value='acara:456'; confirmation.checked=true;
  action.value=outcome; change();
  for(const field of fields) assert.equal(field.disabled,true);
  assert.equal(reference.value,''); assert.equal(relationship.value,''); assert.equal(context.value,'');
  assert.equal(confirmation.checked,false); assert.equal(confirmation.value,'1');
  assert.equal(payload.value,originalJson);
}
action.value='map'; change(); for(const field of fields) assert.equal(field.disabled,false);
action.value='supersede'; replacement.value='ambiguous_name'; change(); assert.equal(relationship.disabled,true);
mode.value='json'; relationship.value='successor'; reference.value='acara:123'; change();
assert.equal(relationship.disabled,false); assert.equal(relationship.value,'successor');
assert.equal(reference.value,'acara:123'); assert.equal(payload.value,originalJson);
"""
    subprocess.run(
        [node, "-e", harness, str(script)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
