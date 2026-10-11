from __future__ import annotations

import re
from typing import Any

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.gui import create_app
from apemap.review.model import education_review_id, member_review_id
from apemap.review.service import ReviewService
from tests.test_review_member_batch import member_cache as member_cache
from tests.test_review_service import review_service as review_service


class MemberProvider:
    def search(self, context: dict[str, Any]) -> dict[str, Any]:
        assert context["member_field"] == "gender"
        url = "https://example.org/member"
        return {
            "sources": [{"url": url, "title": "Official biography"}],
            "suggestions": [
                {
                    "source_url": url,
                    "claim_type": "member_value",
                    "claim_value": {"field": "gender", "value": "Female"},
                    "stance": "supports",
                    "excerpt_or_note": "The official biography states the value.",
                }
            ],
        }


class AssertionProvider:
    def search(self, context: dict[str, Any]) -> dict[str, Any]:
        assert context["selected_candidate"]["institution_ref"] == "acara:1"
        return {
            "sources": [
                {"url": "https://example.org/school", "title": "School register"}
            ],
            "suggestions": [
                {
                    "source_url": "https://example.org/school",
                    "candidate_institution_ref": "acara:1",
                    "claim_type": "identity",
                    "claim_value": "This is Test High School.",
                    "stance": "supports",
                    "excerpt_or_note": "The source identifies the institution.",
                }
            ],
        }


def _token(html: str, name: str) -> str:
    matches = re.findall(rf'name="{name}" value="([^"]*)"', html)
    value = next((entry for entry in matches if entry), None)
    assert value is not None, f"missing non-empty {name}"
    return value


def test_nojs_member_research_retain_and_copy_preserve_decision_draft(
    review_service: ReviewService, member_cache: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    app = create_app(review_service, research_providers={"fixture": MemberProvider()})
    client = app.test_client()
    jobs = app.extensions["research_jobs"]
    review_id = member_review_id("TEST", "gender")
    try:
        evidence_ids = []
        for index in (1, 2):
            record = review_service.save_evidence(
                review_service.prepare_evidence(
                    review_id,
                    {
                        "source_title": f"Retained biography {index}",
                        "source_type": "biography",
                        "source_url": f"https://example.org/member-{index}",
                        "retrieved_at": "2026-10-11T00:00:00+00:00",
                        "claim_type": "member_value",
                        "claim_value": {"field": "gender", "value": "Female"},
                        "stance": "supports",
                        "excerpt_or_note": "The biography supports this exact member field.",
                        "generated_by": "manual",
                    },
                )
            )
            evidence_ids.append(record.evidence_id)
        page = client.get("/members/TEST").get_data(as_text=True)
        csrf = _token(page, "csrf_token")
        started = client.post(
            f"/items/{review_id}/research",
            data={
                "csrf_token": csrf,
                "research_provider_id_gender": "fixture",
                "action_gender": "reject",
                "value_gender": "Draft value",
                "evidence_refs_gender": evidence_ids,
            },
        )
        assert started.status_code == 200
        token = _token(started.get_data(as_text=True), "workspace_draft_token")
        job_id = next(iter(jobs.jobs))
        jobs.get(job_id, review_id).future.result(timeout=5)
        results = client.get(
            f"/items/{review_id}/research/{job_id}?workspace_draft_token={token}"
        )
        html = results.get_data(as_text=True)
        assert 'name="action_gender" value="reject" checked' in html
        for evidence_id in evidence_ids:
            assert f'value="{evidence_id}" form="member-batch-form" checked' in html
        inspected = client.post(
            f"/items/{review_id}/research/{job_id}/suggestions/0",
            data={
                "csrf_token": csrf,
                "source_inspected": "yes",
                "workspace_draft_token": token,
                "action_gender": "reject",
                "value_gender": "Draft value",
            },
        )
        html = inspected.get_data(as_text=True)
        assert inspected.status_code == 200
        assert "Preview evidence record" in html
        assert 'name="value_gender" value="Draft value"' in html
        preview = client.post(
            f"/items/{review_id}/evidence/preview",
            data={
                "csrf_token": csrf,
                "workspace_draft_token": token,
                "research_job_id": job_id,
                "research_suggestion_index": "0",
                "source_inspected": "yes",
                "evidence_source_title": "Official biography",
                "evidence_source_type": "assisted-search",
                "evidence_source_url": "https://example.org/member",
                "evidence_retrieved_at": "2026-10-11T00:00:00+00:00",
                "evidence_claim_type": "member_value",
                "evidence_claim_value_json": '{"field":"gender","value":"Female"}',
                "evidence_stance": "supports",
                "evidence_excerpt_or_note": "The official biography states the value.",
            },
        )
        assert preview.status_code == 200
        preview_html = preview.get_data(as_text=True)
        assert "Evidence retention preview" in preview_html
        evidence_token = _token(preview_html, "evidence_preview_token")
        saved = client.post(
            f"/items/{review_id}/evidence/save",
            data={
                "csrf_token": csrf,
                "workspace_draft_token": token,
                "evidence_preview_token": evidence_token,
            },
        )
        assert saved.status_code == 200
        assert "Official biography" in saved.get_data(as_text=True)
        record = review_service.retained_evidence(review_id)[0]
        copied = client.post(
            f"/items/{review_id}/evidence/{record['evidence_id']}/copy",
            data={
                "csrf_token": csrf,
                "workspace_draft_token": token,
                "action_gender": "reject",
                "value_gender": "Draft value",
            },
        )
        copied_html = copied.get_data(as_text=True)
        assert copied.status_code == 200
        assert 'name="action_gender" value="reject" checked' in copied_html
        assert 'name="value_gender" value="Female"' in copied_html
        for evidence_id in evidence_ids:
            assert (
                f'value="{evidence_id}" form="member-batch-form" checked' in copied_html
            )
    finally:
        jobs.close()


def test_nojs_assertion_retain_then_copy_preserves_signed_draft_and_queue_context(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    app = create_app(
        review_service, research_providers={"fixture": AssertionProvider()}
    )
    client = app.test_client()
    jobs = app.extensions["research_jobs"]
    review_id = education_review_id("TEST", "Test High School")
    queue_url = "?queue_view=all&queue_q=Test"
    try:
        page = client.get(f"/items/{review_id}{queue_url}").get_data(as_text=True)
        csrf = _token(page, "csrf_token")
        started = client.post(
            f"/items/{review_id}/research{queue_url}",
            data={
                "csrf_token": csrf,
                "research_provider_id": "fixture",
                "action": "research",
                "field_institution_ref": "acara:1",
                "field_relationship_type": "attended",
                "source_url": "https://example.org/decision",
                "notes": "Keep this decision draft",
            },
        )
        assert started.status_code == 200
        assert 'data-workspace-action="preview" formnovalidate' in started.get_data(
            as_text=True
        )
        token = _token(started.get_data(as_text=True), "workspace_draft_token")
        job_id = next(iter(jobs.jobs))
        jobs.get(job_id, review_id).future.result(timeout=5)
        client.get(
            f"/items/{review_id}/research/{job_id}?workspace_draft_token={token}"
        )
        inspected = client.post(
            f"/items/{review_id}/research/{job_id}/suggestions/0",
            data={
                "csrf_token": csrf,
                "source_inspected": "yes",
                "workspace_draft_token": token,
                "action": "research",
                "field_institution_ref": "acara:1",
                "field_relationship_type": "attended",
                "source_url": "https://example.org/decision",
                "notes": "Keep this decision draft",
            },
        )
        assert inspected.status_code == 200
        evidence_preview = client.post(
            f"/items/{review_id}/evidence/preview",
            data={
                "csrf_token": csrf,
                "workspace_draft_token": token,
                "research_job_id": job_id,
                "research_suggestion_index": "0",
                "source_inspected": "yes",
                "evidence_source_title": "School register",
                "evidence_source_type": "assisted-search",
                "evidence_source_url": "https://example.org/school",
                "evidence_retrieved_at": "2026-10-11T00:00:00+00:00",
                "evidence_claim_type": "identity",
                "evidence_claim_value": "This is Test High School.",
                "evidence_candidate_institution_ref": "acara:1",
                "evidence_stance": "supports",
                "evidence_excerpt_or_note": "The source identifies the institution.",
            },
        )
        assert evidence_preview.status_code == 200
        evidence_token = _token(
            evidence_preview.get_data(as_text=True), "evidence_preview_token"
        )
        retained = client.post(
            f"/items/{review_id}/evidence/save",
            data={
                "csrf_token": csrf,
                "workspace_draft_token": token,
                "evidence_preview_token": evidence_token,
            },
        )
        assert retained.status_code == 200, retained.get_data(as_text=True)
        retained_html = retained.get_data(as_text=True)
        assert (
            'name="action"' in retained_html
            and 'value="research" selected' in retained_html
        )
        assert (
            'name="notes"' in retained_html
            and "Keep this decision draft" in retained_html
        )
        assert (
            'name="field_institution_ref"' in retained_html
            and 'value="acara:1"' in retained_html
        )
        record = review_service.retained_evidence(review_id)[0]
        copied = client.post(
            f"/items/{review_id}/evidence/{record['evidence_id']}/copy",
            data={
                "csrf_token": csrf,
                "workspace_draft_token": token,
                "action": "research",
                "field_institution_ref": "acara:1",
                "field_relationship_type": "attended",
                "source_url": "https://example.org/decision",
                "notes": "Keep this decision draft",
            },
        )
        copied_html = copied.get_data(as_text=True)
        assert copied.status_code == 200
        assert "Keep this decision draft" in copied_html
        assert 'value="acara:1"' in copied_html
        assert "q=Test" in copied_html
    finally:
        jobs.close()
