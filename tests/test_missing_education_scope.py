"""Published missing-name audits preserve scope and never apply research decisions."""

from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection
from apemap.review import missing_education as module
from apemap.review.integration import capture_review_sources
from apemap.review.research_batches import ResearchBatches
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_service import review_service as review_service


@pytest.fixture
def published_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, ResearchBatches, dict[str, list[dict[str, Any]]]]:
    members = [
        {
            "member_id": f"aph-{name}",
            "aph_id": name.upper(),
            "display_name": name,
            "wikidata_id": None,
        }
        for name in ("missing", "home", "named", "later")
    ]
    services = [
        {
            "member_id": member["member_id"],
            "service_id": f"service-{index}",
            "parliament_number": 48,
            "is_opening_day_member": member["member_id"] != "aph-later",
            "chamber": "senate",
        }
        for index, member in enumerate(members)
    ]
    services.append({**services[0], "service_id": "duplicate-service"})
    education = [
        {
            "member_id": "aph-home",
            "level": "secondary",
            "institution_resolution": "home_educated",
            "school_name_as_recorded": "Home educated",
            "confidence": "unconfirmed",
        },
        {
            "member_id": "aph-named",
            "level": "secondary",
            "institution_resolution": "unresolved",
            "school_name_as_recorded": "Named historical school",
            "confidence": "unconfirmed",
        },
        {
            "member_id": "aph-missing",
            "level": "tertiary",
            "institution_resolution": "direct",
            "school_name_as_recorded": "University",
            "confidence": "verified",
        },
    ]
    records = {
        "members": members,
        "parliament_service": services,
        "member_education": education,
    }
    release = tmp_path / "release"
    (release / "data").mkdir(parents=True)
    manifest: dict[str, Any] = {
        "data_release_version": "0.7.0",
        "cohort_definition": "opening_day",
        "parliaments": [48],
        "files": {},
    }
    payload_hashes = {}
    for table, rows in records.items():
        path = release / "data" / f"{table}.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        payload_hashes[table] = digest
        manifest["files"][f"data/{table}.csv"] = {
            "sha256": digest,
            "size_bytes": len(raw),
        }
    raw_manifest = json.dumps(manifest).encode()
    (release / "manifest.json").write_bytes(raw_manifest)
    monkeypatch.setitem(
        module.PUBLISHED_RELEASE,
        "manifest_sha256",
        hashlib.sha256(raw_manifest).hexdigest(),
    )
    monkeypatch.setitem(module.PUBLISHED_RELEASE, "payload_sha256", payload_hashes)
    service = ReviewService(
        log_path=tmp_path / "decisions.jsonl", db_path=tmp_path / "absent.duckdb"
    )
    monkeypatch.setattr(service, "source_revision", lambda **kwargs: "source")
    monkeypatch.setattr(
        module,
        "project_missing_education_scope",
        lambda *args: module._scope(records, 48),
    )
    monkeypatch.setattr(module, "RAW_WIKIMEDIA_DIR", tmp_path / "wikimedia")
    return release, ResearchBatches(service), records


def test_audit_preserves_home_skip_and_excludes_named_and_later_members(
    published_scope: tuple[Path, ResearchBatches, dict[str, list[dict[str, Any]]]],
) -> None:
    release, batches, _ = published_scope
    cache = module.RAW_WIKIMEDIA_DIR / "members"
    cache.mkdir(parents=True)
    (cache / "MISSING.json").write_text(
        json.dumps(
            {
                "aph_id": "MISSING",
                "status": "matched",
                "wikidata_id": "Q123",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Actual_returned_title",
                "source_url": "https://query.wikidata.org/",
                "retrieved_at": "2026-10-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    state = module.prepare_missing_education_batch(
        batches, "missing", release, expected_count=2
    )
    assert state["manifest"]["audit_count"] == 2
    assert state["manifest"]["skipped_count"] == 1
    assert state["manifest"]["case_count"] == 1
    assert state["manifest"]["audit_summary"]["skipped"][0]["member_id"] == "aph-home"
    assert {row["member_id"] for row in state["audit"]["members"]} == {
        "aph-home",
        "aph-missing",
    }
    task = state["tasks"][0]
    assert task["review_id"] == "education:missing:missing"
    assert task["purpose"] == module.PURPOSE
    assert task["context"]["member"]["wikidata_id"] is None
    assert task["context"]["identity_hint"]["wikidata_id"] == "Q123"
    assert task["context"]["assertion"]["recorded_school_name"] == ""
    assert state["manifest"]["models"] == []
    assert state["manifest"]["provenance"]["model"] is None
    assert batches.show("missing")["tasks"] == state["tasks"]
    assert not batches.service.log_path.exists()
    assert not batches.service.evidence_path.exists()
    assert not batches.service.db_path.exists()


@pytest.mark.parametrize("artifact", ["manifest.json", "data/member_education.csv"])
def test_published_artifact_tampering_is_rejected_before_batch_creation(
    published_scope: tuple[Path, ResearchBatches, dict[str, list[dict[str, Any]]]],
    artifact: str,
) -> None:
    release, batches, _ = published_scope
    path = release / artifact
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="manifest|checksum"):
        module.prepare_missing_education_batch(
            batches, "tampered", release, expected_count=2
        )
    assert not batches.path("tampered").exists()


def test_expected_count_and_current_projection_drift_are_rejected(
    published_scope: tuple[Path, ResearchBatches, dict[str, list[dict[str, Any]]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, batches, records = published_scope
    with pytest.raises(ValueError, match="Expected 49 audit members, found 2"):
        module.prepare_missing_education_batch(batches, "wrong-count", release)
    changed = deepcopy(records)
    changed["member_education"].append(
        {
            "member_id": "aph-missing",
            "level": "secondary",
            "institution_resolution": "unresolved",
            "school_name_as_recorded": "New reviewed school",
        }
    )
    monkeypatch.setattr(
        module,
        "project_missing_education_scope",
        lambda *args: module._scope(changed, 48),
    )
    with pytest.raises(StaleReviewError, match="differs from the published audit"):
        module.prepare_missing_education_batch(
            batches, "changed", release, expected_count=2
        )
    assert not batches.path("changed").exists()


def test_projection_reads_without_changing_source_or_authority(
    review_service: ReviewService,
) -> None:
    with get_connection(review_service.db_path) as conn:
        conn.execute("DELETE FROM member_education")
        conn.execute("UPDATE parliament_service SET is_opening_day_member = TRUE")
        capture_review_sources(conn)
    before = review_service.db_path.read_bytes()
    rows = module.project_missing_education_scope(review_service, 48)
    assert [row["member_id"] for row in rows] == ["aph-test"]
    assert rows[0]["status"] == "needs_research"
    assert review_service.db_path.read_bytes() == before
    assert not review_service.log_path.exists()
    assert not review_service.evidence_path.exists()


def test_prepare_missing_education_cli_uses_normal_batch_workflow(
    published_scope: tuple[Path, ResearchBatches, dict[str, list[dict[str, Any]]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apemap.review import research_batch_cli

    release, batches, _ = published_scope
    monkeypatch.setattr(research_batch_cli, "_batches", lambda ctx: batches)
    result = CliRunner().invoke(
        app,
        [
            "review",
            "research-batch",
            "prepare-missing-education",
            "cli",
            "--release-dir",
            str(release),
            "--parliament",
            "48",
            "--expected-count",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["manifest"]["purpose"] == module.PURPOSE
    assert batches.show("cli")["manifest"]["origin"] == "agent-session"
