"""School presentation, grouped queues and guided append-only decisions."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("flask")

from apemap.review.gui import create_app, PREVIEW_TTL
from apemap.review.model import ReviewEvent, active_heads, school_review_id
from apemap.review.schools import group_school_rows, school_view
from apemap.review.service import ReviewService
from apemap.review.store import encode_event
from tests.test_review_gui import (
    FixtureService,
    Inputs,
    SOURCE,
    SCHOOL_ID,
)
from tests.db_fixtures import DatabaseFactory

NAME = "St Clare's College (ACT)"
REVIEW_ID = school_review_id(NAME)


def row(evidence: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    return {
        "review_id": REVIEW_ID,
        "entity_type": "school",
        "candidate_id": candidate_id,
        "payload": {"recorded_name": NAME},
        "evidence": evidence,
        "parliaments": [47, 48],
        "status": "pending",
    }


def event(action: str = "research", decision_id: str = "earlier") -> ReviewEvent:
    return ReviewEvent(
        decision_id=decision_id,
        review_id=REVIEW_ID,
        entity_type="school",
        action=action,
        payload={
            "recorded_name": NAME,
            "legacy": {
                "canonical_acara_id": "49968",
                "canonical_name": "St Clare's College",
            },
        },
        reviewer="Fixture Reviewer",
        notes="Relationship needs evidence",
        reviewed_at="2026-10-05",
        recorded_at="2026-10-05T00:00:00+00:00",
    )


def clare_item() -> dict[str, Any]:
    return {
        "review_id": REVIEW_ID,
        "entity_type": "school",
        "decision": event().to_dict(),
        "history": [],
        "context": {},
        "candidates": [
            row(
                {
                    "institution_id": "inst-unmatched-clare",
                    "school_name": NAME,
                    "acara_id": None,
                },
                "placeholder",
            ),
            row(
                {
                    "suggested_institution_name": "St Clare's College, Canberra",
                    "wikidata_id": "Q7592815",
                    "source_url": SOURCE,
                },
                "wiki",
            ),
        ],
    }


def test_st_clare_lead_placeholder_and_legacy_are_separate() -> None:
    service = FixtureService()
    service.institutions[0].update(
        institution_ref="acara:49968", school_name="St Clare's College"
    )
    view = school_view(clare_item(), service.resolve_institution)
    assert view.name == NAME and view.status == "needs_research"
    assert view.saved_reference == "" and view.saved_relationship == ""
    assert [target.reference for target in view.targets] == ["acara:49968"]
    assert view.targets[0].legacy and view.targets[0].sources == []
    assert len(view.source_records) == 1
    assert view.leads[0].name == "St Clare's College, Canberra"
    assert view.leads[0].reference == "" and view.leads[0].sources == [SOURCE]


def test_group_only_same_explicit_reference_and_keep_sources() -> None:
    service = FixtureService()
    item = {
        "review_id": REVIEW_ID,
        "candidates": [
            row({"acara_id": "123", "source_url": SOURCE}, "one"),
            row(
                {
                    "institution_ref": "acara:123",
                    "source_url": "https://example.edu.au/two",
                },
                "two",
            ),
            row({"acara_id": "456", "school_name": "Fixture School"}, "three"),
        ],
    }
    view = school_view(item, service.resolve_institution)
    assert [t.reference for t in view.targets] == ["acara:123", "acara:456"]
    assert len(view.targets[0].sources) == 2
    assert not view.leads and not view.source_records


def test_missing_register_unknown_legacy_and_empty_item() -> None:
    view = school_view(clare_item(), lambda ref: None)
    assert not view.targets and len(view.leads) == 2
    assert view.leads[1].reference == "acara:49968"
    empty = school_view({"review_id": REVIEW_ID, "candidates": []}, lambda ref: None)
    assert empty.name == REVIEW_ID and not empty.targets and not empty.leads


def test_grouping_preserves_other_entity_rows_and_parliaments() -> None:
    a = row({}, "a")
    b = {**row({}, "b"), "parliaments": [46]}
    other = {**a, "entity_type": "member", "review_id": "member:abc:gender"}
    groups = group_school_rows([a, other, b])
    assert len(groups) == 2 and groups[1] is other
    assert groups[0]["parliaments"] == [46, 47, 48]
    assert groups[0]["candidates"] == [a, b]
    assert a["parliaments"] == [47, 48]


@pytest.fixture
def real_school(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, ReviewService]:
    from apemap.review import service as service_module

    monkeypatch.setattr(service_module, "RAW_APH_DIR", tmp_path / "aph")
    monkeypatch.setattr(service_module, "RAW_WIKIMEDIA_DIR", tmp_path / "wiki")
    external = tmp_path / "external"
    external.mkdir()
    (external / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Suburb\n"
        "49968,St Clare's College,Catholic,Secondary,ACT,Griffith\n"
        "123,Fixture School,Government,Secondary,TAS,Hobart\n",
        encoding="utf-8",
    )
    log = tmp_path / "decisions.jsonl"
    log.write_bytes(encode_event(event()))
    service = ReviewService(
        log_path=log, db_path=tmp_path / "absent.duckdb", external_dir=external
    )
    app = create_app(service)
    app.config["TESTING"] = True
    return app.test_client(), service


def guided(client: Any, review_id: str = REVIEW_ID, **changes: Any) -> dict[str, Any]:
    html = client.get(f"/items/{review_id}").get_data(as_text=True)
    values = Inputs(html).values
    return {
        "csrf_token": values["csrf_token"],
        "form_token": values["form_token"],
        "school_workflow": "1",
        "payload_mode": "guided",
        "payload": json.dumps({"recorded_name": NAME, "provenance": "retained"}),
        "field_recorded_name": NAME,
        "field_institution_ref": "acara:49968",
        "field_relationship_type": "alias",
        "action": "map",
        "source_url": SOURCE,
        "reviewer": "School Reviewer",
        "notes": "Confirmed recorded-name relationship",
        **changes,
    }


def test_guided_mapping_replaces_earlier_event_after_read_only_preview(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    baseline = service.log_path.read_bytes()
    html = client.get(f"/items/{REVIEW_ID}").get_data(as_text=True)
    assert (
        "Earlier mapping lead—needs evidence" in html and "No school selected" in html
    )
    assert 'value="alias" selected' not in html
    preview = client.post(f"/items/{REVIEW_ID}/preview", data=guided(client))
    assert preview.status_code == 200
    assert "Replaces 1 active decision" in preview.get_data(as_text=True)
    assert "Canonical preview unavailable" in preview.get_data(as_text=True)
    assert service.log_path.read_bytes() == baseline
    values = Inputs(preview.get_data(as_text=True)).values
    saved = client.post(
        f"/items/{REVIEW_ID}/save",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
        },
    )
    assert saved.status_code == 303 and service.log_path.read_bytes().startswith(
        baseline
    )
    head = active_heads(service.events())[REVIEW_ID][0]
    assert head.action == "supersede" and head.effective_action == "map"
    assert head.supersedes == ["earlier"] and head.payload["provenance"] == "retained"
    updated = client.get(saved.headers["Location"]).get_data(as_text=True)
    assert "Saved mapping:" in updated and 'value="alias" selected' in updated


@pytest.mark.parametrize("action", ["research", "reject"])
def test_research_rejection_remove_target_but_retain_provenance(
    real_school: tuple[Any, ReviewService], action: str
) -> None:
    client, service = real_school
    response = client.post(
        f"/items/{REVIEW_ID}/preview", data=guided(client, action=action)
    )
    assert response.status_code == 200
    values = Inputs(response.get_data(as_text=True)).values
    client.post(
        f"/items/{REVIEW_ID}/save",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
        },
    )
    payload = service.show(REVIEW_ID)["decision"]["payload"]
    assert "institution_ref" not in payload and "relationship_type" not in payload
    assert payload["provenance"] == "retained"


@pytest.mark.parametrize(
    "field,value",
    [
        ("field_relationship_type", ""),
        ("field_institution_ref", ""),
    ],
)
def test_mapping_requires_target_and_relationship(
    real_school: tuple[Any, ReviewService], field: str, value: str
) -> None:
    client, service = real_school
    baseline = service.log_path.read_bytes()
    assert (
        client.post(
            f"/items/{REVIEW_ID}/preview", data=guided(client, **{field: value})
        ).status_code
        == 400
    )
    assert service.log_path.read_bytes() == baseline


def test_mapping_without_source_previews_and_saves_append_only(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    baseline = service.log_path.read_bytes()
    response = client.post(
        f"/items/{REVIEW_ID}/preview", data=guided(client, source_url="")
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "No source URL supplied." in html
    assert "Evidence source URL (optional)" in html
    assert service.log_path.read_bytes() == baseline
    values = Inputs(html).values
    saved = client.post(
        f"/items/{REVIEW_ID}/save",
        data={
            "csrf_token": values["csrf_token"],
            "preview_token": values["preview_token"],
        },
    )
    assert saved.status_code == 303
    assert service.log_path.read_bytes().startswith(baseline)
    assert service.events()[-1].source_url == ""


@pytest.mark.parametrize(
    "source", ["not a URL", "javascript:alert(1)", "https://user:secret@example.org"]
)
def test_mapping_rejects_invalid_optional_source(
    real_school: tuple[Any, ReviewService], source: str
) -> None:
    client, service = real_school
    baseline = service.log_path.read_bytes()
    response = client.post(
        f"/items/{REVIEW_ID}/preview", data=guided(client, source_url=source)
    )
    assert response.status_code == 400 and "data-save-school" not in response.get_data(
        as_text=True
    )
    assert service.log_path.read_bytes() == baseline


@pytest.mark.parametrize("snapshot", [False, True])
def test_parliamentarian_context_uses_source_attendance_and_service(
    real_school: tuple[Any, ReviewService],
    database_factory: DatabaseFactory,
    snapshot: bool,
) -> None:
    client, service = real_school
    path, conn = database_factory(None)
    conn.executemany(
        "INSERT INTO members (member_id, family_name, given_name, display_name, aph_id) VALUES (?, 'Person', 'Test', ?, ?)",
        [
            ("one", "First Person", "ONE"),
            ("two", "Second <Person>", "TWO"),
            ("other", "Unrelated Person", "OTHER"),
        ],
    )
    conn.executemany(
        "INSERT INTO parliament_service (service_id, member_id, parliament_number, chamber, party, party_abbrev, electorate, state_or_territory) VALUES (?, ?, ?, 'representatives', 'Party', 'P', ?, ?)",
        [
            ("s1", "one", 47, "Canberra", "ACT"),
            ("s2", "one", 48, "Canberra", "ACT"),
            ("s3", "one", 48, "Canberra", "ACT"),
            ("s4", "two", 48, "Hobart", "TAS"),
        ],
    )
    conn.executemany(
        "INSERT INTO institutions (institution_id, school_name, sector, suburb, state, country) VALUES (?, ?, 'Other', ?, ?, ?)",
        [
            ("i1", NAME, "Griffith", "ACT", "Australia"),
            ("i2", NAME.upper(), "Dublin", None, "Ireland"),
            ("i3", "Another School", None, None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded) VALUES (?, ?, ?, 'secondary', 'attended_unspecified', ?, '2026-10-02T00:00:00+00:00', 'verified', ?)",
        [
            ("e1", "one", "i1", SOURCE, NAME),
            ("e2", "two", "i2", "javascript:alert(1)", NAME.upper()),
            ("e3", "other", "i3", SOURCE, "Another School"),
        ],
    )
    if snapshot:
        from apemap.review.integration import capture_review_sources

        capture_review_sources(conn)
        conn.execute("UPDATE parliament_service SET state_or_territory = 'VIC'")
        conn.execute("UPDATE institutions SET country = 'Changed country'")
        conn.execute("UPDATE members SET display_name = 'Changed name'")
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded = 'Changed school'"
        )
    conn.close()
    service.db_path = path
    baseline = hashlib.sha256(path.read_bytes()).hexdigest()
    members = service.show(REVIEW_ID)["context"]["members"]
    assert [member["display_name"] for member in members] == [
        "First Person",
        "Second <Person>",
    ]
    assert len(members[0]["services"]) == 2  # Repeated service intervals share a term.
    html = client.get(f"/items/{REVIEW_ID}").get_data(as_text=True)
    assert 'href="https://handbook.aph.gov.au/individual/ONE"' in html
    assert "Second &lt;Person&gt;" in html and "Unrelated Person" not in html
    assert (
        "Represented state/territory: ACT" in html
        and "Represented state/territory: TAS" in html
    )
    assert "Griffith · ACT · Australia" in html and "Dublin · Ireland" in html
    assert "Changed country" not in html and "Changed school" not in html
    assert "Review attendance" in html and 'href="javascript:alert(1)"' not in html
    assert html.index("Parliamentarians using this school name") < html.index(
        "Possible matches"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == baseline


def test_school_without_database_has_explicit_missing_member_context(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    assert service.show(REVIEW_ID)["context"]["members"] == []
    assert "No parliamentarian context is available" in client.get(
        f"/items/{REVIEW_ID}"
    ).get_data(as_text=True)


def test_stale_form_retains_draft_and_requires_fresh_preview(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    draft = guided(client)
    appended = service.prepare(
        REVIEW_ID,
        "research",
        {"recorded_name": NAME},
        notes="New independent review",
        reviewer="Another Reviewer",
        supersedes=["earlier"],
    )
    service.save(appended)
    baseline = service.log_path.read_bytes()
    response = client.post(f"/items/{REVIEW_ID}/preview", data=draft)
    html = response.get_data(as_text=True)
    assert response.status_code == 409 and "Decisions changed" in html
    assert 'aria-label="Current decision"' in html and "New independent review" in html
    assert (
        "Confirmed recorded-name relationship" in html and 'value="acara:49968"' in html
    )
    assert "data-save-school" not in html and service.log_path.read_bytes() == baseline
    draft["form_token"] = Inputs(html).values["form_token"]
    assert client.post(f"/items/{REVIEW_ID}/preview", data=draft).status_code == 200


def test_guided_conflict_supersedes_every_head(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    service.log_path.write_bytes(
        encode_event(event()) + encode_event(event("reject", "other-head"))
    )
    response = client.post(
        f"/items/{REVIEW_ID}/preview", data=guided(client, action="research")
    )
    assert response.status_code == 200
    values = Inputs(response.get_data(as_text=True)).values
    assert (
        client.post(
            f"/items/{REVIEW_ID}/save",
            data={
                "csrf_token": values["csrf_token"],
                "preview_token": values["preview_token"],
            },
        ).status_code
        == 303
    )
    head = active_heads(service.events())[REVIEW_ID][0]
    assert (
        set(head.supersedes) == {"earlier", "other-head"}
        and head.effective_action == "research"
    )


def test_no_js_selection_search_and_source_preserve_draft(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    baseline = service.log_path.read_bytes()
    for changes, expected in [
        ({"choose_ref": "acara:123"}, 'value="acara:123"'),
        (
            {"use_source": "https://example.edu.au/new"},
            'value="https://example.edu.au/new"',
        ),
        ({"find_school": "Fixture"}, "Hobart"),
    ]:
        response = client.post(
            f"/items/{REVIEW_ID}/preview", data=guided(client, **changes)
        )
        html = response.get_data(as_text=True)
        assert response.status_code == 200 and expected in html
        assert (
            "School Reviewer" in html and "Confirmed recorded-name relationship" in html
        )
        assert 'value="alias" selected' in html and "data-save-school" not in html
    assert service.log_path.read_bytes() == baseline


def test_form_token_tamper_wrong_item_and_expiration(
    real_school: tuple[Any, ReviewService], monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import gui as gui_module

    client, service = real_school
    draft = guided(client)
    assert (
        client.post(
            f"/items/{REVIEW_ID}/save",
            data={
                "csrf_token": draft["csrf_token"],
                "preview_token": draft["form_token"],
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/items/{REVIEW_ID}/preview",
            data={**draft, "form_token": draft["form_token"] + "x"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/items/{SCHOOL_ID}/preview",
            data={**draft, "field_recorded_name": "Fixture School"},
        ).status_code
        == 400
    )
    now = gui_module.time.time()
    monkeypatch.setattr(gui_module.time, "time", lambda: now + PREVIEW_TTL + 1)
    assert client.post(f"/items/{REVIEW_ID}/preview", data=draft).status_code == 400
    assert len(service.events()) == 1


def test_queue_groups_before_search_and_parliament_filter() -> None:
    service = FixtureService()
    service.institutions[0]["institution_ref"] = "acara:49968"
    service.rows = [
        row({"school_name": NAME}, "one"),
        {
            **row({"suggested_institution_name": "Canberra lead"}, "two"),
            "parliaments": [46],
        },
    ]
    service.saved = [event()]
    client = create_app(service).test_client()
    html = client.get("/?entity_type=school&q=Canberra&parliament=47").get_data(
        as_text=True
    )
    assert "1 matching school mappings" in html and "46, 47, 48" in html
    assert html.count('<th scope="row">') == 1
    assert "1 unresolved evidence leads" in html
    assert len(service.candidates()) == 2


def test_exact_manual_resolution_only_accepts_one_active_definition(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    definition = ReviewEvent(
        decision_id="manual-definition",
        review_id="institution:manual:overseas",
        entity_type="manual_institution",
        action="accept",
        payload={
            "institution_ref": "manual:overseas",
            "school_name": "Overseas College",
            "country": "United Kingdom",
            "sector": "Other",
        },
        source_url=SOURCE,
        reviewer="Fixture Reviewer",
        reviewed_at="2026-10-05",
        recorded_at="2026-10-05T00:00:00+00:00",
    )
    with service.log_path.open("ab") as stream:
        stream.write(encode_event(definition))
    assert service.resolve_institution("manual:overseas") == definition.payload
    baseline = service.log_path.read_bytes()
    response = client.post(
        f"/items/{REVIEW_ID}/preview",
        data=guided(
            client,
            field_institution_ref="manual:overseas",
            field_relationship_type="direct",
        ),
    )
    assert response.status_code == 200 and "Overseas College" in response.get_data(
        as_text=True
    )
    assert service.log_path.read_bytes() == baseline
    assert service.resolve_institution("manual:missing") is None
    assert service.resolve_institution("acara:unknown") is None
    with service.log_path.open("ab") as stream:
        stream.write(
            encode_event(replace(definition, decision_id="conflicting-definition"))
        )
    assert service.resolve_institution("manual:overseas") is None


@pytest.mark.parametrize("source", [SOURCE, ""])
def test_readable_canonical_preview_leaves_database_unchanged(
    real_school: tuple[Any, ReviewService],
    database_factory: DatabaseFactory,
    source: str,
) -> None:
    client, service = real_school
    path, conn = database_factory(None)
    conn.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name, aph_id) VALUES ('aph-test', 'Person', 'Test', 'Test Person', 'TEST')"
    )
    conn.execute(
        "INSERT INTO parliament_service (service_id, member_id, parliament_number, chamber, party, party_abbrev, state_or_territory, service_start, service_end) VALUES ('srv-test', 'aph-test', 48, 'representatives', 'Party', 'P', 'ACT', '2025-07-22', NULL)"
    )
    conn.execute(
        "INSERT INTO institutions (institution_id, school_name, sector) VALUES ('inst-unmatched-clare', ?, 'Other')",
        [NAME],
    )
    conn.execute(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin) VALUES ('edu-test', 'aph-test', 'inst-unmatched-clare', 'secondary', 'attended_unspecified', ?, '2026-10-02T00:00:00+10:00', 'verified', ?, 'aph')",
        [SOURCE, NAME],
    )
    from apemap.review.integration import capture_review_sources

    capture_review_sources(conn)
    conn.close()
    service.db_path = path
    baseline = hashlib.sha256(path.read_bytes()).hexdigest()
    response = client.post(
        f"/items/{REVIEW_ID}/preview", data=guided(client, source_url=source)
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Affected education assertions" in html and "1 updated" in html
    assert "acara-49968" in html and "Alternate name" in html
    assert hashlib.sha256(path.read_bytes()).hexdigest() == baseline
    assert len(service.events()) == 1


@pytest.mark.parametrize("action", ["research", "reject"])
def test_unresolved_decision_requires_reason(
    real_school: tuple[Any, ReviewService], action: str
) -> None:
    client, service = real_school
    response = client.post(
        f"/items/{REVIEW_ID}/preview",
        data=guided(
            client,
            action=action,
            notes="",
            field_institution_ref="",
            field_relationship_type="",
        ),
    )
    assert response.status_code == 400
    assert "requires a reason" in response.get_data(as_text=True)
    assert len(service.events()) == 1


def test_advanced_school_payload_retains_explicit_action(
    real_school: tuple[Any, ReviewService],
) -> None:
    client, service = real_school
    draft = guided(
        client,
        payload_mode="json",
        advanced_action="supersede",
        replacement_action="accept",
        supersedes="earlier",
        payload=json.dumps(
            {
                "recorded_name": NAME,
                "institution_ref": "acara:49968",
                "relationship_type": "rename",
                "extra": "Preserved",
            }
        ),
    )
    response = client.post(f"/items/{REVIEW_ID}/preview", data=draft)
    assert response.status_code == 200
    values = Inputs(response.get_data(as_text=True)).values
    assert (
        client.post(
            f"/items/{REVIEW_ID}/save",
            data={
                "csrf_token": values["csrf_token"],
                "preview_token": values["preview_token"],
            },
        ).status_code
        == 303
    )
    head = active_heads(service.events())[REVIEW_ID][0]
    assert (
        head.effective_action == "accept"
        and head.payload["relationship_type"] == "rename"
    )
    assert head.payload["extra"] == "Preserved"
