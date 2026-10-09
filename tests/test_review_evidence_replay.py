"""Retained evidence is validated, pinned, and replayed without gaining authority."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from apemap.release import build_release, verify_release
from apemap.release.recipe import build_recipe_release, load_recipe, pin_recipe
from apemap.review.evidence import EvidenceRecord, legacy_evidence, parse_evidence
from apemap.review.integration import (
    apply_review_events,
    capture_review_snapshot,
    project_review_records,
    read_review_events,
    review_snapshot_evidence,
    review_snapshot_metadata,
)
from apemap.review.model import ReviewEvent, education_review_id, school_review_id
from apemap.review.store import StaleReviewError, encode_event
from tests.test_release import create_release_db
from tests.test_release_recipe import recipe_fixture as _recipe_fixture
from tests.test_review_integration import seeded_review as _seeded_review

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

    from apemap.ingest.matching import SchoolMatcher

pytestmark = pytest.mark.integration
seeded_review = _seeded_review
recipe_fixture = _recipe_fixture


def mapping(refs: list[str] | None = None) -> ReviewEvent:
    payload: dict[str, Any] = {
        "aph_id": "APH-ONE",
        "recorded_school_name": "First High School",
        "institution_ref": "acara:2",
        "relationship_type": "direct",
    }
    if refs is not None:
        payload["evidence_refs"] = refs
    return ReviewEvent(
        decision_id="assertion-map",
        review_id=education_review_id("APH-ONE", "First High School"),
        entity_type="member_education",
        action="map",
        payload=payload,
        source_url="https://example.org/relationship",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
        reviewer="Fixture reviewer",
    )


def evidence(
    *, stance: str = "supports", quality: float = 1.0, review_id: str | None = None
) -> EvidenceRecord:
    return EvidenceRecord(
        review_id=review_id or mapping().review_id,
        candidate_institution_ref="acara:2",
        source_title="Retained historical school evidence",
        source_type="institution-history",
        source_url="https://example.org/history",
        retrieved_at="2026-10-09T00:00:00+00:00",
        claim_type="relationship",
        claim_value={"relationship_type": "direct"},
        stance=stance,
        excerpt_or_note="Fixture evidence with a reviewer-assessed stance",
        generated_by="manual",
        source_quality=quality,
        match_strength=quality,
        temporal_relevance=quality,
        geographic_relevance=quality,
    )


def write_evidence(path: Path, records: list[EvidenceRecord]) -> bytes:
    raw = b"".join(
        (json.dumps(record.to_dict(), ensure_ascii=False) + "\r\n").encode("utf-8")
        for record in records
    )
    path.write_bytes(raw)
    return raw


def test_multiple_supporting_and_opposing_refs_do_not_change_source_facts(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher], tmp_path: Path
) -> None:
    conn, matcher = seeded_review
    records = [evidence(), evidence(stance="contradicts", quality=0.3)]
    event = mapping([record.evidence_id for record in records])
    ledger = tmp_path / "decisions.jsonl"
    ledger.write_bytes(encode_event(event))
    write_evidence(tmp_path / "evidence.jsonl", records)
    consumed = read_review_events(ledger)

    expected = project_review_records(conn, [mapping()], matcher)
    actual = project_review_records(conn, consumed, matcher)

    assert actual == expected
    attendance = actual["member_education"][0]
    assert attendance["institution_id"] == "acara-2"
    assert attendance["confidence"] == "verified"
    assert attendance["source_url"] == "https://example.org/aph"
    apply_review_events(conn, events=consumed, matcher=matcher)
    assert conn.execute(
        "SELECT confidence, source_url FROM member_education"
    ).fetchone() == ("verified", "https://example.org/aph")


@pytest.mark.parametrize("invalid", ["missing", "case", "candidate"])
def test_offline_replay_rejects_unresolvable_evidence_refs(
    invalid: str, tmp_path: Path
) -> None:
    record = evidence()
    if invalid == "case":
        record = replace(
            record,
            review_id=education_review_id("OTHER", "First High School"),
            evidence_id="",
        )
    elif invalid == "candidate":
        record = replace(record, candidate_institution_ref="acara:1", evidence_id="")
    ledger = tmp_path / "decisions.jsonl"
    ledger.write_bytes(encode_event(mapping([record.evidence_id])))
    if invalid != "missing":
        write_evidence(tmp_path / "evidence.jsonl", [record])

    with pytest.raises(
        ValueError, match="Unknown evidence|different case|different candidate"
    ):
        read_review_events(ledger)


def test_evidence_quality_changes_never_change_canonical_authority(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
) -> None:
    conn, matcher = seeded_review
    low, high = evidence(quality=0), evidence(quality=1)
    low_event, high_event = mapping([low.evidence_id]), mapping([high.evidence_id])

    low_result = project_review_records(
        conn, [low_event], matcher, evidence_records=[low]
    )
    high_result = project_review_records(
        conn, [high_event], matcher, evidence_records=[high]
    )

    assert low_result == high_result
    assert conn.execute("SELECT institution_id FROM member_education").fetchone() == (
        "acara-1",
    )


def test_legacy_citations_can_be_referenced_without_migrating_retained_bytes(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher], tmp_path: Path
) -> None:
    conn, matcher = seeded_review
    original = mapping()
    event = replace(
        mapping([legacy_evidence([original])[0].evidence_id]),
        decision_id="resolution-with-legacy-evidence",
        action="supersede",
        replacement_action="map",
        supersedes=[original.decision_id],
    )
    ledger = tmp_path / "decisions.jsonl"
    raw = encode_event(original) + encode_event(event)
    ledger.write_bytes(raw)
    consumed = read_review_events(ledger)
    project_review_records(conn, consumed, matcher)
    snapshot = capture_review_snapshot(conn, consumed, decision_log_path=ledger)

    assert snapshot["referenced_evidence_count"] == 1
    assert "evidence_log_sha256" not in snapshot
    assert review_snapshot_evidence(conn) is None
    assert ledger.read_bytes() == raw
    assert not (tmp_path / "evidence.jsonl").exists()


@pytest.mark.parametrize("filename", ["evidence.jsonl", "separate-evidence.jsonl"])
def test_snapshot_archives_exact_evidence_and_rejects_changes_during_build(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
    tmp_path: Path,
    filename: str,
) -> None:
    conn, matcher = seeded_review
    record = evidence()
    ledger, retained = tmp_path / "decisions.jsonl", tmp_path / filename
    ledger.write_bytes(encode_event(mapping([record.evidence_id])))
    raw = write_evidence(retained, [record])
    consumed = read_review_events(ledger, evidence_log_path=retained)
    apply_review_events(
        conn, events=consumed, matcher=matcher, evidence_log_path=retained
    )
    snapshot = capture_review_snapshot(
        conn, consumed, decision_log_path=ledger, evidence_log_path=retained
    )
    assert snapshot["evidence_log_sha256"] == hashlib.sha256(raw).hexdigest()
    assert snapshot["evidence_count"] == 1
    assert snapshot["evidence_source"] == "retained_review_evidence"
    assert review_snapshot_evidence(conn) == raw

    write_evidence(retained, [record, evidence(stance="contradicts")])
    with pytest.raises(StaleReviewError, match="Evidence log changed"):
        capture_review_snapshot(
            conn, consumed, decision_log_path=ledger, evidence_log_path=retained
        )
    assert review_snapshot_metadata(conn) == snapshot
    assert review_snapshot_evidence(conn) == raw

    conn.execute("UPDATE review_build_evidence SET evidence_jsonl = ?", [b"tampered\n"])
    with pytest.raises(ValueError, match="archived evidence"):
        review_snapshot_metadata(conn)


def test_consumed_evidence_path_cannot_silently_change(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher], tmp_path: Path
) -> None:
    conn, _ = seeded_review
    ledger = tmp_path / "decisions.jsonl"
    ledger.write_bytes(b"")
    consumed = read_review_events(ledger)
    with pytest.raises(ValueError, match="differs from the consumed"):
        capture_review_snapshot(
            conn,
            consumed,
            decision_log_path=ledger,
            evidence_log_path=tmp_path / "another.jsonl",
        )


def test_legacy_snapshot_metadata_has_no_added_evidence_fields(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher], tmp_path: Path
) -> None:
    conn, _ = seeded_review
    ledger = tmp_path / "decisions.jsonl"
    ledger.write_bytes(b"")
    snapshot = capture_review_snapshot(conn, [], decision_log_path=ledger)

    assert set(snapshot) == {
        "schema_version",
        "decision_log_sha256",
        "event_count",
        "effective_decisions",
        "source_parliaments",
        "source_provenance",
    }
    assert review_snapshot_evidence(conn) is None


def test_fresh_recipe_pins_evidence_without_changing_legacy_recipe(
    recipe_fixture: tuple[Path, Path],
) -> None:
    root, template = recipe_fixture
    original = template.read_bytes()
    configuration = json.loads(original)
    retained = (root / configuration["decision_log"]).with_name("evidence.jsonl")
    raw = write_evidence(
        retained, [evidence(review_id=school_review_id("Unresolved Fixture School"))]
    )
    assert "evidence_log" not in load_recipe(template, root=root)
    pinned = root / "new-evidence-recipe.json"
    pin_recipe(template, pinned, root=root)
    recipe = load_recipe(pinned, root=root)

    assert recipe["evidence_log"] == retained.relative_to(root).as_posix()
    assert recipe["evidence_log_sha256"] == hashlib.sha256(raw).hexdigest()
    assert template.read_bytes() == original
    retained.write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_recipe(pinned, root=root)
    # Historical recipes do not start consuming a later unpinned evidence file.
    assert load_recipe(template, root=root) == configuration


def test_recipe_copies_pinned_evidence_before_ingestion(
    recipe_fixture: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, template = recipe_fixture
    configuration = load_recipe(template, root=root)
    working = (root / configuration["decision_log"]).with_name("evidence.jsonl")
    raw = write_evidence(
        working, [evidence(review_id=school_review_id("Unresolved Fixture School"))]
    )
    pinned = root / "new-evidence-recipe.json"
    pin_recipe(template, pinned, root=root)

    def frozen_build(
        *args: Any,
        decision_log_path: Path,
        release_recipe: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        archived = decision_log_path.with_name("evidence.jsonl")
        assert archived != working
        assert archived.read_bytes() == raw
        working.write_bytes(b"later working revision\n")
        assert archived.read_bytes() == raw
        assert release_recipe["evidence_log_sha256"] == hashlib.sha256(raw).hexdigest()
        read_review_events(decision_log_path)
        return {"version": args[4]}

    monkeypatch.setattr("apemap.release.recipe.build_historical_release", frozen_build)
    assert build_recipe_release(
        pinned,
        tmp_path / "unused.duckdb",
        tmp_path / "unused-release",
        "0.6.0-rc.1",
        root=root,
        source_commit="a" * 40,
    ) == {"version": "0.6.0-rc.1"}


@pytest.mark.parametrize("preexisting_review", [False, True])
def test_release_uses_archived_evidence_and_verifies_consumed_hash(
    tmp_path: Path,
    preexisting_review: bool,
) -> None:
    from apemap.db import get_connection

    db = create_release_db(tmp_path / "evidence-release.duckdb")
    ledger, retained = tmp_path / "decisions.jsonl", tmp_path / "evidence.jsonl"
    ledger.write_bytes(b"")
    raw = write_evidence(retained, [evidence()])
    with get_connection(db) as conn:
        snapshot = capture_review_snapshot(
            conn, read_review_events(ledger), decision_log_path=ledger
        )
    retained.write_bytes(b"invalid later evidence\n")
    ledger.write_bytes(b"invalid later authority\n")
    output = tmp_path / "release"
    prior_report = output / "review" / "coverage.json"
    if preexisting_review:
        prior_report.parent.mkdir(parents=True)
        prior_report.write_bytes(b'{"source":"historical ingestion fixture"}\n')
    build_release(
        db_path=db,
        output_dir=output,
        version="0.6.0-rc.1",
        parliaments=[47],
        strict=False,
    )
    manifest = json.loads((output / "manifest.json").read_bytes())

    assert (output / "review/evidence.jsonl").read_bytes() == raw
    if preexisting_review:
        assert (
            prior_report.read_bytes() == b'{"source":"historical ingestion fixture"}\n'
        )
    assert manifest["review_snapshot"] == snapshot
    assert (
        manifest["files"]["review/evidence.jsonl"]["sha256"]
        == snapshot["evidence_log_sha256"]
    )
    assert parse_evidence(raw)[0].evidence_id == evidence().evidence_id
    assert verify_release(output)["valid"]
    manifest["review_snapshot"]["evidence_log_sha256"] = "a" * 64
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert any(
        "differs from the consumed review snapshot" in error
        for error in verify_release(output)["errors"]
    )
