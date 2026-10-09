"""Offline GUI contracts over the same typed review requests as the CLI."""

from __future__ import annotations

from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("flask", reason="Install the review-ui extra for GUI tests")
pytest.importorskip("waitress", reason="Install the review-ui extra for GUI tests")

from apemap.review.gui import (
    PREVIEW_TTL,
    _read_preview,
    _sign_preview,
    create_app,
    serve,
    source_link,
)
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    entity_for_review_id,
    institution_review_id,
    member_review_id,
    school_review_id,
    service_review_id,
    validate_event,
)
from apemap.review.store import StaleReviewError, encode_event
from apemap.review.service import ReviewService
from tests.test_review_service import review_service as review_service

SCHOOL_ID = school_review_id("Fixture School")
SOURCE = "https://example.edu.au/history"
SCHOOL_PAYLOAD = {
    "recorded_name": "Fixture School",
    "institution_ref": "acara:123",
    "relationship_type": "direct",
}


class Inputs(HTMLParser):
    """Read hidden form values without a browser or third-party HTML parser."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("name"):
            self.values[str(values["name"])] = values.get("value") or ""


class FixtureService:
    """Keep persistence tests focused on HTTP orchestration and model validation."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [
            {
                "review_id": SCHOOL_ID,
                "entity_type": "school",
                "candidate_id": "candidate-fixture",
                "payload": dict(SCHOOL_PAYLOAD),
                "evidence": {
                    "source_url": SOURCE,
                    "text": "<script>alert(1)</script>",
                    "unsafe_url": "javascript:alert(1)",
                },
                "parliaments": [47],
                "status": "pending",
            }
        ]
        self.saved: list[ReviewEvent] = []
        self.prepared: list[dict[str, Any]] = []
        self.revision = "empty"
        self.source_revision = "fixture-source"
        self.institutions = [
            {
                "institution_ref": "acara:123",
                "acara_id": "123",
                "school_name": "Fixture School",
                "state": "TAS",
                "suburb": "Hobart",
                "sector": "Government",
                "school_type": "Secondary",
                "institution_status": "current",
            },
            {
                "institution_ref": "acara:456",
                "acara_id": "456",
                "school_name": "Fixture School",
                "state": "VIC",
                "suburb": "Melbourne",
                "sector": "Independent",
                "school_type": "Combined",
                "institution_status": "historical_only",
            },
        ]
        self.lookup_calls: list[tuple[str, int]] = []

    def candidates(
        self,
        entity_type: str | None = None,
        status: str | None = None,
        parliament: int | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self.rows
            if (not entity_type or row["entity_type"] == entity_type)
            and (not status or row["status"] == status)
            and (not parliament or parliament in row["parliaments"])
            and (not search or search.casefold() in json.dumps(row).casefold())
        ]

    def show(self, review_id: str) -> dict[str, Any]:
        entity = entity_for_review_id(review_id)
        events = [
            event.to_dict() for event in self.saved if event.review_id == review_id
        ]
        return {
            "review_id": review_id,
            "entity_type": entity,
            "candidates": [row for row in self.rows if row["review_id"] == review_id],
            "decision": events[-1] if events else None,
            "history": events,
            "context": {
                "source": "offline fixture",
                "members": [{"aph_id": "abc", "display_name": "Fixture Member"}],
            },
        }

    def lookup_institutions(
        self, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        self.lookup_calls.append((query, limit))
        return [
            row
            for row in self.institutions
            if query.casefold() in row["school_name"].casefold()
        ][:limit]

    def review_revision(self) -> str:
        return self.revision

    def school_decisions(self) -> dict[str, list[dict[str, Any]]]:
        return {
            event.review_id: [event.to_dict()]
            for event in self.saved
            if event.entity_type == "school"
        }

    def resolve_institution(self, reference: str) -> dict[str, Any] | None:
        return next(
            (
                dict(row)
                for row in self.institutions
                if row["institution_ref"] == reference
            ),
            None,
        )

    def institution_resolver(self) -> Any:
        return self.resolve_institution

    def prepare(
        self,
        review_id: str,
        action: str,
        payload: dict[str, Any],
        source_url: str = "",
        reviewer: str | None = None,
        notes: str = "",
        supersedes: list[str] | None = None,
        replacement_action: str | None = None,
    ) -> dict[str, Any]:
        event = ReviewEvent(
            decision_id=f"gui-event-{len(self.saved) + 1}",
            review_id=review_id,
            entity_type=entity_for_review_id(review_id),
            action=action,
            payload=payload,
            source_url=source_url,
            reviewer=reviewer or "Git Reviewer",
            notes=notes,
            reviewed_at="2026-10-05",
            recorded_at="2026-10-05T00:00:00+00:00",
            supersedes=supersedes or [],
            replacement_action=replacement_action,
        )
        validate_event(event)
        preview = {
            "event": event.to_dict(),
            "revision": self.revision,
            "source_revision": self.source_revision,
            "changes": {"before": None, "after": payload, "affected_assertions": 1},
            "validation": ["Payload validated", "Source fixture is local"],
        }
        self.prepared.append(preview)
        return preview

    def save(self, preview: dict[str, Any]) -> ReviewEvent:
        if (
            preview["revision"] != self.revision
            or preview["source_revision"] != self.source_revision
        ):
            raise StaleReviewError("Evidence or decisions changed")
        event = ReviewEvent.from_dict(preview["event"])
        self.saved.append(event)
        self.revision = str(len(self.saved))
        for row in self.rows:
            if row["review_id"] == event.review_id:
                row["status"] = event.status
        return event


@pytest.fixture
def gui() -> tuple[Any, FixtureService]:
    service = FixtureService()
    app = create_app(service)
    app.config["TESTING"] = True
    return app.test_client(), service


def test_queue_resolves_institutions_once_per_request(
    gui: tuple[Any, FixtureService], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = gui
    original = service.institution_resolver
    calls = 0

    def counted() -> Any:
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(service, "institution_resolver", counted)
    assert client.get("/?entity_type=school").status_code == 200
    assert calls == 1
    assert client.get("/?entity_type=school").status_code == 200
    assert calls == 2


def form(client: Any, review_id: str = SCHOOL_ID, **overrides: Any) -> dict[str, Any]:
    response = client.get(f"/items/{review_id}")
    values = Inputs(response.get_data(as_text=True)).values
    return {
        "csrf_token": values["csrf_token"],
        "action": "accept",
        "payload_mode": "json",
        "payload": json.dumps(SCHOOL_PAYLOAD),
        "source_url": SOURCE,
        "notes": "Sourced correction",
        **overrides,
    }


def save_form(response: Any) -> dict[str, str]:
    values = Inputs(response.get_data(as_text=True)).values
    return {
        "csrf_token": values["csrf_token"],
        "preview_token": values["preview_token"],
    }


def test_queue_filters_pagination_and_escape_evidence(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    service.rows.extend(
        {
            **service.rows[0],
            "review_id": school_review_id(f"Fixture {i}"),
            "payload": {"recorded_name": f"Fixture {i}"},
            "candidate_id": f"candidate-{i}",
        }
        for i in range(50)
    )
    response = client.get("/?entity_type=school&status=pending&parliament=47&q=Fixture")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "51 matching school mappings" in html
    assert "page 1 of 2" in html and "Next page" in html
    assert html.count('<th scope="row">') == 50
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in client.get(
        f"/items/{SCHOOL_ID}"
    ).get_data(as_text=True)
    assert client.get("/?page=2").get_data(as_text=True).count('<th scope="row">') == 1
    assert "No candidates match" in client.get("/?parliament=48").get_data(as_text=True)
    assert client.get("/?page=abc").status_code == 400
    assert client.get("/?entity_type=unknown").status_code == 400


def test_detail_context_sources_history_and_preview_do_not_save(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    response = client.get(f"/items/{SCHOOL_ID}")
    html = response.get_data(as_text=True)
    assert "Fixture Member" in html
    assert f'href="{SOURCE}"' in html
    assert 'href="javascript:' not in html
    assert "No decisions have been recorded" in html
    response = client.post(f"/items/{SCHOOL_ID}/preview", data=form(client))
    assert response.status_code == 200
    assert "Validated semantic preview" in response.get_data(as_text=True)
    assert "affected_assertions" in response.get_data(as_text=True)
    assert not service.saved
    response = client.post(f"/items/{SCHOOL_ID}/save", data=save_form(response))
    assert response.status_code == 303
    assert len(service.saved) == 1
    assert service.saved[0].reviewer == "Git Reviewer"
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Decision saved" in html and "gui-event-1" in html
    assert service.rows[0]["status"] == "accepted"


def test_local_school_lookup_is_read_only_bounded_and_disambiguates_results(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    response = client.get("/institutions/lookup", query_string={"q": " Fixture "})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["query"] == "Fixture"
    assert service.lookup_calls == [("Fixture", 20)]
    assert [row["institution_ref"] for row in payload["results"]] == [
        "acara:123",
        "acara:456",
    ]
    assert [row["state"] for row in payload["results"]] == ["TAS", "VIC"]
    assert payload["results"][1]["suburb"] == "Melbourne"
    assert payload["results"][1]["school_type"] == "Combined"
    assert payload["results"][1]["sector"] == "Independent"
    assert payload["results"][1]["institution_status"] == "historical_only"
    assert client.get("/institutions/lookup?q=missing").get_json()["results"] == []
    calls_before_empty = list(service.lookup_calls)
    assert client.get("/institutions/lookup?q=%20").get_json()["results"] == []
    assert service.lookup_calls == calls_before_empty
    assert (
        client.get("/institutions/lookup", query_string={"q": "x" * 201}).status_code
        == 400
    )
    service.institutions.extend(
        {
            **service.institutions[0],
            "acara_id": str(index),
            "institution_ref": f"acara:{index}",
        }
        for index in range(25)
    )
    assert len(client.get("/institutions/lookup?q=Fixture").get_json()["results"]) == 20
    assert service.revision == "empty"
    assert not service.prepared and not service.saved


def test_lookup_controls_only_render_for_school_and_education_and_use_local_script(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    school_html = client.get(f"/items/{SCHOOL_ID}").get_data(as_text=True)
    assert "data-school-lookup" in school_html
    assert 'src="/static/school-mapping.js?v=' in school_html
    for path in ("/new/member_education",):
        response = client.get(path)
        html = response.get_data(as_text=True)
        assert "data-institution-lookup" in html
        assert 'data-lookup-url="/institutions/lookup"' in html
        assert 'type="button" data-lookup-search' in html
        assert 'src="/static/institution-lookup.js?v=' in html
        assert "Search by school name" in html
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        assert "connect-src 'self'" in response.headers["Content-Security-Policy"]
    for path in (
        f"/items/{member_review_id('abc', 'gender')}",
        "/new/manual_institution",
        f"/items/{service_review_id('abc', 47)}",
    ):
        html = client.get(path).get_data(as_text=True)
        assert "data-institution-lookup" not in html
        assert "institution-lookup.js" not in html
    assert client.get("/static/institution-lookup.js").status_code == 200
    assert not service.lookup_calls and not service.prepared and not service.saved


@pytest.mark.parametrize("mode", ["guided", "json"])
def test_chosen_school_reference_preserves_the_remaining_review_draft(
    gui: tuple[Any, FixtureService],
    mode: str,
) -> None:
    client, service = gui
    chosen = client.get("/institutions/lookup?q=Fixture").get_json()["results"][1]
    payload = {
        **SCHOOL_PAYLOAD,
        "institution_ref": chosen["institution_ref"],
        "draft_context": "Retain this context",
    }
    response = client.post(
        f"/items/{SCHOOL_ID}/preview",
        data=form(
            client,
            payload=json.dumps(payload),
            payload_mode=mode,
            field_recorded_name=SCHOOL_PAYLOAD["recorded_name"],
            field_institution_ref=chosen["institution_ref"],
            field_relationship_type="direct",
            reviewer="Draft reviewer",
            notes="Retain my notes",
        ),
    )
    assert response.status_code == 200
    proposed = service.prepared[-1]["event"]
    assert proposed["payload"] == payload
    assert proposed["reviewer"] == "Draft reviewer"
    assert proposed["notes"] == "Retain my notes"
    assert proposed["source_url"] == SOURCE
    assert not service.saved and service.revision == "empty"


@pytest.mark.parametrize("action", ["accept", "map", "reject", "research", "supersede"])
def test_all_actions_share_service_validation(
    gui: tuple[Any, FixtureService], action: str
) -> None:
    client, service = gui
    overrides: dict[str, str] = {"action": action, "reviewer": "Researcher"}
    if action == "supersede":
        overrides.update(supersedes="old-a, old-b", replacement_action="reject")
    response = client.post(
        f"/items/{SCHOOL_ID}/preview", data=form(client, **overrides)
    )
    assert response.status_code == 200
    assert service.prepared[-1]["event"]["action"] == action
    if action == "supersede":
        assert service.prepared[-1]["event"]["supersedes"] == ["old-a", "old-b"]
        assert service.prepared[-1]["event"]["replacement_action"] == "reject"
    assert (
        client.post(f"/items/{SCHOOL_ID}/save", data=save_form(response)).status_code
        == 303
    )


@pytest.mark.parametrize("revision", ["revision", "source_revision"])
def test_stale_preview_never_overwrites(
    gui: tuple[Any, FixtureService], revision: str
) -> None:
    client, service = gui
    response = client.post(
        f"/items/{SCHOOL_ID}/preview",
        data=form(
            client, action="research", notes="Retain my reason", reviewer="Researcher"
        ),
    )
    setattr(service, revision, "external-change")
    response = client.post(f"/items/{SCHOOL_ID}/save", data=save_form(response))
    assert response.status_code == 409
    assert "Nothing was saved" in response.get_data(as_text=True)
    assert 'value="research" selected' in response.get_data(as_text=True)
    assert "Retain my reason" in response.get_data(as_text=True)
    assert 'value="Researcher"' in response.get_data(as_text=True)
    assert not service.saved


def test_invalid_forms_preserve_draft_and_never_save(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    response = client.post(
        f"/items/{SCHOOL_ID}/preview", data=form(client, payload="{draft invalid")
    )
    assert response.status_code == 400
    assert "{draft invalid" in response.get_data(as_text=True)
    assert (
        client.post(
            f"/items/{SCHOOL_ID}/preview", data=form(client, payload="[]")
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/items/{SCHOOL_ID}/preview",
            data=form(client, source_url="javascript:alert(1)"),
        ).status_code
        == 400
    )
    assert not service.saved and not service.prepared


def test_guided_manual_institution_exposes_closed_status(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    page = client.get("/new/manual_institution")
    assert 'value="closed"' in page.get_data(as_text=True)
    response = client.post(
        "/new/manual_institution/preview",
        data={
            "csrf_token": Inputs(page.get_data(as_text=True)).values["csrf_token"],
            "action": "accept",
            "payload_mode": "guided",
            "payload": "{}",
            "field_institution_ref": "manual:closed-school",
            "field_school_name": "Closed School",
            "field_sector": "Other",
            "field_country": "New Zealand",
            "field_institution_status": "closed",
            "source_url": "https://example.org/history",
            "reviewer": "Reviewer",
        },
    )
    assert response.status_code == 200
    assert service.prepared[-1]["event"]["payload"]["institution_status"] == "closed"


def test_csrf_origin_host_and_security_headers(gui: tuple[Any, FixtureService]) -> None:
    client, service = gui
    for token in ("", "wrong", "é"):
        assert (
            client.post(
                f"/items/{SCHOOL_ID}/preview", data=form(client, csrf_token=token)
            ).status_code
            == 403
        )
    assert (
        client.post(
            f"/items/{SCHOOL_ID}/preview",
            data=form(client),
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert (
        client.post(
            f"/items/{SCHOOL_ID}/preview",
            data=form(client),
            headers={"Origin": "http://localhost"},
        ).status_code
        == 200
    )
    response = client.get("/")
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert not service.saved


def test_preview_tamper_expiry_wrong_item_and_replay(
    gui: tuple[Any, FixtureService], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = gui
    response = client.post(f"/items/{SCHOOL_ID}/preview", data=form(client))
    signed = save_form(response)
    damaged = {
        **signed,
        "preview_token": signed["preview_token"][:-1]
        + ("a" if signed["preview_token"][-1] != "a" else "b"),
    }
    assert client.post(f"/items/{SCHOOL_ID}/save", data=damaged).status_code == 400
    assert client.post("/items/school:other/save", data=signed).status_code == 400
    from apemap.review import gui as gui_module

    now = gui_module.time.time()
    monkeypatch.setattr(gui_module.time, "time", lambda: now + PREVIEW_TTL + 1)
    assert client.post(f"/items/{SCHOOL_ID}/save", data=signed).status_code == 400
    monkeypatch.undo()
    assert not service.saved
    assert client.post(f"/items/{SCHOOL_ID}/save", data=signed).status_code == 303
    assert client.post(f"/items/{SCHOOL_ID}/save", data=signed).status_code == 409
    assert len(service.saved) == 1


@pytest.mark.parametrize(
    "entity,payload,review_id",
    [
        (
            "manual_institution",
            {
                "institution_ref": "manual:overseas-school",
                "school_name": "Overseas School",
                "sector": "Other",
                "country": "New Zealand",
            },
            institution_review_id("manual:overseas-school"),
        ),
        (
            "member_education",
            {
                "aph_id": "abc",
                "recorded_school_name": "Another School",
                "attended_status": "attended_unspecified",
                "confidence": "provisional",
                "retrieved_at": "2026-10-05T10:00:00+11:00",
            },
            education_review_id("abc", "Another School"),
        ),
    ],
)
def test_create_manual_institution_and_education(
    gui: tuple[Any, FixtureService],
    entity: str,
    payload: dict[str, Any],
    review_id: str,
) -> None:
    client, service = gui
    response = client.get(f"/new/{entity}")
    data = {
        "csrf_token": Inputs(response.get_data(as_text=True)).values["csrf_token"],
        "action": "accept",
        "payload_mode": "json",
        "payload": json.dumps(payload),
        "source_url": SOURCE,
    }
    response = client.post(f"/new/{entity}/preview", data=data)
    assert response.status_code == 200
    assert service.prepared[-1]["event"]["review_id"] == review_id
    assert not service.saved
    assert (
        client.post(f"/items/{review_id}/save", data=save_form(response)).status_code
        == 303
    )


def test_guided_fields_and_missing_evidence_addition(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    response = client.post(
        f"/items/{SCHOOL_ID}/preview",
        data=form(
            client,
            payload_mode="guided",
            field_recorded_name="Fixture School",
            field_institution_ref="manual:fixture",
            field_relationship_type="rename",
        ),
    )
    assert response.status_code == 200
    assert (
        service.prepared[-1]["event"]["payload"]["institution_ref"] == "manual:fixture"
    )
    missing = client.get("/items/education:abc:missing").get_data(as_text=True)
    assert "Add an education assertion for this member" in missing
    assert "aph_id=abc" in missing
    assert 'value="abc"' in client.get("/new/member_education?aph_id=abc").get_data(
        as_text=True
    )


def test_member_and_complete_service_editors(gui: tuple[Any, FixtureService]) -> None:
    client, service = gui
    member = member_review_id("abc", "gender")
    response = client.post(
        f"/items/{member}/preview",
        data=form(
            client,
            member,
            payload=json.dumps({"aph_id": "abc", "field": "gender", "value": None}),
        ),
    )
    assert response.status_code == 200
    assert service.prepared[-1]["event"]["payload"]["value"] is None
    response = client.post(
        f"/items/{member}/preview",
        data=form(
            client,
            member,
            payload_mode="guided",
            payload="{}",
            field_aph_id="abc",
            field_field="gender",
            field_value="Male",
            clear_value="1",
        ),
    )
    assert response.status_code == 200
    assert service.prepared[-1]["event"]["payload"]["value"] is None
    service_id = service_review_id("abc", 47)
    payload = {
        "aph_id": "abc",
        "parliament_number": 47,
        "intervals": [
            {
                "service_start": "2022-07-26",
                "service_end": "2025-03-28",
                "chamber": "senate",
                "party": "Fixture Party",
                "state_or_territory": "Tasmania",
                "source_url": SOURCE,
                "retrieved_at": "2026-10-05T00:00:00+00:00",
            }
        ],
    }
    response = client.post(
        f"/items/{service_id}/preview",
        data=form(client, service_id, payload=json.dumps(payload)),
    )
    assert response.status_code == 200
    assert "complete intervals array" in response.get_data(as_text=True)
    assert service.prepared[-1]["event"]["payload"] == payload


def test_loopback_serve_and_evidence_url_schemes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "apemap.review.gui.waitress_serve",
        lambda application, **kwargs: seen.update(kwargs),
    )
    serve(FixtureService(), 8766)
    assert seen == {
        "host": "127.0.0.1",
        "port": 8766,
        "threads": 4,
        "max_request_body_size": 256 * 1024,
    }
    with pytest.raises(ValueError, match="Port"):
        serve(FixtureService(), 70000)
    with pytest.raises(ValueError, match="Workers"):
        serve(FixtureService(), workers=0)
    serve(FixtureService(), workers=2)
    assert seen["threads"] == 2
    for value in (
        None,
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https:",
        "https://[",
    ):
        assert source_link(value) is None
    assert source_link(SOURCE) == SOURCE


def test_signed_save_token_does_not_repeat_large_canonical_display() -> None:
    service = FixtureService()
    preview = service.prepare(SCHOOL_ID, "accept", SCHOOL_PAYLOAD, SOURCE)
    preview["changes"]["large_source_snapshot"] = "x" * 1_000_000
    token = _sign_preview(preview, b"fixture-secret")
    assert len(token) < 4000
    commit = _read_preview(token, b"fixture-secret")
    assert commit == {
        key: preview[key] for key in ("event", "revision", "source_revision")
    }


def test_real_service_manual_and_missing_education_preview_leave_database_unchanged(
    tmp_path: Path, database_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import service as service_module
    from apemap.review.candidates import build_candidates
    from apemap.review.service import ReviewService

    db_path, conn = database_factory()
    conn.execute(
        "INSERT INTO members (member_id,aph_id,display_name,family_name,given_name) VALUES ('gui-member','GUI-APH','GUI Member','Member','GUI')"
    )
    conn.execute(
        "INSERT INTO parliament_service (service_id,member_id,parliament_number,chamber,party,party_abbrev,state_or_territory) VALUES ('gui-service','gui-member',47,'senate','Fixture','FIX','TAS')"
    )
    conn.close()
    baseline = hashlib.sha256(db_path.read_bytes()).hexdigest()
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setattr(service_module, "RAW_APH_DIR", tmp_path / "aph")
    monkeypatch.setattr(service_module, "RAW_WIKIMEDIA_DIR", tmp_path / "wiki")
    monkeypatch.setattr(
        service_module,
        "build_candidates",
        lambda connection, **kwargs: build_candidates(
            connection, cache_dir=tmp_path / "cache", **kwargs
        ),
    )
    log = tmp_path / "decisions.jsonl"
    service = ReviewService(log_path=log, db_path=db_path, external_dir=external)
    client = create_app(service).test_client()
    ref = "manual:gui-overseas"
    institution_id = institution_review_id(ref)
    institution = {
        "institution_ref": ref,
        "school_name": "GUI Overseas School",
        "sector": "Other",
        "country": "New Zealand",
    }
    response = client.post(
        f"/items/{institution_id}/preview",
        data=form(
            client,
            institution_id,
            payload=json.dumps(institution),
            reviewer="Fixture Researcher",
        ),
    )
    assert response.status_code == 200
    assert not log.exists()
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == baseline
    assert (
        client.post(
            f"/items/{institution_id}/save", data=save_form(response)
        ).status_code
        == 303
    )
    before = log.read_bytes()
    missing_id = education_review_id("GUI-APH", "")
    education = {
        "aph_id": "GUI-APH",
        "recorded_school_name": "GUI Overseas School",
        "institution_ref": ref,
        "attended_status": "attended_unspecified",
        "confidence": "provisional",
        "retrieved_at": "2026-10-05T10:00:00+11:00",
    }
    response = client.post(
        f"/items/{missing_id}/preview",
        data=form(
            client,
            missing_id,
            payload=json.dumps(education),
            reviewer="Fixture Researcher",
        ),
    )
    assert response.status_code == 200
    new_id = education_review_id("GUI-APH", education["recorded_school_name"])
    assert f"/items/{new_id}/save" in response.get_data(as_text=True)
    assert log.read_bytes() == before
    assert "canonical" in response.get_data(as_text=True)
    assert (
        client.post(f"/items/{new_id}/save", data=save_form(response)).status_code
        == 303
    )
    assert service.show(new_id)["decision"]["payload"] == education
    effects = service.semantic_diff([], service.events())["canonical"]["after"]
    assert effects["member_education"]["after_count"] == 1
    assert log.read_bytes().startswith(before)
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == baseline


def test_real_merged_conflict_is_visible_and_repaired_by_superseding_all_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import service as service_module
    from apemap.review.service import ReviewService

    monkeypatch.setattr(service_module, "RAW_APH_DIR", tmp_path / "aph")
    monkeypatch.setattr(service_module, "RAW_WIKIMEDIA_DIR", tmp_path / "wiki")
    parents = [
        ReviewEvent(
            decision_id=decision_id,
            review_id=SCHOOL_ID,
            entity_type="school",
            action=action,
            payload={"recorded_name": "Fixture School"},
            reviewer="First Researcher",
            notes="Conflicting independently recorded review",
            reviewed_at="2026-10-05",
            recorded_at="2026-10-05T00:00:00+00:00",
        )
        for decision_id, action in (("head-a", "reject"), ("head-b", "research"))
    ]
    baseline = b"".join(encode_event(event) for event in parents)
    log = tmp_path / "decisions.jsonl"
    log.write_bytes(baseline)
    service = ReviewService(
        log_path=log,
        db_path=tmp_path / "absent.duckdb",
        external_dir=tmp_path / "external",
    )
    client = create_app(service).test_client()
    queue = client.get("/?status=conflict")
    assert queue.status_code == 200
    assert "1 matching review entries" in queue.get_data(as_text=True)
    detail = client.get(f"/items/{SCHOOL_ID}")
    html = detail.get_data(as_text=True)
    assert detail.status_code == 200
    assert "Conflicting active decisions" in html
    assert (
        "Your guided decision replaces every active alternative after preview" in html
    )
    assert 'value="head-a, head-b"' in html
    assert "No alternative currently has authority" in html
    response = client.post(
        f"/items/{SCHOOL_ID}/preview",
        data=form(
            client,
            action="supersede",
            payload=json.dumps({"recorded_name": "Fixture School"}),
            supersedes="head-a, head-b",
            replacement_action="research",
            notes="Both alternatives need further official evidence",
            reviewer="Conflict Reviewer",
        ),
    )
    assert response.status_code == 200
    assert "head-a" in response.get_data(as_text=True)
    assert "head-b" in response.get_data(as_text=True)
    assert log.read_bytes() == baseline
    assert (
        client.post(f"/items/{SCHOOL_ID}/save", data=save_form(response)).status_code
        == 303
    )
    assert log.read_bytes().startswith(baseline)
    assert service.show(SCHOOL_ID)["conflicts"] == []
    assert service.show(SCHOOL_ID)["decision"]["replacement_action"] == "research"
    assert service.candidates()[0]["status"] == "needs_research"


def test_assertion_mapping_controls_keep_other_entity_actions_scoped(
    gui: tuple[Any, FixtureService],
) -> None:
    client, _service = gui
    review_id = education_review_id("abc", "Fixture School")
    html = client.get(f"/items/{review_id}").get_data(as_text=True)
    assert html.count('value="map"') == 2
    assert "Map this assertion" in html
    assert 'name="field_relationship_type"' in html
    assert "optional when accepting attendance" in html
    assert "preserving attendance and its provenance" in html
    assert "Needs research retains attendance" in html
    assert "Reject removes the attendance claim" in html
    assert "HTTP(S) evidence source" in html
    assert "Their evidence applies only to this assertion" in html
    for review_id in (
        member_review_id("abc", "gender"),
        service_review_id("abc", 47),
        institution_review_id("manual:fixture"),
    ):
        html = client.get(f"/items/{review_id}").get_data(as_text=True)
        assert 'value="map"' not in html
        assert 'name="field_relationship_type"' not in html


@pytest.mark.parametrize("action", ["map", "supersede"])
def test_guided_assertion_mapping_previews_and_saves_the_exact_resolution(
    gui: tuple[Any, FixtureService], action: str
) -> None:
    client, service = gui
    review_id = education_review_id("abc", "Fixture School")
    response = client.post(
        f"/items/{review_id}/preview",
        data=form(
            client,
            review_id,
            action=action,
            payload="{}",
            payload_mode="guided",
            field_aph_id="abc",
            field_recorded_school_name="Fixture School",
            field_institution_ref="acara:456",
            field_relationship_type="alias",
            field_attended_status="graduated",
            field_confidence="verified",
            field_retrieved_at="2026-10-05T10:00:00+11:00",
            supersedes="prior-resolution" if action == "supersede" else "",
            replacement_action="map",
            notes="Resolve this member's school identity",
        ),
    )
    assert response.status_code == 200
    proposed = service.prepared[-1]["event"]
    assert proposed["payload"] == {
        "aph_id": "abc",
        "recorded_school_name": "Fixture School",
        "institution_ref": "acara:456",
        "relationship_type": "alias",
    }
    assert proposed["source_url"] == SOURCE
    assert proposed["action"] == action
    assert proposed["replacement_action"] == ("map" if action == "supersede" else None)
    assert not service.saved
    assert (
        client.post(f"/items/{review_id}/save", data=save_form(response)).status_code
        == 303
    )
    assert service.saved[-1].payload == proposed["payload"]
    assert service.saved[-1].source_url == SOURCE
    html = client.get(f"/items/{review_id}").get_data(as_text=True)
    assert 'value="map" selected' in html
    assert f'value="{SOURCE}"' in html


def test_guided_acceptance_keeps_relationship_optional(
    gui: tuple[Any, FixtureService],
) -> None:
    client, service = gui
    review_id = education_review_id("abc", "Fixture School")
    response = client.post(
        f"/items/{review_id}/preview",
        data=form(
            client,
            review_id,
            payload="{}",
            payload_mode="guided",
            field_aph_id="abc",
            field_recorded_school_name="Fixture School",
            field_institution_ref="acara:123",
            field_relationship_type="",
            field_attended_status="attended_unspecified",
            field_confidence="verified",
            field_retrieved_at="2026-10-05T10:00:00+11:00",
        ),
    )
    assert response.status_code == 200
    assert "relationship_type" not in service.prepared[-1]["event"]["payload"]
    assert service.prepared[-1]["event"]["payload"]["attended_status"] == (
        "attended_unspecified"
    )


@pytest.mark.parametrize(
    "overrides", [{"source_url": ""}, {"field_relationship_type": ""}]
)
def test_incomplete_assertion_mapping_retains_draft_and_never_saves(
    gui: tuple[Any, FixtureService], overrides: dict[str, str]
) -> None:
    client, service = gui
    review_id = education_review_id("abc", "Fixture School")
    values = {
        "action": "map",
        "payload": "{}",
        "payload_mode": "guided",
        "field_aph_id": "abc",
        "field_recorded_school_name": "Fixture School",
        "field_institution_ref": "acara:456",
        "field_relationship_type": "alias",
        "notes": "Keep this resolution draft",
        **overrides,
    }
    response = client.post(
        f"/items/{review_id}/preview", data=form(client, review_id, **values)
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'value="map" selected' in html
    assert 'value="acara:456"' in html
    assert "Keep this resolution draft" in html
    assert not service.prepared and not service.saved


@pytest.mark.parametrize("entity", ["school", "member_education"])
def test_school_and_assertion_pages_compare_current_targets_and_defaults(
    gui: tuple[Any, FixtureService],
    monkeypatch: pytest.MonkeyPatch,
    entity: str,
) -> None:
    client, service = gui
    original_show = service.show
    default = {"action": "map", "payload": dict(SCHOOL_PAYLOAD)}
    first_id = education_review_id("abc", "Fixture School")
    second_id = education_review_id("def", "Fixture School")

    def show(review_id: str) -> dict[str, Any]:
        item = original_show(review_id)
        item["context"] = {
            "school_relationship": default,
            "resolution_warnings": ["Recorded name refers to different institutions"],
            "members": [
                {
                    "aph_id": aph_id,
                    "display_name": display_name,
                    "education": [
                        {
                            "review_id": assertion_id,
                            "recorded_name": "Fixture School",
                            "current_resolution": {
                                "institution_ref": ref,
                                "institution_name": name,
                                "relationship_type": "alias",
                                "resolution_scope": scope,
                                "status": "accepted",
                            },
                            "default_relationship": default,
                            "decision": {"action": "map"}
                            if scope == "assertion"
                            else None,
                            "relationship_conflicts": [
                                {"message": "<b>Different from school-wide default</b>"}
                            ]
                            if scope == "assertion"
                            else [],
                        }
                    ],
                }
                for aph_id, display_name, assertion_id, ref, name, scope in (
                    (
                        "abc",
                        "First Member",
                        first_id,
                        "acara:456",
                        "Member School",
                        "assertion",
                    ),
                    (
                        "def",
                        "Second Member",
                        second_id,
                        "acara:123",
                        "Default School",
                        "school",
                    ),
                )
            ],
        }
        return item

    monkeypatch.setattr(service, "show", show)
    review_id = SCHOOL_ID if entity == "school" else first_id
    response = client.get(f"/items/{review_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'aria-label="Assertion relationship comparison"' in html
    assert "First Member" in html and "Second Member" in html
    assert "Member School" in html and "Default School" in html
    assert "acara:456" in html and "acara:123" in html
    assert f'href="/items/{first_id}"' in html
    assert f'href="/items/{second_id}"' in html
    assert "Recorded name refers to different institutions" in html
    assert "&lt;b&gt;Different from school-wide default&lt;/b&gt;" in html
    assert "<b>Different from school-wide default</b>" not in html
    assert not service.prepared and not service.saved


@pytest.mark.parametrize("entity", ["school", "member_education"])
def test_relationship_preview_identifies_default_application_and_disagreements(
    gui: tuple[Any, FixtureService],
    monkeypatch: pytest.MonkeyPatch,
    entity: str,
) -> None:
    client, service = gui
    original_prepare = service.prepare
    first_id = education_review_id("abc", "Fixture School")
    second_id = education_review_id("def", "Fixture School")

    def prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        preview = original_prepare(*args, **kwargs)
        preview["relationship_conflicts"] = [
            {"message": "Member School differs from Default School"}
        ]
        preview["default_application"] = {
            "available": True,
            "school_review_id": SCHOOL_ID,
            "assertions": [
                {
                    "review_id": assertion_id,
                    "aph_id": aph_id,
                    "display_name": display_name,
                    "recorded_name": "Fixture School",
                    "applies": applies,
                    "reason": reason,
                }
                for assertion_id, aph_id, display_name, applies, reason in (
                    (
                        first_id,
                        "abc",
                        "First Member",
                        False,
                        "Explicit assertion resolution",
                    ),
                    (
                        second_id,
                        "def",
                        "Second Member",
                        True,
                        "Uses school-wide default",
                    ),
                )
            ],
        }
        return preview

    monkeypatch.setattr(service, "prepare", prepare)
    review_id = SCHOOL_ID if entity == "school" else first_id
    payload = (
        SCHOOL_PAYLOAD
        if entity == "school"
        else {
            "aph_id": "abc",
            "recorded_school_name": "Fixture School",
            "institution_ref": "acara:456",
            "relationship_type": "alias",
        }
    )
    response = client.post(
        f"/items/{review_id}/preview",
        data=form(client, review_id, action="map", payload=json.dumps(payload)),
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'aria-label="School-wide default application"' in html
    assert 'aria-label="Relationship disagreements"' in html
    assert "Member School differs from Default School" in html
    assert (
        "Explicit assertion resolution" in html and "Uses school-wide default" in html
    )
    assert f'href="/items/{first_id}"' in html
    assert f'href="/items/{second_id}"' in html
    assert "<td>No</td>" in html and "<td>Yes</td>" in html
    assert not service.saved


@pytest.mark.integration
def test_real_guided_assertion_mapping_preserves_source_and_signed_save(
    review_service: ReviewService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apemap.db import get_connection
    from apemap.review import service as service_module
    from apemap.review.integration import project_review_records

    service = review_service
    monkeypatch.setattr(service_module, "RAW_APH_DIR", tmp_path / "aph")
    monkeypatch.setattr(service_module, "RAW_WIKIMEDIA_DIR", tmp_path / "wiki")
    db_before = hashlib.sha256(service.db_path.read_bytes()).hexdigest()
    with get_connection(service.db_path, read_only=True) as conn:
        source = project_review_records(conn, [], matcher=service.matcher())[
            "member_education"
        ][0]
    client = create_app(service).test_client()
    review_id = education_review_id("TEST", "Test High School")
    response = client.post(
        f"/items/{review_id}/preview",
        data=form(
            client,
            review_id,
            action="map",
            payload_mode="guided",
            payload=json.dumps(
                {
                    "aph_id": "TEST",
                    "recorded_school_name": "Test High School",
                    "years_attended": "1980-1985",
                    "graduation_year": 1985,
                }
            ),
            field_aph_id="TEST",
            field_recorded_school_name="Test High School",
            field_institution_ref="acara:2",
            field_relationship_type="direct",
            field_attended_status="graduated",
            field_confidence="provisional",
            field_retrieved_at="2026-10-09T10:00:00+11:00",
            source_url="https://example.org/member-school-identity",
            reviewer="Fixture GUI reviewer",
        ),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert not service.log_path.exists()
    assert hashlib.sha256(service.db_path.read_bytes()).hexdigest() == db_before
    response = client.post(f"/items/{review_id}/save", data=save_form(response))
    assert response.status_code == 303
    event = service.show(review_id)["decision"]
    assert event["action"] == "map"
    assert event["payload"] == {
        "aph_id": "TEST",
        "recorded_school_name": "Test High School",
        "institution_ref": "acara:2",
        "relationship_type": "direct",
    }
    assert event["source_url"] == "https://example.org/member-school-identity"
    with get_connection(service.db_path, read_only=True) as conn:
        after = project_review_records(
            conn, service.events(), matcher=service.matcher()
        )["member_education"][0]
    mutable = {"institution_id", "institution_resolution", "resolution_source_url"}
    from apemap.education_context import CONTEXT_FIELDS

    mutable.update(CONTEXT_FIELDS)
    assert {name: value for name, value in after.items() if name not in mutable} == {
        name: value for name, value in source.items() if name not in mutable
    }
    assert after["institution_id"] == "acara-2"
    assert after["historical_context_scope"] == "assertion"
    assert hashlib.sha256(service.db_path.read_bytes()).hexdigest() == db_before
