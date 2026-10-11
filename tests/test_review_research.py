"""Cited provider adapters, disposable jobs and explicit human retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from apemap.review.research import (
    HTTPResearchProvider,
    ResearchJobs,
    load_providers,
    validate_result,
)
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    member_review_id,
    school_digest,
    school_review_id,
)
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError, encode_event
from tests.test_review_service import review_service as review_service

CASE = education_review_id("TEST", "Test High School")
URL = "https://example.org/school-history"
FORGED_TIMESTAMP = "2001-01-01T00:00:00+00:00"


def result() -> dict[str, Any]:
    return {
        "retrieved_at": FORGED_TIMESTAMP,
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
                "retrieved_at": FORGED_TIMESTAMP,
            }
        ],
    }


@pytest.mark.parametrize("provider", ["openai", "openrouter", "gemini"])
def test_official_provider_requests_and_citation_shapes(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    context = {"review_id": CASE, "candidates": [{"institution_ref": "acara:1"}]}
    content = json.dumps(result())
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
    received_at = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    clock = Mock()
    clock.now.return_value = received_at

    class Response:
        content = b"fixture"

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            clock.now.assert_called_once_with(timezone.utc)
            clock.now.return_value = received_at + timedelta(seconds=5)
            return data

    def post(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setenv("TEST_RESEARCH_KEY", "private-test-secret")
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("apemap.review.research.datetime", clock)
    output = HTTPResearchProvider(
        provider, "fixture-model", "TEST_RESEARCH_KEY"
    ).search(context)
    assert output["suggestions"][0]["source_quality"] is None
    assert output["suggestions"][0]["generated_by"] == "agent-search"
    assert output["retrieved_at"] == received_at.isoformat()
    assert output["suggestions"][0]["retrieved_at"] == received_at.isoformat()
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
    elif provider == "openrouter":
        assert payload["max_tool_calls"] == 2


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


def test_validation_ignores_provider_and_suggestion_timestamps() -> None:
    before = datetime.now(timezone.utc)
    output = validate_result(
        {"review_id": CASE, "candidates": [{"institution_ref": "acara:1"}]}, result()
    )
    assert (
        before
        <= datetime.fromisoformat(output["retrieved_at"])
        <= datetime.now(timezone.utc)
    )
    assert output["suggestions"][0]["retrieved_at"] == output["retrieved_at"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gender", " female "),
        ("date_of_birth", "1972-02-03"),
        ("wikidata_id", "https://www.wikidata.org/wiki/Q123"),
    ],
)
def test_member_research_validates_exact_normalized_field_claim(
    field: str, value: str
) -> None:
    review_id = member_review_id("TEST", field)
    context = {
        "review_id": review_id,
        "member_field": field,
        "candidates": [{"candidate_id": "candidate-1"}],
    }
    claim = {
        **result(),
        "suggestions": [
            {
                "candidate_institution_ref": None,
                "source_url": URL,
                "claim_type": "member_value",
                "claim_value": {"field": field, "value": value},
                "stance": "supports",
                "excerpt_or_note": "Cited biographical source",
            }
        ],
    }
    output = validate_result(context, claim)
    expected = {
        "gender": "Female",
        "date_of_birth": "1972-02-03",
        "wikidata_id": "Q123",
    }[field]
    assert output["suggestions"][0]["claim_value"] == {
        "field": field,
        "value": expected,
    }
    assert output["suggestions"][0]["candidate_institution_ref"] is None


@pytest.mark.parametrize(
    "claim_value",
    [
        {"field": "gender", "value": "1972-02-03"},
        {"field": "date_of_birth", "value": "not a date"},
        {"field": "wikidata_id", "value": "Q0"},
        {"field": "gender", "value": "Female", "extra": True},
    ],
)
def test_member_research_rejects_mismatched_or_invalid_values(
    claim_value: dict[str, Any],
) -> None:
    field = (
        "date_of_birth"
        if claim_value["field"] == "gender" and "1972" in str(claim_value)
        else claim_value["field"]
    )
    context = {"review_id": member_review_id("TEST", field), "member_field": field}
    value = {
        **result(),
        "suggestions": [
            {
                "candidate_institution_ref": None,
                "source_url": URL,
                "claim_type": "member_value",
                "claim_value": claim_value,
                "stance": "supports",
                "excerpt_or_note": "Claim",
            }
        ],
    }
    with pytest.raises(ValueError):
        validate_result(context, value)


def test_member_research_disallows_candidate_refs_and_unsupported_types() -> None:
    context = {
        "review_id": member_review_id("TEST", "gender"),
        "member_field": "gender",
    }
    value = {
        **result(),
        "suggestions": [
            {
                "candidate_institution_ref": "acara:1",
                "source_url": URL,
                "claim_type": "member_context",
                "claim_value": {"note": "Biographical detail"},
                "stance": "contextual",
                "excerpt_or_note": "Claim",
            }
        ],
    }
    with pytest.raises(ValueError):
        validate_result(context, value)


@pytest.mark.parametrize("field", ["gender", "date_of_birth", "wikidata_id"])
def test_member_research_context_is_scoped_to_one_exact_field(
    review_service: ReviewService, field: str
) -> None:
    context = review_service.research_context(member_review_id("TEST", field))
    assert context["review_id"] == member_review_id("TEST", field)
    assert context["member_field"] == field
    assert set(context["assertion"]) == {
        "field",
        "source_value",
        "effective_value",
        "proposed_value",
        "comparison",
        "source_url",
    }
    assert all("aph_id" not in row for row in context["conflicts"])
    assert len(context["candidates"]) <= 10


def test_school_research_context_preserves_school_identity_and_local_candidates(
    review_service: ReviewService,
) -> None:
    context = review_service.research_context(
        "school:" + school_digest("Test High School")
    )
    assert context["assertion"]["recorded_school_name"] == "Test High School"
    assert context["candidates"]
    assert all(row.get("institution_ref") for row in context["candidates"])


def test_member_evidence_retention_and_copy_are_exact_and_read_only(
    review_service: ReviewService,
) -> None:
    service = review_service
    case = member_review_id("TEST", "gender")
    payload = {
        "source_title": "Official biography",
        "source_type": "biography",
        "source_url": URL,
        "retrieved_at": "2026-10-10T00:00:00+00:00",
        "claim_type": "member_value",
        "claim_value": {"field": "gender", "value": "Female"},
        "stance": "supports",
        "excerpt_or_note": "The biography identifies the member as female.",
        "generated_by": "manual",
    }
    record = service.save_evidence(service.prepare_evidence(case, payload))
    assert service.copy_member_evidence_value(case, record.evidence_id) == "Female"
    with pytest.raises(ValueError, match="different|exact|field"):
        service.copy_member_evidence_value(
            member_review_id("TEST", "date_of_birth"), record.evidence_id
        )
    invalid = {
        **payload,
        "claim_value": {"field": "date_of_birth", "value": "1972-02-03"},
    }
    with pytest.raises(ValueError, match="exact field"):
        service.prepare_evidence(case, invalid)


@pytest.mark.parametrize(
    "case",
    ["school:" + school_digest("Test High School"), CASE],
)
def test_school_identity_evidence_copy_is_exact_and_read_only(
    review_service: ReviewService, case: str
) -> None:
    service = review_service
    payload = {
        "source_title": "Local school history",
        "source_type": "school-register",
        "source_url": URL,
        "retrieved_at": "2026-10-10T00:00:00+00:00",
        "candidate_institution_ref": "acara:1",
        "claim_type": "identity",
        "claim_value": "The source identifies Test High School as this institution.",
        "stance": "supports",
        "excerpt_or_note": "Identity claim",
        "generated_by": "manual",
    }
    record = service.save_evidence(service.prepare_evidence(case, payload))
    decision_revision = service.review_revision()
    assert service.copy_school_evidence_value(case, record.evidence_id) == "acara:1"
    assert service.review_revision() == decision_revision
    with pytest.raises(ValueError, match="belong to this review case"):
        service.copy_school_evidence_value(
            "school:" + school_digest("Other School"), record.evidence_id
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"stance": "contradicts"}, "supporting"),
        ({"stance": "contextual"}, "supporting"),
        ({"claim_type": "location"}, "identity claim"),
        ({"candidate_institution_ref": None}, "no institution reference"),
        ({"local_target_missing": True}, "unavailable locally"),
    ],
)
def test_school_identity_copy_rejects_untrusted_or_non_supporting_claims(
    review_service: ReviewService,
    changes: dict[str, Any],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = review_service
    case = "school:" + school_digest("Test High School")
    local_target_missing = changes.get("local_target_missing", False)
    evidence_changes = {
        key: value for key, value in changes.items() if key != "local_target_missing"
    }
    payload = {
        "source_title": "School source",
        "source_type": "school-register",
        "source_url": URL,
        "retrieved_at": "2026-10-10T00:00:00+00:00",
        "candidate_institution_ref": "acara:1",
        "claim_type": "identity",
        "claim_value": {"institution": "Test High School"},
        "stance": "supports",
        "excerpt_or_note": "Evidence",
        "generated_by": "manual",
        **evidence_changes,
    }
    record = service.save_evidence(service.prepare_evidence(case, payload))
    decision_revision = service.review_revision()
    if local_target_missing:
        monkeypatch.setattr(service, "resolve_institution", lambda _reference: None)
    with pytest.raises(ValueError, match=message):
        service.copy_school_evidence_value(case, record.evidence_id)
    assert service.review_revision() == decision_revision


def test_school_research_context_includes_selected_candidates(
    review_service: ReviewService,
) -> None:
    case = "school:" + school_digest("Test High School")
    context = review_service.research_context(case, candidate_id="acara:1")
    assert context["selected_candidate"]["institution_ref"] == "acara:1"
    assert "postcode" in context["selected_candidate"]
    with pytest.raises(ValueError, match="Selected candidate"):
        review_service.research_context(case, candidate_id="acara:404")


@pytest.mark.parametrize(
    "case",
    ["school:" + school_digest("Test High School"), CASE],
)
def test_research_context_includes_selected_local_alternative(
    review_service: ReviewService, case: str
) -> None:
    # This is a legitimate local lookup result for a different query, but it is
    # not among the top scored candidates for the reviewed recorded name.
    context = review_service.research_context(case, candidate_id="acara:2")
    assert context["selected_candidate"]["institution_ref"] == "acara:2"
    assert any(row["institution_ref"] == "acara:2" for row in context["candidates"])
    assert context["selected_candidate"]["score"] is not None
    with pytest.raises(ValueError, match="Selected candidate"):
        review_service.research_context(case, candidate_id="acara:404")


def test_school_research_context_includes_effective_reviewed_target(
    review_service: ReviewService,
) -> None:
    service = review_service
    case = school_review_id("Test High School")
    event = ReviewEvent.from_dict(
        {
            "decision_id": "school-map-test",
            "review_id": case,
            "entity_type": "school",
            "action": "map",
            "payload": {
                "recorded_name": "Test High School",
                "institution_ref": "acara:1",
                "relationship_type": "direct",
            },
            "source_url": URL,
            "reviewer": "Reviewer",
            "reviewed_at": "2026-10-10",
            "recorded_at": "2026-10-10T00:00:00+00:00",
        }
    )
    service.log_path.write_bytes(encode_event(event))
    context = service.research_context(case, candidate_id="acara:2")
    assert context["assertion"]["effective_institution_ref"] == "acara:1"
    assert context["decision"]["payload"]["institution_ref"] == "acara:1"
    assert context["selected_candidate"]["institution_ref"] == "acara:2"


def test_assertion_research_context_does_not_duplicate_default_selected_target(
    review_service: ReviewService,
) -> None:
    service = review_service
    event = ReviewEvent.from_dict(
        {
            "decision_id": "education-map-test",
            "review_id": CASE,
            "entity_type": "member_education",
            "action": "map",
            "payload": {
                "aph_id": "TEST",
                "recorded_school_name": "Test High School",
                "institution_ref": "acara:1",
                "relationship_type": "direct",
            },
            "source_url": URL,
            "reviewer": "Reviewer",
            "reviewed_at": "2026-10-10",
            "recorded_at": "2026-10-10T00:00:00+00:00",
        }
    )
    service.log_path.write_bytes(encode_event(event))
    context = service.research_context(CASE)
    references = [row["institution_ref"] for row in context["candidates"]]
    assert references.count("acara:1") == 1
    assert len(references) == len(set(references))


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


def test_jobs_ignore_provider_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    context = {"review_id": CASE, "candidates": [{"institution_ref": "acara:1"}]}
    jobs = ResearchJobs({"fixture": FixtureProvider()})
    try:
        before = datetime.now(timezone.utc)
        identifier = jobs.start("fixture", context)
        jobs.get(identifier, CASE).future.result(timeout=5)
        output = jobs.result(identifier, CASE, {})
        assert output["status"] == "complete"
        assert (
            before
            <= datetime.fromisoformat(output["retrieved_at"])
            <= datetime.now(timezone.utc)
        )
        assert output["suggestions"][0]["retrieved_at"] == output["retrieved_at"]
    finally:
        jobs.close()


def test_offline_jobs_never_call_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    provider = Mock(spec=FixtureProvider)
    jobs = ResearchJobs({"fixture": provider})
    try:
        with pytest.raises(ValueError, match="offline"):
            jobs.start("fixture", {"review_id": CASE})
        provider.search.assert_not_called()
        assert jobs.jobs == {}
    finally:
        jobs.close()


def test_ui_provider_choice_inspection_preview_and_retention(
    review_service: ReviewService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
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
            headers={"Accept": "application/json"},
        )
        assert started.status_code == 200
        path = started.get_json()["detail_url"]
        identifier = path.rsplit("/", 1)[-1]
        manager.get(identifier, CASE).future.result(timeout=5)
        output = client.get(path)
        assert output.status_code == 200
        assert "&lt;script&gt;" in output.get_data(as_text=True)
        status = client.get(path + "/status").get_json()
        assert status["status"] == "complete"
        assert status["suggestions"][0]["source_url"] == URL
        draft = client.post(
            path + "/suggestions/0",
            data={"csrf_token": token, "source_inspected": "yes"},
        )
        assert draft.status_code == 200, draft.get_data(as_text=True)
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
        retained = service.evidence_records()[0]
        assert retained.generated_by == "agent-search"
        assert retained.retrieved_at == suggestion["retrieved_at"]
        assert retained.retrieved_at != FORGED_TIMESTAMP
        assert not service.log_path.exists()
        assert client.get("/readiness").status_code == 200
    finally:
        manager.close()


def test_jobs_and_signed_retention_reject_stale_decisions(
    review_service: ReviewService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
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
