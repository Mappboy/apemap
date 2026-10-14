"""Deterministic manual proposals retain the research batch approval boundary."""

from pathlib import Path
from typing import Any

import pytest

from apemap.db import get_connection
from apemap.review.evidence import EvidenceRecord
from apemap.review.model import school_review_id
from apemap.review.store import StaleReviewError
from apemap.review.research_batches import ResearchBatches
from tests.test_research_batches import accept, batches as batches, school_proposal
from tests.test_review_service import review_service as review_service


def facts_and_evidence(
    rid: str, name: str = "Historical School", country: str = "Australia"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    facts: dict[str, Any] = {
        "name": name,
        "country": country,
        "status": "closed",
        "evidence_refs": {},
    }
    records = []
    for field in ("name", "country", "status"):
        record = EvidenceRecord(
            review_id=rid,
            candidate_institution_ref=None,
            source_title="School history",
            source_type="assisted-search",
            source_url="https://education.example.org/history",
            retrieved_at="2026-10-14T00:00:00+00:00",
            claim_type="institution_fact",
            claim_value={"field": field, "value": facts[field]},
            stance="supports",
            excerpt_or_note="The institutional history establishes this fact.",
            generated_by="agent-search",
        ).to_dict()
        records.append(record)
        facts["evidence_refs"][field] = [record["evidence_id"]]
    return facts, records


def fake_school(batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch) -> str:
    rid = school_review_id("Historical School")
    monkeypatch.setattr(
        batches.service,
        "candidates",
        lambda kind: [{"review_id": rid, "evidence": {}, "entity_type": "school"}],
    )
    monkeypatch.setattr(
        batches.service,
        "research_context",
        lambda key: {
            "review_id": key,
            "assertion": {"recorded_school_name": "Historical School"},
            "candidates": [],
            "selected_candidate": None,
        },
    )
    monkeypatch.setattr(
        batches.service, "show", lambda key: {"context": {"members": []}}
    )
    return rid


def test_manual_prepare_is_persistent_and_writes_no_ledgers(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    state = batches.prepare_manual_proposal(
        "manual", rid, facts, records, {"provider_id": "configured", "model": "actual"}
    )
    assert state["manifest"]["origin"] == "institution-facts"
    item = state["proposals"][0]
    assert item["attempts"] == []
    assert item["manual_candidate"]["state"] == "create"
    assert item["manual_institutions"][0]["institution_status"] == "closed"
    assert not batches.service.events()
    assert not batches.service.evidence_records()
    assert ResearchBatches(batches.service).show("manual")["proposals"] == [item]


def test_exact_case_preparation_does_not_silently_pick_another_case(
    batches: ResearchBatches,
) -> None:
    state = batches.prepare("selected", "members", review_ids=["member:m8:wikidata_id"])
    assert [task["review_id"] for task in state["tasks"]] == ["member:m8:wikidata_id"]
    with pytest.raises(ValueError, match="requested"):
        batches.prepare("missing", "members", review_ids=["member:absent:wikidata_id"])
    assert not batches.path("missing").exists()


def test_manual_acceptance_records_explicit_mapping_choice(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    state = batches.prepare_manual_proposal("manual", rid, facts, records)
    item = state["proposals"][0]
    accept(batches, "manual", item, institution_action="create_only")
    state = batches.show("manual")
    payload = batches._proposal_payload(state, item)
    assert "institution_ref" not in payload
    assert payload["requires_individual_resolution"] is True
    assert state["reviews"][item["proposal_id"]]["institution_action"] == "create_only"


def test_repeated_manual_save_is_idempotent_and_preserves_provenance(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    first = batches.prepare_manual_proposal(
        "manual", rid, facts, records, {"provider_id": "one"}
    )
    repeated = batches.prepare_manual_proposal(
        "manual", rid, facts, records, {"provider_id": "one"}
    )
    assert repeated["content_sha256"] == first["content_sha256"]
    assert len(list(batches.path("manual").glob("proposals-*.json"))) == 1
    with pytest.raises(ValueError, match="differs"):
        batches.prepare_manual_proposal(
            "manual", rid, facts, records, {"provider_id": "different"}
        )


def test_incomplete_manual_facts_remain_research_needed(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    facts["evidence_refs"].pop("country")
    state = batches.prepare_manual_proposal("manual", rid, facts, records)
    item = state["proposals"][0]
    assert item["outcome"] == "unresolved"
    assert item["manual_institutions"] == []
    with pytest.raises(ValueError, match="further research"):
        accept(batches, "manual", item, institution_action="create_map")
    batches.record_review(
        "manual",
        item["proposal_id"],
        "research",
        {record["evidence_id"]: record["stance"] for record in records},
        reviewer="Reviewer",
        notes="Country support is missing.",
    )
    assert not batches.service.events()


def test_manual_batch_ui_exposes_sources_and_preserves_creation_choice(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("flask")
    from apemap.review.gui import create_app
    from apemap.review import research_batches
    from tests.test_research_batch_ui import Controls

    monkeypatch.setattr(research_batches, "default_reviewer", lambda: "Test reviewer")
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    item = batches.prepare_manual_proposal("manual", rid, facts, records)["proposals"][
        0
    ]
    with create_app(batches.service).test_client() as client:
        page = client.get("/research-batches/manual")
        assert page.status_code == 200
        assert b"Sourced manual institution proposal" in page.data
        assert b"Unknown" in page.data
        controls = Controls(page.get_data(as_text=True))
        data = {
            "csrf_token": controls.inputs["csrf_token"]["value"],
            "binding": controls.inputs["binding"]["value"],
            "action": "accept",
            "institution_action": "create_only",
        }
        for record in records:
            data["inspected_" + record["evidence_id"]] = "yes"
            data["disposition_" + record["evidence_id"]] = record["stance"]
        response = client.post(
            f"/research-batches/manual/review/{item['proposal_id']}",
            data=data,
        )
        assert response.status_code == 303
        loaded = client.get(response.location)
        assert (
            Controls(loaded.get_data(as_text=True)).selected["institution_action"]
            == "create_only"
        )


def test_guided_candidate_import_keeps_model_attempt_rules(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    task = batches.prepare("guided", "schools", review_ids=[rid])["tasks"][0]
    facts, records = facts_and_evidence(rid)
    raw = school_proposal(task)
    raw.update(institution_candidate=facts, evidence=records, attempts=[], payload=None)
    with pytest.raises(ValueError, match="Luna"):
        batches.import_results("guided", [raw])
    raw["attempts"] = [
        {
            "model": "gpt-6-luna",
            "status": "resolved",
            "searches": 1,
            "source_opens": 1,
            "token_usage": None,
            "gaps": [],
        }
    ]
    raw["outcome"] = "mapping"
    assert (
        batches.import_results("guided", [raw])["proposals"][0]["manual_candidate"][
            "state"
        ]
        == "create"
    )


@pytest.mark.parametrize("alias,individual", [(True, False), (False, True)])
def test_unsafe_shared_mapping_is_blocked_but_create_only_is_available(
    batches: ResearchBatches,
    monkeypatch: pytest.MonkeyPatch,
    alias: bool,
    individual: bool,
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(
        rid, "Actual Institution" if alias else "Historical School"
    )
    state = batches.prepare_manual_proposal(
        "guarded", rid, facts, records, {"requires_individual_resolution": individual}
    )
    item = state["proposals"][0]
    with pytest.raises(ValueError, match="Create only"):
        accept(batches, "guarded", item, institution_action="create_map")
    accept(batches, "guarded", item, institution_action="create_only")
    assert "institution_ref" not in batches._proposal_payload(
        batches.show("guarded"), item
    )


def test_alias_mapping_requires_an_explicit_unattributed_identity_claim(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid, "Actual Institution")
    records.append(
        EvidenceRecord(
            review_id=rid,
            candidate_institution_ref=None,
            source_title="History",
            source_type="assisted-search",
            source_url="https://education.example.org/history",
            retrieved_at="2026-10-14T00:00:00+00:00",
            claim_type="identity",
            claim_value={
                "name": "Actual Institution",
                "country": "Australia",
                "recorded_school_name": "Historical School",
            },
            stance="supports",
            excerpt_or_note="History documents the former recorded school name.",
            generated_by="agent-search",
        ).to_dict()
    )
    item = batches.prepare_manual_proposal("alias", rid, facts, records)["proposals"][0]
    accept(batches, "alias", item, institution_action="create_map")
    assert batches._proposal_payload(batches.show("alias"), item)["institution_ref"]


def test_source_change_during_manual_save_cannot_rebind_old_evidence(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    expected = {
        "source_revision": "source",
        "decision_revision": batches.service.review_revision(),
    }
    original = batches._manual_candidate

    def change_source(
        facts: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        candidate = original(facts, evidence)
        monkeypatch.setattr(
            batches.service, "source_revision", lambda **kwargs: "changed"
        )
        return candidate

    monkeypatch.setattr(batches, "_manual_candidate", change_source)
    with pytest.raises(StaleReviewError, match="inputs changed"):
        batches.prepare_manual_proposal(
            "stale", rid, facts, records, expected_revisions=expected
        )
    assert not batches.path("stale").exists()


def test_unsupplied_member_cannot_enter_a_manual_fact_proposal(
    batches: ResearchBatches, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    rid = fake_school(batches, monkeypatch)
    facts, records = facts_and_evidence(rid)
    prior = records[0]["evidence_id"]
    records[0] = replace(
        EvidenceRecord.from_dict(records[0]),
        claim_value={
            "field": "name",
            "value": facts["name"],
            "aph_id": "other",
            "recorded_school_name": "Historical School",
        },
        evidence_id="",
    ).to_dict()
    facts["evidence_refs"]["name"] = [
        records[0]["evidence_id"] if ref == prior else ref
        for ref in facts["evidence_refs"]["name"]
    ]
    with pytest.raises(ValueError, match="supplied"):
        batches.prepare_manual_proposal("forged", rid, facts, records)
    assert not batches.path("forged").exists()
    assert not batches.service.events()


@pytest.mark.integration
def test_education_scoped_facts_create_only_keeps_foreign_refs_off_school_decision(
    review_service: Any, tmp_path: Path
) -> None:
    from apemap.review.integration import capture_review_sources
    from apemap.review.model import education_review_id

    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "UPDATE institutions SET acara_id = NULL, school_name = 'Historical School'"
        )
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded = 'Historical School'"
        )
        capture_review_sources(conn)
    batches = ResearchBatches(review_service)
    rid = school_review_id("Historical School")
    facts, records = facts_and_evidence(
        education_review_id("TEST", "Historical School")
    )
    item = batches.prepare_manual_proposal("scoped", rid, facts, records)["proposals"][
        0
    ]
    with pytest.raises(ValueError, match="individual"):
        accept(batches, "scoped", item, institution_action="create_map")
    accept(batches, "scoped", item, institution_action="create_only")
    batches.retain("scoped")
    result = batches.preview(
        "scoped", tmp_path / "scoped.json", reviewer="Test reviewer"
    )
    assert result["preview"]["events"][1]["payload"]["evidence_refs"] == []
    batches.apply("scoped", Path(result["preview_path"]), result["sha256"])
    assert len(review_service.events()) == 2


@pytest.mark.integration
def test_international_facts_create_a_direct_manual_mapping_and_replay_offline(
    review_service: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review.integration import (
        apply_review_events,
        capture_review_sources,
        read_review_events,
    )

    name = "International College"
    with get_connection(review_service.db_path) as conn:
        conn.execute("UPDATE institutions SET acara_id = NULL, school_name = ?", [name])
        conn.execute("UPDATE member_education SET school_name_as_recorded = ?", [name])
        capture_review_sources(conn)
    batches = ResearchBatches(review_service)
    rid = school_review_id(name)
    facts, records = facts_and_evidence(rid, name, "New Zealand")
    item = batches.prepare_manual_proposal("international", rid, facts, records)[
        "proposals"
    ][0]
    ref = item["manual_candidate"]["institution_ref"]
    accept(batches, "international", item, institution_action="create_map")
    batches.retain("international")
    preview = batches.preview(
        "international", tmp_path / "international.json", reviewer="Test reviewer"
    )
    definition, mapping = preview["preview"]["events"]
    assert definition["payload"]["country"] == "New Zealand"
    assert definition["payload"]["sector"] is None
    assert not definition["payload"].get("acara_id")
    assert mapping["payload"]["institution_ref"] == ref
    assert mapping["payload"]["relationship_type"] == "direct"
    batches.apply("international", Path(preview["preview_path"]), preview["sha256"])
    decisions, evidence = (
        review_service.log_path.read_bytes(),
        review_service.evidence_path.read_bytes(),
    )
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    consumed = read_review_events(review_service.log_path)
    with get_connection(review_service.db_path) as conn:
        apply_review_events(conn, events=consumed, matcher=review_service.matcher())
        row = conn.execute(
            "SELECT i.country, i.acara_id, i.sector, e.institution_resolution, e.attended_status "
            "FROM member_education e JOIN institutions i USING (institution_id) WHERE e.education_id='edu-test'"
        ).fetchone()
        assert row == ("New Zealand", None, None, "direct", "attended_unspecified")
    assert review_service.log_path.read_bytes() == decisions
    assert review_service.evidence_path.read_bytes() == evidence


@pytest.mark.integration
def test_historical_manual_original_and_independent_successor_survive_replay(
    review_service: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy
    from dataclasses import replace
    from apemap.review.integration import (
        apply_review_events,
        capture_review_sources,
        read_review_events,
    )

    name = "Historical School"
    with get_connection(review_service.db_path) as conn:
        conn.execute("UPDATE institutions SET acara_id = NULL, school_name = ?", [name])
        conn.execute("UPDATE member_education SET school_name_as_recorded = ?", [name])
        capture_review_sources(conn)
    batches = ResearchBatches(review_service)
    rid = school_review_id(name)
    task = batches.prepare("successor", "schools", review_ids=[rid])["tasks"][0]
    facts, records = facts_and_evidence(rid)
    sector = replace(
        EvidenceRecord.from_dict(records[0]),
        claim_value={"field": "sector", "value": "Government"},
        source_url="https://education.example.org/original-sector",
        evidence_id="",
    ).to_dict()
    records.append(sector)
    facts["sector"] = "Government"
    facts["evidence_refs"]["sector"] = [sector["evidence_id"]]
    successor = replace(
        EvidenceRecord.from_dict(records[0]),
        candidate_institution_ref="acara:2",
        claim_type="successor",
        claim_value={
            "attended_name": name,
            "attended_country": "Australia",
            "institution_ref": "acara:2",
            "relationship_type": "successor",
        },
        source_url="https://education.example.org/successor",
        evidence_id="",
    ).to_dict()
    records.append(successor)
    raw = school_proposal(task)
    raw.update(
        outcome="successor",
        reasoning="The original school closed; an independently documented successor has a distinct institution identity.",
        institution_candidate=facts,
        evidence=records,
        payload={
            "recorded_name": name,
            "institution_ref": "acara:2",
            "relationship_type": "successor",
            "historical_scope_confirmed": True,
            "campus_continuity": "different_campus",
            "campus_continuity_source_url": successor["source_url"],
        },
    )
    unsupported = deepcopy(raw)
    unsupported["evidence"] = records[:-1]
    with pytest.raises(ValueError, match="linking the original identity"):
        batches.import_results("successor", [unsupported])
    forged_original = deepcopy(raw)
    forged_original["payload"]["attended_institution_ref"] = "manual:unrelated-original"
    with pytest.raises(ValueError, match="built original"):
        batches.import_results("successor", [forged_original])
    assert not review_service.events()
    item = batches.import_results("successor", [raw])["proposals"][0]
    ref = item["manual_candidate"]["institution_ref"]
    assert item["payload"]["attended_institution_ref"] == ref
    accept(batches, "successor", item, institution_action="create_map")
    batches.retain("successor")
    preview = batches.preview(
        "successor", tmp_path / "successor.json", reviewer="Test reviewer"
    )
    definition, mapping = preview["preview"]["events"]
    assert definition["payload"]["institution_ref"] == ref
    assert definition["payload"]["sector"] == "Government"
    assert definition["payload"]["institution_status"] == "closed"
    assert "acara_id" not in definition["payload"]
    assert definition["payload"].get("latitude") is None
    assert definition["payload"].get("longitude") is None
    assert mapping["payload"]["institution_ref"] == "acara:2"
    assert mapping["payload"]["attended_institution_ref"] == ref
    assert mapping["payload"]["campus_continuity"] == "different_campus"
    batches.apply("successor", Path(preview["preview_path"]), preview["sha256"])
    decisions, evidence = (
        review_service.log_path.read_bytes(),
        review_service.evidence_path.read_bytes(),
    )
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    consumed = read_review_events(review_service.log_path)
    with get_connection(review_service.db_path) as conn:
        apply_review_events(conn, events=consumed, matcher=review_service.matcher())
        row = conn.execute(
            "SELECT e.institution_id, e.attended_institution_id, i.sector, original.sector, "
            "original.institution_status, e.campus_continuity, e.attended_status "
            "FROM member_education e JOIN institutions i ON e.institution_id=i.institution_id "
            "JOIN institutions original ON e.attended_institution_id=original.institution_id "
            "WHERE e.education_id='edu-test'"
        ).fetchone()
        assert row == (
            "acara-2",
            ref,
            "Independent",
            "Government",
            "closed",
            "different_campus",
            "attended_unspecified",
        )
    assert review_service.log_path.read_bytes() == decisions
    assert review_service.evidence_path.read_bytes() == evidence


@pytest.mark.integration
@pytest.mark.parametrize("split", [False, True])
def test_identical_definitions_are_deduplicated_across_batch_subsets(
    review_service: Any, tmp_path: Path, split: bool
) -> None:
    from apemap.review.integration import capture_review_sources

    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "UPDATE institutions SET acara_id = NULL, school_name = 'Historical School'"
        )
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded = 'Historical School'"
        )
        conn.execute(
            "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, "
            "source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin) "
            "VALUES ('edu-alias', 'aph-test', 'acara-1', 'secondary', 'attended_unspecified', "
            "'https://example.org/bio', '2026-10-02T00:00:00+10:00', 'verified', 'Historical Alias', 'aph')"
        )
        capture_review_sources(conn)
    batches = ResearchBatches(review_service)
    prepared = batches.prepare(
        "shared",
        "schools",
        review_ids=[
            school_review_id(name) for name in ("Historical School", "Historical Alias")
        ],
    )
    definition = {
        "institution_ref": "manual:shared-history",
        "school_name": "Historical School",
        "country": "Australia",
        "sector": "Government",
        "institution_status": "closed",
    }
    raw_results = []
    for task in prepared["tasks"]:
        raw = school_proposal(task)
        raw["payload"] = {
            "recorded_name": task["context"]["assertion"]["recorded_school_name"],
            "institution_ref": definition["institution_ref"],
            "relationship_type": "direct",
        }
        raw["manual_institutions"] = [definition]
        raw_results.append(raw)
    items = batches.import_results("shared", raw_results)["proposals"]
    accept(batches, "shared", items[0])
    if not split:
        accept(batches, "shared", items[1])
    batches.retain("shared")
    first = batches.preview("shared", tmp_path / "first.json", reviewer="Test reviewer")
    assert (
        sum(
            event["entity_type"] == "manual_institution"
            for event in first["preview"]["events"]
        )
        == 1
    )
    batches.apply("shared", Path(first["preview_path"]), first["sha256"])
    if split:
        accept(batches, "shared", items[1])
        batches.retain("shared")
        second = batches.preview(
            "shared", tmp_path / "second.json", reviewer="Test reviewer"
        )
        assert len(second["preview"]["events"]) == 1
        batches.apply("shared", Path(second["preview_path"]), second["sha256"])
    assert (
        len(
            [
                event
                for event in review_service.events()
                if event.entity_type == "manual_institution"
            ]
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "mode,expected_action", [("create_map", "accept"), ("create_only", "research")]
)
def test_manual_preview_and_apply_preserve_the_exact_choice(
    review_service: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_action: str,
) -> None:
    from apemap.review.integration import capture_review_sources

    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "UPDATE institutions SET acara_id = NULL, school_name = 'Historical School'"
        )
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded = 'Historical School'"
        )
        capture_review_sources(conn)
    batches = ResearchBatches(review_service)
    rid = school_review_id("Historical School")
    facts, records = facts_and_evidence(rid)
    state = batches.prepare_manual_proposal("manual", rid, facts, records)
    item = state["proposals"][0]
    accept(batches, "manual", item, institution_action=mode)
    batches.retain("manual")
    path = tmp_path / "manual-preview.json"
    result = batches.preview("manual", path, reviewer="Test reviewer")
    events = result["preview"]["events"]
    assert [event["action"] for event in events] == ["accept", expected_action]
    definition = events[0]["payload"]
    assert definition["institution_fact_evidence_refs"] == facts["evidence_refs"]
    applied = batches.apply("manual", path, result["sha256"])
    assert len(applied["events"]) == 2
    recovered = batches.apply("manual", path, result["sha256"])
    assert recovered["already_applied"]
    assert len(review_service.events()) == 2
