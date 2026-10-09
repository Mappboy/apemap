"""Offline research provenance, explained ranking, and guided decision controls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("flask", reason="Install the review-ui extra for GUI tests")
pytest.importorskip("waitress", reason="Install the review-ui extra for GUI tests")

from apemap.review.evidence import EvidenceRecord
from apemap.review.gui import _sign_preview, create_app
from apemap.review.model import education_review_id
from apemap.review.scoring import score_candidates
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_gui import (
    FixtureService,
    Inputs,
    SCHOOL_ID,
    SOURCE,
    form,
    save_form,
)
from tests.test_review_service import review_service as review_service

EDUCATION_ID = education_review_id("abc", "Fixture School")


def research_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "candidate_institution_ref": "acara:123",
        "source_title": "School history <script>alert(1)</script>",
        "source_type": "school-history",
        "source_url": SOURCE,
        "retrieved_at": "2026-10-09T10:00:00+11:00",
        "claim_type": "identity",
        "claim_value": "The recorded name refers to this school",
        "stance": "supports",
        "excerpt_or_note": "A dated school history identifies the campus.",
        "generated_by": "manual",
        **overrides,
    }


class EvidenceFixtureService(FixtureService):
    def __init__(self) -> None:
        super().__init__()
        self.evidence: list[EvidenceRecord] = []
        self.evidence_revision = "empty-evidence"
        self.default_available = True
        self.rows.append(
            {
                "review_id": EDUCATION_ID,
                "entity_type": "member_education",
                "candidate_id": "candidate-attendance",
                "payload": {"aph_id": "abc", "recorded_school_name": "Fixture School"},
                "evidence": {"source_url": SOURCE, "attended_status": "graduated"},
                "parliaments": [47],
                "status": "pending",
            }
        )

    def show(self, review_id: str) -> dict[str, Any]:
        item = super().show(review_id)
        source = {
            "review_id": EDUCATION_ID,
            "recorded_name": "Fixture School",
            "source_url": SOURCE,
            "years_attended": "1980–1985",
            "location": "Original source location",
            "current_resolution": {
                "institution_ref": "acara:123",
                "institution_name": "Fixture School",
                "state": "TAS",
                "suburb": "Hobart",
                "resolution_scope": "assertion",
                "relationship_type": "direct",
                "status": "resolved",
            },
        }
        item["context"]["members"][0]["education"] = [
            source,
            {
                **source,
                "source_url": "https://example.org/second-bio",
                "years_attended": "1981–1985",
            },
        ]
        item["retained_evidence"] = [record.to_dict() for record in self.evidence]
        item["evidence_revision"] = self.evidence_revision
        item["ranked_candidates"] = score_candidates(
            review_id,
            [{**row, "recorded_name": "Fixture School"} for row in self.institutions],
            self.evidence,
        )
        return item

    def prepare_evidence(
        self, review_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        record = EvidenceRecord.from_dict({**payload, "review_id": review_id})
        return {
            "kind": "evidence",
            "review_id": review_id,
            "record": record.to_dict(),
            "revision": self.evidence_revision,
            "source_revision": self.source_revision,
            "validation": ["Immutable research evidence validated"],
        }

    def save_evidence(self, preview: dict[str, Any]) -> EvidenceRecord:
        if (
            preview["revision"] != self.evidence_revision
            or preview["source_revision"] != self.source_revision
        ):
            raise StaleReviewError("Evidence inputs changed")
        record = EvidenceRecord.from_dict(preview["record"])
        self.evidence.append(record)
        self.evidence_revision = str(len(self.evidence))
        self.source_revision = "fixture-source-" + self.evidence_revision
        return record

    def prepare(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        preview = super().prepare(*args, **kwargs)
        if preview["event"]["entity_type"] == "school":
            preview["default_application"] = {
                "available": self.default_available,
                "assertions": [],
            }
        return preview


@pytest.fixture
def evidence_gui() -> tuple[Any, EvidenceFixtureService]:
    service = EvidenceFixtureService()
    app = create_app(service)
    app.config["TESTING"] = True
    return app.test_client(), service


def research_form(
    client: Any, review_id: str = EDUCATION_ID, **overrides: Any
) -> dict[str, Any]:
    csrf = Inputs(client.get(f"/items/{review_id}").get_data(as_text=True)).values[
        "csrf_token"
    ]
    return {
        "csrf_token": csrf,
        **{
            "evidence_" + key: value
            for key, value in research_payload(**overrides).items()
        },
    }


def evidence_save_form(response: Any) -> dict[str, str]:
    values = Inputs(response.get_data(as_text=True)).values
    return {key: values[key] for key in ("csrf_token", "evidence_preview_token")}


def test_ranked_cards_show_ties_locations_and_grouped_source_records(
    evidence_gui: tuple[Any, EvidenceFixtureService],
) -> None:
    client, _service = evidence_gui
    html = client.get(f"/items/{EDUCATION_ID}").get_data(as_text=True)
    assert html.count("Ambiguous exact-name match") == 2
    assert "Hobart · TAS" in html and "Melbourne · VIC" in html
    assert (
        "Scores are advisory" in html
        and "Parliamentary state is not school-location evidence" in html
    )
    assert html.count('<th scope="row">') == 1
    assert "2 source record(s)" in html
    assert "1980–1985" in html and "1981–1985" in html
    assert (
        "Original source location" in html and "https://example.org/second-bio" in html
    )
    assert '<option value="map" selected>' in html
    assert 'form="review-decision" name="choose_ref"' in html
    assert '<option value="accept" selected>' in client.get(
        "/new/member_education?aph_id=abc"
    ).get_data(as_text=True)


@pytest.mark.parametrize("override", [False, True])
def test_comparison_preserves_distinct_source_matches_and_deduplicates_override(
    evidence_gui: tuple[Any, EvidenceFixtureService],
    monkeypatch: pytest.MonkeyPatch,
    override: bool,
) -> None:
    client, service = evidence_gui
    original = service.show

    def differing_matches(review_id: str) -> dict[str, Any]:
        item = original(review_id)
        education = item["context"]["members"][0]["education"]
        first = education[0]["current_resolution"]
        if override:
            education[1]["current_resolution"] = dict(first)
        else:
            first.update(resolution_scope="source", status="pending")
            education[1]["current_resolution"] = {
                **first,
                "institution_ref": "acara:456",
                "suburb": "Melbourne",
                "state": "VIC",
            }
        return item

    monkeypatch.setattr(service, "show", differing_matches)
    html = client.get(f"/items/{EDUCATION_ID}").get_data(as_text=True)
    comparison = html.split("<td data-current-resolutions>", 1)[1].split("</td>", 1)[0]
    assert html.count('<th scope="row">') == 1
    assert "2 source record(s)" in html
    assert "1980–1985" in html and "1981–1985" in html
    assert "acara:123" in comparison and "Hobart · TAS" in comparison
    if override:
        assert comparison.count("data-resolution-summary") == 1
        assert "Multiple source matches" not in comparison
        assert "acara:456" not in comparison
    else:
        assert comparison.count("data-resolution-summary") == 2
        assert "Multiple source matches; resolve this assertion" in comparison
        assert "acara:456" in comparison and "Melbourne · VIC" in comparison


def test_research_preview_and_signed_retention_never_write_a_decision(
    evidence_gui: tuple[Any, EvidenceFixtureService],
) -> None:
    client, service = evidence_gui
    response = client.post(
        f"/items/{EDUCATION_ID}/evidence/preview",
        data=research_form(
            client, match_strength="0.75", claim_value_json='{"campus": "Hobart"}'
        ),
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Validated evidence preview" in html
    assert (
        "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        and "<script>alert(1)</script>" not in html
    )
    assert not service.evidence and not service.saved and not service.prepared
    response = client.post(
        f"/items/{EDUCATION_ID}/evidence/save", data=evidence_save_form(response)
    )
    assert response.status_code == 303
    assert len(service.evidence) == 1 and not service.saved
    record = service.evidence[0]
    assert record.match_strength == 0.75 and record.to_dict()["claim_value"] == {
        "campus": "Hobart"
    }
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Research evidence retained" in html and "Supporting sources" in html
    assert 'name="evidence_refs"' in html and record.evidence_id in html


def test_contradicting_sources_and_components_remain_visible(
    evidence_gui: tuple[Any, EvidenceFixtureService],
) -> None:
    client, service = evidence_gui
    service.evidence = [
        EvidenceRecord.from_dict(
            {
                "review_id": EDUCATION_ID,
                **research_payload(stance="contradicts", geographic_relevance=1),
            }
        )
    ]
    html = client.get(f"/items/{EDUCATION_ID}").get_data(as_text=True)
    assert "Contradicting sources" in html
    assert "-12.0 points" in html and "28.0 / 100" in html
    assert "Explicit evidence dimensions; counted once per source URL" in html


def test_candidate_selection_only_fills_the_decision_draft(
    evidence_gui: tuple[Any, EvidenceFixtureService],
) -> None:
    client, service = evidence_gui
    response = client.post(
        f"/items/{EDUCATION_ID}/preview",
        data=form(
            client,
            EDUCATION_ID,
            action="map",
            payload_mode="guided",
            field_aph_id="abc",
            field_recorded_school_name="Fixture School",
            choose_ref="acara:456",
        ),
    )
    assert response.status_code == 200
    assert 'name="field_institution_ref" type="text"' in response.get_data(as_text=True)
    assert (
        Inputs(response.get_data(as_text=True)).values["field_institution_ref"]
        == "acara:456"
    )
    assert not service.prepared and not service.saved and not service.evidence


@pytest.mark.parametrize("payload_mode", ["guided", "json"])
def test_evidence_refs_overlay_only_guided_decisions(
    evidence_gui: tuple[Any, EvidenceFixtureService], payload_mode: str
) -> None:
    client, service = evidence_gui
    record = EvidenceRecord.from_dict({"review_id": EDUCATION_ID, **research_payload()})
    service.evidence = [record]
    response = client.post(
        f"/items/{EDUCATION_ID}/preview",
        data=form(
            client,
            EDUCATION_ID,
            action="map",
            payload_mode=payload_mode,
            payload=json.dumps(
                {
                    "aph_id": "abc",
                    "recorded_school_name": "Fixture School",
                    "institution_ref": "acara:123",
                    "relationship_type": "direct",
                }
            ),
            field_aph_id="abc",
            field_recorded_school_name="Fixture School",
            field_institution_ref="acara:123",
            field_relationship_type="direct",
            evidence_selection="1",
            evidence_refs=record.evidence_id,
        ),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = service.prepared[-1]["event"]["payload"]
    assert payload.get("evidence_refs", []) == (
        [record.evidence_id] if payload_mode == "guided" else []
    )


@pytest.mark.parametrize("change", ["tamper", "stale", "wrong-case", "decision-token"])
def test_retention_rejects_invalid_or_stale_previews(
    evidence_gui: tuple[Any, EvidenceFixtureService], change: str
) -> None:
    client, service = evidence_gui
    preview = client.post(
        f"/items/{EDUCATION_ID}/evidence/preview", data=research_form(client)
    )
    data = evidence_save_form(preview)
    path = f"/items/{EDUCATION_ID}/evidence/save"
    if change == "tamper":
        data["evidence_preview_token"] += "0"
    elif change == "stale":
        service.source_revision = "changed"
    elif change == "wrong-case":
        path = f"/items/{SCHOOL_ID}/evidence/save"
    else:
        decision = client.post(f"/items/{SCHOOL_ID}/preview", data=form(client))
        data["evidence_preview_token"] = save_form(decision)["preview_token"]
    response = client.post(path, data=data)
    assert response.status_code == (409 if change == "stale" else 400)
    assert not service.evidence and not service.saved


def test_evidence_token_cannot_save_a_decision_and_csrf_is_required(
    evidence_gui: tuple[Any, EvidenceFixtureService],
) -> None:
    client, service = evidence_gui
    preview = client.post(
        f"/items/{EDUCATION_ID}/evidence/preview", data=research_form(client)
    )
    data = evidence_save_form(preview)
    assert (
        client.post(
            f"/items/{EDUCATION_ID}/save",
            data={
                "csrf_token": data["csrf_token"],
                "preview_token": data["evidence_preview_token"],
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/items/{EDUCATION_ID}/evidence/save",
            data={"evidence_preview_token": data["evidence_preview_token"]},
        ).status_code
        == 403
    )
    assert not service.evidence and not service.saved


@pytest.mark.parametrize(
    "invalid",
    [{"claim_value_json": "{"}, {"source_quality": "many"}, {"source_quality": "2"}],
)
def test_evidence_validation_preserves_input_and_does_not_retain(
    evidence_gui: tuple[Any, EvidenceFixtureService], invalid: dict[str, str]
) -> None:
    client, service = evidence_gui
    response = client.post(
        f"/items/{EDUCATION_ID}/evidence/preview", data=research_form(client, **invalid)
    )
    assert response.status_code == 400
    assert "School history &lt;script&gt;" in response.get_data(as_text=True)
    assert not service.evidence and not service.saved


def test_school_default_without_assertion_effects_has_no_save_and_rejects_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apemap.review import gui as module

    secret = b"test signing secret"
    monkeypatch.setattr(module.secrets, "token_bytes", lambda _count: secret)
    service = EvidenceFixtureService()
    service.default_available = False
    client = create_app(service).test_client()
    response = client.post(f"/items/{SCHOOL_ID}/preview", data=form(client))
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Run a review build and inspect the affected assertions" in html
    assert "data-save-school" not in html
    assert (
        client.post(
            f"/items/{SCHOOL_ID}/save",
            data={
                "csrf_token": Inputs(html).values["csrf_token"],
                "preview_token": _sign_preview(service.prepared[-1], secret),
            },
        ).status_code
        == 400
    )
    assert not service.saved


@pytest.mark.integration
def test_real_service_signed_research_retention_and_referenced_resolution(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import service as module

    service = review_service
    monkeypatch.setattr(module, "RAW_APH_DIR", tmp_path / "aph")
    monkeypatch.setattr(module, "RAW_WIKIMEDIA_DIR", tmp_path / "wiki")
    before = hashlib.sha256(service.db_path.read_bytes()).hexdigest()
    review_id = education_review_id("TEST", "Test High School")
    client = create_app(service).test_client()
    preview = client.post(
        f"/items/{review_id}/evidence/preview",
        data=research_form(
            client, review_id, candidate_institution_ref="acara:2", match_strength="1"
        ),
    )
    assert preview.status_code == 200, preview.get_data(as_text=True)
    assert not service.evidence_path.exists() and not service.log_path.exists()
    saved = client.post(
        f"/items/{review_id}/evidence/save", data=evidence_save_form(preview)
    )
    assert saved.status_code == 303, saved.get_data(as_text=True)
    assert not service.log_path.exists()
    evidence = service.retained_evidence(review_id)[0]
    response = client.post(
        f"/items/{review_id}/preview",
        data=form(
            client,
            review_id,
            action="map",
            payload_mode="guided",
            payload="{}",
            field_aph_id="TEST",
            field_recorded_school_name="Test High School",
            field_institution_ref="acara:2",
            field_relationship_type="direct",
            evidence_selection="1",
            evidence_refs=evidence["evidence_id"],
            reviewer="Offline reviewer",
        ),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    saved = client.post(f"/items/{review_id}/save", data=save_form(response))
    assert saved.status_code == 303, saved.get_data(as_text=True)
    assert service.events()[0].payload["evidence_refs"] == [evidence["evidence_id"]]
    assert hashlib.sha256(service.db_path.read_bytes()).hexdigest() == before
