"""Member batches through real offline services and the browser form contract."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.gui import create_app
from apemap.review.members import MemberFieldDraft
from apemap.review.service import ReviewService
from apemap.review.store import ReviewBusyError, log_revision
from tests.test_review_member_batch import member_cache as member_cache
from tests.test_review_service import review_service as review_service


class MemberForm(HTMLParser):
    """Read successful draft controls, including selected evidence options."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self.forms = 0
        self.select: str | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.forms += 1
        if tag == "input" and values.get("name"):
            self.values[str(values["name"])] = values.get("value") or ""
        if tag == "select":
            self.select = values.get("name")
        if (
            tag == "option"
            and self.select
            and (self.select not in self.values or "selected" in values)
        ):
            self.values[self.select] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.select = None


class MemberPresentation(HTMLParser):
    """Inspect reviewer prose separately from collapsed technical JSON."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.details: list[bool] = []
        self.prose: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "details":
            self.details.append("technical-details" in (values.get("class") or ""))
            assert "open" not in values
        if tag == "pre":
            assert any(self.details), "Raw JSON must be under Technical details"

    def handle_endtag(self, tag: str) -> None:
        if tag == "details":
            self.details.pop()

    def handle_data(self, data: str) -> None:
        if not any(self.details):
            self.prose.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.prose).split())


def test_member_batch_preview_save_and_matching_history(
    review_service: ReviewService,
) -> None:
    client = create_app(review_service).test_client()
    before_db = hashlib.sha256(review_service.db_path.read_bytes()).hexdigest()
    response = client.get("/members/TEST")
    html = response.get_data(as_text=True)
    assert "Test Person" in html
    assert "Review assertion" in html and "Review service" in html
    draft = MemberForm(html).values
    draft.update(
        action_gender="accept",
        action_date_of_birth="reject",
        action_wikidata_id="research",
        reviewer="Reviewer",
        notes_date_of_birth="Wrong proposal",
        notes_wikidata_id="Find identity",
    )
    response = client.post("/members/TEST/preview", data=draft)
    html = response.get_data(as_text=True)
    parsed = MemberForm(html)
    assert parsed.forms == 1
    assert "preview_token" in parsed.values
    assert "wikidata_id" in html and "Unresolved fields remaining" in html
    assert "Selected decisions and combined effects" in html
    prose = MemberPresentation(html).text()
    assert "Gender: Male → Female — Accept" in prose
    assert "Birth date: Retain 1970-01-01 — Reject proposal" in prose
    assert "Wikidata ID: Unresolved; no value recorded — Needs research" in prose
    assert "Member comparison" in prose and "Current reviewed" in prose
    assert 'src="/static/member.js?v=' in html and "<script>" not in html
    assert 'style="' not in html
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert not review_service.events()
    response = client.post("/members/TEST/save", data=parsed.values)
    assert response.status_code == 302
    events = review_service.events()
    assert len(events) == 3
    gender = next(event for event in events if event.payload["field"] == "gender")
    assert gender.payload["value"] == "Female"
    assert gender.payload["candidate_id"] == draft["candidate_id_gender"]
    assert hashlib.sha256(review_service.db_path.read_bytes()).hexdigest() == before_db
    html = client.get("/members/TEST").get_data(as_text=True)
    assert 'name="action_gender"' not in html
    assert gender.decision_id in html
    assert "Decision history" in html
    assert "Accepted · Female · Reviewer" in MemberPresentation(html).text()


@pytest.mark.parametrize("action", ["reject", "research"])
def test_readable_preview_retains_reviewed_value_when_resolving_conflict(
    review_service: ReviewService, action: str
) -> None:
    from dataclasses import replace

    from apemap.review.store import encode_event

    first = review_service.save_batch(
        review_service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="gender",
                    action="accept",
                    value="Other",
                    source_url="https://example.org/bio",
                    reviewer="Reviewer",
                )
            ],
        )
    )[0]
    parallel = replace(
        first,
        decision_id="parallel-ui-head",
        payload={**first.payload, "value": "Female"},
    )
    review_service.log_path.write_bytes(encode_event(first) + encode_event(parallel))
    client = create_app(review_service).test_client()
    draft = MemberForm(client.get("/members/TEST").get_data(as_text=True)).values
    draft.update(
        action_gender=action,
        retained_decision_id_gender=first.decision_id,
        reviewer="Reviewer",
        notes_gender="Keep reviewed value",
    )
    html = client.post("/members/TEST/preview", data=draft).get_data(as_text=True)
    prose = MemberPresentation(html).text()
    label = "Reject proposal" if action == "reject" else "Needs research"
    assert f"Gender: Resolve conflicting decisions; retain Other — {label}" in prose
    assert "preview_token" in MemberForm(html).values
    assert len(review_service.events(allow_conflicts=True)) == 2


def test_candidate_alternatives_are_readable_and_escape_untrusted_text(
    review_service: ReviewService, member_cache: Path
) -> None:
    data = json.loads(member_cache.read_text())
    data["status"] = "ambiguous"
    data["candidates"] = [
        {
            "qid": "Q123",
            "gender": "Female",
            "dob": "1972-02-03",
            "article": "https://example.org/bio",
        },
        {"qid": "<script>bad()</script>", "bindings": [{"raw": "retained details"}]},
    ]
    member_cache.write_text(json.dumps(data))
    html = (
        create_app(review_service)
        .test_client()
        .get("/members/TEST")
        .get_data(as_text=True)
    )
    prose = MemberPresentation(html).text()
    assert "Q123 · Gender: Female · Birth date: 1972-02-03" in prose
    assert "retained details" not in prose and "retained details" in html
    assert "<script>bad()</script>" not in html
    assert "comparison-ambiguous" in html and "alert-warning" in html


@pytest.mark.parametrize(
    "changed",
    [
        "value",
        "action",
        "notes",
        "reviewer",
        "source_url",
        "candidate_id",
        "retained_decision_id",
    ],
)
def test_current_visible_draft_must_equal_signed_preview(
    review_service: ReviewService, changed: str
) -> None:
    client = create_app(review_service).test_client()
    draft = MemberForm(client.get("/members/TEST").get_data(as_text=True)).values
    draft.update(action_gender="accept", reviewer="Reviewer")
    response = client.post("/members/TEST/preview", data=draft)
    values = MemberForm(response.get_data(as_text=True)).values
    assert "preview_token" in values
    values["reviewer" if changed == "reviewer" else f"{changed}_gender"] = (
        "research" if changed == "action" else "Changed draft"
    )
    html = client.post("/members/TEST/save", data=values).get_data(as_text=True)
    assert "Draft inputs changed" in html
    assert not review_service.events()


@pytest.mark.parametrize("failure", ["busy", "file", "stale"])
def test_failed_member_save_preserves_draft(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    client = create_app(review_service).test_client()
    draft = MemberForm(client.get("/members/TEST").get_data(as_text=True)).values
    draft.update(
        action_gender="research",
        notes_gender="Preserve this draft",
        reviewer="Reviewer",
    )
    values = MemberForm(
        client.post("/members/TEST/preview", data=draft).get_data(as_text=True)
    ).values
    before = log_revision(review_service.log_path)
    if failure == "stale":
        original = review_service.semantic_diff

        def changed(*args: Any, **kwargs: Any) -> dict[str, Any]:
            diff = original(*args, **kwargs)
            diff["decisions"] = []
            return diff

        monkeypatch.setattr(review_service, "semantic_diff", changed)
    else:

        def fail(*args: Any, **kwargs: Any) -> None:
            raise (
                ReviewBusyError("Writer is busy")
                if failure == "busy"
                else OSError("File error")
            )

        monkeypatch.setattr(review_service, "save_batch", fail)
    response = client.post("/members/TEST/save", data=values)
    html = response.get_data(as_text=True)
    parsed = MemberForm(html)
    assert parsed.values["notes_gender"] == "Preserve this draft"
    assert ("preview_token" in parsed.values) is (failure == "busy")
    assert log_revision(review_service.log_path) == before


def test_suppressed_missing_proposals_are_context_only(
    review_service: ReviewService, member_cache: Path
) -> None:
    data = json.loads(member_cache.read_text())
    data.pop("gender")
    member_cache.write_text(json.dumps(data))
    html = (
        create_app(review_service)
        .test_client()
        .get("/members/TEST")
        .get_data(as_text=True)
    )
    assert 'name="action_gender"' not in html
    assert "proposed_missing" in html
    assert "Fields requiring no decision" in html


def test_stale_draft_survives_when_new_evidence_suppresses_field(
    review_service: ReviewService, member_cache: Path
) -> None:
    client = create_app(review_service).test_client()
    draft = MemberForm(client.get("/members/TEST").get_data(as_text=True)).values
    draft.update(
        action_gender="research", notes_gender="Keep this draft", reviewer="Reviewer"
    )
    values = MemberForm(
        client.post("/members/TEST/preview", data=draft).get_data(as_text=True)
    ).values
    data = json.loads(member_cache.read_text())
    data["gender"] = "Male"
    member_cache.write_text(json.dumps(data))
    html = client.post("/members/TEST/save", data=values).get_data(as_text=True)
    parsed = MemberForm(html)
    assert parsed.values["notes_gender"] == "Keep this draft"
    assert parsed.values["action_gender"] == "research"
    assert "Leave unchanged before previewing again" in html
    assert "preview_token" not in parsed.values
    assert not review_service.events()


def test_legacy_member_links_and_filter_intersection(
    review_service: ReviewService,
) -> None:
    client = create_app(review_service).test_client()
    assert (
        client.get("/items/member:test:gender").headers["Location"]
        == "/members/test#field-gender"
    )
    html = client.get("/?entity_type=member&parliament=48&q=Test+Person").get_data(
        as_text=True
    )
    assert "Test Person" in html and "3 actionable fields" in html
    html = client.get("/?entity_type=member&unmatched_schools=1").get_data(as_text=True)
    assert "Test Person" not in html
    assert "<strong>0</strong> pending" in html


def test_unmatched_filter_excludes_mappings_and_preserves_conflicts(
    review_service: ReviewService,
) -> None:
    from dataclasses import replace
    from apemap.review.model import school_review_id
    from apemap.review.store import encode_event

    service = review_service
    review_id = school_review_id("Test High School")
    event = service.save(
        service.prepare(
            review_id,
            "map",
            {
                "recorded_name": "Test High School",
                "institution_ref": "acara:1",
                "relationship_type": "direct",
            },
            source_url="https://example.org/history",
            reviewer="Reviewer",
        )
    )
    client = create_app(service).test_client()
    html = client.get(
        "/?unmatched_schools=1&entity_type=school&parliament=48"
    ).get_data(as_text=True)
    assert f"/items/{review_id}" not in html
    assert "<strong>0</strong> accepted" in html
    parallel = replace(
        event,
        decision_id="parallel-school",
        payload={**event.payload, "institution_ref": "acara:2"},
    )
    service.log_path.write_bytes(encode_event(event) + encode_event(parallel))
    before = service.log_path.read_bytes()
    html = client.get(
        "/?unmatched_schools=1&entity_type=school&parliament=48&status=conflict&q=Test"
    ).get_data(as_text=True)
    assert f"/items/{review_id}" in html
    assert "<strong>1</strong> conflict" in html
    assert service.log_path.read_bytes() == before
    client.get("/")
    assert service.log_path.read_bytes() == before


@pytest.mark.unit
@pytest.mark.parametrize("event", ["input", "change"])
def test_member_script_disables_save_on_draft_edits(event: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js required for offline browser contract")
    script = Path(__file__).parents[1] / "apemap/review/static/member.js"
    harness = """
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const listeners = {}, button = {disabled:false}, message = {hidden:true};
const form = {addEventListener: (event, callback) => listeners[event] = callback};
const elements = {'member-batch-form':form, 'save-batch-btn':button, 'save-disabled-msg':message};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document: {getElementById: id => elements[id]}});
listeners[process.argv[2]]();
assert.equal(button.disabled, true); assert.equal(message.hidden, false);
"""
    result = subprocess.run(
        [node, "-e", harness, str(script), event],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
