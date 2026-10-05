"""Independent fixture coverage for evidence-preserving migration proposals."""

from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from apemap.review.migration import (
    audit_aliases,
    compare_migration_records,
    propose_legacy_migration,
    propose_review_csv_import,
)
from apemap.review.model import education_review_id, validate_events

pytestmark = pytest.mark.unit


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def migration_inputs(tmp_path: Path) -> tuple[Path, Path]:
    reference, external = tmp_path / "legacy", tmp_path / "external"
    reference.mkdir()
    external.mkdir()
    aliases = {
        "old school": {
            "canonical_acara_id": "10",
            "canonical_name": "Current School",
            "state": "TAS",
            "sector": "Government",
            "school_type": "Sec",
            "notes": "Official rename",
            "source_url": "https://school.example/history",
            "relationship_type": "rename",
            "reviewed_at": "2020-01-01",
        },
        "unsourced school": {
            "canonical_acara_id": "10",
            "canonical_name": "Current School",
            "state": "TAS",
            "sector": "Government",
            "school_type": "Sec",
            "notes": "Target metadata agrees but relationship has no evidence",
        },
        "wrong school": {
            "canonical_acara_id": "10",
            "canonical_name": "Current School",
            "state": "VIC",
            "sector": "Government",
            "school_type": "Sec",
            "notes": "Sourced but conflicting target",
            "source_url": "https://school.example/history",
            "relationship_type": "rename",
            "reviewed_at": "2020-01-01",
        },
    }
    (reference / "school_aliases.json").write_text(
        json.dumps({"aliases": aliases}), encoding="utf-8"
    )
    write_csv(
        external / "school-location-2025.csv",
        [
            {
                "ACARA SML ID": "10",
                "School Name": "Current School",
                "State": "TAS",
                "School Sector": "Government",
                "School Type": "Secondary",
            },
        ],
        ["ACARA SML ID", "School Name", "State", "School Sector", "School Type"],
    )
    write_csv(
        reference / "manual_member_education.csv",
        [
            {
                "aph_id": "ABC",
                "school_name": "Old School",
                "source_url": "https://aph.example/person",
                "retrieved_at": "2020-01-02T00:00:00+10:00",
                "confidence": "verified",
                "reviewer_notes": "Attendance without graduation inference",
                "attended_status": "attended_unspecified",
            }
        ],
        [
            "aph_id",
            "school_name",
            "source_url",
            "retrieved_at",
            "confidence",
            "reviewer_notes",
            "attended_status",
        ],
    )
    write_csv(
        reference / "historical_service_overrides.csv",
        [],
        ["aph_id", "parliament_number"],
    )
    return reference, external


def test_proposals_preserve_exact_lineage_and_do_not_accept_target_agreement(
    migration_inputs: tuple[Path, Path],
) -> None:
    reference, external = migration_inputs
    before = {path.name: path.read_bytes() for path in reference.iterdir()}
    events, audit = propose_legacy_migration(
        reference,
        external,
        reviewer="Fixture importer",
        reviewed_at="2026-10-05",
        recorded_at="2026-10-05T00:00:00+00:00",
    )
    validate_events(events, acara_ids={"10"})
    assert len(events) == 4
    school = {
        event.payload["recorded_name"]: event
        for event in events
        if event.entity_type == "school"
    }
    assert school["old school"].action == "map"
    assert school["unsourced school"].action == "research"
    assert school["wrong school"].action == "research"
    assert next(row for row in audit if row["recorded_name"] == "wrong school")[
        "metadata_disagreements"
    ] == ["state"]
    education = next(
        event for event in events if event.entity_type == "member_education"
    )
    assert education.payload["institution_ref"] == "acara:10"
    assert education.payload["retrieved_at"] == "2020-01-02T00:00:00+10:00"
    assert education.payload["attended_status"] == "attended_unspecified"
    assert education.legacy is not None
    assert education.legacy["original_reviewer"] is None
    assert (
        education.legacy["source_sha256"]
        == hashlib.sha256(before["manual_member_education.csv"]).hexdigest()
    )
    assert {path.name: path.read_bytes() for path in reference.iterdir()} == before


def test_migration_identity_excludes_attestation_time(
    migration_inputs: tuple[Path, Path],
) -> None:
    reference, external = migration_inputs
    first, _ = propose_legacy_migration(
        reference,
        external,
        reviewer="One",
        reviewed_at="2026-10-05",
        recorded_at="2026-10-05T00:00:00+00:00",
    )
    second, _ = propose_legacy_migration(
        reference,
        external,
        reviewer="Two",
        reviewed_at="2026-10-06",
        recorded_at="2026-10-06T00:00:00+00:00",
    )
    assert [event.decision_id for event in first] == [
        event.decision_id for event in second
    ]
    assert asdict(first[0]) != asdict(second[0])


def test_service_migration_preserves_each_interval_evidence(
    migration_inputs: tuple[Path, Path],
) -> None:
    reference, external = migration_inputs
    row = {
        "aph_id": "ABC",
        "parliament_number": "47",
        "service_start": "2022-07-26",
        "service_end": "2025-03-28",
        "chamber": "senate",
        "party": "Example Party",
        "state_or_territory": "TAS",
        "source_url": "https://aph.example/service",
        "retrieved_at": "2026-10-05T00:00:00+00:00",
        "reviewer_notes": "Complete term from official member service record",
    }
    write_csv(reference / "historical_service_overrides.csv", [row], list(row))
    events, _ = propose_legacy_migration(reference, external, reviewer="Importer")
    validate_events(events, acara_ids={"10"})
    service = next(event for event in events if event.entity_type == "service")
    assert service.payload["intervals"][0]["source_url"] == row["source_url"]
    assert service.payload["intervals"][0]["retrieved_at"] == row["retrieved_at"]


def test_audit_reports_missing_target_without_fuzzy_acceptance(
    migration_inputs: tuple[Path, Path],
) -> None:
    reference, external = migration_inputs
    alias_path = reference / "school_aliases.json"
    data = json.loads(alias_path.read_text(encoding="utf-8"))
    data["aliases"]["old school"]["canonical_acara_id"] = "999"
    alias_path.write_text(json.dumps(data), encoding="utf-8")
    row = next(
        item
        for item in audit_aliases(alias_path, external)
        if item["recorded_name"] == "old school"
    )
    assert row["disposition"] == "research"
    assert row["metadata_disagreements"] == ["missing_target_id"]


def test_legacy_cli_dry_run_returns_a_report_without_writing(
    migration_inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from apemap.cli import app
    from apemap.review import service

    reference, external = migration_inputs
    log = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(service, "RAW_APH_DIR", tmp_path / "missing-aph-cache")
    result = CliRunner().invoke(
        app,
        [
            "review",
            "--log-path",
            str(log),
            "--external-dir",
            str(external),
            "--db-path",
            str(tmp_path / "missing-review.duckdb"),
            "import",
            "--legacy-dir",
            str(reference),
            "--reviewer",
            "Importer",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["applied"] is False
    assert payload["new_events"] == 4
    assert payload["report"]["proposed_event_count"] == 4
    assert payload["report"]["mapped_aliases"] == 1
    assert payload["report"]["research_aliases"] == 2
    assert len(payload["report"]["alias_audit"]) == 3
    assert not log.exists()


def test_csv_import_requires_explicit_value_and_manual_evidence(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    rows = [
        {
            "member_id": "aph-ABC",
            "field": "date_of_birth",
            "review_status": "accepted",
            "resolved_value": "19/04/1970",
            "manual_source_url": "",
            "wikidata_value": "1970-04-19",
        }
    ]
    write_csv(path, rows, list(rows[0]))
    original = path.read_bytes()
    events, report = propose_review_csv_import(path, reviewer="Importer")
    assert events == []
    assert len(report["errors"]) == 1
    events, report = propose_review_csv_import(
        path, reviewer="Importer", incomplete_as_research=True
    )
    assert len(events) == 1
    assert events[0].action == "research"
    assert "value" not in events[0].payload
    assert report["errors"] == []
    rows[0]["manual_source_url"] = "https://aph.example/person"
    write_csv(path, rows, list(rows[0]))
    events, report = propose_review_csv_import(path, reviewer="Importer")
    assert events[0].payload["value"] == "1970-04-19"
    assert events[0].payload["aph_id"] == "abc"
    assert report["errors"] == []
    # A dry run did not alter either input version.
    assert hashlib.sha256(original).hexdigest() != report["source_sha256"]


def test_csv_import_does_not_use_generated_candidate_as_resolved_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "review.csv"
    row = {
        "member_id": "aph-ABC",
        "field": "date_of_birth",
        "review_status": "accepted",
        "resolved_value": "",
        "manual_source_url": "https://aph.example/person",
        "wikidata_value": "1970-04-19",
    }
    write_csv(path, [row], list(row))
    events, report = propose_review_csv_import(path, reviewer="Importer")
    assert not events
    assert "resolved_value" in report["errors"][0]["reason"]


def test_education_csv_import_preserves_original_assertion_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "education.csv"
    row = {
        "member_id": "aph-ABC",
        "raw_school_text": "Old School Name",
        "review_status": "accepted",
        "resolved_value": "Current School Name",
        "resolved_acara_id": "10",
        "attended_status": "attended_unspecified",
        "confidence": "verified",
        "retrieved_at": "2020-01-02T00:00:00+10:00",
        "manual_source_url": "https://aph.example/person",
    }
    write_csv(path, [row], list(row))
    events, report = propose_review_csv_import(path, reviewer="Importer")
    assert report["errors"] == []
    assert events[0].review_id == education_review_id("abc", "Old School Name")
    assert events[0].payload["recorded_school_name"] == "Old School Name"
    assert events[0].payload["institution_ref"] == "acara:10"
    assert events[0].legacy is not None
    assert events[0].legacy["record"]["resolved_value"] == "Current School Name"
    row["resolved_acara_id"] = ""
    write_csv(path, [row], list(row))
    events, report = propose_review_csv_import(path, reviewer="Importer")
    assert not events
    assert "explicit institution mapping" in report["errors"][0]["reason"]


def test_manual_school_csv_import_uses_valid_ascii_identity(tmp_path: Path) -> None:
    path = tmp_path / "school.csv"
    row = {
        "institution_id": "inst-unmatched-original",
        "raw_school_text": "École Secondaire Example",
        "review_status": "accepted",
        "resolved_school_name": "École Secondaire Example",
        "resolved_country": "Canada",
        "manual_source_url": "https://school.example/history",
    }
    write_csv(path, [row], list(row))
    events, report = propose_review_csv_import(path, reviewer="Importer")
    assert report["errors"] == []
    assert len(events) == 2
    validate_events(events)
    assert events[0].payload["institution_ref"].startswith("manual:")
    assert "latitude" not in events[0].payload


def test_parity_gate_rejects_evidence_changes_even_for_research_aliases() -> None:
    original = {
        "education_id": "edu-abc-one",
        "school_name_as_recorded": "Unreviewed School",
        "institution_id": "acara-10",
        "confidence": "verified",
        "source_url": "https://aph.example/person",
    }
    before = {
        "members": [],
        "parliament_service": [],
        "institutions": [],
        "member_education": [original],
    }
    after = {
        **before,
        "member_education": [
            {
                **original,
                "institution_id": "inst-unmatched-unreviewed-school",
                "confidence": "unconfirmed",
                "institution_resolution": "unresolved",
                "resolution_source_url": None,
            }
        ],
    }
    allowed = compare_migration_records(before, after, {"Unreviewed School"})
    assert allowed["passed"]
    assert (
        allowed["declared_policy_changes"][0]["policy"]
        == "unsourced_alias_requires_research"
    )
    after["member_education"][0]["source_url"] = "https://different.example/claim"
    rejected = compare_migration_records(before, after, {"Unreviewed School"})
    assert not rejected["passed"]
    assert "source_url" in rejected["unexpected_changes"][0]["changed_fields"]


def test_parity_gate_rejects_fabricated_research_target_and_orphan() -> None:
    education = {
        "education_id": "edu-abc-one",
        "school_name_as_recorded": "Unreviewed School",
        "institution_id": "acara-10",
        "confidence": "verified",
        "institution_resolution": "rename",
        "resolution_source_url": None,
    }
    before = {
        "members": [],
        "parliament_service": [],
        "institutions": [],
        "member_education": [education],
    }
    after = {
        **before,
        "member_education": [
            {
                **education,
                "institution_id": "acara-999",
                "institution_resolution": "successor",
                "resolution_source_url": "https://unrelated.example/claim",
            }
        ],
    }
    assert not compare_migration_records(before, after, {"Unreviewed School"})["passed"]
    orphan = {"institution_id": "acara-999", "school_name": "Invented School"}
    after = {**before, "institutions": [orphan]}
    assert not compare_migration_records(before, after, set())["passed"]
    assert compare_migration_records(
        before, after, set(), source_institutions=[orphan]
    )["passed"]
    after["institutions"] = [{**orphan, "school_name": "Changed source metadata"}]
    assert not compare_migration_records(
        before, after, set(), source_institutions=[orphan]
    )["passed"]


def test_incomplete_csv_schema_validation_can_retain_research(tmp_path: Path) -> None:
    path = tmp_path / "education.csv"
    row = {
        "member_id": "aph-ABC",
        "raw_school_text": "Old School Name",
        "review_status": "accepted",
        "resolved_value": "Old School Name",
        "attended_status": "attended_unspecified",
        "confidence": "verified",
        "retrieved_at": "2020-01-02",
        "manual_source_url": "https://aph.example/person",
    }
    write_csv(path, [row], list(row))
    blocked, report = propose_review_csv_import(path, reviewer="Importer")
    assert not blocked
    assert report["errors"]
    events, report = propose_review_csv_import(
        path, reviewer="Importer", incomplete_as_research=True
    )
    assert report["errors"] == []
    assert report["blocked"][0]["retained_as_research"]
    assert events[0].action == "research"
    assert "retrieved_at" not in events[0].payload
    assert events[0].legacy is not None
    assert events[0].legacy["record"]["retrieved_at"] == "2020-01-02"


def test_parity_gate_does_not_hide_duplicate_canonical_ids() -> None:
    empty = {
        "members": [],
        "parliament_service": [],
        "institutions": [],
        "member_education": [],
    }
    original = {"member_id": "aph-abc", "full_name": "Example Member"}
    result = compare_migration_records(
        {**empty, "members": [original]},
        {**empty, "members": [original, original]},
        set(),
    )
    assert not result["passed"]
    assert result["unexpected_changes"][0]["reason"] == (
        "Duplicate canonical identity in parity input"
    )
