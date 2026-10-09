"""Cited provider adapters, disposable jobs and explicit human retention."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from apemap.review.research import (
    HTTPResearchProvider,
    ResearchJobs,
    load_providers,
    validate_result,
)
from apemap.review.model import education_review_id, member_review_id
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_service import review_service as review_service

CASE = education_review_id("TEST", "Test High School")
URL = "https://example.org/school-history"


def result() -> dict[str, Any]:
    return {
        "sources": [{"url": URL, "title": "History <script>"}],
        "suggestions": [
            {
                "candidate_institution_ref": "acara:1",
                "source_url": URL,
                "claim_type": "identity",
                "claim_value": "School identity",
                "stance": "supports",
                "excerpt_or_note": "Source names the school",
                "source_quality": 1.0,
            }
        ],
    }


@pytest.mark.parametrize("provider", ["openai", "openrouter", "gemini"])
def test_official_provider_requests_and_citation_shapes(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    context = {"review_id": CASE, "candidates": [{"institution_ref": "acara:1"}]}
    content = json.dumps({"suggestions": result()["suggestions"]})
    data = {
        "openai": {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": content,
                            "annotations": [
                                {"type": "url_citation", "url": URL, "title": "History"}
                            ],
                        }
                    ],
                }
            ]
        },
        "openrouter": {
            "choices": [
                {
                    "message": {
                        "content": content,
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url_citation": {"url": URL, "title": "History"},
                            }
                        ],
                    }
                }
            ]
        },
        "gemini": {
            "candidates": [
                {
                    "content": {"parts": [{"text": content}]},
                    "groundingMetadata": {
                        "groundingChunks": [{"web": {"uri": URL, "title": "History"}}],
                        "searchEntryPoint": {
                            "renderedContent": "<a href='https://google.com'>Search</a>"
                        },
                    },
                }
            ]
        },
    }[provider]
    calls = []

    class Response:
        content = b"fixture"

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return data

    def post(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("TEST_RESEARCH_KEY", "private-test-secret")
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    monkeypatch.setattr(requests, "post", post)
    output = HTTPResearchProvider(
        provider, "fixture-model", "TEST_RESEARCH_KEY"
    ).search(context)
    assert output["suggestions"][0]["source_quality"] is None
    assert output["suggestions"][0]["generated_by"] == "agent-search"
    assert "private-test-secret" not in json.dumps(output)
    payload = calls[0][1]["json"]
    assert calls[0][1]["timeout"] == (10, 90)
    assert payload["tools"][0] == (
        {"google_search": {}}
        if provider == "gemini"
        else {"type": "web_search"}
        if provider == "openai"
        else {
            "type": "openrouter:web_search",
            "parameters": {"max_total_results": 10, "max_uses": 2},
        }
    )
    if provider == "gemini":
        assert output["search_entry_point"] and "?key=" not in calls[0][0]


def test_configuration_supports_multiple_choices_and_rejects_credentials(
    tmp_path: Path,
) -> None:
    config = Path(__file__).parents[1] / "docs/research-providers.example.json"
    assert set(load_providers(config)) == {"openai", "openrouter", "gemini"}
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": [
                    {
                        "id": "bad",
                        "provider": "openai",
                        "model": "fixture",
                        "api_key": "secret",
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="environment"):
        load_providers(path)


@pytest.mark.parametrize("change", ["citation", "candidate", "unsafe"])
def test_untrusted_suggestions_are_rejected(change: str) -> None:
    value = result()
    if change == "citation":
        value["sources"] = []
    elif change == "candidate":
        value["suggestions"][0]["candidate_institution_ref"] = "acara:999"
    else:
        value["sources"][0]["url"] = "javascript:alert(1)"
    with pytest.raises(ValueError):
        validate_result(
            {"review_id": CASE, "candidates": [{"institution_ref": "acara:1"}]}, value
        )


def test_network_errors_are_redacted_and_offline_does_not_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fail(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise requests.ConnectionError("sensitive-provider-body private-test-secret")

    monkeypatch.setattr(requests, "post", fail)
    monkeypatch.setenv("TEST_RESEARCH_KEY", "private-test-secret")
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    adapter = HTTPResearchProvider("openai", "fixture", "TEST_RESEARCH_KEY")
    with pytest.raises(ValueError, match="connection failure") as error:
        adapter.search({})
    assert "private-test-secret" not in str(error.value)
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    with pytest.raises(ValueError, match="offline"):
        adapter.search({})
    assert len(calls) == 1


class FixtureProvider:
    def search(self, context: dict[str, Any]) -> dict[str, Any]:
        return result()


def test_ui_provider_choice_inspection_preview_and_retention(
    review_service: ReviewService,
) -> None:
    pytest.importorskip("flask")
    from apemap.review.gui import create_app
    from tests.test_review_gui import Inputs

    service = review_service
    application = create_app(
        service,
        research_providers={"first": FixtureProvider(), "second": FixtureProvider()},
    )
    client = application.test_client()
    manager = application.extensions["research_jobs"]
    try:
        page = client.get(f"/items/{CASE}")
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'value="first"' in html and 'value="second"' in html
        token = Inputs(html).values["csrf_token"]
        started = client.post(
            f"/items/{CASE}/research",
            data={"csrf_token": token, "provider_id": "second"},
        )
        assert started.status_code == 303
        path = started.headers["Location"]
        identifier = path.rsplit("/", 1)[-1]
        manager.get(identifier, CASE).future.result(timeout=5)
        output = client.get(path)
        assert output.status_code == 200
        assert "&lt;script&gt;" in output.get_data(as_text=True)
        assert client.get(path + "/status").get_json() == {"status": "complete"}
        draft = client.post(path + "/suggestions/0", data={"csrf_token": token})
        assert draft.status_code == 200
        assert not service.evidence_path.exists() and not service.log_path.exists()
        suggestion = manager.result(identifier, CASE, service.research_revisions())[
            "suggestions"
        ][0]
        data = {
            "csrf_token": token,
            "research_job_id": identifier,
            "research_suggestion_index": "0",
            **{
                "evidence_" + key: str(value)
                for key, value in suggestion.items()
                if value is not None
            },
        }
        blocked = client.post(f"/items/{CASE}/evidence/preview", data=data)
        assert blocked.status_code == 400 and "Inspect the source" in blocked.get_data(
            as_text=True
        )
        data["source_inspected"] = "yes"
        preview = client.post(f"/items/{CASE}/evidence/preview", data=data)
        assert preview.status_code == 200
        fields = Inputs(preview.get_data(as_text=True)).values
        saved = client.post(
            f"/items/{CASE}/evidence/save",
            data={
                "csrf_token": token,
                "evidence_preview_token": fields["evidence_preview_token"],
            },
        )
        assert saved.status_code == 303
        assert service.evidence_records()[0].generated_by == "agent-search"
        assert not service.log_path.exists()
        assert client.get("/readiness").status_code == 200
    finally:
        manager.close()


def test_jobs_and_signed_retention_reject_stale_decisions(
    review_service: ReviewService,
) -> None:
    from apemap.review.gui import _read_preview, _sign_evidence_preview

    service = review_service
    context = service.research_context(CASE)
    assert context["member"]["aph_id"] == "TEST"
    assert context["assertions"][0]["attendance_period"]["basis"] == "estimated"
    jobs = ResearchJobs({"fixture": FixtureProvider()})
    try:
        identifier = jobs.start("fixture", context)
        jobs.get(identifier, CASE).future.result(timeout=5)
        output = jobs.result(identifier, CASE, service.research_revisions())
        assert output["status"] == "complete"
        assert not service.log_path.exists() and not service.evidence_path.exists()
        preview = service.prepare_evidence(CASE, output["suggestions"][0])
        preview["research_revisions"] = service.research_revisions()
        signed = _read_preview(_sign_evidence_preview(preview, b"fixture"), b"fixture")
        assert signed["research_revisions"] == preview["research_revisions"]
        service.save(
            service.prepare(
                member_review_id("TEST", "gender"),
                "accept",
                {"aph_id": "TEST", "field": "gender", "value": "Other"},
                source_url=URL,
                reviewer="Reviewer",
            )
        )
        with pytest.raises(StaleReviewError):
            jobs.result(identifier, CASE, service.research_revisions())
        with pytest.raises(StaleReviewError):
            service.save_evidence(signed)
        assert not service.evidence_path.exists()
    finally:
        jobs.close()
