"""Missing-school research stays pending until explicit individual approval."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apemap.db import get_connection, init_schema
from apemap.review.evidence import EvidenceRecord
from apemap.review.integration import _write_records, project_review_records
from apemap.review.manual_institutions import build_manual_institution_candidate
from apemap.review.missing_education_results import PURPOSE
from apemap.review.model import education_review_id, member_review_id, school_review_id
from apemap.review.research_batches import ResearchBatches, _digest, _write
from apemap.review.service import ReviewService, build_review_event
from apemap.review.store import StaleReviewError, append_events
from tests.test_manual_research_batches import facts_and_evidence
from tests.test_research_batches import accept
from tests.test_review_service import review_service as review_service

RID = education_review_id("TEST", "")
WIKIPEDIA = "https://en.wikipedia.org/wiki/Test_Person"


def record(
    review_id: str,
    url: str,
    claim_type: str,
    claim: dict[str, Any],
    target: str | None = None,
) -> dict[str, Any]:
    return EvidenceRecord(
        review_id=review_id,
        candidate_institution_ref=target,
        source_title="Fixture source",
        source_type="agent-research",
        source_url=url,
        retrieved_at="2026-10-18T01:00:00+00:00",
        claim_type=claim_type,
        claim_value=claim,
        stance="supports",
        excerpt_or_note="The source explicitly supports the stated fact.",
        generated_by="agent-search",
    ).to_dict()


def result(*, positive: bool = True, two_schools: bool = False) -> dict[str, Any]:
    identity = {
        "name": "Test Person",
        "aph_id": "TEST",
        "wikidata_id": "Q1",
        "wikidata_status": "confirmed",
        "wikipedia_url": WIKIPEDIA,
        "wikipedia_status": "confirmed",
        "reasoning": "The article and entity identify this parliamentarian.",
        "evidence_refs": {},
    }
    records = []
    for field, url in (
        ("wikidata_id", "https://www.wikidata.org/wiki/Q1"),
        ("wikipedia_url", WIKIPEDIA),
    ):
        evidence = record(
            member_review_id("TEST", "wikidata_id"),
            url,
            "member_identity",
            {"aph_id": "TEST", field: identity[field]},
        )
        records.append(evidence)
        identity["evidence_refs"][field] = [evidence["evidence_id"]]
    education = []
    for name, target in (
        (
            [("Test High School", "acara:1"), ("Other High School", "acara:2")]
            if two_schools
            else [("Test High School", "acara:1")]
        )
        if positive
        else []
    ):
        review_id = education_review_id("TEST", name)
        attendance = record(
            review_id,
            "https://example.org/attendance/" + target.split(":")[1],
            "attendance",
            {"aph_id": "TEST", "recorded_school_name": name, "level": "secondary"},
        )
        school = record(
            review_id,
            "https://example.org/institution/" + target.split(":")[1],
            "institution_identity",
            {"name": name, "country": "Australia"},
            target,
        )
        records.extend([attendance, school])
        education.append(
            {
                "aph_id": "TEST",
                "recorded_school_name": name,
                "attended_status": "attended_unspecified",
                "confidence": "verified",
                "retrieved_at": "2026-10-18T01:00:00+00:00",
                "institution_ref": target,
                "relationship_type": "direct",
                "evidence_refs": [attendance["evidence_id"], school["evidence_id"]],
            }
        )
    return {
        "review_id": RID,
        "outcome": "attendance" if positive else "unresolved",
        "value": None,
        "reasoning": "Explicit sourced secondary attendance."
        if positive
        else "Both passes found no sourced secondary attendance; this is not evidence of non-attendance.",
        "identity": identity,
        "alternatives": [],
        "attempts": [],
        "payload": None,
        "manual_institutions": [],
        "education_proposals": education,
        "evidence": records,
        "stages": {
            "wikipedia": {
                "status": "completed",
                "outcome": "found" if positive else "not_found",
                "notes": "Read the article and inspected its relevant citations.",
            },
            "final_broad": {
                "status": "not_needed" if positive else "completed",
                "outcome": "not_needed" if positive else "not_found",
                "notes": "Wikipedia establishes attendance."
                if positive
                else "One final broad search found no supported school.",
            },
        },
        "research_log": {
            "searches": 0 if positive else 1,
            "source_opens": 1,
            "search_queries": []
            if positive
            else [{"stage": "final_broad", "query": '"Test Person" secondary school'}],
            "opened_sources": [
                {"stage": "wikipedia", "url": WIKIPEDIA, "status": "opened"}
            ],
        },
    }


@pytest.fixture
def missing_batches(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> ResearchBatches:
    with get_connection(review_service.db_path) as conn:
        conn.execute("DELETE FROM member_education")
        conn.execute("DELETE FROM review_source_member_education")

    def unavailable_reviewer() -> str:
        raise AssertionError("Tests must supply an explicit reviewer")

    monkeypatch.setattr("apemap.review.service.default_reviewer", unavailable_reviewer)
    monkeypatch.setattr(
        "apemap.review.research_batches.default_reviewer", unavailable_reviewer
    )
    batches = ResearchBatches(review_service)
    task = {
        "purpose": PURPOSE,
        "review_id": RID,
        "context": {
            "member": {"aph_id": "TEST", "display_name": "Test Person"},
            "assertion": {"recorded_school_name": ""},
            "services": [],
        },
        "decision_heads": [],
    }
    path = batches.path("missing")
    path.mkdir(parents=True)
    audit = {"fixture": True}
    _write(path / "audit.json", audit)
    _write(path / "tasks.json", [task])
    _write(
        path / "manifest.json",
        {
            "schema_version": 1,
            "batch_id": "missing",
            "purpose": PURPOSE,
            "kind": "schools",
            "origin": "agent-session",
            "source_revision": review_service.source_revision(include_evidence=False),
            "decision_revision": review_service.review_revision(),
            "task_sha256": _digest([task]),
            "audit_sha256": _digest(audit),
            "case_count": 1,
        },
    )
    return batches


def test_research_import_and_negative_review_change_no_authority(
    missing_batches: ResearchBatches,
) -> None:
    state = missing_batches.import_results("missing", [result(positive=False)])
    proposal = state["proposals"][0]
    assert not state["sample"] and not state["reviews"]
    assert proposal["purpose"] == PURPOSE
    assert proposal["stages"]["final_broad"]["outcome"] == "not_found"
    with pytest.raises(ValueError, match="negatives"):
        accept(missing_batches, "missing", proposal)
    missing_batches.record_review(
        "missing",
        proposal["proposal_id"],
        "research",
        {item["evidence_id"]: item["stance"] for item in proposal["evidence"]},
        reviewer="Human reviewer",
        notes="Completed both passes; preserve the negative finding.",
    )
    assert (
        missing_batches.show("missing")["reviews"][proposal["proposal_id"]]["action"]
        == "research"
    )
    assert not missing_batches.service.events()
    assert not missing_batches.service.evidence_path.exists()
    with pytest.raises(ValueError, match="No approved"):
        missing_batches.retain("missing")


def test_two_new_schools_expand_into_named_accepts_and_receipt(
    missing_batches: ResearchBatches, tmp_path: Path
) -> None:
    proposal = missing_batches.import_results("missing", [result(two_schools=True)])[
        "proposals"
    ][0]
    assert not missing_batches.service.events()
    with pytest.raises(ValueError, match="No approved"):
        missing_batches.preview(
            "missing", tmp_path / "unreviewed.json", reviewer="Human reviewer"
        )
    accept(missing_batches, "missing", proposal)
    missing_batches.retain("missing")
    preview = missing_batches.preview(
        "missing", tmp_path / "accepted.json", reviewer="Human reviewer"
    )
    events = preview["preview"]["events"]
    assert [event["review_id"] for event in events] == [
        education_review_id("TEST", name)
        for name in ("Test High School", "Other High School")
    ]
    assert {event["action"] for event in events} == {"accept"}
    assert all(
        event["source_url"].startswith("https://example.org/attendance/")
        for event in events
    )
    missing_batches.validate_preview(preview["preview"])
    assert all(event["payload"]["purpose"] == PURPOSE for event in events)
    for changed in ("extra", "missing", "source", "date", "purpose"):
        tampered = deepcopy(preview["preview"])
        if changed == "extra":
            tampered["events"].append(deepcopy(events[0]))
        elif changed == "missing":
            tampered["events"].pop()
        elif changed == "source":
            tampered["events"][0]["source_url"] = WIKIPEDIA
        elif changed == "purpose":
            tampered["events"][0]["payload"].pop("purpose")
        else:
            tampered["events"][0]["payload"]["graduation_year"] = 1988
        with pytest.raises(ValueError, match="exactly"):
            missing_batches.validate_preview(tampered)
    applied = missing_batches.apply(
        "missing", Path(preview["preview_path"]), preview["sha256"]
    )
    assert applied["new_events"] == 2
    assert len(missing_batches.show("missing")["applied_proposals"]) == 1
    assert (
        missing_batches.apply(
            "missing", Path(preview["preview_path"]), preview["sha256"]
        )["new_events"]
        == 0
    )


@pytest.mark.parametrize(
    ("name", "shared_default"),
    [
        ("Unmatched Archived Academy", False),
        ("Test High School", False),
        ("Test High School", True),
    ],
)
def test_sourced_unmapped_attendance_keeps_its_reviewed_confidence(
    missing_batches: ResearchBatches,
    tmp_path: Path,
    name: str,
    shared_default: bool,
) -> None:
    service = missing_batches.service
    if shared_default:
        manifest = missing_batches.show("missing")["manifest"]
        append_events(
            service.log_path,
            [
                build_review_event(
                    school_review_id(name),
                    "map",
                    {
                        "recorded_name": name,
                        "institution_ref": "acara:1",
                        "relationship_type": "direct",
                    },
                    source_url="https://example.org/shared-school-identity",
                    reviewer="School reviewer",
                )
            ],
            expected_revision=service.review_revision(),
        )
        manifest["decision_revision"] = service.review_revision()
        (missing_batches.path("missing") / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    raw = result()
    assertion_id = education_review_id("TEST", name)
    attendance = record(
        assertion_id,
        "https://example.org/secondary-attendance",
        "attendance",
        {"aph_id": "TEST", "recorded_school_name": name, "level": "secondary"},
    )
    raw["evidence"] = raw["evidence"][:2] + [attendance]
    payload = raw["education_proposals"][0]
    payload.pop("institution_ref")
    payload.pop("relationship_type")
    payload["recorded_school_name"] = name
    payload["evidence_refs"] = [attendance["evidence_id"]]
    proposal = missing_batches.import_results("missing", [raw])["proposals"][0]
    accept(missing_batches, "missing", proposal)
    missing_batches.retain("missing")
    preview = missing_batches.preview(
        "missing", tmp_path / "unmapped.json", reviewer="Human reviewer"
    )
    assert "institution_ref" not in preview["preview"]["events"][0]["payload"]
    missing_batches.apply("missing", Path(preview["preview_path"]), preview["sha256"])
    events = service.events()
    attendance_event = next(
        event for event in events if event.review_id == assertion_id
    )
    resolution = build_review_event(
        assertion_id,
        "research",
        {
            "aph_id": "TEST",
            "recorded_school_name": name,
            "resolution_only": True,
            "resolution_reason": "no_suitable_candidate",
        },
        reviewer="Human reviewer",
        notes="Attendance is verified; institution identity needs research.",
        supersedes=[attendance_event.decision_id],
    )
    for selected in (events, events + [resolution]):
        with get_connection(service.db_path, read_only=True) as conn:
            projected = project_review_records(
                conn,
                selected,
                matcher=service.matcher(),
                evidence_records=service.evidence_records(selected),
            )
        assert len(projected["member_education"]) == 1
        assertion = projected["member_education"][0]
        assert assertion["school_name_as_recorded"] == name
        assert assertion["confidence"] == "verified"
        assert assertion["institution_resolution"] == "unresolved"
        placeholder = assertion["institution_id"]
        assert placeholder.startswith("inst-unmatched-reviewed-")
        with duckdb.connect(":memory:") as conn:
            init_schema(conn)
            _write_records(conn, projected)
            assert conn.execute(
                "SELECT c.profile_institution_id, c.finance_institution_id, "
                "i.acara_id, c.broad_sector, c.detailed_sector, "
                "c.attendance_longitude, c.attendance_latitude, "
                "c.location_basis, c.sector_basis "
                "FROM v_education_attendance_context c "
                "LEFT JOIN institutions i ON c.institution_id = i.institution_id"
            ).fetchone() == (
                placeholder,
                placeholder,
                None,
                None,
                None,
                None,
                None,
                "unresolved",
                "unresolved",
            )
    legacy = [
        replace(
            event,
            payload={
                key: value for key, value in event.payload.items() if key != "purpose"
            },
        )
        if event.review_id == assertion_id
        else event
        for event in events
    ]
    with get_connection(service.db_path, read_only=True) as conn:
        projected = project_review_records(
            conn,
            legacy,
            matcher=service.matcher(),
            evidence_records=service.evidence_records(legacy),
        )
    assertion = projected["member_education"][0]
    assert assertion["confidence"] == service.matcher().match(name).confidence
    if name == "Test High School":
        assert assertion["institution_id"] == "acara-1"
        assert assertion["institution_resolution"] == (
            "direct" if shared_default else "unresolved"
        )
    else:
        assert assertion["confidence"] == "unconfirmed"


@pytest.mark.parametrize(
    "change",
    [
        "member",
        "level",
        "date",
        "graduation",
        "target",
        "identity",
        "stages",
        "count",
        "attempts",
        "broad",
        "purpose",
        "manual_payload",
    ],
)
def test_unsupported_research_cannot_be_imported(
    missing_batches: ResearchBatches, change: str
) -> None:
    raw = result()
    if change == "member":
        raw["education_proposals"][0]["aph_id"] = "OTHER"
    elif change == "level":
        raw["education_proposals"][0]["level"] = "primary"
    elif change == "date":
        raw["education_proposals"][0]["years_attended"] = "1980-1986"
    elif change == "graduation":
        raw["education_proposals"][0]["attended_status"] = "graduated"
    elif change == "target":
        raw["education_proposals"][0]["institution_ref"] = "acara:2"
    elif change == "identity":
        raw["identity"]["wikidata_id"] = "Q2"
    elif change == "stages":
        raw["stages"].pop("wikipedia")
    elif change == "count":
        raw["research_log"]["searches"] = 5
    elif change == "attempts":
        raw["attempts"] = [{"model": "inferred"}]
    elif change == "purpose":
        raw["education_proposals"][0]["purpose"] = PURPOSE
    elif change == "manual_payload":
        raw["payload"] = {"purpose": PURPOSE}
    else:
        raw["research_log"]["search_queries"] = [
            {"stage": "final_broad", "query": "unnecessary"}
        ]
        raw["research_log"]["searches"] = 1
    with pytest.raises(ValueError):
        missing_batches.import_results("missing", [raw])
    assert not missing_batches.service.events()


def test_manual_school_definition_and_attendance_remain_separate(
    missing_batches: ResearchBatches, tmp_path: Path
) -> None:
    raw = result()
    name = "Historical School"
    facts, records = facts_and_evidence(
        school_review_id(name), name=name, country="New Zealand"
    )
    definition = build_manual_institution_candidate(facts, records, [])["definition"]
    assert definition is not None
    old = raw["education_proposals"][0]
    attendance = record(
        education_review_id("TEST", name),
        "https://example.org/biography",
        "attendance",
        {"aph_id": "TEST", "recorded_school_name": name, "level": "secondary"},
    )
    identity = record(
        education_review_id("TEST", name),
        "https://example.org/history",
        "institution_identity",
        {"name": name, "country": "New Zealand"},
        definition["institution_ref"],
    )
    raw["evidence"] = raw["evidence"][:2] + [attendance, identity] + records
    raw["education_proposals"] = [
        {
            **old,
            "recorded_school_name": name,
            "institution_ref": definition["institution_ref"],
            "evidence_refs": [attendance["evidence_id"], identity["evidence_id"]],
        }
    ]
    raw["manual_institutions"] = [definition]
    proposal = missing_batches.import_results("missing", [raw])["proposals"][0]
    accept(missing_batches, "missing", proposal)
    missing_batches.retain("missing")
    preview = missing_batches.preview(
        "missing", tmp_path / "historical.json", reviewer="Human reviewer"
    )
    assert [event["entity_type"] for event in preview["preview"]["events"]] == [
        "manual_institution",
        "member_education",
    ]
    missing_batches.validate_preview(preview["preview"])
    assert not missing_batches.service.events()


def test_later_attendance_reopens_research_instead_of_overwriting(
    missing_batches: ResearchBatches, tmp_path: Path
) -> None:
    proposal = missing_batches.import_results("missing", [result()])["proposals"][0]
    accept(missing_batches, "missing", proposal)
    missing_batches.retain("missing")
    payload = dict(proposal["education_proposals"][0])
    payload.pop("evidence_refs")
    append_events(
        missing_batches.service.log_path,
        [
            build_review_event(
                education_review_id("TEST", "Test High School"),
                "accept",
                payload,
                source_url="https://example.org/new-decision",
                reviewer="Other reviewer",
            )
        ],
        expected_revision=missing_batches.service.review_revision(),
    )
    with pytest.raises(StaleReviewError):
        missing_batches.preview(
            "missing", tmp_path / "stale.json", reviewer="Human reviewer"
        )


def test_frozen_audit_cannot_change(missing_batches: ResearchBatches) -> None:
    (missing_batches.path("missing") / "audit.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot"):
        missing_batches.show("missing")


def test_final_broad_pass_counts_every_actual_query(
    missing_batches: ResearchBatches,
) -> None:
    raw = result(positive=False)
    raw["research_log"]["search_queries"] = [
        {"stage": "final_broad", "query": f"Actual search query {number}"}
        for number in range(7)
    ]
    raw["research_log"]["searches"] = 7
    with pytest.raises(ValueError, match="six actual"):
        missing_batches.import_results("missing", [raw])
