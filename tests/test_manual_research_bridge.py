"""Quick cited research persists proposals through the existing batch reviewer."""

from __future__ import annotations

import re
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
from typing import Any

import pytest

pytest.importorskip("flask")

from apemap.review.gui import create_app
from apemap.review.model import school_review_id
from apemap.review.research_batches import ResearchBatches
from apemap.review.service import ReviewService
from apemap.review.integration import capture_review_sources
from apemap.db import get_connection
from tests.test_research_batches import accept
from tests.test_review_service import review_service as review_service


class ManualSchoolProvider:
    model = "fixture-model"
    provider = "fixture"

    def search(self, context: dict[str, Any]) -> dict[str, Any]:
        values = {
            "name": "Test Historical School",
            "country": "Australia",
            "status": "closed",
        }
        return {
            "sources": [
                {"url": "https://example.org/history", "title": "School history"}
            ],
            "suggestions": [
                {
                    "candidate_institution_ref": None,
                    "source_url": "https://example.org/history",
                    "claim_type": "institution_fact",
                    "claim_value": {"field": field, "value": value},
                    "stance": "supports",
                    "excerpt_or_note": f"School history establishes {field}: {value}.",
                }
                for field, value in values.items()
            ],
            "institution_candidate": {
                **values,
                "evidence_refs": {field: [index] for index, field in enumerate(values)},
            },
        }


def start_research(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, str, str]:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    app = create_app(service, research_providers={"fixture": ManualSchoolProvider()})
    client = app.test_client()
    review_id = school_review_id("Test High School")
    html = client.get(f"/items/{review_id}").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert csrf is not None
    started = client.post(
        f"/items/{review_id}/research",
        data={"csrf_token": csrf.group(1), "provider_id": "fixture"},
        headers={"Accept": "application/json"},
    )
    assert started.status_code == 200
    job_id = started.get_json()["job_id"]
    app.extensions["research_jobs"].get(job_id, review_id).future.result(timeout=5)
    return app, client, job_id, csrf.group(1)


def test_save_quick_manual_proposal_is_persistent_and_retry_safe(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    review_id = school_review_id("Test High School")
    try:
        responses = [
            client.post(
                f"/items/{review_id}/research/{job_id}/manual-proposals/0",
                data={"csrf_token": csrf},
                headers={"Accept": "application/json"},
            )
            for _ in range(2)
        ]
        assert all(response.status_code == 200 for response in responses), [
            response.get_json() for response in responses
        ]
        saved = responses[0].get_json()
        assert responses[1].get_json()["batch_id"] == saved["batch_id"]
        state = ResearchBatches(review_service).show(saved["batch_id"])
        proposal = state["proposals"][0]
        assert (
            proposal["manual_candidate"]["institution_ref"]
            == "manual:test-historical-school"
        )
        assert proposal["attempts"] == []
        assert state["manifest"]["provenance"]["model"] == "fixture-model"
        assert not state["reviews"]
        assert not review_service.log_path.exists()
        assert not review_service.evidence_path.exists()
        html = client.get(saved["review_url"]).get_data(as_text=True)
        assert "Proposed manual institution" in html
        assert "Test Historical School" in html
    finally:
        app.extensions["research_jobs"].close()


def test_nojs_manual_proposal_save_redirects_to_review(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    review_id = school_review_id("Test High School")
    try:
        page = client.get(f"/items/{review_id}/research/{job_id}")
        assert "Save manual institution proposal" in page.get_data(as_text=True)
        response = client.post(
            f"/items/{review_id}/research/{job_id}/manual-proposals/0",
            data={"csrf_token": csrf},
        )
        assert response.status_code == 303
        assert response.headers["Location"].startswith("/research-batches/")
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()


def test_stale_quick_manual_proposal_cannot_be_saved(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    review_id = school_review_id("Test High School")
    try:
        source = review_service.external_dir / "school-location-2025.csv"
        source.write_text(
            source.read_text() + "3,New School,Government,Secondary,TAS,-42,147\n"
        )
        response = client.post(
            f"/items/{review_id}/research/{job_id}/manual-proposals/0",
            data={"csrf_token": csrf},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 409
        assert "changed" in response.get_json()["error"].lower()
        assert not ResearchBatches(review_service).list_batches()
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()


def test_quick_manual_proposal_requires_csrf_and_exact_case(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    review_id = school_review_id("Test High School")
    try:
        assert (
            client.post(
                f"/items/{review_id}/research/{job_id}/manual-proposals/0"
            ).status_code
            == 403
        )
        response = client.post(
            f"/items/{school_review_id('Other High School')}/research/{job_id}/manual-proposals/0",
            data={"csrf_token": csrf},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 400
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()


def test_actual_manual_proposal_browser_flow(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("APEMAP_BROWSER_TESTS") != "1":
        pytest.skip("Set APEMAP_BROWSER_TESTS=1 with Playwright and Edge installed")
    from werkzeug.serving import make_server

    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    executable = shutil.which("node")
    assert executable is not None, "Node.js is required for the browser check"
    app = create_app(
        review_service, research_providers={"fixture": ManualSchoolProvider()}
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                executable,
                str(Path(__file__).with_name("manual_research_page.test.cjs")),
                f"http://127.0.0.1:{server.server_port}/items/{school_review_id('Test High School')}",
                os.environ.get("APEMAP_BROWSER_SCREENSHOTS", ""),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        events = review_service.events()
        assert len(events) == 2
        assert (
            next(
                event for event in events if event.entity_type == "school"
            ).effective_action
            == "research"
        )
        assert not next(
            event for event in events if event.entity_type == "school"
        ).payload.get("institution_ref")
        assert (
            len(
                [
                    record
                    for record in review_service.evidence_records()
                    if record.generated_by == "agent-search"
                ]
            )
            == 3
        )
    finally:
        app.extensions["research_jobs"].close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_source_change_during_quick_save_cannot_rebind_old_finding(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    original = ResearchBatches.prepare_manual_proposal

    def changed(self: ResearchBatches, *args: Any, **kwargs: Any) -> Any:
        source = review_service.external_dir / "school-location-2025.csv"
        source.write_text(
            source.read_text() + "3,Changed School,Government,Secondary,TAS,-42,147\n"
        )
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ResearchBatches, "prepare_manual_proposal", changed)
    try:
        response = client.post(
            f"/items/{school_review_id('Test High School')}/research/{job_id}/manual-proposals/0",
            data={"csrf_token": csrf},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 409
        assert not ResearchBatches(review_service).list_batches()
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()


def test_multi_member_manual_finding_requires_individual_resolution(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "INSERT INTO members (member_id, family_name, given_name, display_name, aph_id) VALUES ('aph-other', 'Person', 'Other', 'Other Person', 'OTHER')"
        )
        conn.execute(
            "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin) VALUES ('edu-other', 'aph-other', 'acara-1', 'secondary', 'attended_unspecified', 'https://example.org/other', '2026-10-02T00:00:00+10:00', 'verified', 'Test High School', 'aph')"
        )
        capture_review_sources(conn)
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    try:
        response = client.post(
            f"/items/{school_review_id('Test High School')}/research/{job_id}/manual-proposals/0",
            data={"csrf_token": csrf},
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 200, response.get_json()
        batches = ResearchBatches(review_service)
        state = batches.show(response.get_json()["batch_id"])
        assert state["manifest"]["provenance"]["requires_individual_resolution"] is True
        item = state["proposals"][0]
        with pytest.raises(ValueError, match="individual"):
            accept(
                batches,
                state["manifest"]["batch_id"],
                item,
                institution_action="create_map",
            )
        accept(
            batches,
            state["manifest"]["batch_id"],
            item,
            institution_action="create_only",
        )
        assert not review_service.log_path.exists()
    finally:
        app.extensions["research_jobs"].close()


def test_successful_partial_research_can_be_inspected_but_not_saved_as_manual_proposal(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client, job_id, csrf = start_research(review_service, monkeypatch)
    jobs = app.extensions["research_jobs"]
    original = jobs.result

    def partial(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {**original(*args, **kwargs), "status": "partial"}

    monkeypatch.setattr(jobs, "result", partial)
    rid = school_review_id("Test High School")
    try:
        inspected = client.post(
            f"/items/{rid}/research/{job_id}/suggestions/0",
            data={"csrf_token": csrf, "source_inspected": "yes"},
            headers={"Accept": "application/json"},
        )
        assert inspected.status_code == 200
        preview = client.post(
            f"/items/{rid}/evidence/preview",
            data={
                **inspected.get_json()["evidence_values"],
                "csrf_token": csrf,
                "source_inspected": "yes",
            },
            headers={"Accept": "application/json"},
        )
        assert preview.status_code == 200, preview.get_json()
        saved = client.post(
            f"/items/{rid}/research/{job_id}/manual-proposals/0",
            data={"csrf_token": csrf},
            headers={"Accept": "application/json"},
        )
        assert saved.status_code == 400
        assert not review_service.log_path.exists()
    finally:
        jobs.close()
