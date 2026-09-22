"""Tests for APH ingestion pipeline, school matching, dual snapshots, and CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app, parse_parliament_args
from apemap.ingest.matching import (
    SchoolMatcher,
    extract_schools_from_bio_text,
    is_international_text,
    split_school_string,
)
from apemap.ingest.pipeline import run_aph_ingestion

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SAMPLE_APH_PATH = FIXTURES_DIR / "aph_sample.json"


@pytest.fixture
def sample_aph_records() -> list[dict[str, Any]]:
    """Load deterministic APH sample data fixture."""
    return json.loads(SAMPLE_APH_PATH.read_text(encoding="utf-8"))


def test_split_school_string() -> None:
    """Verify splitting of delimited school names across different formats."""
    # Single school
    assert split_school_string("Sydney Grammar School") == ["Sydney Grammar School"]

    # Slash delimiter
    slash = split_school_string("Narrabeen Boys High School / Manly High School")
    assert slash == ["Narrabeen Boys High School", "Manly High School"]

    # Semicolon delimiter
    semi = split_school_string("Canberra High School; Narrabundah College")
    assert semi == ["Canberra High School", "Narrabundah College"]

    # 'and' delimiter when both are schools
    and_split = split_school_string("Perth Modern School and Scotch College")
    assert and_split == ["Perth Modern School", "Scotch College"]

    # Comma separating two distinct schools
    comma_two = split_school_string(
        "Melbourne Girls Grammar School, Albury High School"
    )
    assert comma_two == ["Melbourne Girls Grammar School", "Albury High School"]

    # Comma indicating suburb / city should NOT be split into two schools
    comma_loc = split_school_string("Wesley College, Melbourne")
    assert comma_loc == ["Wesley College, Melbourne"]


def test_extract_schools_from_bio_text() -> None:
    """Verify fallback regex extraction from biographical text while excluding degrees."""
    bio_texts = [
        "Bachelor of Economics, University of Sydney",
        "Doctor of Philosophy, Monash University",
        "Attended Albury High School from 1987 to 1992",
        "Educated at North Sydney Boys High School",
    ]
    extracted = extract_schools_from_bio_text(bio_texts)
    assert "Albury High School" in extracted
    assert "North Sydney Boys High School" in extracted
    assert not any("University" in s for s in extracted)
    assert not any("Bachelor" in s for s in extracted)


def test_is_international_text() -> None:
    """Verify detection of international / overseas institutions."""
    assert is_international_text("Eton College, UK") is True
    assert is_international_text("Singapore American School") is True
    assert is_international_text("St George's School, Switzerland") is True
    assert is_international_text("Sydney Grammar School") is False
    assert is_international_text("Canberra High School") is False


def test_school_matcher_unmatched_and_international(tmp_path: Path) -> None:
    """Verify matcher generates provisional institutions and tags international entries."""
    matcher = SchoolMatcher(external_dir=tmp_path)  # empty dir
    res_intl = matcher.match("Eton College, UK")
    assert res_intl.is_international is True
    assert res_intl.confidence == "unconfirmed"
    assert res_intl.sector == "Other"
    assert res_intl.institution_id.startswith("inst-unmatched-")

    res_unmatched = matcher.match("Some Obscure Nonexistent High School")
    assert res_unmatched.confidence == "unconfirmed"
    assert res_unmatched.sector == "Other"


def test_pipeline_dual_snapshot_isolation(
    sample_aph_records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Verify dual snapshot isolation (baseline vs ongoing tracker) across 46, 47, 48."""
    out_dir = tmp_path / "processed"
    db_file = tmp_path / "test_aped.duckdb"

    result = run_aph_ingestion(
        parliaments=[46, 47, 48],
        raw_individuals=sample_aph_records,
        db_path=db_file,
        output_dir=out_dir,
        export_parquet_files=True,
    )

    conn = result["connection"]

    # 1. Total records inserted
    assert result["members_count"] == 7
    assert result["service_count"] > 0
    assert result["education_count"] > 0

    # 2. Check 48th Parliament snapshot isolation
    # Mid-term member (entered 2025-10-15 after 48th opening 2025-07-22)
    midterm = conn.execute(
        """
        SELECT is_opening_day_member, is_current_member
        FROM parliament_service
        WHERE member_id = 'aph-mid48' AND parliament_number = 48
        """
    ).fetchone()
    assert midterm is not None
    assert midterm[0] is False  # NOT opening day member
    assert midterm[1] is True  # IS current member

    # Opening day member in 48th (Albanese)
    albanese_48 = conn.execute(
        """
        SELECT is_opening_day_member, is_current_member
        FROM parliament_service
        WHERE member_id = 'aph-r36' AND parliament_number = 48
        """
    ).fetchone()
    assert albanese_48 is not None
    assert albanese_48[0] is True  # IS opening day member
    assert albanese_48[1] is True  # IS current member

    # 3. Check 46th Parliament member who left before dissolution (Abbott)
    abbott_46 = conn.execute(
        """
        SELECT is_opening_day_member, is_current_member
        FROM parliament_service
        WHERE member_id = 'aph-ez5' AND parliament_number = 46
        """
    ).fetchone()
    assert abbott_46 is not None
    assert abbott_46[0] is True  # was opening day member (service started 1994)
    assert abbott_46[1] is False  # ended 2019-05-18 before 46th dissolution

    # 4. Verify baseline and current views
    opening_48 = conn.execute(
        "SELECT COUNT(*) FROM v_parliament_members_opening WHERE parliament_number = 48"
    ).fetchone()
    current_48 = conn.execute(
        "SELECT COUNT(*) FROM v_parliament_members_current WHERE parliament_number = 48"
    ).fetchone()
    assert opening_48 is not None and current_48 is not None
    # Current includes Jane Midterm while opening excludes her
    assert current_48[0] == opening_48[0] + 1

    # 5. Verify 48th parliament views
    df_48 = conn.execute("SELECT * FROM member_aph_48").df()
    assert len(df_48) > 0
    edu_48 = conn.execute("SELECT * FROM member_secondary_school_education_48").df()
    assert len(edu_48) > 0

    # 6. Verify coverage metrics view in DuckDB
    cov_rows = conn.execute("SELECT * FROM v_coverage_metrics").df()
    assert len(cov_rows) == 3  # 46, 47, 48
    p48_row = cov_rows[cov_rows["parliament_number"] == 48].iloc[0]
    assert p48_row["total_parliamentarians"] >= 4
    assert p48_row["opening_day_parliamentarians"] < p48_row["total_parliamentarians"]

    # 7. Verify review CSV and coverage metrics JSON output
    unmatched_csv = result["unmatched_csv"]
    assert unmatched_csv.exists()
    csv_content = unmatched_csv.read_text(encoding="utf-8")
    assert "Eton College, UK" in csv_content

    metrics_json = result["coverage_metrics_json"]
    assert metrics_json.exists()
    metrics_data = json.loads(metrics_json.read_text(encoding="utf-8"))
    assert "48" in metrics_data or 48 in metrics_data

    # 8. Verify Parquet exports
    assert "members" in result["parquet_paths"]
    assert result["parquet_paths"]["members"].exists()

    conn.close()


def test_pipeline_rerun_is_idempotent(
    sample_aph_records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Rerunning ingestion into one database keeps exported artifacts stable."""
    db_file = tmp_path / "idempotent.duckdb"
    external_dir = tmp_path / "external"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    for output_dir in (first_dir, second_dir):
        result = run_aph_ingestion(
            parliaments=[48],
            raw_individuals=sample_aph_records,
            db_path=db_file,
            output_dir=output_dir,
            export_parquet_files=True,
            external_dir=external_dir,
        )
        result["connection"].close()

    def manifest(root: Path) -> dict[str, str]:
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    assert manifest(first_dir) == manifest(second_dir)


def test_parse_parliament_args() -> None:
    """Verify parliament command line argument parser."""
    assert parse_parliament_args("46,47,48") == [46, 47, 48]
    assert parse_parliament_args("46 47 48") == [46, 47, 48]
    assert parse_parliament_args("48") == [48]
    with pytest.raises(Exception):
        parse_parliament_args("invalid,number")


def test_cli_ingest_aph(
    sample_aph_records: list[dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Typer CLI invocation of `apemap ingest aph`."""
    db_file = tmp_path / "cli_aped.duckdb"
    out_dir = tmp_path / "cli_processed"

    # Mock AphClient.fetch_individuals to return sample fixture
    monkeypatch.setattr(
        "apemap.ingest.pipeline.AphClient.fetch_individuals",
        lambda self, refresh=False: sample_aph_records,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ingest",
            "aph",
            "--parliament",
            "46,47,48",
            "--db-path",
            str(db_file),
            "--output-dir",
            str(out_dir),
            "--no-export-parquet",
        ],
    )
    assert result.exit_code == 0
    assert "Parliament Coverage & Gap Metrics" in result.stdout
    assert "Ingestion Complete!" in result.stdout
    assert db_file.exists()
    assert (out_dir / "unmatched_schools.csv").exists()
    assert (out_dir / "coverage_metrics.json").exists()
