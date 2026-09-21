"""Unit tests for ACARA dataset ingestion, DataFrame builders, DuckDB sync, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection, init_schema
from apemap.ingest.acara import (
    convert_xlsx_to_csv,
    load_institutions_dataframe,
    load_snapshots_dataframe,
    run_acara_ingestion,
)

runner = CliRunner()


@pytest.fixture
def mock_external_dir(tmp_path: Path) -> Path:
    """Create a temporary directory containing mock ACARA JSON and CSV files."""
    ext_dir = tmp_path / "external"
    ext_dir.mkdir(parents=True, exist_ok=True)

    # 1. Mock acara_school_results.json
    schools_json = [
        {
            "ACARAId": 40001,
            "SchoolName": "Test Government High School",
            "SchoolType": "Secondary",
            "SchoolSector": "Gov",
            "IndependentSchool": "N",
            "AddressList": [
                {
                    "City": "Melbourne",
                    "StateProvince": "VIC",
                    "PostalCode": "3000",
                    "GridLocation": {"Latitude": -37.8136, "Longitude": 144.9631},
                }
            ],
        },
        {
            "ACARAId": 40002,
            "SchoolName": "Test Catholic Grammar",
            "SchoolType": "Combined",
            "SchoolSector": "NG",
            "IndependentSchool": "N",
            "AddressList": [
                {
                    "City": "Sydney",
                    "StateProvince": "NSW",
                    "PostalCode": "2000",
                    "GridLocation": {"Latitude": -33.8688, "Longitude": 151.2093},
                }
            ],
        },
        {
            "ACARAId": 40003,
            "SchoolName": "Test Independent Academy",
            "SchoolType": "Combined",
            "SchoolSector": "NG",
            "IndependentSchool": "Y",
            "AddressList": [
                {
                    "City": "Brisbane",
                    "StateProvince": "QLD",
                    "PostalCode": "4000",
                    "GridLocation": {"Latitude": -27.4698, "Longitude": 153.0251},
                }
            ],
        },
    ]
    with open(ext_dir / "acara_school_results.json", "w", encoding="utf-8") as f:
        json.dump(schools_json, f)

    # 2. Mock school-location-2025.csv to test enrichment
    loc_df = pd.DataFrame(
        [
            {
                "ACARA SML ID": "40001",
                "School Name": "Test Government High School",
                "School Sector": "Government",
                "School Type": "Secondary",
                "Campus Type": "School Single Campus",
                "State": "VIC",
                "Suburb": "Melbourne",
                "Postcode": "3000",
                "Latitude": "-37.8136",
                "Longitude": "144.9631",
            },
            {
                "ACARA SML ID": "40004",
                "School Name": "Only In Location CSV School",
                "School Sector": "Government",
                "School Type": "Primary",
                "Campus Type": "School Single Campus",
                "State": "WA",
                "Suburb": "Perth",
                "Postcode": "6000",
                "Latitude": "-31.9505",
                "Longitude": "115.8605",
            },
        ]
    )
    loc_df.to_csv(ext_dir / "school-location-2025.csv", index=False)

    # 3. Mock school-profile-2008-2025.csv
    prof_df = pd.DataFrame(
        [
            {
                "Calendar Year": "2025",
                "ACARA SML ID": "40001",
                "School Name": "Test Government High School",
                "State": "VIC",
                "Sector": "Government",
                "School Type": "Secondary",
                "Total Enrolments": "1250",
                "ICSEA": "1080",
            },
            {
                "Calendar Year": "2025",
                "ACARA SML ID": "40002",
                "School Name": "Test Catholic Grammar",
                "State": "NSW",
                "Sector": "Catholic",
                "School Type": "Combined",
                "Total Enrolments": "850",
                "ICSEA": "1120",
            },
        ]
    )
    prof_df.to_csv(ext_dir / "school-profile-2008-2025.csv", index=False)

    return ext_dir


def test_convert_xlsx_to_csv(tmp_path: Path) -> None:
    """Verify XLSX to CSV conversion extracts correct data rows and headers."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SchoolLocations 2025"
    ws.append(["ACARA SML ID", "School Name", "State"])
    ws.append([40001, "Test High", "VIC"])
    ws.append([40002, "Test College", "NSW"])

    xlsx_file = tmp_path / "test.xlsx"
    csv_file = tmp_path / "test.csv"
    wb.save(str(xlsx_file))

    out_path = convert_xlsx_to_csv(
        xlsx_file, csv_file, sheet_name="SchoolLocations 2025"
    )
    assert out_path.exists()

    df = pd.read_csv(out_path)
    assert len(df) == 2
    assert list(df.columns) == ["ACARA SML ID", "School Name", "State"]
    assert df.iloc[0]["School Name"] == "Test High"


def test_load_institutions_dataframe(mock_external_dir: Path) -> None:
    """Verify institutions dataframe correctly maps sectors, parses IDs, and enriches coordinates."""
    df = load_institutions_dataframe(external_dir=mock_external_dir)

    assert len(df) == 4  # 3 from JSON + 1 new from location CSV
    assert set(df.columns) == {
        "institution_id",
        "acara_id",
        "school_name",
        "school_type",
        "sector",
        "campus_type",
        "state",
        "suburb",
        "postcode",
        "longitude",
        "latitude",
    }

    # Verify sector mappings
    gov = df[df["acara_id"] == "40001"].iloc[0]
    assert gov["sector"] == "Government"
    assert gov["institution_id"] == "acara-40001"

    cath = df[df["acara_id"] == "40002"].iloc[0]
    assert cath["sector"] == "Catholic"

    ind = df[df["acara_id"] == "40003"].iloc[0]
    assert ind["sector"] == "Independent"

    only_loc = df[df["acara_id"] == "40004"].iloc[0]
    assert only_loc["school_name"] == "Only In Location CSV School"
    assert only_loc["state"] == "WA"


def test_load_snapshots_dataframe(mock_external_dir: Path) -> None:
    """Verify snapshots dataframe parses year, enrolments, ICSEA, and institution_id."""
    df = load_snapshots_dataframe(external_dir=mock_external_dir)

    assert len(df) == 2
    assert set(df.columns) == {
        "institution_id",
        "snapshot_year",
        "total_enrolments",
        "icsea",
        "financial_profile_2021",
    }

    row1 = df[df["institution_id"] == "acara-40001"].iloc[0]
    assert row1["snapshot_year"] == 2025
    assert row1["total_enrolments"] == 1250
    assert row1["icsea"] == 1080


def test_run_acara_ingestion_offline(mock_external_dir: Path, tmp_path: Path) -> None:
    """Verify complete ingestion pipeline synchronizes institutions and snapshots into DuckDB."""
    db_file = tmp_path / "test.duckdb"
    out_dir = tmp_path / "processed"

    summary = run_acara_ingestion(
        download_latest=False,
        use_longitudinal=True,
        db_path=db_file,
        export_parquet_files=True,
        output_dir=out_dir,
        external_dir=mock_external_dir,
    )

    assert summary["institutions_loaded"] >= 4
    assert summary["school_snapshots_loaded"] == 2
    assert summary["parquet_exported"] is True

    # Check Parquet files
    inst_parquet = out_dir / "institutions.parquet"
    snap_parquet = out_dir / "school_snapshots.parquet"
    assert inst_parquet.exists()
    assert snap_parquet.exists()

    # Query DuckDB
    conn = get_connection(db_file)
    init_schema(conn)
    rows = conn.execute(
        "SELECT school_name, sector FROM institutions WHERE acara_id = '40001'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("Test Government High School", "Government")


def test_cli_ingest_acara_help() -> None:
    """Verify apemap ingest acara command is exposed with appropriate help documentation."""
    result = runner.invoke(app, ["ingest", "acara", "--help"])
    assert result.exit_code == 0
    assert "acara [OPTIONS]" in result.output
    assert "--download" in result.output
    assert "--longitudinal" in result.output


def test_cli_ingest_acara_no_download(mock_external_dir: Path, tmp_path: Path) -> None:
    """Verify apemap ingest acara --no-download executes via Typer CLI."""
    db_file = tmp_path / "cli_test.duckdb"

    from unittest.mock import patch

    with patch("apemap.cli.run_acara_ingestion") as mock_run:
        mock_run.return_value = {
            "institutions_loaded": 4,
            "school_snapshots_loaded": 2,
            "school_finances_2021_migrated": 0,
            "parquet_exported": True,
            "exported_files": {},
        }
        result = runner.invoke(
            app,
            [
                "ingest",
                "acara",
                "--no-download",
                "--db-path",
                str(db_file),
                "--no-export-parquet",
            ],
        )
        assert result.exit_code == 0
        assert "ACARA Ingestion & Finance Isolation Summary" in result.output
        mock_run.assert_called_once()
