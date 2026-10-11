from html.parser import HTMLParser

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.gui import create_app
from apemap.review.model import education_review_id, school_review_id
from apemap.review.service import ReviewService

pytest_plugins = ("tests.test_review_service",)


class WorkspaceControls(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.named_controls: dict[str, int] = {}
        self.details: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        name = values.get("name")
        if tag in {"input", "select", "textarea"} and name:
            self.named_controls[name] = self.named_controls.get(name, 0) + 1
        if tag == "details":
            self.details.append((str(values.get("class", "")), "open" in values))


class FixtureResearchProvider:
    model = "fixture"

    def search(self, context: dict[str, object]) -> dict[str, object]:
        return {"sources": [], "suggestions": []}


def test_assertion_workspace_puts_decision_controls_before_technical_details(
    review_service: ReviewService,
) -> None:
    review_id = education_review_id("TEST", "Test High School")
    response = (
        create_app(
            review_service, research_providers={"fixture": FixtureResearchProvider()}
        )
        .test_client()
        .get(f"/items/{review_id}")
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    parser = WorkspaceControls()
    parser.feed(html)

    assert "data-review-workspace" in html
    assert "data-comparison-table" in html
    assert "data-evidence-summary" in html
    assert 'data-workspace-action="preview"' in html
    assert 'form="review-decision"' in html
    assert 'name="field_relationship_type"' in html
    assert 'name="action"' in html
    assert "Current effective institution:" in html
    assert "Test High School · acara:1" in html
    assert "Current effective institution:" in html
    assert "Test High School · acara:1" in html
    assert parser.named_controls["action"] == 1
    assert parser.named_controls["field_relationship_type"] == 1
    assert any(
        "advanced-field-details" in css and not opened for css, opened in parser.details
    )
    assert "Advanced assertion payload and supersession fields" in html
    assert "Advanced assertion payload and supersession fields" in html
    assert f'data-review-id="{review_id}"' in html
    assert f'formaction="/items/{review_id}/research"' in html


def test_school_research_panel_uses_the_school_review_id_and_decision_form(
    review_service: ReviewService,
) -> None:
    review_id = school_review_id("Test High School")
    response = (
        create_app(
            review_service,
            research_providers={"fixture": FixtureResearchProvider()},
        )
        .test_client()
        .get(f"/items/{review_id}")
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'data-review-id="{review_id}"' in html
    assert f'formaction="/items/{review_id}/research"' in html
    assert 'data-research-provider form="school-draft"' in html


def test_school_and_assertion_templates_keep_save_next_preview_gated() -> None:
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "apemap/review/templates"
    school = (templates / "school.html").read_text(encoding="utf-8")
    item = (templates / "item.html").read_text(encoding="utf-8")

    for source in (school, item):
        assert 'name="advance" value="next"' in source
        assert 'name="navigation_token"' in source
        assert 'name="preview_token"' in source
        assert 'data-workspace-action="save"' in source

    assert 'type="submit" name="choose_ref"' in school
    assert 'form="school-draft" formnovalidate' in school
    assert (
        'form="school-draft" formnovalidate data-workspace-action="preview"' in school
    )
    assert 'name="field_relationship_type" data-resolution-field required' in school
    assert "Candidate score reasons" in school
    assert "Conflicting evidence:" in school
    assert school.index('aria-label="School candidates"') < school.index(
        "comparison_table(primary_rows"
    )
    assert "['name', 'locality', 'acara identifier']" in school
    assert "comparison_table(additional_rows)" in school
    assert "data-additional-comparison-summary" in school
    assert school.index('data-workspace-action="preview"') < school.index(
        "Optional source and notes"
    )
    assert "data-assertion-draft data-comparison-url=" in item
    assert "data-selected-assertion" in item
    assert (
        'name="choose_ref"' in item and 'form="review-decision" formnovalidate' in item
    )
    assert item.index('aria-label="Assertion school candidates"') < item.index(
        "comparison_table(workspace_rows"
    )
    assert item.index("comparison_table(workspace_rows") < item.index(
        "summary>Assertion context, research, and decision history"
    )
