"""Shared CLI/GUI previews, offline projections, and canonical effects."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.review.candidates import (
    annotate_candidates,
    build_candidates,
    candidate,
    export_candidates,
    load_into_duckdb,
)
from apemap.review.model import (
    education_review_id,
    member_review_id,
    resolve_events,
    school_review_id,
)
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError, encode_event
from tests.db_fixtures import DatabaseFactory
from tests.test_review_model import school_event


def test_register_cache_is_content_bound_and_blocked_keys_are_isolated(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import service as module

    constructor = module.SchoolMatcher
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return constructor(*args, **kwargs)

    monkeypatch.setattr(module, "SchoolMatcher", counted)
    first = review_service.matcher()
    first.review_blocked_keys.add("test school")
    assert not review_service.matcher().review_blocked_keys
    assert calls == 1
    resolver = review_service.institution_resolver()
    metadata = resolver("acara:1")
    assert metadata is not None
    metadata["school_name"] = "Client mutation"
    assert resolver("acara:1") != metadata
    target = review_service.resolve_institution("acara:1")
    assert target is not None and target["school_name"] == "Test High School"
    path = review_service.external_dir / "school-location-2025.csv"
    original = path.stat()
    path.write_bytes(path.read_bytes().replace(b"Test High", b"Next High"))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert (
        review_service.matcher().acara_id_map["1"]["school_name"] == "Next High School"
    )
    target = review_service.resolve_institution("acara:1")
    assert target is not None and target["school_name"] == "Next High School"
    old_target = resolver("acara:1")
    assert old_target is not None and old_target["school_name"] == "Test High School"
    assert calls == 2


def test_case_candidate_projection_matches_whole_queue(
    review_service: ReviewService, tmp_path: Path
) -> None:
    from apemap.db import get_connection

    cache = tmp_path / "cache"
    (cache / "institutions").mkdir(parents=True)
    (cache / "institutions" / "lead.json").write_text(
        json.dumps(
            {
                "raw_school_text": "Test High School",
                "suggested_institution_name": "Possible School",
                "source_url": "https://example.org/history",
                "status": "candidate",
            }
        ),
        encoding="utf-8",
    )
    with get_connection(review_service.db_path, read_only=True) as conn:
        whole = build_candidates(conn, cache_dir=cache)
        for key in {row["review_id"] for row in whole}:
            assert build_candidates(conn, cache_dir=cache, review_id=key) == [
                row for row in whole if row["review_id"] == key
            ]
        assert build_candidates(conn, cache_dir=cache, review_id="school:missing") == []


def test_prepare_projects_once_per_before_and_after(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import integration as module

    original = module.project_review_records
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "project_review_records", counted)
    review_service.prepare(
        school_review_id("Test High School"),
        "research",
        {"recorded_name": "Test High School"},
        reviewer="Reviewer",
        notes="Check history",
    )
    assert calls == 2


@pytest.mark.parametrize("changed", ["source", "ledger"])
def test_changes_during_semantic_diff_invalidate_preview(
    review_service: ReviewService, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    original = review_service.semantic_diff

    def change_inputs(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if changed == "source":
            with (review_service.external_dir / "school-location-2025.csv").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write("3,New School,Government,Secondary,TAS,-42,147\n")
        else:
            review_service.log_path.write_bytes(encode_event(school_event()))
        return result

    monkeypatch.setattr(review_service, "semantic_diff", change_inputs)
    with pytest.raises(StaleReviewError, match="inputs changed"):
        review_service.prepare(
            school_review_id("Test High School"),
            "research",
            {"recorded_name": "Test High School"},
            reviewer="Reviewer",
            notes="Check history",
        )


pytestmark = pytest.mark.integration


@pytest.fixture
def review_service(tmp_path: Path, database_factory: DatabaseFactory) -> ReviewService:
    path, conn = database_factory(None)
    conn.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name, aph_id, gender, date_of_birth) VALUES ('aph-test', 'Person', 'Test', 'Test Person', 'TEST', 'Male', '1970-01-01')"
    )
    conn.execute(
        "INSERT INTO parliament_service (service_id, member_id, parliament_number, chamber, party, party_abbrev, state_or_territory, service_start, service_end) VALUES ('srv-test', 'aph-test', 48, 'representatives', 'Labor', 'ALP', 'TAS', '2025-07-22', NULL)"
    )
    conn.execute(
        "INSERT INTO institutions (institution_id, acara_id, school_name, sector) VALUES ('acara-1', '1', 'Test High School', 'Government')"
    )
    conn.execute(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin) VALUES ('edu-test', 'aph-test', 'acara-1', 'secondary', 'attended_unspecified', 'https://example.org/bio', '2026-10-02T00:00:00+10:00', 'verified', 'Test High School', 'aph')"
    )
    from apemap.review.integration import capture_review_sources

    capture_review_sources(conn)
    conn.close()
    external = tmp_path / "external"
    external.mkdir()
    (external / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n1,Test High School,Government,Secondary,TAS,-42,147\n2,Other High School,Independent,Secondary,TAS,-42.1,147.1\n",
        encoding="utf-8",
    )
    return ReviewService(
        log_path=tmp_path / "decisions.jsonl", db_path=path, external_dir=external
    )


def test_member_preview_never_mutates_and_supersession_restores_source(
    review_service: ReviewService,
) -> None:
    service = review_service
    preview = service.prepare(
        member_review_id("TEST", "date_of_birth"),
        "accept",
        {"aph_id": "TEST", "field": "date_of_birth", "value": "1971-02-03"},
        source_url="https://example.org/bio",
        reviewer="Reviewer",
    )
    assert not service.log_path.exists()
    assert "members" in preview["changes"]["canonical"]["after"]
    accepted = service.save(preview)
    replacement = service.prepare(
        accepted.review_id,
        "supersede",
        {"aph_id": "TEST", "field": "date_of_birth", "value": None},
        supersedes=[accepted.decision_id],
        replacement_action="reject",
        reviewer="Reviewer",
        notes="Source date was correct",
    )
    service.save(replacement)
    assert resolve_events(service.events())[accepted.review_id].status == "rejected"


def test_stale_log_and_source_edits_do_not_overwrite(
    review_service: ReviewService,
) -> None:
    service = review_service
    payload = {"aph_id": "TEST", "field": "gender", "value": "Other"}
    preview = service.prepare(
        member_review_id("TEST", "gender"),
        "accept",
        payload,
        source_url="https://example.org/bio",
        reviewer="Reviewer",
    )
    service.save(preview)
    old = service.log_path.read_bytes()
    with pytest.raises(StaleReviewError):
        service.save(preview)
    assert service.log_path.read_bytes() == old
    fresh = service.prepare(
        school_review_id("Test High School"),
        "research",
        {"recorded_name": "Test High School"},
        reviewer="Reviewer",
        notes="Check history",
    )
    with (service.external_dir / "school-location-2025.csv").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("3,New School,Government,Secondary,TAS,-42,147\n")
    with pytest.raises(StaleReviewError, match="Source"):
        service.save(fresh)
    assert service.log_path.read_bytes() == old


def test_education_rejection_preview_removes_target_only(
    review_service: ReviewService,
) -> None:
    preview = review_service.prepare(
        education_review_id("TEST", "Test High School"),
        "reject",
        {"aph_id": "TEST", "recorded_school_name": "Test High School"},
        reviewer="Reviewer",
        notes="Did not attend",
    )
    impact = preview["changes"]["canonical"]["after"]["member_education"]
    assert impact["after_count"] == 0
    assert impact["before_count"] == 1


def test_candidates_export_rebuild_never_reads_csv_edits(
    review_service: ReviewService, tmp_path: Path
) -> None:
    from apemap.db import get_connection

    service = review_service
    preview = service.prepare(
        school_review_id("Test High School"),
        "map",
        {
            "recorded_name": "Test High School",
            "institution_ref": "acara:2",
            "relationship_type": "alias",
        },
        source_url="https://example.org/history",
        reviewer="Reviewer",
    )
    service.save(preview)
    original = service.log_path.read_bytes()
    with get_connection(service.db_path) as conn:
        items = annotate_candidates(
            build_candidates(conn, cache_dir=tmp_path / "empty-cache"), service.events()
        )
        load_into_duckdb(conn, service.events(), items)
        output = tmp_path / "generated"
        export_candidates(conn, items, output)
        (output / "school_candidates.csv").write_text(
            "manual edits have no authority", encoding="utf-8"
        )
        export_candidates(conn, items, output)
        assert conn.execute(
            "SELECT COUNT(*) FROM review_effective_decisions"
        ).fetchone() == (1,)
        shutil.rmtree(output)
        export_candidates(conn, items, output)
    assert service.log_path.read_bytes() == original


def test_candidate_id_changes_with_evidence_not_row_rank() -> None:
    a = candidate(
        "school:key", "school", {}, {"value": "first", "retrieved_at": "old"}, [48]
    )
    b = candidate(
        "school:key", "school", {}, {"value": "first", "retrieved_at": "new"}, [47]
    )
    changed = candidate("school:key", "school", {}, {"value": "second"}, [48])
    assert a["candidate_id"] == b["candidate_id"]
    assert a["candidate_id"] != changed["candidate_id"]


def test_cli_json_read_and_write_commands_share_service(
    review_service: ReviewService, tmp_path: Path
) -> None:
    service = review_service
    runner = CliRunner()
    arguments = [
        "review",
        "--log-path",
        str(service.log_path),
        "--db-path",
        str(service.db_path),
        "--external-dir",
        str(service.external_dir),
    ]
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({"aph_id": "TEST", "field": "gender", "value": "Other"}),
        encoding="utf-8",
    )
    command = arguments + [
        "accept",
        member_review_id("TEST", "gender"),
        "--payload",
        str(payload),
        "--source",
        "https://example.org/bio",
        "--reviewer",
        "Reviewer",
    ]
    dry_run = runner.invoke(app, command + ["--dry-run"])
    assert dry_run.exit_code == 0, dry_run.output
    assert not service.log_path.exists()
    saved = runner.invoke(app, command)
    assert saved.exit_code == 0, saved.output
    assert json.loads(saved.output)["payload"]["value"] == "Other"
    checked = runner.invoke(app, arguments + ["check"])
    assert checked.exit_code == 0, checked.output
    history = runner.invoke(
        app, arguments + ["history", member_review_id("TEST", "gender")]
    )
    assert len(json.loads(history.output)) == 1


def test_reference_validation_with_real_pinned_register(
    review_service: ReviewService,
) -> None:
    invalid = school_event(
        payload={
            "recorded_name": "Saint Mary's",
            "institution_ref": "acara:999",
            "relationship_type": "alias",
        }
    )
    with pytest.raises(ValueError, match="does not exist"):
        review_service.check([invalid])


def test_cli_and_guided_ui_preserve_identical_successor_evidence_payloads(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("flask")
    pytest.importorskip("waitress")
    from apemap.review.gui import create_app
    from tests.test_review_gui import Inputs

    payload = {
        "recorded_name": "Test High School",
        "institution_ref": "acara:2",
        "relationship_type": "successor",
        "attended_institution_ref": "acara:1",
        "attended_identity_source_url": "https://example.org/identity",
        "historical_scope_confirmed": True,
        "historical_latitude": -40.5,
        "historical_longitude": 145.5,
        "historical_location_source_url": "https://example.org/location",
        "historical_broad_sector": "Government",
        "historical_broad_sector_source_url": "https://example.org/sector",
        "campus_continuity": "different_campus",
        "campus_continuity_source_url": "https://example.org/campus",
    }
    path = tmp_path / "successor.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    review_id = school_review_id("Test High School")
    result = CliRunner().invoke(
        app,
        [
            "review",
            "--log-path",
            str(review_service.log_path),
            "--db-path",
            str(review_service.db_path),
            "--external-dir",
            str(review_service.external_dir),
            "accept",
            review_id,
            "--payload",
            str(path),
            "--source",
            "https://example.org/relationship",
            "--reviewer",
            "Researcher",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    cli_event = json.loads(result.output)["event"]
    captured: list[dict[str, Any]] = []
    original_prepare = review_service.prepare

    def capture(*args: Any, **kwargs: Any) -> dict[str, Any]:
        preview = original_prepare(*args, **kwargs)
        captured.append(preview["event"])
        return preview

    monkeypatch.setattr(review_service, "prepare", capture)
    client = create_app(review_service).test_client()
    hidden = Inputs(client.get(f"/items/{review_id}").get_data(as_text=True)).values
    fields = {
        "field_" + name: "1" if value is True else str(value)
        for name, value in payload.items()
    }
    response = client.post(
        f"/items/{review_id}/preview",
        data={
            "csrf_token": hidden["csrf_token"],
            "form_token": hidden["form_token"],
            "school_workflow": "1",
            "payload_mode": "guided",
            "payload": "{}",
            "action": "map",
            "source_url": "https://example.org/relationship",
            "reviewer": "Researcher",
            **fields,
        },
    )
    assert response.status_code == 200
    assert cli_event["payload"] == payload
    assert all(
        str(value) in response.get_data(as_text=True)
        for value in (
            "acara:1",
            "https://example.org/location",
            "https://example.org/sector",
        )
    )
    gui_event = captured[-1]
    assert gui_event["payload"] == cli_event["payload"]
    assert gui_event["source_url"] == cli_event["source_url"]
    assert not review_service.log_path.exists()


def test_semantic_diff_ignores_event_only_change(review_service: ReviewService) -> None:
    a = school_event(
        payload={
            "recorded_name": "Saint Mary's",
            "institution_ref": "acara:1",
            "relationship_type": "alias",
        }
    )
    b = replace(
        a,
        decision_id="second",
        action="supersede",
        supersedes=[a.decision_id],
        replacement_action="map",
        notes="Added explanatory note",
    )
    assert review_service.semantic_diff([a], [a, b])["decisions"] == []


def test_semantic_diff_compares_prior_effective_facts_and_compact_counts(
    review_service: ReviewService,
) -> None:
    service = review_service
    accepted = service.save(
        service.prepare(
            member_review_id("TEST", "date_of_birth"),
            "accept",
            {"aph_id": "TEST", "field": "date_of_birth", "value": "1971-02-03"},
            source_url="https://example.org/bio",
            reviewer="Reviewer",
        )
    )
    replacement = replace(
        accepted,
        decision_id="corrected-dob",
        action="supersede",
        replacement_action="accept",
        supersedes=[accepted.decision_id],
        payload={**accepted.payload, "value": "1972-03-04"},
    )
    differences = service.semantic_diff([accepted], [accepted, replacement])
    changes = differences["canonical"]["after"]["members"]
    assert len(changes["before"]) == len(changes["after"]) == 1
    assert changes["before"][0]["date_of_birth"] == "1971-02-03"
    assert changes["after"][0]["date_of_birth"] == "1972-03-04"
    assert changes["updated_count"] == 1
    assert (
        differences["canonical"]["counts"]["before"]
        == differences["canonical"]["counts"]["after"]
    )
    assert (
        differences["canonical"]["counts"]["after"]["members_with_resolved_education"]
        == 1
    )


def test_semantic_diff_includes_evidence_provenance(
    review_service: ReviewService,
) -> None:
    first = school_event(
        payload={
            "recorded_name": "Saint Mary's",
            "institution_ref": "acara:1",
            "relationship_type": "alias",
        }
    )
    second = replace(
        first,
        decision_id="new-source",
        action="supersede",
        replacement_action="map",
        supersedes=[first.decision_id],
        source_url="https://example.org/new-evidence",
    )
    assert (
        review_service.semantic_diff([first], [first, second])["decisions"][0]["after"][
            "source_url"
        ]
        == second.source_url
    )


def test_offline_build_rejects_invalid_manifest_without_network(
    review_service: ReviewService, tmp_path: Path
) -> None:
    manifest = tmp_path / "inputs.json"
    manifest.write_text(
        '{"files":{"data/raw/absent":{"required":true}}}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Pinned input"):
        review_service.build(inputs_manifest=manifest)
    assert not review_service.log_path.exists()


def test_build_never_overwrites_views_from_a_newer_consumed_revision(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.db import get_connection
    from apemap.review.integration import capture_review_snapshot

    raw = tmp_path / "aph"
    raw.mkdir()
    (raw / "individuals.json").write_text("[]", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"created_at":"2026-10-05T00:00:00+00:00"}', encoding="utf-8")
    monkeypatch.setattr("apemap.review.service.RAW_APH_DIR", raw)
    monkeypatch.setattr("apemap.inputs.verify_inputs_manifest", lambda path: (True, []))
    newer = school_event(action="research", notes="New evidence to inspect")

    def concurrent_ingestion(*, conn: duckdb.DuckDBPyConnection, **kwargs: Any) -> None:
        review_service.log_path.write_bytes(encode_event(newer))
        capture_review_snapshot(
            conn, [newer], decision_log_path=review_service.log_path
        )
        load_into_duckdb(conn, [newer])

    monkeypatch.setattr(
        "apemap.ingest.pipeline.run_aph_ingestion", concurrent_ingestion
    )
    output = tmp_path / "out"
    with pytest.raises(StaleReviewError, match="consumed a newer"):
        review_service.build(inputs_manifest=manifest, output_dir=output)
    with get_connection(review_service.db_path, read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM review_decision_events"
        ).fetchone() == (1,)
    assert not (output / "summary.json").exists()
    assert not (output / "build-metadata.json").exists()


def test_conflicted_merged_decisions_can_be_inspected_and_repaired(
    review_service: ReviewService,
) -> None:
    service = review_service
    review_id = member_review_id("TEST", "gender")
    first = service.prepare(
        review_id,
        "accept",
        {"aph_id": "TEST", "field": "gender", "value": "Female"},
        source_url="https://example.org/a",
        reviewer="A",
    )
    second = service.prepare(
        review_id,
        "accept",
        {"aph_id": "TEST", "field": "gender", "value": "Other"},
        source_url="https://example.org/b",
        reviewer="B",
    )
    from apemap.review.model import ReviewEvent

    events = [
        ReviewEvent.from_dict(first["event"]),
        ReviewEvent.from_dict(second["event"]),
    ]
    service.log_path.write_bytes(b"".join(encode_event(event) for event in events))
    with pytest.raises(ValueError, match="Conflicting"):
        service.check()
    assert len(service.show(review_id)["conflicts"]) == 2
    assert len(service.history(review_id)) == 2
    assert service.candidates("member", "conflict")
    repair = service.prepare(
        review_id,
        "supersede",
        {"aph_id": "TEST", "field": "gender", "value": "Female"},
        source_url="https://example.org/research",
        reviewer="Reviewer",
        supersedes=[event.decision_id for event in events],
        replacement_action="accept",
    )
    assert repair["changes"]["decisions"][0]["before"]["conflicts"]
    service.save(repair)
    assert service.check()["effective_decisions"] == 1


def test_cli_defaults_preserve_explicit_payload_facts(
    review_service: ReviewService, tmp_path: Path
) -> None:
    runner = CliRunner()
    service = review_service
    options = [
        "review",
        "--log-path",
        str(service.log_path),
        "--db-path",
        str(service.db_path),
        "--external-dir",
        str(service.external_dir),
    ]
    path = tmp_path / "graduation.json"
    path.write_text(
        json.dumps(
            {
                "aph_id": "TEST",
                "recorded_school_name": "Test High School",
                "institution_ref": "acara:1",
                "attended_status": "graduated",
                "confidence": "provisional",
                "graduation_year": 1988,
                "retrieved_at": "2026-10-02T00:00:00+10:00",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        options
        + [
            "accept",
            education_review_id("TEST", "Test High School"),
            "--payload",
            str(path),
            "--source",
            "https://example.org/evidence",
            "--reviewer",
            "Researcher",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)["event"]["payload"]
    assert value["attended_status"] == "graduated"
    assert value["confidence"] == "provisional"
    path.write_text(
        json.dumps(
            {
                "recorded_name": "Test High School",
                "institution_ref": "acara:2",
                "relationship_type": "successor",
            }
        ),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        options
        + [
            "accept",
            school_review_id("Test High School"),
            "--payload",
            str(path),
            "--source",
            "https://example.org/history",
            "--reviewer",
            "Researcher",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.output)["event"]["payload"]["relationship_type"]
        == "successor"
    )


def test_register_json_and_cached_evidence_changes_invalidate_preview(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import apemap.review.service as service_module

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(service_module, "RAW_WIKIMEDIA_DIR", cache_dir)
    service = review_service
    review_id = member_review_id("TEST", "gender")
    payload = {"aph_id": "TEST", "field": "gender", "value": "Other"}
    first = service.prepare(
        review_id,
        "accept",
        payload,
        source_url="https://example.org/bio",
        reviewer="Reviewer",
    )
    (cache_dir / "candidate.json").write_text('{"gender":"Female"}', encoding="utf-8")
    with pytest.raises(StaleReviewError):
        service.save(first)
    second = service.prepare(
        review_id,
        "accept",
        payload,
        source_url="https://example.org/bio",
        reviewer="Reviewer",
    )
    (service.external_dir / "acara_school_results.json").write_text(
        '[{"ACARAId":"1","SchoolName":"Changed register name","SchoolSector":"Gov"}]',
        encoding="utf-8",
    )
    with pytest.raises(StaleReviewError):
        service.save(second)
    assert not service.log_path.exists()


def test_unknown_member_rejected_but_outside_selected_cohort_is_valid(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import apemap.review.service as service_module

    raw_dir = tmp_path / "aph"
    raw_dir.mkdir()
    (raw_dir / "individuals.json").write_text('[{"PHID":"OUTSIDE"}]', encoding="utf-8")
    monkeypatch.setattr(service_module, "RAW_APH_DIR", raw_dir)
    service = review_service
    with pytest.raises(ValueError, match="Unknown member"):
        service.prepare(
            member_review_id("TYPO", "gender"),
            "accept",
            {"aph_id": "TYPO", "field": "gender", "value": "Other"},
            source_url="https://example.org/bio",
            reviewer="Reviewer",
        )
    preview = service.prepare(
        member_review_id("OUTSIDE", "gender"),
        "accept",
        {"aph_id": "OUTSIDE", "field": "gender", "value": "Other"},
        source_url="https://example.org/bio",
        reviewer="Reviewer",
    )
    assert preview["event"]["payload"]["aph_id"] == "OUTSIDE"


def test_existing_identity_conflict_and_name_cache_evidence_are_retained(
    review_service: ReviewService, tmp_path: Path
) -> None:
    from apemap.db import get_connection

    cache = tmp_path / "wikimedia" / "members"
    cache.mkdir(parents=True)
    conflict = {
        "aph_id": "TEST",
        "wikidata_id": None,
        "date_of_birth": None,
        "gender": None,
        "status": "conflict",
        "notes": "Multiple Wikidata entities claim APH ID TEST: ['Q1', 'Q2']",
        "source_query": "SELECT ?item WHERE { ?item wdt:P10020 'TEST' }",
        "source_url": "https://query.wikidata.org/sparql",
    }
    (cache / "TEST.json").write_text(json.dumps(conflict), encoding="utf-8")
    (cache / "name_test_person.json").write_text(
        json.dumps({"gender": "Female", "wikidata_id": "Q3", "status": "ok"}),
        encoding="utf-8",
    )
    with get_connection(review_service.db_path, read_only=True) as conn:
        items = build_candidates(conn, cache_dir=cache.parent)
    cases = [item for item in items if item["entity_type"] == "member"]
    conflicts = [item for item in cases if item["evidence"]["status"] == "conflict"]
    assert len(conflicts) == 3
    assert all(item["evidence"]["notes"] == conflict["notes"] for item in conflicts)
    assert all(
        item["evidence"]["source_query"] == conflict["source_query"]
        for item in conflicts
    )
    gender = [
        item
        for item in cases
        if item["review_id"] == member_review_id("TEST", "gender")
    ]
    assert {item["evidence"]["match_method"] for item in gender} == {"aph_id", "name"}


@pytest.mark.parametrize("invalid", [[], None, "cache", {"result": []}])
@pytest.mark.parametrize("kind", ["members", "institutions"])
def test_invalid_cache_shape_has_actionable_error(
    review_service: ReviewService, tmp_path: Path, invalid: object, kind: str
) -> None:
    from apemap.db import get_connection

    cache = tmp_path / "wikimedia" / kind
    cache.mkdir(parents=True)
    path = cache / ("TEST.json" if kind == "members" else "school.json")
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with get_connection(review_service.db_path, read_only=True) as conn:
        with pytest.raises(ValueError, match="Invalid Wikimedia cache object"):
            build_candidates(conn, cache_dir=cache.parent)


def test_acara_refresh_captures_source_facts_and_reapplies_reviews(
    review_service: ReviewService, tmp_path: Path
) -> None:
    from apemap.db import get_connection
    from apemap.ingest.acara import run_acara_ingestion
    from apemap.review.integration import (
        apply_review_events,
        capture_review_snapshot,
        review_snapshot_metadata,
    )

    service = review_service
    service.save(
        service.prepare(
            member_review_id("TEST", "date_of_birth"),
            "accept",
            {"aph_id": "TEST", "field": "date_of_birth", "value": "1971-02-03"},
            source_url="https://example.org/bio",
            reviewer="Reviewer",
        )
    )
    with get_connection(service.db_path) as conn:
        conn.execute("BEGIN")
        apply_review_events(conn, events=service.events(), matcher=service.matcher())
        capture_review_snapshot(
            conn,
            service.events(),
            decision_log_path=service.log_path,
            source_provenance={"aph": "original-source-hash"},
        )
        conn.execute("COMMIT")
        for _ in range(2):
            run_acara_ingestion(
                download_latest=False,
                use_longitudinal=False,
                conn=conn,
                gpkg_path=tmp_path / "missing-legacy.gpkg",
                external_dir=service.external_dir,
                output_dir=tmp_path / "acara-output",
                export_parquet_files=False,
                decision_log_path=service.log_path,
            )
            corrected = conn.execute("SELECT date_of_birth FROM members").fetchone()
            original = conn.execute(
                "SELECT date_of_birth FROM review_source_members"
            ).fetchone()
            assert corrected is not None and str(corrected[0]) == "1971-02-03"
            assert original is not None and str(original[0]) == "1970-01-01"
            assert conn.execute(
                "SELECT count(*) FROM review_source_institutions WHERE acara_id='2'"
            ).fetchone() == (1,)
            provenance = review_snapshot_metadata(conn)["source_provenance"]
            assert provenance["aph"] == "original-source-hash"
            assert "school-location-2025.csv" in provenance["acara"]["files"]
