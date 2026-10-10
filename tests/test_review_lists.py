"""Dedicated review lists share grouping, revisions and read-only authority."""

from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.gui import create_app
from apemap.review.members import MemberFieldDraft
from apemap.review.model import ReviewEvent, member_review_id, school_review_id
from apemap.review.service import ReviewService
from tests.test_review_gui import FixtureService
from tests.test_review_member_batch import member_cache as member_cache
from tests.test_review_service import review_service as review_service


class ListHTML(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.links: list[str] = []
        self.names: list[str] = []
        self.statuses: list[str] = []
        self.filter_action = ""
        self.status_select = False
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            self.links.append(values.get("href") or "")
        if tag in {"input", "select"}:
            self.names.append(values.get("name") or "")
        if tag == "form" and values.get("class") == "filters":
            self.filter_action = values.get("action") or ""
        if tag == "select":
            self.status_select = values.get("name") == "status"
        if tag == "option" and self.status_select:
            self.statuses.append(values.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self.status_select = False


class ListService(FixtureService):
    def __init__(self) -> None:
        super().__init__()
        self.rows = []
        self.candidate_calls = 0

    def candidates(
        self,
        entity_type: str | None = None,
        status: str | None = None,
        parliament: int | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        self.candidate_calls += 1
        return super().candidates(entity_type, status, parliament, search)

    def queue_revision(self) -> str:
        return json.dumps(
            [self.rows, [event.to_dict() for event in self.saved]], sort_keys=True
        )

    def school_decisions(self) -> dict[str, list[dict[str, Any]]]:
        heads: dict[str, list[dict[str, Any]]] = {}
        for event in self.saved:
            heads.setdefault(event.review_id, []).append(event.to_dict())
        return heads


def member_row(
    aph_id: str,
    name: str,
    status: str = "pending",
    *,
    field: str = "gender",
    parliament: int = 48,
) -> dict[str, Any]:
    return {
        "review_id": member_review_id(aph_id, field),
        "entity_type": "member",
        "candidate_id": f"candidate-{aph_id}-{field}-{status}",
        "payload": {"aph_id": aph_id, "field": field},
        "evidence": {"display_name": name, "comparison": "different"},
        "status": status,
        "parliaments": [parliament],
    }


def school_row(
    name: str, *, progress: str | None = None, parliament: int = 48
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "review_id": school_review_id(name),
        "entity_type": "school",
        "candidate_id": f"candidate-{name}",
        "payload": {"recorded_name": name},
        "evidence": {"acara_id": "123", "source_url": "https://example.org/history"},
        "status": "pending",
        "parliaments": [parliament],
    }
    if progress:
        row["individual_resolution"] = {"status": progress}
    return row


def school_head(
    name: str, action: str, *, decision_id: str | None = None
) -> ReviewEvent:
    return ReviewEvent(
        decision_id=decision_id or f"decision-{name}-{action}",
        review_id=school_review_id(name),
        entity_type="school",
        action=action,
        payload={
            "recorded_name": name,
            "institution_ref": "acara:123",
            "relationship_type": "direct",
        },
        source_url="https://example.org/history",
        reviewer="Reviewer",
        notes="Fixture review",
        reviewed_at="2026-10-11",
        recorded_at="2026-10-11T00:00:00+00:00",
        supersedes=[],
    )


def test_member_list_groups_open_work_sorts_and_fixes_scope() -> None:
    service = ListService()
    service.rows = [
        member_row("done", "Completed", "rejected"),
        member_row("accepted", "Accepted", "accepted"),
        member_row("research", "Research", "needs_research"),
        member_row("pending-b", "alpha"),
        member_row("pending-a", "Alpha"),
        member_row("conflict", "Zeta", "conflict"),
        member_row("pending-a", "Alpha", "rejected", field="date_of_birth"),
        school_row("Other school"),
    ]
    client = create_app(service).test_client()
    html = client.get("/members?entity_type=school&unmatched_schools=1").get_data(
        as_text=True
    )
    parsed = ListHTML(html)
    assert [url for url in parsed.links if url.startswith("/members/")] == [
        "/members/conflict",
        "/members/pending-a",
        "/members/pending-b",
        "/members/research",
    ]
    assert "4 matching members" in html and "2 correction fields" in html
    assert parsed.statuses == ["", "conflict", "pending", "needs_research"]
    assert parsed.filter_action == "/members"
    assert "entity_type" not in parsed.names and "unmatched_schools" not in parsed.names
    assert not any(url.startswith("/items/school:") for url in parsed.links)
    assert "Completed</a>" not in html and "Accepted</a>" not in html
    assert "Completed" in client.get("/?entity_type=member").get_data(as_text=True)
    assert service.candidate_calls == 1


def test_school_list_includes_suggestions_and_excludes_completed_resolutions() -> None:
    service = ListService()
    names = [
        "Pending",
        "Research",
        "Rejected",
        "Conflict",
        "Individual",
        "Mapped",
        "Resolved",
    ]
    service.rows = [
        school_row(
            name,
            progress="needs_individual_review"
            if name == "Individual"
            else "resolved_individually"
            if name == "Resolved"
            else None,
        )
        for name in names
    ]
    service.rows.append(
        {
            **school_row("Pending"),
            "candidate_id": "second-pending",
            "evidence": {"suggested_institution_name": "Unverified historical campus"},
        }
    )
    service.saved = [
        school_head("Mapped", "map"),
        school_head("Research", "research"),
        school_head("Rejected", "reject"),
        school_head("Conflict", "accept"),
        school_head("Conflict", "map", decision_id="parallel-head"),
    ]
    client = create_app(service).test_client()
    html = client.get(
        "/schools/unmatched?entity_type=member&unmatched_schools=0"
    ).get_data(as_text=True)
    parsed = ListHTML(html)
    assert [url for url in parsed.links if url.startswith("/items/school:")] == [
        f"/items/{school_review_id(name)}"
        for name in ["Conflict", "Individual", "Pending", "Research", "Rejected"]
    ]
    assert "5 matching unmatched schools" in html
    assert "1 possible targets · 1 unresolved evidence leads" in html
    assert parsed.filter_action == "/schools/unmatched"
    assert (
        "accepted" not in parsed.statuses
        and "resolved_individually" not in parsed.statuses
    )
    assert "entity_type" not in parsed.names and "unmatched_schools" not in parsed.names


@pytest.mark.parametrize(
    "path, school", [("/members", False), ("/schools/unmatched", True)]
)
def test_list_filters_counts_and_pagination_stay_in_context(
    path: str, school: bool
) -> None:
    service = ListService()
    for index in reversed(range(51)):
        name = f"Person {index:03}"
        service.rows.append(
            school_row(name) if school else member_row(f"m-{index:03}", name)
        )
    service.rows.append(
        school_row("Different", parliament=47)
        if school
        else member_row("different", "Different", parliament=47)
    )
    client = create_app(service).test_client()
    html = client.get(f"{path}?status=pending&parliament=48&q=Person").get_data(
        as_text=True
    )
    parsed = ListHTML(html)
    detail_prefix = "/items/school:" if school else "/members/"
    assert len([url for url in parsed.links if url.startswith(detail_prefix)]) == 50
    assert "51 matching" in html and "page 1 of 2" in html
    assert "Person 000</a>" in html and "Person 050</a>" not in html
    next_url = next(
        url
        for url in parsed.links
        if parse_qs(urlsplit(url).query).get("page") == ["2"]
    )
    assert urlsplit(next_url).path == path
    assert parse_qs(urlsplit(next_url).query) == {
        "status": ["pending"],
        "parliament": ["48"],
        "q": ["Person"],
        "page": ["2"],
    }
    last = client.get(next_url).get_data(as_text=True)
    assert (
        len([url for url in ListHTML(last).links if url.startswith(detail_prefix)]) == 1
    )
    assert "page 2 of 2" in last
    assert "Person 050</a>" in last
    assert f'href="{path}">Clear filters' in html
    assert "page 2 of 2" in client.get(f"{path}?q=Person&page=999").get_data(
        as_text=True
    )
    assert "page 1 of 2" in client.get(f"{path}?q=Person&page=0").get_data(as_text=True)
    assert service.candidate_calls == 1


@pytest.mark.parametrize("path", ["/members", "/schools/unmatched"])
@pytest.mark.parametrize(
    "query",
    [
        "page=invalid",
        "parliament=invalid",
        "status=accepted",
        "status=resolved_individually",
        "status=unknown",
    ],
)
def test_dedicated_list_rejects_invalid_filters(path: str, query: str) -> None:
    assert (
        create_app(ListService()).test_client().get(f"{path}?{query}").status_code
        == 400
    )


def test_home_counts_and_shared_navigation_use_complete_grouped_workloads() -> None:
    service = ListService()
    service.rows = [
        member_row("a", "Alpha"),
        member_row("a", "Alpha", field="wikidata_id"),
        member_row("done", "Done", "rejected"),
        school_row("Suggested school"),
        school_row("Resolved school", progress="resolved_individually"),
    ]
    client = create_app(service).test_client()
    html = client.get("/?q=no-matches").get_data(as_text=True)
    assert "<strong>1</strong><span>Members to review</span>" in html
    assert "<strong>1</strong><span>Unmatched schools</span>" in html
    assert "0 matching review entries" in html
    for url in ("/", "/members", "/schools/unmatched", "/members/a"):
        parsed = ListHTML(client.get(url).get_data(as_text=True))
        assert "/members" in parsed.links and "/schools/unmatched" in parsed.links


def test_empty_lists_and_search_results_have_explicit_messages() -> None:
    empty = create_app(ListService()).test_client()
    assert "No members require review" in empty.get("/members").get_data(as_text=True)
    assert "No unmatched schools match these filters." in empty.get(
        "/schools/unmatched"
    ).get_data(as_text=True)
    service = ListService()
    service.rows = [member_row("a", "Alpha"), school_row("Suggested school")]
    client = create_app(service).test_client()
    assert "No members require review" in client.get("/members?q=absent").get_data(
        as_text=True
    )
    assert "No unmatched schools match these filters." in client.get(
        "/schools/unmatched?q=absent"
    ).get_data(as_text=True)
    assert "No candidates match these filters." in client.get("/?q=absent").get_data(
        as_text=True
    )


def test_real_member_saves_invalidate_lists_without_mutating_authority_on_reads(
    review_service: ReviewService,
) -> None:
    service = review_service
    client = create_app(service).test_client()
    before_db = hashlib.sha256(service.db_path.read_bytes()).hexdigest()
    before_log = service.log_path.read_bytes() if service.log_path.exists() else b""
    assert "/members/TEST" in client.get("/members").get_data(as_text=True)
    for url in ("/", "/members", "/schools/unmatched"):
        assert client.get(url).status_code == 200
    assert (
        service.log_path.read_bytes() if service.log_path.exists() else b""
    ) == before_log
    service.save_batch(
        service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field=field,
                    action="reject",
                    notes="Not supported",
                    reviewer="Reviewer",
                )
                for field in ("gender", "date_of_birth", "wikidata_id")
            ],
        )
    )
    assert "/members/TEST" not in client.get("/members").get_data(as_text=True)
    assert "/members/TEST" in client.get("/?entity_type=member").get_data(as_text=True)
    after_log = service.log_path.read_bytes()
    for url in ("/", "/members", "/schools/unmatched"):
        client.get(url)
    assert service.log_path.read_bytes() == after_log
    assert hashlib.sha256(service.db_path.read_bytes()).hexdigest() == before_db


def test_real_school_save_updates_unmatched_list_and_home_count(
    review_service: ReviewService,
) -> None:
    client = create_app(review_service).test_client()
    review_id = school_review_id("Test High School")
    assert f"/items/{review_id}" in client.get("/schools/unmatched").get_data(
        as_text=True
    )
    review_service.save(
        review_service.prepare(
            review_id,
            "map",
            {
                "recorded_name": "Test High School",
                "institution_ref": "acara:1",
                "relationship_type": "direct",
            },
            source_url="https://example.org/history",
            reviewer="Reviewer",
        )
    )
    assert f"/items/{review_id}" not in client.get("/schools/unmatched").get_data(
        as_text=True
    )
    assert "<strong>0</strong><span>Unmatched schools</span>" in client.get(
        "/"
    ).get_data(as_text=True)
