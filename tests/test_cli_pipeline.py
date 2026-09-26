"""Tests for the deterministic CLI commands, validation engine, and export pipeline."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from typer.testing import CliRunner

from apemap.analysis import (
    compute_age_at_date,
    compute_funding_summary,
    compute_parliament_demographics,
    compute_sector_summary,
    get_age_bracket,
)
from apemap.cli import app
from apemap.db import (
    CANONICAL_TABLES,
    ensure_spatial,
    get_connection,
    init_schema,
)
from apemap.validate import validate_database

runner = CliRunner()


def artifact_manifest(directory: Path) -> dict[str, str]:
    """Return relative artifact paths and SHA-256 hashes for a directory."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def table_row_count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """Return a canonical table row count with an explicit fetch assertion."""
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


@pytest.fixture
def populated_db(tmp_path: Path) -> tuple[Path, duckdb.DuckDBPyConnection]:
    """Provide a temporary DuckDB database populated with deterministic test data."""
    db_file = tmp_path / "test_aped.duckdb"
    conn = get_connection(db_file)
    ensure_spatial(conn)
    init_schema(conn)

    # Insert 200+ members to pass parliament size sanity check
    # Member 1: Born 1970-01-01 -> Age on 2022-07-26 is 52. Attended Gov school.
    # Member 2: Born 1980-08-01 -> Age on 2022-07-26 is 41. Attended Catholic school.
    # Member 3: Born 1990-05-15 -> Age on 2022-07-26 is 32. Attended Gov AND Ind schools (Combined/Multiple).
    members_data = [
        ("mem-1", "Smith", "Alice", "Alice Smith", "Female", date(1970, 1, 1), "APH-1"),
        ("mem-2", "Jones", "Bob", "Bob Jones", "Male", date(1980, 8, 1), "APH-2"),
        (
            "mem-3",
            "Brown",
            "Charlie",
            "Charlie Brown",
            "Male",
            date(1990, 5, 15),
            "APH-3",
        ),
    ]
    for i in range(4, 228):
        members_data.append(
            (
                f"mem-{i}",
                f"Family{i}",
                f"Given{i}",
                f"Given{i} Family{i}",
                "Other",
                date(1985, 1, 1),
                f"APH-{i}",
            )
        )

    for idx, (mid, fam, giv, disp, gen, dob, aph) in enumerate(members_data, 1):
        conn.execute(
            """
            INSERT INTO members (member_id, family_name, given_name, display_name, gender, date_of_birth, aph_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [mid, fam, giv, disp, gen, dob, aph],
        )
        chamber = "representatives" if idx <= 151 else "senate"
        conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber,
                party, party_abbrev, state_or_territory, service_start, service_end,
                is_opening_day_member, is_current_member
            ) VALUES (?, ?, 47, ?, 'Labor', 'ALP', 'NSW', '2022-07-26', '2025-04-11', TRUE, TRUE)
            """,
            [f"srv-{mid}", mid, chamber],
        )

    # Institutions
    institutions_data = [
        ("inst-gov", "1001", "Gov High School", "Government", 151.20, -33.86),
        ("inst-cath", "1002", "Catholic College", "Catholic", 144.96, -37.81),
        ("inst-ind", "1003", "Grammar School", "Independent", 153.02, -27.47),
    ]
    for iid, aid, sname, sec, lon, lat in institutions_data:
        conn.execute(
            """
            INSERT INTO institutions (institution_id, school_name, sector, longitude, latitude)
            VALUES (?, ?, ?, ?, ?)
            """,
            [iid, sname, sec, lon, lat],
        )

    # Member Education
    # mem-1 -> inst-gov
    # mem-2 -> inst-cath
    # mem-3 -> inst-gov AND inst-ind
    edu_data = [
        ("edu-1", "mem-1", "inst-gov", "secondary", "graduated", "verified"),
        ("edu-2", "mem-2", "inst-cath", "secondary", "graduated", "verified"),
        ("edu-3", "mem-3", "inst-gov", "secondary", "graduated", "verified"),
        ("edu-4", "mem-3", "inst-ind", "secondary", "graduated", "verified"),
    ]
    for eid, mid, iid, lvl, stat, conf in edu_data:
        conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level,
                attended_status, source_url, retrieved_at, confidence
            ) VALUES (?, ?, ?, ?, ?, 'https://example.com', '2025-01-01 00:00:00+00', ?)
            """,
            [eid, mid, iid, lvl, stat, conf],
        )

    # School Snapshots
    for iid in ("inst-gov", "inst-cath", "inst-ind"):
        conn.execute(
            """
            INSERT INTO school_snapshots (institution_id, snapshot_year, total_enrolments, icsea)
            VALUES (?, 2024, 1000, 1100)
            """,
            [iid],
        )

    # School Finances 2021
    # inst-gov has 15000 gross, 14000 net
    # inst-cath has 18000 gross, 17000 net
    # inst-ind has NULL finance to test omission from N
    conn.execute(
        """
        INSERT INTO school_finances_2021 (
            institution_id, acara_id, total_gross_income_per_student, total_net_recurrent_income_per_student, reporting_year
        ) VALUES
        ('inst-gov', '1001', 15000, 14000, 2021),
        ('inst-cath', '1002', 18000, 17000, 2021)
        """
    )

    # Electoral boundaries (mock)
    conn.execute(
        """
        INSERT INTO electoral_boundaries (
            boundary_id, election_year, electorate, state_or_territory,
            geometry, source_url, source_dataset, retrieved_at
        ) VALUES (
            'bnd-2025-test', 2025, 'Test Electorate', 'NSW',
            ST_GeomFromText('POLYGON((150 -33, 151 -33, 151 -34, 150 -34, 150 -33))'),
            'https://www.aec.gov.au', 'AUS-March-2025-esri.zip', '2026-03-26 00:00:00+00'
        )
        """
    )

    # Education sector benchmarks (mock)
    conn.execute(
        """
        INSERT INTO education_sector_benchmarks (
            benchmark_year, sector, student_enrolment_share,
            student_enrolments, total_student_enrolments,
            source_title, source_url, released_at
        ) VALUES
        (2025, 'Government', 0.628, 2613404, 4160918, 'Schools, 2025', 'https://www.abs.gov.au', '2026-03-05 00:00:00+00'),
        (2025, 'Catholic', 0.200, 831692, 4160918, 'Schools, 2025', 'https://www.abs.gov.au', '2026-03-05 00:00:00+00'),
        (2025, 'Independent', 0.172, 715822, 4160918, 'Schools, 2025', 'https://www.abs.gov.au', '2026-03-05 00:00:00+00')
        """
    )

    return db_file, conn


def test_cli_help_displays_subcommands() -> None:
    """Ensure top-level CLI help lists all required issue subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("transform", "validate", "analyze", "export", "run-all", "ingest"):
        assert cmd in result.output


def test_compute_age_at_date_determinism() -> None:
    """Verify age calculations are exact and evaluated against fixed benchmark dates."""
    # Person born 1980-07-27, tested against 2022-07-26 (day before 42nd birthday) -> 41
    assert compute_age_at_date("1980-07-27", "2022-07-26") == 41
    # Person born 1980-07-26, tested against 2022-07-26 (exact birthday) -> 42
    assert compute_age_at_date("1980-07-26", "2022-07-26") == 42
    # Invalid or None inputs
    assert compute_age_at_date(None, "2022-07-26") is None
    assert compute_age_at_date("not-a-date", "2022-07-26") is None

    # Age brackets
    assert get_age_bracket(25) == "Under 30"
    assert get_age_bracket(35) == "30-39"
    assert get_age_bracket(45) == "40-49"
    assert get_age_bracket(55) == "50-59"
    assert get_age_bracket(65) == "60-69"
    assert get_age_bracket(75) == "70+"
    assert get_age_bracket(None) == "Unknown"


def test_sector_summary_distinguishes_mps_and_instances(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify sector breakdown classifies multi-school MPs into Combined/Multiple."""
    _path, conn = populated_db
    summary = compute_sector_summary(conn, 47)

    # 3 MPs have schools recorded: mem-1 (Gov), mem-2 (Cath), mem-3 (Gov + Ind -> Combined/Multiple)
    unique = summary["unique_parliamentarians_by_sector"]
    assert unique["Government"] == 1
    assert unique["Catholic"] == 1
    assert unique["Independent"] == 0
    assert unique["Combined/Multiple"] == 1
    assert summary["parliamentarians_with_known_schools"] == 3

    # Percentages of known parliamentarians must sum to ~100%
    pcts = summary["percentage_of_known_parliamentarians"]
    assert pytest.approx(sum(pcts.values()), abs=0.5) == 100.0

    # Attendance instances: 2 Government (mem-1, mem-3), 1 Catholic (mem-2), 1 Independent (mem-3)
    insts = summary["attendance_instances_by_sector"]
    assert insts["Government"] == 2
    assert insts["Catholic"] == 1
    assert insts["Independent"] == 1
    assert summary["total_attendance_instances"] == 4


def test_funding_summary_reports_sample_size_n(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify funding statistics report explicit sample size N and omit NULLs."""
    _path, conn = populated_db
    funding = compute_funding_summary(conn, 47)

    sec_gov = funding["by_sector"]["Government"]
    assert sec_gov["gross_income_sample_size"] == 1  # inst-gov is one unique school
    assert sec_gov["gross_income_per_student_avg"] == 15000
    assert sec_gov["net_recurrent_income_per_student_avg"] == 14000

    sec_ind = funding["by_sector"]["Independent"]
    assert sec_ind["gross_income_sample_size"] == 0
    assert sec_ind["gross_income_per_student_avg"] is None
    assert funding["total_schools_with_finance_data"] == 2
    assert funding["overall_gross_income_sample_size"] == 2
    assert funding["overall_net_recurrent_income_sample_size"] == 2


def test_funding_summary_does_not_overweight_shared_schools(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify school finance averages use each qualifying school once."""
    _path, conn = populated_db
    conn.execute(
        """
        INSERT INTO institutions (institution_id, school_name, sector)
        VALUES ('inst-gov-2', 'Second Government School', 'Government')
        """
    )
    conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status,
            source_url, retrieved_at, confidence
        ) VALUES (
            'edu-5', 'mem-1', 'inst-gov-2', 'secondary', 'graduated',
            'https://example.com', '2025-01-01 00:00:00+00', 'verified'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO school_finances_2021 (
            institution_id, acara_id, total_gross_income_per_student,
            total_net_recurrent_income_per_student, reporting_year
        ) VALUES ('inst-gov-2', '1004', 21000, 20000, 2021)
        """
    )

    funding = compute_funding_summary(conn, 47)
    sec_gov = funding["by_sector"]["Government"]
    assert sec_gov["gross_income_sample_size"] == 2
    assert sec_gov["gross_income_per_student_avg"] == 18000
    assert sec_gov["net_recurrent_income_sample_size"] == 2
    assert sec_gov["net_recurrent_income_per_student_avg"] == 17000


def test_opening_day_demographics_use_one_opening_service_per_person(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Exclude later entrants and deduplicate duplicate opening service rows."""
    _path, conn = populated_db
    conn.execute(
        """
        INSERT INTO members (
            member_id, family_name, given_name, display_name, gender, date_of_birth,
            aph_id
        ) VALUES (
            'mem-late', 'Late', 'Joiner', 'Joiner Late', 'Female',
            '1988-01-01', 'APH-LATE'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party, party_abbrev,
            state_or_territory, service_start, is_opening_day_member, is_current_member
        ) VALUES (
            'srv-late', 'mem-late', 47, 'representatives', 'Labor', 'ALP', 'NSW',
            '2023-01-01', FALSE, TRUE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party, party_abbrev,
            state_or_territory, service_start, is_opening_day_member, is_current_member
        ) VALUES (
            'srv-mem-1-later', 'mem-1', 47, 'senate', 'Greens', 'GRN', 'VIC',
            '2023-01-01', TRUE, FALSE
        )
        """
    )

    demographics = compute_parliament_demographics(conn, 47)
    assert demographics["total_parliamentarians"] == 227
    assert demographics["opening_day_parliamentarians"] == 227
    assert demographics["opening_day_current_parliamentarians"] == 227
    assert demographics["parties"] == {"ALP": 227}


def test_unsupported_parliament_is_rejected_by_analysis() -> None:
    """Do not manufacture a benchmark date for an unsupported parliament."""
    conn = get_connection()
    init_schema(conn)
    with pytest.raises(ValueError, match="Unsupported parliament number 999"):
        compute_parliament_demographics(conn, 999)
    conn.close()


def test_validate_database_success(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify validation passes on clean canonical database."""
    _path, conn = populated_db
    report = validate_database(conn, [47])
    assert report.passed is True
    assert len(report.failures) == 0
    assert report.checks_passed == report.checks_run


def test_validate_database_detects_empty_table(tmp_path: Path) -> None:
    """Verify validation fails when canonical tables are empty."""
    db_file = tmp_path / "empty.duckdb"
    conn = get_connection(db_file)
    init_schema(conn)

    report = validate_database(conn, [47])
    assert report.passed is False
    assert any("is empty (0 rows)" in f for f in report.failures)
    conn.close()


def test_validate_database_detects_foreign_key_orphan(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify validation detects orphan foreign keys when unconstrained tables have dangling IDs."""
    _path, conn = populated_db

    # Recreate member_education without FK to test orphan detection
    conn.execute("DROP VIEW IF EXISTS v_member_secondary_education")
    conn.execute("DROP VIEW IF EXISTS member_secondary_school_education_46")
    conn.execute("DROP VIEW IF EXISTS member_secondary_school_education_47")
    conn.execute("DROP VIEW IF EXISTS member_secondary_school_education_48")
    conn.execute("DROP VIEW IF EXISTS v_coverage_metrics")
    conn.execute("DROP TABLE member_education")
    conn.execute(
        """
        CREATE TABLE member_education (
            education_id VARCHAR PRIMARY KEY,
            member_id VARCHAR NOT NULL,
            institution_id VARCHAR NOT NULL,
            level VARCHAR NOT NULL,
            years_attended VARCHAR,
            graduation_year INTEGER,
            attended_status VARCHAR NOT NULL,
            source_url VARCHAR NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL,
            confidence VARCHAR NOT NULL,
            reviewer_notes VARCHAR
        )
        """
    )
    conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level,
            attended_status, source_url, retrieved_at, confidence
        ) VALUES (
            'orphan-edu', 'mem-nonexistent', 'inst-gov', 'secondary',
            'graduated', 'https://example.com', '2025-01-01 00:00:00+00', 'verified'
        )
        """
    )
    # Re-apply views
    schema_dir = Path(__file__).resolve().parent.parent / "apemap" / "schema"
    conn.execute((schema_dir / "02_views.sql").read_text(encoding="utf-8"))

    report = validate_database(conn, [47])
    assert report.passed is False
    assert any("member_education -> members" in f for f in report.failures)


def test_validate_database_detects_invalid_opening_chamber_benchmark(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify validation fails when Parliament 47 opening-day chamber benchmark is not 151+76."""
    _path, conn = populated_db
    conn.execute(
        """
        UPDATE parliament_service
        SET chamber = 'representatives'
        WHERE service_id = 'srv-mem-152'
        """
    )
    report = validate_database(conn, [47])
    assert report.passed is False
    assert any(
        "Parliament 47 opening-day chamber benchmark failed" in f
        for f in report.failures
    )


def test_validate_database_detects_service_overlap_failure(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Verify validation detects service records that do not overlap the declared parliament term."""
    _path, conn = populated_db
    conn.execute(
        """
        UPDATE parliament_service
        SET service_start = '2019-07-02', service_end = '2022-04-11'
        WHERE service_id = 'srv-mem-1'
        """
    )
    report = validate_database(conn, [47])
    assert report.passed is False
    assert any("service records that do not overlap" in f for f in report.failures)


def test_cli_transform(tmp_path: Path) -> None:
    """Test apemap transform CLI command initializes schema."""
    db_file = tmp_path / "transform.duckdb"
    nonexistent_gpkg = tmp_path / "nonexistent.gpkg"
    result = runner.invoke(
        app,
        ["transform", "--db-path", str(db_file), "--gpkg-path", str(nonexistent_gpkg)],
    )
    assert result.exit_code == 0
    assert "Transform step complete!" in result.output

    conn = get_connection(db_file)
    for tbl in CANONICAL_TABLES:
        res = conn.execute(f"SELECT count(*) FROM {tbl}").fetchone()
        assert res is not None and res[0] == 0
    conn.close()


def test_cli_validate_command(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
) -> None:
    """Test apemap validate command prints Rich tables and exits cleanly."""
    db_file, _conn = populated_db
    result = runner.invoke(app, ["validate", "--db-path", str(db_file), "-p", "47"])
    assert result.exit_code == 0
    assert "ALL" in result.output
    assert "VALIDATION CHECKS PASSED" in result.output


def test_cli_analyze_command(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Test apemap analyze outputs tables and JSON metrics."""
    db_file, _conn = populated_db
    out_dir = tmp_path / "analysis_out"
    result = runner.invoke(
        app,
        [
            "analyze",
            "--db-path",
            str(db_file),
            "-p",
            "47",
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0
    json_path = out_dir / "analysis_metrics.json"
    assert json_path.exists()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "47" in data["parliaments"]
    p47 = data["parliaments"]["47"]
    assert p47["demographics"]["reference_opening_date"] == "2022-07-26"
    assert p47["sectors"]["unique_parliamentarians_by_sector"]["Combined/Multiple"] == 1


def test_cli_export_command_and_reproducibility(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Test the complete canonical export set is reproducible byte-for-byte."""
    db_file, _conn = populated_db
    out_dir_1 = tmp_path / "export_1"
    out_dir_2 = tmp_path / "export_2"

    res1 = runner.invoke(
        app,
        [
            "export",
            "--db-path",
            str(db_file),
            "-p",
            "47",
            "--output-dir",
            str(out_dir_1),
        ],
    )
    assert res1.exit_code == 0
    geojson_1 = out_dir_1 / "parliament_47_combined.geojson"
    assert geojson_1.exists()

    res2 = runner.invoke(
        app,
        [
            "export",
            "--db-path",
            str(db_file),
            "-p",
            "47",
            "--output-dir",
            str(out_dir_2),
        ],
    )
    assert res2.exit_code == 0
    assert (out_dir_2 / "parliament_47_combined.geojson").exists()

    # GeoJSON structure check
    geo_data = json.loads(geojson_1.read_text(encoding="utf-8"))
    assert geo_data["type"] == "FeatureCollection"
    assert len(geo_data["features"]) > 0
    first_feat = geo_data["features"][0]
    assert first_feat["geometry"]["type"] == "Point"
    assert len(first_feat["geometry"]["coordinates"]) == 2

    manifest_1 = artifact_manifest(out_dir_1)
    manifest_2 = artifact_manifest(out_dir_2)
    expected_artifacts = {
        *(f"{table}.parquet" for table in CANONICAL_TABLES),
        "analysis_metrics.json",
        str(Path("analysis") / "metadata.json"),
        str(Path("analysis") / "demographics.json"),
        str(Path("analysis") / "education_sectors.json"),
        str(Path("analysis") / "school_finance.json"),
        str(Path("analysis") / "parliament_comparison.json"),
        "parliament_47_combined.geojson",
    }
    assert set(manifest_1) == expected_artifacts
    assert manifest_1 == manifest_2
    analysis_data = json.loads(
        (out_dir_1 / "analysis_metrics.json").read_text(encoding="utf-8")
    )
    assert "generated_at" not in analysis_data
    assert (out_dir_1 / "analysis_metrics.json").read_bytes() == (
        out_dir_2 / "analysis_metrics.json"
    ).read_bytes()


@pytest.mark.parametrize("command", ["analyze", "export", "validate", "run-all"])
def test_cli_rejects_unsupported_parliament(command: str, tmp_path: Path) -> None:
    """All parliament-specific CLI commands reject unknown benchmark metadata."""
    result = runner.invoke(
        app,
        [command, "--db-path", str(tmp_path / f"{command}.duckdb"), "-p", "999"],
    )
    assert result.exit_code != 0
    assert "Unsupported parliament number" in result.output


def test_cli_run_all_mocked(
    populated_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Test apemap run-all pipeline orchestration with mocked ingestion steps."""
    db_file, conn = populated_db
    out_dir = tmp_path / "run_all_out"

    with (
        patch("apemap.cli.run_acara_ingestion") as mock_acara,
        patch("apemap.cli.run_aph_ingestion") as mock_aph,
    ):
        mock_acara.return_value = {}
        mock_aph.return_value = {}

        result = runner.invoke(
            app,
            [
                "run-all",
                "--db-path",
                str(db_file),
                "-p",
                "47",
                "--output-dir",
                str(out_dir),
                "--no-download",
                "--no-refresh",
            ],
        )

        assert result.exit_code == 0
        assert "APEMAP Pipeline Completed Successfully!" in result.output
        mock_acara.assert_called_once()
        mock_aph.assert_called_once()
        assert (out_dir / "parliament_47_combined.geojson").exists()
        assert (out_dir / "members.parquet").exists()

        first_manifest = artifact_manifest(out_dir)
        first_counts = {
            table: table_row_count(conn, table) for table in CANONICAL_TABLES
        }

        result = runner.invoke(
            app,
            [
                "run-all",
                "--db-path",
                str(db_file),
                "-p",
                "47",
                "--output-dir",
                str(out_dir),
                "--no-download",
                "--no-refresh",
            ],
        )

        assert result.exit_code == 0
        assert mock_acara.call_count == 2
        assert mock_aph.call_count == 2
        second_counts = {
            table: table_row_count(conn, table) for table in CANONICAL_TABLES
        }
        assert second_counts == first_counts
        assert artifact_manifest(out_dir) == first_manifest
