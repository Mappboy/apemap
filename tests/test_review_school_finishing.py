"""Selected-candidate explanations and direct per-member resolution paths."""

from typing import Any
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.evidence import EvidenceRecord
from apemap.review.gui import create_app
from apemap.review.model import education_review_id
from apemap.review.workspace import school_candidate_evidence
from tests.test_review_evidence_gui import EvidenceFixtureService, research_payload
from tests.test_review_gui import SCHOOL_ID, form


class SplitSchoolService(EvidenceFixtureService):
    def show(self, review_id: str) -> dict[str, Any]:
        item = super().show(review_id)
        item["context"]["members"].append(
            {
                "aph_id": "other",
                "display_name": "Other Member",
                "education": [
                    {
                        "review_id": education_review_id("other", "Fixture School"),
                        "recorded_name": "Fixture School",
                    }
                ],
            }
        )
        return item


def explained_service() -> SplitSchoolService:
    service = SplitSchoolService()
    service.evidence = [
        EvidenceRecord.from_dict(
            {
                **research_payload(
                    source_title="School history",
                    claim_value="History confirms the Hobart campus",
                    excerpt_or_note="History confirms the Hobart campus",
                ),
                "review_id": SCHOOL_ID,
            }
        ),
        EvidenceRecord.from_dict(
            {
                **research_payload(
                    candidate_institution_ref="acara:456",
                    stance="contradicts",
                    source_title="Other campus history",
                    claim_value="Melbourne campus conflicts with this attendance",
                    excerpt_or_note="Melbourne campus conflicts with this attendance",
                ),
                "review_id": SCHOOL_ID,
            }
        ),
    ]
    return service


def test_school_explanation_and_split_path_precede_secondary_tools() -> None:
    service = explained_service()
    client = create_app(service).test_client()
    response = client.post(
        f"/items/{SCHOOL_ID}/preview",
        data={**form(client), "choose_ref": "acara:123"},
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-inline-evidence aria-label="Selected candidate evidence"' in html
    brief = html.split('aria-label="Selected candidate evidence">')[1].split(
        '<details class="workspace-evidence"'
    )[0]
    assert "History confirms the Hobart campus" in brief
    assert "School history" in brief
    assert "Unknown:" in brief
    assert "Melbourne campus conflicts" not in brief
    assert html.index("data-inline-evidence") < html.index("School candidates")
    assert html.index('href="#individual-school-resolution"') < html.index(
        "Related parliamentarians and attendance history"
    )
    split = html.split('id="individual-school-resolution"')[1].split("</section>")[0]
    for aph_id in ("abc", "other"):
        assert f"/items/{education_review_id(aph_id, 'Fixture School')}" in split
    assert "does not create a school-wide mapping" in split
    assert "Full evidence provenance" in html
    assert not service.saved


def test_comparison_refresh_does_not_mix_candidate_evidence() -> None:
    service = explained_service()
    client = create_app(service).test_client()
    for reference, expected, excluded in (
        ("acara:123", "School history", "Other campus history"),
        ("acara:456", "Other campus history", "School history"),
    ):
        response = client.get(
            f"/items/{SCHOOL_ID}/comparison", query_string={"candidate_ref": reference}
        )
        assert response.status_code == 200
        records = response.get_json()["evidence"]
        titles = [record.get("source_title") for record in records]
        assert expected in titles
        assert excluded not in titles
        assert any(
            "not attendance evidence" in row.get("summary", "") for row in records
        )
    assert not service.saved


def test_candidate_unspecified_context_is_preserved_without_other_targets() -> None:
    item = {
        "retained_evidence": [
            {"stance": "contextual", "summary": "Attendance dates unknown"},
            {"stance": "supports", "candidate_institution_ref": "acara:2"},
        ]
    }
    assert school_candidate_evidence(item, "acara:1") == [item["retained_evidence"][0]]


@pytest.mark.integration
def test_actual_school_page_desktop_and_narrow_layout() -> None:
    if os.environ.get("APEMAP_BROWSER_TESTS") != "1":
        pytest.skip("Set APEMAP_BROWSER_TESTS=1 with Playwright and Edge installed")
    from werkzeug.serving import make_server

    executable = shutil.which("node")
    assert executable is not None, "Node.js is required for the browser check"
    server = make_server("127.0.0.1", 0, create_app(explained_service()))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                executable,
                str(Path(__file__).with_name("review_school_page.test.cjs")),
                f"http://127.0.0.1:{server.server_port}/items/{SCHOOL_ID}",
                os.environ.get("APEMAP_BROWSER_SCREENSHOTS", ""),
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
