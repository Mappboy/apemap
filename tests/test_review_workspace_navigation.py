"""Offline route regressions for filtered workspace navigation and save-next."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("flask", reason="Install the review-ui extra for GUI tests")
pytest.importorskip("waitress", reason="Install the review-ui extra for GUI tests")

from apemap.review.gui import create_app
from apemap.review.model import education_review_id
from apemap.review.store import StaleReviewError
from tests.test_review_gui import FixtureService, Inputs
from tests.test_review_lists import ListService, member_row


class Links(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "a":
            self._current = values
            self._current["text"] = ""

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


class NavService(ListService):
    """List fixture whose successful member save changes queue membership."""

    def __init__(self) -> None:
        super().__init__()
        self.after_save: Any = None
        self.save_error: Exception | None = None

    def save_batch(self, preview: dict[str, Any]) -> list[Any]:
        if self.save_error is not None:
            raise self.save_error
        events = super().save_batch(preview)
        for event in events:
            for row in self.rows:
                if row["review_id"] == event.review_id:
                    row["status"] = "rejected"
        if self.after_save is not None:
            self.after_save(self)
        return events


def nav_client(service: NavService) -> Any:
    app = create_app(service)
    app.config["TESTING"] = True
    return app.test_client()


def member_ids(count: int) -> list[dict[str, Any]]:
    return [member_row(f"m-{i:03}", f"Person {i:03}") for i in range(count)]


def preview_member(
    client: Any, aph_id: str, *, query: str = "", value: str = ""
) -> Any:
    detail = client.get(f"/members/{aph_id}{query}")
    csrf = Inputs(detail.get_data(as_text=True)).values["csrf_token"]
    return client.post(
        f"/members/{aph_id}/preview{query}",
        data={
            "csrf_token": csrf,
            "action_gender": "reject",
            "notes_gender": "Reviewed in offline fixture",
            "value_gender": value,
        },
    )


def test_filtered_counts_and_detail_positions_use_full_queue_across_pages() -> None:
    service = NavService()
    service.rows = member_ids(53)
    service.rows.append(member_row("m-000", "Person 000", field="date_of_birth"))
    client = nav_client(service)
    filter_query = "?parliament=48&q=Person&preset=member_corrections"

    first = client.get("/members" + filter_query + "&page=1").get_data(as_text=True)
    second = client.get("/members" + filter_query + "&page=2").get_data(as_text=True)
    detail_query = (
        "?queue_view=members&queue_parliament=48&queue_q=Person"
        "&queue_preset=member_corrections&queue_page=2"
    )
    detail = client.get("/members/m-050" + detail_query).get_data(as_text=True)

    assert "53 matching members" in first
    assert "53 matching members" in second
    assert first.count("2 correction fields") == 1
    assert "2 correction fields" not in second
    assert "51 of 53" in detail


def test_save_next_uses_signed_pre_save_successors_that_survive() -> None:
    service = NavService()
    service.rows = member_ids(5)

    def change_queue(current: NavService) -> None:
        for row in current.rows:
            if row["review_id"].startswith("member:m-001:"):
                row["status"] = "accepted"
        current.rows.append(member_row("m-999", "Aardvark"))

    service.after_save = change_queue
    client = nav_client(service)
    query = "?queue_view=members&queue_q=Person&queue_parliament=48"
    response = preview_member(client, "m-000", query=query)
    values = Inputs(response.get_data(as_text=True)).values
    assert response.status_code == 200

    saved = client.post(
        "/members/m-000/save" + query,
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
            "navigation_token": values["navigation_token"],
            "action_gender": "reject",
            "notes_gender": "Reviewed in offline fixture",
            "advance": "next",
        },
    )

    assert saved.status_code == 303
    assert urlsplit(saved.headers["Location"]).path == "/members/m-002"
    assert parse_qs(urlsplit(saved.headers["Location"]).query)["queue_q"] == ["Person"]
    assert [event.review_id for event in service.saved] == ["member:m-000:gender"]


def test_save_next_exhaustion_returns_filtered_queue_without_wrapping() -> None:
    service = NavService()
    service.rows = [*member_ids(1), member_row("other", "Other person")]
    client = nav_client(service)
    response = preview_member(
        client, "m-000", query="?queue_view=members&queue_q=Person+000"
    )
    values = Inputs(response.get_data(as_text=True)).values

    saved = client.post(
        "/members/m-000/save?queue_view=members&queue_q=Person+000",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
            "navigation_token": values["navigation_token"],
            "action_gender": "reject",
            "notes_gender": "Reviewed in offline fixture",
            "advance": "next",
        },
    )

    location = urlsplit(saved.headers["Location"])
    assert saved.status_code == 303
    assert location.path == "/members"
    assert parse_qs(location.query)["q"] == ["Person 000"]
    assert "m-000" not in location.path
    assert len(service.saved) == 1
    queue = client.get(saved.headers["Location"])
    assert queue.status_code == 200
    assert "Other person" not in queue.get_data(as_text=True)


def test_skip_is_a_get_link_and_does_not_record_an_event() -> None:
    service = NavService()
    service.rows = member_ids(2)
    client = nav_client(service)
    page = client.get("/members/m-000?queue_view=members&queue_q=Person")
    links = Links(page.get_data(as_text=True)).links
    skip = next(link for link in links if link.get("data-workspace-action") == "skip")

    assert skip["text"].strip() == "Skip"
    assert skip["href"].startswith("/members/m-001?")
    followed = client.get(skip["href"])
    assert followed.status_code == 200
    assert "Fixture Member" in followed.get_data(as_text=True)
    assert not service.saved and not service.prepared


def test_last_item_skip_returns_to_filtered_queue_without_event() -> None:
    service = NavService()
    service.rows = member_ids(1)
    client = nav_client(service)
    page = client.get("/members/m-000?queue_view=members&queue_q=Person")
    links = Links(page.get_data(as_text=True)).links
    skip = next(link for link in links if link.get("data-workspace-action") == "skip")

    assert skip["text"].strip() == "Skip"
    assert skip["href"].startswith("/members?")
    response = client.get(skip["href"])
    assert response.status_code == 200
    assert "Person 0" in response.get_data(as_text=True)
    assert not service.saved and not service.prepared


def test_bad_navigation_signature_is_rejected_before_member_batch_write() -> None:
    service = NavService()
    service.rows = member_ids(2)
    client = nav_client(service)
    response = preview_member(client, "m-000")
    values = Inputs(response.get_data(as_text=True)).values

    rejected = client.post(
        "/members/m-000/save",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
            "navigation_token": "tampered",
            "action_gender": "reject",
            "notes_gender": "Reviewed in offline fixture",
            "advance": "next",
        },
    )

    assert rejected.status_code == 400
    assert "navigation" in rejected.get_data(as_text=True).lower()
    assert not service.saved


@pytest.mark.parametrize("failure", [StaleReviewError("stale"), OSError("disk")])
def test_failed_save_does_not_advance_and_preserves_the_draft(
    failure: Exception,
) -> None:
    service = NavService()
    service.rows = member_ids(2)
    client = nav_client(service)
    response = preview_member(client, "m-000", value="draft value to preserve")
    values = Inputs(response.get_data(as_text=True)).values
    service.save_error = failure

    failed = client.post(
        "/members/m-000/save",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
            "navigation_token": values["navigation_token"],
            "action_gender": "reject",
            "notes_gender": "Reviewed in offline fixture",
            "value_gender": "draft value to preserve",
            "advance": "next",
        },
    )

    html = failed.get_data(as_text=True)
    # Member save failures rerender the form; they never issue a redirect.
    assert failed.status_code == 200
    assert "Location" not in failed.headers
    assert 'value="draft value to preserve"' in html
    assert 'name="action_gender" value="reject" checked' in html
    assert not service.saved


def test_repeated_education_candidates_do_not_navigate_to_same_review_id() -> None:
    service = FixtureService()
    review_id = education_review_id("abc", "Example College")
    row = {
        "review_id": review_id,
        "entity_type": "member_education",
        "candidate_id": "candidate-a",
        "payload": {"aph_id": "abc", "recorded_school_name": "Example College"},
        "evidence": {"display_name": "Fixture Member"},
        "parliaments": [48],
        "status": "pending",
    }
    service.rows = [row, {**row, "candidate_id": "candidate-b"}]
    client = create_app(service).test_client()
    page = client.get(f"/items/{review_id}?queue_entity=member_education")
    links = Links(page.get_data(as_text=True)).links
    next_links = [link for link in links if link.get("data-workspace-action") == "next"]

    assert all(
        urlsplit(link["href"]).path != f"/items/{review_id}" for link in next_links
    )
    if not next_links:
        assert "1 of 1" in page.get_data(as_text=True)


def test_changed_draft_after_preview_does_not_advance_or_save() -> None:
    service = NavService()
    service.rows = member_ids(2)
    client = nav_client(service)
    response = preview_member(client, "m-000", value="original draft")
    values = Inputs(response.get_data(as_text=True)).values

    rejected = client.post(
        "/members/m-000/save",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
            "navigation_token": values["navigation_token"],
            "action_gender": "reject",
            "notes_gender": "Changed after preview",
            "value_gender": "changed draft to retain",
            "advance": "next",
        },
    )

    html = rejected.get_data(as_text=True)
    assert rejected.status_code == 200
    assert "Location" not in rejected.headers
    assert "Draft inputs changed after preview" in html
    assert 'value="changed draft to retain"' in html
    assert not service.saved
