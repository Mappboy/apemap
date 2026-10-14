"""Evidence interpretation stays a draft until a cited definition is explicitly saved."""

from __future__ import annotations

import json
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
from typing import Any

import pytest

pytest.importorskip("flask")

from apemap.review.gui import create_app
from apemap.review.evidence import EvidenceRecord
from apemap.review.institution_drafts import (
    validate_interpretation,
    validate_draft_provenance,
)
from apemap.review.model import school_review_id
from apemap.review.research import HTTPResearchProvider
from apemap.review.service import ReviewService
from tests.test_review_gui import Inputs
from tests.test_review_service import review_service as review_service

CASE = school_review_id("Test High School")


class FormValues(Inputs):
    def __init__(self, html: str) -> None:
        self.textarea: str | None = None
        self.checked: dict[str, list[str]] = {}
        super().__init__(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") and "checked" in attributes:
            self.checked.setdefault(str(attributes["name"]), []).append(
                attributes.get("value") or ""
            )
        if tag == "textarea":
            self.textarea = dict(attrs).get("name")
            if self.textarea:
                self.values[self.textarea] = ""

    def handle_data(self, data: str) -> None:
        if self.textarea:
            self.values[self.textarea] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea":
            self.textarea = None


class InstitutionProvider:
    provider = "fixture"
    model = "fixture-model"

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def search(self, context: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("Institution interpretation must not search")

    def interpret_institution(self, context: dict[str, Any]) -> dict[str, Any]:
        self.contexts.append(context)
        ref = context["retained_evidence"][0]["evidence_id"]
        return {
            "fields": {
                field: {"value": value, "evidence_ids": [ref]}
                for field, value in {
                    "name": "Historical School",
                    "country": "Australia",
                    "status": "closed",
                }.items()
            },
            "gaps": ["Sector has not been established."],
            "conflicts": ["Closure day differs between sources; leave it unspecified."],
        }


def retain(
    service: ReviewService, *, case: str = CASE, **overrides: Any
) -> dict[str, Any]:
    record = {
        "source_title": "Historical school evidence",
        "source_type": "school-history",
        "source_url": "https://example.org/history",
        "retrieved_at": "2026-10-14T00:00:00Z",
        "candidate_institution_ref": None,
        "claim_type": "school_identity",
        "claim_value": {"name": "Historical School"},
        "stance": "supports",
        "excerpt_or_note": "Historical School in Australia closed in 2013; closure day is disputed.",
        **overrides,
    }
    return service.save_evidence(service.prepare_evidence(case, record)).to_dict()


def begin(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, InstitutionProvider, dict[str, Any], str]:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    retained = retain(service)
    provider = InstitutionProvider()
    app = create_app(service, research_providers={"fixture": provider})
    client = app.test_client()
    csrf = Inputs(client.get(f"/items/{CASE}").get_data(as_text=True)).values[
        "csrf_token"
    ]
    started = client.post(
        f"/items/{CASE}/institution-draft?queue_view=schools&queue_entity=school&queue_page=1",
        data={
            "csrf_token": csrf,
            "institution_provider_id": "fixture",
            "school_workflow": "1",
            "payload_mode": "guided",
            "action": "research",
            "notes": "Preserve school notes",
            "reviewer": "Reviewer",
            "field_relationship_type": "direct",
            "source_url": "https://example.org/attendance",
            "evidence_selection": "1",
            "evidence_refs": [retained["evidence_id"]],
        },
        headers={"Accept": "application/json"},
    )
    assert started.status_code == 200, started.get_data(as_text=True)
    job = started.get_json()
    app.extensions["research_jobs"].get(job["job_id"], CASE).future.result(timeout=5)
    return app, client, provider, job, csrf


def preview_form(client: Any, job: dict[str, Any], csrf: str) -> dict[str, Any]:
    page = client.get(job["review_url"])
    assert page.status_code == 200
    inputs = FormValues(page.get_data(as_text=True)).values
    return {
        "csrf_token": csrf,
        "institution_generation_token": inputs["institution_generation_token"],
        "payload_mode": "guided",
        "payload": inputs["payload"],
        "field_institution_ref": "manual:historical-school",
        "field_school_name": "Historical School",
        "field_country": "Australia",
        "field_institution_status": "closed",
        "field_sector": "",
        "source_url": "https://example.org/history",
        "reviewer": "Reviewer",
        "institution_sources_confirmed": "yes",
        **{
            f"institution_sources_{field}": inputs[f"institution_sources_{field}"]
            for field in ("name", "country", "status")
        },
    }


def test_generate_preview_save_only_institution_and_return_school_draft(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, provider, job, csrf = begin(review_service, monkeypatch)
    try:
        evidence = review_service.evidence_path.read_bytes()
        html = client.get(job["review_url"]).get_data(as_text=True)
        assert (
            "Closure day differs" in html and "Sector has not been established" in html
        )
        assert len(provider.contexts) == 1
        assert not review_service.log_path.exists()
        form = preview_form(client, job, csrf)
        preview = client.post(f"/items/{CASE}/institution-draft-preview", data=form)
        assert preview.status_code == 200, preview.get_data(as_text=True)
        assert not review_service.log_path.exists()
        inputs = Inputs(preview.get_data(as_text=True)).values
        saved = client.post(
            f"/items/{CASE}/institution-draft-save",
            data={
                "csrf_token": csrf,
                "preview_token": inputs["preview_token"],
                "institution_generation_token": inputs["institution_generation_token"],
            },
        )
        assert saved.status_code == 200, saved.get_data(as_text=True)
        returned_form = FormValues(saved.get_data(as_text=True))
        returned = returned_form.values
        assert returned_form.checked.get("evidence_refs") == [
            provider.contexts[0]["retained_evidence"][0]["evidence_id"]
        ]
        assert returned["field_institution_ref"] == "manual:historical-school"
        assert returned["notes"] == "Preserve school notes"
        assert returned["reviewer"] == "Reviewer"
        assert "queue_view=schools" in saved.get_data(as_text=True)
        events = review_service.events()
        assert len(events) == 1 and events[0].entity_type == "manual_institution"
        assert events[0].payload.get("sector") is None
        assert (
            events[0].payload["institution_draft_provenance"]["model"]
            == "fixture-model"
        )
        assert review_service.evidence_path.read_bytes() == evidence
        assert review_service.check()["valid"]
    finally:
        app.extensions["research_jobs"].close()


@pytest.mark.parametrize(
    "mutation",
    ["unconfirmed", "edited", "foreign_citation", "source", "evidence", "decision"],
)
def test_draft_preview_rejects_unreviewed_edits_and_stale_inputs(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    app, client, _, job, csrf = begin(review_service, monkeypatch)
    try:
        form = preview_form(client, job, csrf)
        if mutation == "unconfirmed":
            form.pop("institution_sources_confirmed")
        elif mutation == "edited":
            form["field_state"] = "TAS"
        elif mutation == "foreign_citation":
            form["institution_sources_country"] = "evidence:foreign"
        elif mutation == "source":
            source = review_service.external_dir / "school-location-2025.csv"
            source.write_text(
                source.read_text() + "3,New School,Government,Secondary,TAS,-42,147\n"
            )
        elif mutation == "evidence":
            retain(review_service, excerpt_or_note="Additional research")
        else:
            review_service.save(
                review_service.prepare(
                    CASE,
                    "research",
                    {"recorded_name": "Test High School"},
                    reviewer="Reviewer",
                    notes="New decision",
                )
            )
        before = (
            review_service.log_path.read_bytes()
            if review_service.log_path.exists()
            else b""
        )
        response = client.post(f"/items/{CASE}/institution-draft-preview", data=form)
        assert response.status_code == (
            409 if mutation in {"source", "evidence", "decision"} else 400
        )
        assert "Historical School" in response.get_data(as_text=True)
        assert (
            review_service.log_path.read_bytes()
            if review_service.log_path.exists()
            else b""
        ) == before
    finally:
        app.extensions["research_jobs"].close()


@pytest.mark.parametrize("name", ["Test High School", "Geilston Bay High School"])
def test_existing_manual_institution_can_be_used_without_duplicate_or_mapping(
    review_service: ReviewService,
    name: str,
) -> None:
    from apemap.db import get_connection
    from apemap.review.integration import capture_review_sources

    case = school_review_id(name)
    reference = (
        "manual:geilston-bay-high-school"
        if name.startswith("Geilston")
        else "manual:test-high-school"
    )
    if name != "Test High School":
        with get_connection(review_service.db_path) as conn:
            conn.execute(
                "UPDATE member_education SET school_name_as_recorded = ?", [name]
            )
            capture_review_sources(conn)
    review_service.save(
        review_service.prepare(
            "institution:" + reference,
            "accept",
            {
                "institution_ref": reference,
                "school_name": name,
                "country": "Australia",
                "institution_status": "closed",
                "sector": None,
            },
            source_url="https://example.org/history",
            reviewer="Reviewer",
        )
    )
    app = create_app(review_service)
    try:
        client = app.test_client()
        page = client.get(f"/items/{case}").get_data(as_text=True)
        assert "data-institution-use" in page
        before = review_service.log_path.read_bytes()
        response = client.post(
            f"/items/{case}/institution-use",
            data={
                "csrf_token": Inputs(page).values["csrf_token"],
                "manual_reference": reference,
                "notes": "Keep these",
                "school_workflow": "1",
                "payload_mode": "guided",
                "field_relationship_type": "direct",
            },
        )
        assert response.status_code == 200
        assert (
            Inputs(response.get_data(as_text=True)).values["field_institution_ref"]
            == reference
        )
        assert review_service.log_path.read_bytes() == before
    finally:
        app.extensions["research_jobs"].close()


@pytest.mark.parametrize("provider", ["openai", "openrouter", "gemini"])
def test_interpretation_transport_uses_no_search_tools(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    monkeypatch.setenv("FIXTURE_KEY", "fixture-secret")
    result = {"fields": {}, "gaps": [], "conflicts": []}
    text = json.dumps(result)
    response = {
        "openai": {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}
            ]
        },
        "openrouter": {
            "choices": [{"finish_reason": "stop", "message": {"content": text}}]
        },
        "gemini": {
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}
            ]
        },
    }[provider]

    class Response:
        content = b"{}"

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return response

    def post(url: str, **kwargs: Any) -> Response:
        assert "tools" not in kwargs["json"]
        assert kwargs["timeout"] == (10, 90)
        return Response()

    monkeypatch.setattr("apemap.review.research.requests.post", post)
    assert (
        HTTPResearchProvider(
            provider, "fixture-model", "FIXTURE_KEY"
        ).interpret_institution({"retained_evidence": []})
        == result
    )


@pytest.mark.parametrize(
    "bad",
    [
        "unknown",
        "member",
        "candidate",
        "contradicts",
        "wrong_case",
        "malformed",
        "coordinates",
    ],
)
def test_interpretation_rejects_invalid_citation_scope(
    review_service: ReviewService, bad: str
) -> None:
    record = retain(review_service)
    context = {
        "review_id": CASE,
        "retained_evidence": [record],
        "manual_institutions": [],
    }
    result = InstitutionProvider().interpret_institution(context)
    if bad == "unknown":
        result["fields"]["name"]["evidence_ids"] = ["unknown"]
    elif bad == "member":
        record["claim_value"]["aph_id"] = "TEST"
        record["evidence_id"] = ""
    elif bad == "candidate":
        record["candidate_institution_ref"] = "acara:1"
        record["evidence_id"] = ""
    elif bad == "contradicts":
        record["stance"] = "contradicts"
        record["evidence_id"] = ""
    elif bad == "wrong_case":
        record["review_id"] = school_review_id("Another school")
        record["evidence_id"] = ""
    elif bad == "malformed":
        result["fields"]["name"] = "School"
    else:
        result["fields"]["latitude"] = {
            "value": -42,
            "evidence_ids": [record["evidence_id"]],
        }
    if bad in {"member", "candidate", "contradicts", "wrong_case"}:
        record.update(EvidenceRecord.from_dict(record).to_dict())
        for proposed in result["fields"].values():
            proposed["evidence_ids"] = [record["evidence_id"]]
    with pytest.raises(ValueError):
        validate_interpretation(context, result)


def test_actual_institution_draft_browser_flow(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("APEMAP_BROWSER_TESTS") != "1":
        pytest.skip("Set APEMAP_BROWSER_TESTS=1 with Playwright and Edge installed")
    from werkzeug.serving import make_server
    from apemap.db import get_connection
    from apemap.review.integration import capture_review_sources

    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    name = "Geilston Bay High School"
    case = school_review_id(name)
    with get_connection(review_service.db_path) as conn:
        conn.execute("UPDATE member_education SET school_name_as_recorded = ?", [name])
        capture_review_sources(conn)
    retain(review_service, case=case)
    review_service.save(
        review_service.prepare(
            "institution:manual:geilston-bay-high-school",
            "accept",
            {
                "institution_ref": "manual:geilston-bay-high-school",
                "school_name": name,
                "country": "Australia",
                "institution_status": "closed",
                "sector": None,
            },
            source_url="https://example.org/history",
            reviewer="Reviewer",
        )
    )
    app = create_app(
        review_service, research_providers={"fixture": InstitutionProvider()}
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    evidence = review_service.evidence_path.read_bytes()
    try:
        executable = shutil.which("node")
        assert executable is not None
        result = subprocess.run(
            [
                executable,
                str(Path(__file__).with_name("institution_draft_page.test.cjs")),
                f"http://127.0.0.1:{server.server_port}/items/{case}?queue_view=schools&queue_entity=school&queue_page=1",
                os.environ.get("APEMAP_BROWSER_SCREENSHOTS", ""),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(review_service.events()) == 2
        assert all(
            event.entity_type == "manual_institution"
            for event in review_service.events()
        )
        assert review_service.evidence_path.read_bytes() == evidence
    finally:
        app.extensions["research_jobs"].close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "mutation", ["value", "case", "candidate", "field", "citation", "timestamp"]
)
def test_reviewed_provenance_replay_checks_exact_values_and_citation_scope(
    mutation: str,
) -> None:
    record = EvidenceRecord(
        review_id=CASE,
        candidate_institution_ref=None,
        source_title="School history",
        source_type="school-history",
        source_url="https://example.org/history",
        retrieved_at="2026-10-14T00:00:00Z",
        claim_type="school_identity",
        claim_value={"name": "Historical School"},
        stance="supports",
        excerpt_or_note="Historical School is in Australia.",
        generated_by="manual",
    )
    refs = [record.evidence_id]
    payload: dict[str, Any] = {
        "institution_ref": "manual:historical-school",
        "school_name": "Historical School",
        "country": "Australia",
        "sector": None,
        "institution_status": "unknown",
        "institution_draft_provenance": {
            "review_id": CASE,
            "provider": "fixture",
            "model": "fixture-model",
            "generated_at": "2026-10-14T00:00:00Z",
            "fields": {
                "name": {"value": "Historical School", "evidence_ids": refs},
                "country": {"value": "Australia", "evidence_ids": refs},
            },
        },
    }
    assert validate_draft_provenance(payload, [record]) == set(refs)
    changed = deepcopy(payload)
    provenance = changed["institution_draft_provenance"]
    if mutation == "value":
        changed["country"] = "New Zealand"
    elif mutation == "case":
        provenance["review_id"] = school_review_id("Another School")
    elif mutation == "candidate":
        raw = record.to_dict()
        raw.update(candidate_institution_ref="acara:1", evidence_id="")
        record = EvidenceRecord.from_dict(raw)
        for fact in provenance["fields"].values():
            fact["evidence_ids"] = [record.evidence_id]
    elif mutation == "field":
        provenance["fields"]["postcode"] = {"value": "7000", "evidence_ids": refs}
    elif mutation == "citation":
        provenance["fields"]["name"]["evidence_ids"] = ["unknown"]
    else:
        provenance["generated_at"] = "2026-10-14"
    with pytest.raises(ValueError):
        validate_draft_provenance(changed, [record])


def test_nojs_generation_redirect_and_csrf_offline_errors_preserve_school(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, _, _, csrf = begin(review_service, monkeypatch)
    try:
        url = f"/items/{CASE}/institution-draft"
        assert client.post(url).status_code == 403
        response = client.post(
            url,
            data={
                "csrf_token": csrf,
                "institution_provider_id": "fixture",
                "notes": "No-JS notes",
            },
        )
        assert response.status_code == 303
        job_id = response.headers["Location"].split("?")[0].rsplit("/", 1)[-1]
        app.extensions["research_jobs"].get(job_id, CASE).future.result(timeout=5)
        assert "Historical School" in client.get(response.headers["Location"]).get_data(
            as_text=True
        )
        monkeypatch.setenv("APEMAP_OFFLINE", "1")
        offline = client.post(
            url,
            data={
                "csrf_token": csrf,
                "institution_provider_id": "fixture",
                "notes": "Preserve offline notes",
            },
        )
        assert offline.status_code == 400
        assert "Preserve offline notes" in offline.get_data(as_text=True)
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()


def test_stale_save_preserves_edited_institution_and_writes_no_definition(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, _, job, csrf = begin(review_service, monkeypatch)
    try:
        form = preview_form(client, job, csrf)
        form["notes"] = "Edited institution notes"
        page = client.post(f"/items/{CASE}/institution-draft-preview", data=form)
        assert page.status_code == 200
        inputs = Inputs(page.get_data(as_text=True)).values
        retain(review_service, excerpt_or_note="More institution evidence")
        failed = client.post(
            f"/items/{CASE}/institution-draft-save",
            data={
                "csrf_token": csrf,
                "institution_generation_token": inputs["institution_generation_token"],
                "preview_token": inputs["preview_token"],
            },
        )
        assert failed.status_code == 409
        values = FormValues(failed.get_data(as_text=True)).values
        assert values["notes"] == "Edited institution notes"
        assert values["source_url"] == "https://example.org/history"
        assert values["field_country"] == "Australia"
        assert "Historical school evidence" in failed.get_data(as_text=True)
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()
