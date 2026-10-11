from __future__ import annotations

from html.parser import HTMLParser
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.gui import _read_preview, _sign_member_batch_preview, create_app
from apemap.review.service import ReviewService
from tests.test_review_member_batch import member_cache as member_cache
from tests.test_review_service import review_service as review_service


class MemberWorkspace(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.radios: dict[str, list[tuple[str, bool]]] = {}
        self.details: list[tuple[str, bool]] = []
        self.current_details: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("type") == "radio":
            self.radios.setdefault(str(values.get("name")), []).append(
                (str(values.get("value")), "checked" in values)
            )
        if tag == "details":
            self.details.append((str(values.get("class", "")), "open" in values))
            self.current_details.append(len(self.details) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag == "details" and self.current_details:
            self.current_details.pop()


def test_member_workspace_has_explicit_field_choices_and_collapsed_details(
    review_service: ReviewService,
    member_cache: object,
) -> None:
    html = (
        create_app(review_service)
        .test_client()
        .get("/members/TEST")
        .get_data(as_text=True)
    )
    parsed = MemberWorkspace()
    parsed.feed(html)

    assert "data-review-workspace" in html
    assert 'data-workspace-action="preview"' in html
    assert parsed.radios["action_gender"] == [
        ("accept", False),
        ("reject", False),
        ("research", False),
        ("unchanged", False),
    ]
    assert "Evidence, overrides and decision history" in html
    assert any(
        "advanced-field-details" in css and not opened for css, opened in parsed.details
    )
    assert "Related records (" in html
    assert 'data-workspace-action="save"' not in html


def test_member_workspace_preview_keeps_save_disabled_after_draft_edit(
    review_service: ReviewService,
    member_cache: object,
) -> None:
    client = create_app(review_service).test_client()
    token_html = client.get("/members/TEST").get_data(as_text=True)
    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', token_html)
    assert csrf_match is not None
    csrf = csrf_match.group(1)
    html = client.post(
        "/members/TEST/preview",
        data={"csrf_token": csrf, "action_gender": "accept", "reviewer": "Reviewer"},
    ).get_data(as_text=True)
    assert 'id="save-batch-btn"' in html
    assert 'id="save-next-btn"' in html
    assert 'value="next" data-workspace-action="save" id="save-next-btn"' in html


def test_signed_batch_keeps_unresolved_fields_for_save_next_notice() -> None:
    preview = {
        "aph_id": "TEST",
        "drafts": [],
        "events": [],
        "event_ids": [],
        "revision": "fixture",
        "source_revision": "fixture",
        "effects": {},
        "changes": [],
        "validation_outcome": "valid",
        "unresolved_fields": ["gender", "date_of_birth", "wikidata_id"],
    }
    secret = b"offline-test-secret"
    token = _sign_member_batch_preview(preview, secret)

    assert _read_preview(token, secret)["unresolved_fields"] == [
        "gender",
        "date_of_birth",
        "wikidata_id",
    ]
