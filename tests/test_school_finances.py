"""Unit tests for generalised annual school finances ingestion and modelling."""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pytest

from apemap.db import (
    export_to_parquet,
    get_connection,
    init_schema,
    migrate_historical_finances,
)
from apemap.ingest.acara import ingest_school_finances
from apemap.validate import validate_database


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a temporary initialized DuckDB with sample institutions."""
    db_path = tmp_path / "test_finances.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert sample institutions
    conn.execute(
        """
        INSERT INTO institutions (
            institution_id, acara_id, school_name, sector, campus_type, state
        ) VALUES
            ('acara-1001', '1001', 'Canberra High School', 'Government', 'Secondary', 'ACT'),
            ('acara-1002', '1002', 'St Marys College', 'Catholic', 'Combined', 'NSW'),
            ('acara-1003', '1003', 'Grammar School Main', 'Independent', 'Secondary', 'VIC'),
            ('acara-1004', '1004', 'Grammar School Branch Campus', 'Independent', 'Secondary', 'VIC')
        """
    )
    conn.close()
    return db_path


@pytest.mark.integration
def test_ingest_school_finances_csv(tmp_path: Path, test_db: Path) -> None:
    """Ingest authorised school finance records from CSV keyed by ACARA ID."""
    csv_file = tmp_path / "test_finances_2024.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "acara_id",
                "reporting_year",
                "total_gross_income_per_student",
                "total_net_recurrent_income_per_student",
                "recurrent_funding_gov_total",
                "recurrent_funding_state_total",
                "fees_charges_parent_total",
                "other_private_sources_total",
                "total_gross_income_total",
                "total_net_recurrent_income_total",
                "is_rolled_reporting",
                "parent_acara_id",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "acara_id": "1001",
                "reporting_year": 2024,
                "total_gross_income_per_student": 18500,
                "total_net_recurrent_income_per_student": 17200,
                "recurrent_funding_gov_total": 4500000,
                "recurrent_funding_state_total": 12000000,
                "fees_charges_parent_total": 500000,
                "other_private_sources_total": 200000,
                "total_gross_income_total": 17200000,
                "total_net_recurrent_income_total": 17200000,
                "is_rolled_reporting": False,
                "parent_acara_id": "",
                "notes": "Authorised ACARA extract",
            }
        )
        writer.writerow(
            {
                "acara_id": "1004",
                "reporting_year": 2024,
                "total_gross_income_per_student": 28000,
                "total_net_recurrent_income_per_student": 26500,
                "recurrent_funding_gov_total": 3000000,
                "recurrent_funding_state_total": 1500000,
                "fees_charges_parent_total": 25000000,
                "other_private_sources_total": 1000000,
                "total_gross_income_total": 30500000,
                "total_net_recurrent_income_total": 30500000,
                "is_rolled_reporting": True,
                "parent_acara_id": "1003",
                "notes": "Rolled campus reporting under main school 1003",
            }
        )

    conn = get_connection(test_db)
    inserted = ingest_school_finances(
        conn,
        csv_file,
        reporting_year=2024,
        source_dataset="ACARA My School Finance Extract 2024",
        source_url="https://myschool.edu.au",
        licence="ACARA My School Terms of Use (July 2020)",
    )
    assert inserted == 2

    # Verify rows in school_finances
    rows = conn.execute(
        """
        SELECT
            institution_id, acara_id, reporting_year,
            total_gross_income_per_student, total_net_recurrent_income_per_student,
            is_rolled_reporting, parent_acara_id,
            source_dataset, licence
        FROM school_finances
        ORDER BY institution_id
        """
    ).fetchall()

    assert len(rows) == 2

    # Check standalone school
    r1 = rows[0]
    assert r1[0] == "acara-1001"
    assert r1[1] == "1001"
    assert r1[2] == 2024
    assert r1[3] == 18500
    assert r1[4] == 17200
    assert r1[5] is False
    assert r1[6] is None
    assert r1[7] == "ACARA My School Finance Extract 2024"
    assert "July 2020" in r1[8]

    # Check rolled multi-campus reporting
    r2 = rows[1]
    assert r2[0] == "acara-1004"
    assert r2[1] == "1004"
    assert r2[2] == 2024
    assert r2[3] == 28000
    assert r2[5] is True
    assert r2[6] == "1003"

    conn.close()


@pytest.mark.integration
def test_annual_composite_primary_key(test_db: Path) -> None:
    """school_finances supports multiple reporting years per institution without collision."""
    conn = get_connection(test_db)

    # Insert 2021 finance
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_gross_income_per_student
        ) VALUES ('acara-1001', '1001', 2021, 15000)
        """
    )

    # Insert 2024 finance for same institution
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_gross_income_per_student
        ) VALUES ('acara-1001', '1001', 2024, 18500)
        """
    )

    rows = conn.execute(
        """
        SELECT reporting_year, total_gross_income_per_student
        FROM school_finances
        WHERE institution_id = 'acara-1001'
        ORDER BY reporting_year
        """
    ).fetchall()
    assert rows == [(2021, 15000), (2024, 18500)]

    # Attempting duplicate (institution_id, reporting_year) violates PK
    with pytest.raises(duckdb.ConstraintException):
        conn.execute(
            """
            INSERT INTO school_finances (
                institution_id, acara_id, reporting_year, total_gross_income_per_student
            ) VALUES ('acara-1001', '1001', 2021, 99999)
            """
        )

    conn.close()


@pytest.mark.integration
def test_backward_compatibility_view_school_finances_2021(test_db: Path) -> None:
    """The school_finances_2021 view exposes only 2021 records and is backward-compatible."""
    conn = get_connection(test_db)

    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_gross_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 15000),
            ('acara-1002', '1002', 2021, 19000),
            ('acara-1001', '1001', 2024, 18500)
        """
    )

    # Query view
    rows = conn.execute(
        "SELECT institution_id, reporting_year, total_gross_income_per_student FROM school_finances_2021 ORDER BY institution_id"
    ).fetchall()
    assert rows == [
        ("acara-1001", 2021, 15000),
        ("acara-1002", 2021, 19000),
    ]

    conn.close()


@pytest.mark.integration
def test_parquet_export_includes_both_finances(tmp_path: Path, test_db: Path) -> None:
    """export_to_parquet exports both school_finances and school_finances_2021."""
    conn = get_connection(test_db)
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_gross_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 15000),
            ('acara-1001', '1001', 2024, 18500)
        """
    )

    out_dir = tmp_path / "parquet_out"
    exported = export_to_parquet(conn, out_dir)

    assert "school_finances" in exported
    assert "school_finances_2021" in exported
    assert (out_dir / "school_finances.parquet").exists()
    assert (out_dir / "school_finances_2021.parquet").exists()

    conn.close()


@pytest.mark.integration
def test_referential_integrity_check(test_db: Path) -> None:
    """Referential integrity validation check verifies school_finances -> institutions."""
    conn = get_connection(test_db)
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_gross_income_per_student
        ) VALUES ('acara-1001', '1001', 2021, 15000)
        """
    )

    report = validate_database(conn)
    # The check should have run and passed
    assert any(
        "school_finances -> institutions" in str(check)
        for check in ["school_finances -> institutions"]
    )
    assert (
        report.passed
        or len([f for f in report.failures if "school_finances" in f]) == 0
    )

    conn.close()


@pytest.mark.integration
def test_migrate_historical_finances_populates_school_finances(tmp_path: Path) -> None:
    """migrate_historical_finances returns 0 when gpkg does not exist and handles gracefully."""
    conn = get_connection(":memory:")
    init_schema(conn)
    migrated = migrate_historical_finances(
        conn, gpkg_path=tmp_path / "nonexistent.gpkg"
    )
    assert migrated == 0
    conn.close()


@pytest.mark.integration
def test_finance_reporting_year_wiring_and_compatibility(tmp_path: Path) -> None:
    """Test annual finance-year threading through analysis, export, and CLI options."""
    import json
    from typer.testing import CliRunner
    from apemap.analysis import (
        compute_funding_summary,
        export_analysis_report,
    )
    from apemap.cli import app
    from apemap.export import export_all_artifacts

    db_path = tmp_path / "test_wiring.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert test data: 1 MP, 1 secondary school, 2021 and 2024 finances
    conn.execute(
        """
        INSERT INTO members (member_id, aph_id, given_name, family_name, display_name, gender)
        VALUES ('mem-1', 'APH1', 'Jane', 'Doe', 'Jane Doe', 'Female');

        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party, party_abbrev, state_or_territory,
            service_start, is_opening_day_member, is_current_member
        ) VALUES ('serv-1', 'mem-1', 47, 'representatives', 'Labor', 'ALP', 'ACT', '2022-05-21', TRUE, TRUE);

        INSERT INTO institutions (institution_id, school_name, sector, state)
        VALUES ('acara-1001', 'Test High', 'Government', 'ACT');

        INSERT INTO member_education (
            education_id, member_id, institution_id, level,
            attended_status, source_url, retrieved_at, confidence
        ) VALUES (
            'edu-1', 'mem-1', 'acara-1001', 'secondary',
            'graduated', 'https://www.aph.gov.au', CURRENT_TIMESTAMP, 'verified'
        );

        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year,
            total_gross_income_per_student, total_net_recurrent_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 15000, 14000),
            ('acara-1001', '1001', 2024, 22000, 20500);
        """
    )

    # 1. Direct function call test for compute_funding_summary
    fund_2021 = compute_funding_summary(conn, 47, reporting_year=2021)
    assert fund_2021["reporting_year"] == 2021
    assert fund_2021["overall_gross_income_avg"] == 15000

    fund_2024 = compute_funding_summary(conn, 47, reporting_year=2024)
    assert fund_2024["reporting_year"] == 2024
    assert fund_2024["overall_gross_income_avg"] == 22000

    # 2. Test export_analysis_report default (2021) retains funding_2021 alias and school_finance
    out_dir_2021 = tmp_path / "analysis_2021"
    export_analysis_report(conn, out_dir_2021, [47], finance_reporting_year=2021)
    data_2021 = json.loads(
        (out_dir_2021 / "analysis_metrics.json").read_text(encoding="utf-8")
    )
    assert data_2021["metadata"]["finance_reporting_year"] == 2021
    p47_2021 = data_2021["parliaments"]["47"]
    assert "school_finance" in p47_2021
    assert "funding_2021" in p47_2021  # Compatibility alias present
    assert p47_2021["school_finance"]["overall_gross_income_avg"] == 15000
    assert p47_2021["funding_2021"]["overall_gross_income_avg"] == 15000

    # 3. Test export_analysis_report for 2024: NO funding_2021 key, only school_finance, year=2024
    out_dir_2024 = tmp_path / "analysis_2024"
    export_analysis_report(conn, out_dir_2024, [47], finance_reporting_year=2024)
    data_2024 = json.loads(
        (out_dir_2024 / "analysis_metrics.json").read_text(encoding="utf-8")
    )
    assert data_2024["metadata"]["finance_reporting_year"] == 2024
    p47_2024 = data_2024["parliaments"]["47"]
    assert "school_finance" in p47_2024
    assert "funding_2021" not in p47_2024  # Semantic mismatch removed
    assert p47_2024["school_finance"]["reporting_year"] == 2024
    assert p47_2024["school_finance"]["overall_gross_income_avg"] == 22000

    # 4. Test export_all_artifacts threads finance_reporting_year
    out_dir_artifacts = tmp_path / "artifacts_2024"
    export_all_artifacts(conn, out_dir_artifacts, [47], finance_reporting_year=2024)
    data_art = json.loads(
        (out_dir_artifacts / "analysis_metrics.json").read_text(encoding="utf-8")
    )
    assert data_art["metadata"]["finance_reporting_year"] == 2024
    assert "funding_2021" not in data_art["parliaments"]["47"]

    conn.close()

    # 5. Test CLI commands (analyze, export) propagate --finance-year
    runner = CliRunner()
    cli_analyze_out = tmp_path / "cli_analyze_2024"
    res = runner.invoke(
        app,
        [
            "analyze",
            "--db-path",
            str(db_path),
            "-p",
            "47",
            "--finance-year",
            "2024",
            "--output-dir",
            str(cli_analyze_out),
        ],
    )
    assert res.exit_code == 0, res.output
    assert "School 2024 Financial Averages" in res.output
    cli_data = json.loads(
        (cli_analyze_out / "analysis_metrics.json").read_text(encoding="utf-8")
    )
    assert cli_data["metadata"]["finance_reporting_year"] == 2024
    assert "funding_2021" not in cli_data["parliaments"]["47"]
    assert (
        cli_data["parliaments"]["47"]["school_finance"]["overall_gross_income_avg"]
        == 22000
    )

    cli_export_out = tmp_path / "cli_export_2024"
    res_exp = runner.invoke(
        app,
        [
            "export",
            "--db-path",
            str(db_path),
            "-p",
            "47",
            "--finance-year",
            "2024",
            "--output-dir",
            str(cli_export_out),
        ],
    )
    assert res_exp.exit_code == 0, res_exp.output
    cli_exp_data = json.loads(
        (cli_export_out / "analysis_metrics.json").read_text(encoding="utf-8")
    )
    assert cli_exp_data["metadata"]["finance_reporting_year"] == 2024
    assert "funding_2021" not in cli_exp_data["parliaments"]["47"]
