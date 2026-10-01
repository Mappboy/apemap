"""Tests for the expanded dataset release contract: build, verify, and diff."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection, init_schema
from apemap.release import (
    RESTRICTED_FINANCE_COLUMNS,
    build_release,
    diff_releases,
    verify_release,
)
from tests.test_web_release_contract import seed_web_db


def create_release_db(db_path: Path) -> Path:
    """Create a fully-formed test database using seed_web_db."""
    conn = get_connection(db_path)
    init_schema(conn)
    seed_web_db(conn)
    conn.close()
    return db_path


@pytest.mark.unit
def test_build_and_verify_release(tmp_path: Path) -> None:
    """Verify end-to-end build generates all files and verify_release passes cleanly."""
    db_path = create_release_db(tmp_path / "test_aped.duckdb")
    rel_dir = tmp_path / "release-v1.0.0"

    results = build_release(
        db_path=db_path,
        output_dir=rel_dir,
        version="1.0.0",
        parliaments=[47],
        strict=False,
    )

    assert results["version"] == "1.0.0"
    assert (rel_dir / "manifest.json").exists()
    assert (rel_dir / "SHA256SUMS").exists()

    # Check analysis directory
    assert (rel_dir / "analysis" / "demographics.json").exists()
    assert (rel_dir / "analysis" / "education_sectors.json").exists()
    assert (rel_dir / "analysis" / "party_sectors.json").exists()
    assert (rel_dir / "analysis" / "shared_schools.json").exists()
    assert (rel_dir / "analysis" / "cross_parliament.json").exists()

    # Check web directory
    assert (rel_dir / "web" / "metadata.json").exists()
    assert (rel_dir / "web" / "assertions.json").exists()
    assert (rel_dir / "web" / "members.json").exists()
    assert (rel_dir / "web" / "schools.geojson").exists()
    assert (rel_dir / "web" / "results-summary.json").exists()

    # Check data directory
    assert (rel_dir / "data" / "members.csv").exists()
    assert (rel_dir / "data" / "members.parquet").exists()
    assert (rel_dir / "data" / "school_snapshots.csv").exists()
    assert (rel_dir / "data" / "school_snapshots.parquet").exists()

    # Verify absence of restricted columns in data/
    with (rel_dir / "data" / "school_snapshots.csv").open("r", encoding="utf-8") as f:
        header = next(csv.reader(f))
        assert "financial_profile_2021" not in header
        assert not (set(header) & RESTRICTED_FINANCE_COLUMNS)

    # Run verify_release
    report = verify_release(rel_dir)
    assert report["valid"] is True
    assert report["errors"] == []
    assert report["verified_files_count"] == results["files_count"]


@pytest.mark.unit
def test_verify_release_catches_tampering(tmp_path: Path) -> None:
    """verify_release catches modified files, unmanifested files, and restricted leaks."""
    db_path = create_release_db(tmp_path / "test_tamper.duckdb")
    rel_dir = tmp_path / "release-tamper"
    build_release(
        db_path=db_path,
        output_dir=rel_dir,
        version="1.0.0",
        parliaments=[47],
        strict=False,
    )

    # 1. Modify a file
    demo_file = rel_dir / "analysis" / "demographics.json"
    demo_file.write_text('{"tampered": true}\n', encoding="utf-8")
    rep_tamper = verify_release(rel_dir)
    assert rep_tamper["valid"] is False
    assert any("Hash mismatch" in e for e in rep_tamper["errors"])

    # Restore demographics
    build_release(
        db_path=db_path,
        output_dir=rel_dir,
        version="1.0.0",
        parliaments=[47],
        strict=False,
    )

    # 2. Add unmanifested file
    stray = rel_dir / "stray.txt"
    stray.write_text("untracked\n", encoding="utf-8")
    rep_stray = verify_release(rel_dir)
    assert rep_stray["valid"] is False
    assert any("Unmanifested file found" in e for e in rep_stray["errors"])
    stray.unlink()

    # 3. Inject restricted column into public CSV
    csv_file = rel_dir / "data" / "members.csv"
    csv_file.write_text(
        "member_id,total_gross_income_per_student\nm-1,1000\n", encoding="utf-8"
    )
    rep_leak = verify_release(rel_dir)
    assert rep_leak["valid"] is False
    assert any("Restricted finance column" in e for e in rep_leak["errors"])


@pytest.mark.unit
def test_diff_releases(tmp_path: Path) -> None:
    """diff_releases accurately identifies added, removed, modified, and unchanged files."""
    db_path = create_release_db(tmp_path / "test_diff.duckdb")
    rel_v1 = tmp_path / "release-v1.0.0"
    rel_v2 = tmp_path / "release-v1.1.0"

    build_release(
        db_path=db_path,
        output_dir=rel_v1,
        version="1.0.0",
        parliaments=[47],
        strict=False,
    )

    # Build v2 with changes: add a member in DB
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO members (member_id, family_name, given_name, display_name, gender, aph_id)
        VALUES ('m-new', 'New', 'Member', 'New Member', 'Female', 'aph-new')
        """
    )
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party,
            party_abbrev, state_or_territory, is_opening_day_member, is_current_member
        ) VALUES ('srv-new', 'm-new', 47, 'representatives', 'Labor', 'ALP', 'NSW', TRUE, TRUE)
        """
    )
    conn.close()

    build_release(
        db_path=db_path,
        output_dir=rel_v2,
        version="1.1.0",
        parliaments=[47],
        strict=False,
    )

    diff = diff_releases(rel_v1, rel_v2)
    assert diff["old_version"] == "1.0.0"
    assert diff["new_version"] == "1.1.0"
    assert len(diff["modified_files"]) > 0
    mod_paths = [m["path"] for m in diff["modified_files"]]
    assert "data/members.csv" in mod_paths or "web/members.json" in mod_paths


@pytest.mark.unit
def test_cli_release_build_verify_diff(tmp_path: Path) -> None:
    """Test CLI commands for release build, verify, and diff."""
    db_path = create_release_db(tmp_path / "test_cli.duckdb")
    rel_v1 = tmp_path / "cli-v1.0.0"
    rel_v2 = tmp_path / "cli-v1.0.1"

    runner = CliRunner()

    # 1. Build
    res_build = runner.invoke(
        app,
        [
            "release",
            "build",
            "--db-path",
            str(db_path),
            "--output-dir",
            str(rel_v1),
            "--version",
            "1.0.0",
            "--parliament",
            "47",
            "--no-strict",
        ],
    )
    assert res_build.exit_code == 0
    assert "Release v1.0.0 built successfully!" in res_build.output

    # 2. Verify
    res_verify = runner.invoke(app, ["release", "verify", str(rel_v1)])
    assert res_verify.exit_code == 0
    assert "verified successfully!" in res_verify.output

    # Build v2
    runner.invoke(
        app,
        [
            "release",
            "build",
            "--db-path",
            str(db_path),
            "--output-dir",
            str(rel_v2),
            "--version",
            "1.0.1",
            "--parliament",
            "47",
            "--no-strict",
        ],
    )

    # 3. Diff
    res_diff = runner.invoke(app, ["release", "diff", str(rel_v1), str(rel_v2)])
    assert res_diff.exit_code == 0
    assert "Release Diff: v1.0.0 -> v1.0.1" in res_diff.output
