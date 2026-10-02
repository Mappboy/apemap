"""Unit tests for ACARA dataset ingestion, DataFrame builders, DuckDB sync, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from rich.text import Text
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection, init_schema, temporary_dataframe_view
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
                "Girls Enrolments": "620",
                "Boys Enrolments": "630",
                "Full Time Equivalent Enrolments": "1240.5",
                "ICSEA": "1080",
                "ICSEA Percentile": "75",
                "Bottom SEA Quarter (%)": "10.0",
                "Lower Middle SEA Quarter (%)": "25.0",
                "Upper Middle SEA Quarter (%)": "35.0",
                "Top SEA Quarter (%)": "30.0",
                "Indigenous Enrolments (%)": "5.0",
                "Language Background Other Than English - Yes (%)": "15.0",
                "Year Range": "7-12",
                "Geolocation": "Major Cities",
            },
            {
                "Calendar Year": "2025",
                "ACARA SML ID": "40002",
                "School Name": "Test Catholic Grammar",
                "State": "NSW",
                "Sector": "Catholic",
                "School Type": "Combined",
                "Total Enrolments": "850",
                "Girls Enrolments": "400",
                "Boys Enrolments": "450",
                "Full Time Equivalent Enrolments": "845.0",
                "ICSEA": "1120",
                "ICSEA Percentile": "85",
                "Bottom SEA Quarter (%)": "5.0",
                "Lower Middle SEA Quarter (%)": "15.0",
                "Upper Middle SEA Quarter (%)": "40.0",
                "Top SEA Quarter (%)": "40.0",
                "Indigenous Enrolments (%)": "2.0",
                "Language Background Other Than English - Yes (%)": "20.0",
                "Year Range": "Prep-12",
                "Geolocation": "Inner Regional",
            },
        ]
    )
    prof_df.to_csv(ext_dir / "school-profile-2008-2025.csv", index=False)

    return ext_dir


@pytest.mark.unit
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


@pytest.mark.unit
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
        "country",
        "institution_status",
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


@pytest.mark.unit
def test_load_snapshots_dataframe(mock_external_dir: Path) -> None:
    """Verify snapshots dataframe parses year, enrolments, ICSEA, and socio-educational profile."""
    df = load_snapshots_dataframe(external_dir=mock_external_dir)

    assert len(df) == 2
    assert set(df.columns) == {
        "institution_id",
        "snapshot_year",
        "total_enrolments",
        "girls_enrolments",
        "boys_enrolments",
        "fte_enrolments",
        "icsea",
        "icsea_percentile",
        "sea_bottom_quarter_pct",
        "sea_lower_middle_quarter_pct",
        "sea_upper_middle_quarter_pct",
        "sea_top_quarter_pct",
        "indigenous_enrolments_pct",
        "lbote_pct",
        "year_range",
        "remoteness_category",
        "financial_profile_2021",
    }

    row1 = df[df["institution_id"] == "acara-40001"].iloc[0]
    assert row1["snapshot_year"] == 2025
    assert row1["total_enrolments"] == 1250
    assert row1["girls_enrolments"] == 620
    assert row1["boys_enrolments"] == 630
    assert row1["fte_enrolments"] == 1240.5
    assert row1["icsea"] == 1080
    assert row1["icsea_percentile"] == 75
    assert row1["sea_bottom_quarter_pct"] == 10.0
    assert row1["sea_lower_middle_quarter_pct"] == 25.0
    assert row1["sea_upper_middle_quarter_pct"] == 35.0
    assert row1["sea_top_quarter_pct"] == 30.0
    assert row1["indigenous_enrolments_pct"] == 5.0
    assert row1["lbote_pct"] == 15.0
    assert row1["year_range"] == "7-12"
    assert row1["remoteness_category"] == "Major Cities"
    assert row1["financial_profile_2021"] is None


@pytest.mark.unit
def test_load_snapshots_null_and_missing_handling(tmp_path: Path) -> None:
    """Verify missing value conventions ('NP', 'NA', empty string) resolve to None."""
    ext_dir = tmp_path / "external"
    ext_dir.mkdir(parents=True)
    df_raw = pd.DataFrame(
        [
            {
                "Calendar Year": "2025",
                "ACARA SML ID": "99999",
                "Total Enrolments": "NP",
                "Girls Enrolments": "NA",
                "Boys Enrolments": "",
                "Full Time Equivalent Enrolments": "None",
                "ICSEA": "NP",
                "ICSEA Percentile": "N/A",
                "Bottom SEA Quarter (%)": "NP",
                "Indigenous Enrolments (%)": "",
                "Language Background Other Than English - Yes (%)": "null",
                "Year Range": "None",
                "Geolocation": "nan",
            }
        ]
    )
    df_raw.to_csv(ext_dir / "school-profile-2025.csv", index=False)

    df = load_snapshots_dataframe(external_dir=ext_dir)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["total_enrolments"] is None
    assert row["girls_enrolments"] is None
    assert row["boys_enrolments"] is None
    assert row["fte_enrolments"] is None
    assert row["icsea"] is None
    assert row["icsea_percentile"] is None
    assert row["sea_bottom_quarter_pct"] is None
    assert row["indigenous_enrolments_pct"] is None
    assert row["lbote_pct"] is None
    assert row["year_range"] is None
    assert row["remoteness_category"] is None


@pytest.mark.integration
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


@pytest.mark.unit
@pytest.mark.parametrize("force_color", [None, "1"])
def test_cli_ingest_acara_help(force_color: str | None) -> None:
    """Verify apemap ingest acara command is exposed with appropriate help documentation."""
    result = runner.invoke(
        app, ["ingest", "acara", "--help"], env={"FORCE_COLOR": force_color}
    )
    output = Text.from_ansi(result.output).plain
    assert result.exit_code == 0
    assert "acara [OPTIONS]" in output
    assert "--download" in output
    assert "--longitudinal" in output


@pytest.mark.unit
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


@pytest.mark.integration
def test_run_acara_ingestion_caller_owned_connection(
    mock_external_dir: Path, tmp_path: Path
) -> None:
    """Verify caller-provided DuckDB connection remains open and owned by caller."""
    db_file = tmp_path / "acara_caller.duckdb"
    conn = get_connection(db_file)
    out_dir = tmp_path / "processed_acara_caller"

    summary = run_acara_ingestion(
        download_latest=False,
        use_longitudinal=True,
        conn=conn,
        export_parquet_files=False,
        output_dir=out_dir,
        external_dir=mock_external_dir,
    )

    assert summary["institutions_loaded"] >= 4
    # Connection should still be open and queryable
    row = conn.execute("SELECT count(*) FROM institutions").fetchone()
    assert row is not None
    assert row[0] >= 4
    conn.close()


@pytest.mark.unit
def test_temporary_dataframe_view_cleanup(tmp_path: Path) -> None:
    """Verify temporary_dataframe_view registers and safely unregisters view on normal and error exits."""
    conn = get_connection(":memory:")
    df = pd.DataFrame([{"a": 1, "b": "test"}])

    # Normal exit
    with temporary_dataframe_view(conn, "view_test", df):
        res = conn.execute("SELECT * FROM view_test").df()
        assert len(res) == 1

    # View should be unregistered now
    with pytest.raises(Exception):
        conn.execute("SELECT * FROM view_test")

    # Error exit
    with pytest.raises(RuntimeError):
        with temporary_dataframe_view(conn, "view_err", df):
            raise RuntimeError("forced failure inside context")

    # View should still be unregistered
    with pytest.raises(Exception):
        conn.execute("SELECT * FROM view_err")

    conn.close()
