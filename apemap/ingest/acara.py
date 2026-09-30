"""ACARA 2025 official datasets ingestion pipeline and DuckDB synchronization.

Ingests official ACARA datasets (School Locations, School Profiles),
integrates with acara_school_results.json, isolates historical 2021 finances,
and populates canonical DuckDB tables and Parquet artifacts.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

import openpyxl
import pandas as pd
import requests

from apemap.constants import (
    ACARA_LOCATION_2025_URL,
    ACARA_PROFILE_2025_URL,
    ACARA_PROFILE_LONGITUDINAL_URL,
    EXTERNAL_DIR,
    PROCESSED_DIR,
)
from apemap.db import (
    export_to_parquet,
    get_connection,
    init_schema,
    migrate_historical_finances,
    temporary_dataframe_view,
)
from apemap.ingest.http import create_retry_session

logger = logging.getLogger(__name__)


class AcaraIngestResult(TypedDict):
    """Structured result contract for ACARA ingestion pipeline."""

    institutions_loaded: int
    school_snapshots_loaded: int
    school_finances_loaded: int
    school_finances_2021_migrated: int
    parquet_exported: bool
    exported_files: dict[str, str]


def download_acara_dataset(
    url: str,
    target_path: Path | str,
    force: bool = False,
    timeout: int = 120,
    session: requests.Session | None = None,
) -> Path:
    """Download an official ACARA dataset file via HTTP streaming.

    Args:
        url: Remote URL on ACARA Data Access blob storage.
        target_path: Local filesystem destination.
        force: If True, re-download even if target file already exists.
        timeout: HTTP request timeout in seconds.
        session: Optional caller-managed requests.Session.

    Returns:
        Resolved Path to the downloaded file.
    """
    path = Path(target_path).resolve()
    if path.exists() and not force:
        logger.info("Using cached ACARA file at %s", path)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading ACARA dataset from %s to %s", url, path)

    sess = session or create_retry_session()
    should_close = session is None
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        response = sess.get(url, stream=True, timeout=timeout)
        response.raise_for_status()

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        temp_path.replace(path)
    except (requests.RequestException, OSError) as e:
        logger.error("Failed to download ACARA dataset from %s: %s", url, e)
        if temp_path.exists():
            temp_path.unlink()
        raise
    finally:
        if should_close:
            sess.close()

    logger.info("Successfully downloaded %s (%d bytes)", path.name, path.stat().st_size)
    return path


def convert_xlsx_to_csv(
    xlsx_path: Path | str,
    csv_path: Path | str,
    sheet_name: str | None = None,
    force: bool = False,
) -> Path:
    """Convert an ACARA XLSX sheet to CSV for fast downstream relational parsing.

    Args:
        xlsx_path: Source XLSX file path.
        csv_path: Destination CSV file path.
        sheet_name: Specific worksheet name to convert. If None, uses first data sheet.
        force: If True, reconvert even if target CSV exists.

    Returns:
        Resolved Path to the converted CSV file.
    """
    in_path = Path(xlsx_path).resolve()
    out_path = Path(csv_path).resolve()

    if out_path.exists() and not force:
        logger.info("Using cached CSV at %s", out_path)
        return out_path

    if not in_path.exists():
        raise FileNotFoundError(f"Source XLSX not found at {in_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Converting %s to %s", in_path.name, out_path.name)

    wb = openpyxl.load_workbook(str(in_path), read_only=True, data_only=True)
    try:
        target_sheet = sheet_name
        if not target_sheet or target_sheet not in wb.sheetnames:
            # Pick sheet containing 'Location' or 'Profile' or the second sheet if first is DataDictionary
            for name in wb.sheetnames:
                if "datadictionary" not in name.lower():
                    target_sheet = name
                    break
            if not target_sheet:
                target_sheet = wb.sheetnames[0]

        ws = wb[target_sheet]
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            header = None
        if not header:
            raise ValueError(f"Sheet {target_sheet} is empty in {in_path}")

        header_clean = [
            str(c).strip() if c is not None else f"col_{i}"
            for i, c in enumerate(header)
        ]
        num_cols = len(header_clean)

        row_count = 0
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header_clean)
            for r in rows:
                if any(cell is not None for cell in r):
                    row_slice = list(r[:num_cols])
                    if len(row_slice) < num_cols:
                        row_slice.extend([""] * (num_cols - len(row_slice)))
                    writer.writerow(
                        [cell if cell is not None else "" for cell in row_slice]
                    )
                    row_count += 1

        logger.info("Wrote %d rows to %s", row_count, out_path)
    finally:
        wb.close()
    return out_path


def load_institutions_dataframe(
    external_dir: Path | str | None = None,
) -> pd.DataFrame:
    """Compile canonical institutions DataFrame from ACARA JSON and Location files.

    Args:
        external_dir: Directory containing ACARA datasets.

    Returns:
        Pandas DataFrame ready for insertion into canonical `institutions` table.
    """
    ext_dir = Path(external_dir or EXTERNAL_DIR).resolve()
    json_path = ext_dir / "acara_school_results.json"
    loc_2025_csv = ext_dir / "school-location-2025.csv"
    loc_2022_csv = ext_dir / "school-location-2022.csv"

    records_by_id: dict[str, dict[str, Any]] = {}

    # 1. Base from acara_school_results.json (10,900 schools)
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            schools_json = json.load(f)
        for s in schools_json:
            aid = str(s.get("ACARAId", "")).strip()
            name = str(s.get("SchoolName", "")).strip()
            if not aid or not name:
                continue

            sec_code = s.get("SchoolSector")
            is_ind = s.get("IndependentSchool") == "Y"
            sector = (
                "Government"
                if sec_code == "Gov"
                else ("Independent" if is_ind else "Catholic")
            )

            campus = (
                s.get("Campus", {}).get("CampusType")
                if isinstance(s.get("Campus"), dict)
                else None
            )
            addrs = s.get("AddressList", [])
            addr = addrs[0] if addrs and isinstance(addrs, list) else {}
            suburb = addr.get("City")
            state = addr.get("StateProvince")
            postcode = addr.get("PostalCode")
            grid = (
                addr.get("GridLocation", {})
                if isinstance(addr.get("GridLocation"), dict)
                else {}
            )
            lat = float(grid["Latitude"]) if grid.get("Latitude") is not None else None
            lon = (
                float(grid["Longitude"]) if grid.get("Longitude") is not None else None
            )

            records_by_id[aid] = {
                "institution_id": f"acara-{aid}",
                "acara_id": aid,
                "school_name": name,
                "school_type": str(s.get("SchoolType", "")).strip() or None,
                "sector": sector,
                "campus_type": campus,
                "state": state,
                "suburb": suburb,
                "postcode": postcode,
                "longitude": lon,
                "latitude": lat,
            }

    # 2. Enrich/augment from School Location CSV (2025 preferred, then 2022)
    loc_csv = (
        loc_2025_csv
        if loc_2025_csv.exists()
        else (loc_2022_csv if loc_2022_csv.exists() else None)
    )
    if loc_csv:
        loc_df = pd.read_csv(loc_csv, dtype=str)
        id_col = "ACARA SML ID" if "ACARA SML ID" in loc_df.columns else "ACARAId"
        name_col = "School Name" if "School Name" in loc_df.columns else "SchoolName"
        sec_col = (
            "School Sector" if "School Sector" in loc_df.columns else "SchoolSector"
        )

        if id_col in loc_df.columns and name_col in loc_df.columns:
            for _, row in loc_df.iterrows():
                aid = str(row[id_col]).strip()
                name = str(row[name_col]).strip()
                if not aid or not name:
                    continue

                sector = str(row.get(sec_col, "")).strip()
                if sector not in ("Government", "Catholic", "Independent"):
                    sector = "Other"

                lat = float(row["Latitude"]) if pd.notna(row.get("Latitude")) else None
                lon = (
                    float(row["Longitude"]) if pd.notna(row.get("Longitude")) else None
                )

                if aid in records_by_id:
                    # Enrich coordinate precision or fill missing attributes
                    rec = records_by_id[aid]
                    if rec["latitude"] is None and lat is not None:
                        rec["latitude"] = lat
                        rec["longitude"] = lon
                    if rec["suburb"] is None:
                        rec["suburb"] = str(row.get("Suburb", "")).strip() or None
                    if rec["postcode"] is None:
                        rec["postcode"] = str(row.get("Postcode", "")).strip() or None
                    if rec["state"] is None:
                        rec["state"] = str(row.get("State", "")).strip() or None
                else:
                    records_by_id[aid] = {
                        "institution_id": f"acara-{aid}",
                        "acara_id": aid,
                        "school_name": name,
                        "school_type": str(row.get("School Type", "")).strip() or None,
                        "sector": sector,
                        "campus_type": str(row.get("Campus Type", "")).strip() or None,
                        "state": str(row.get("State", "")).strip() or None,
                        "suburb": str(row.get("Suburb", "")).strip() or None,
                        "postcode": str(row.get("Postcode", "")).strip() or None,
                        "longitude": lon,
                        "latitude": lat,
                    }

    if not records_by_id:
        return pd.DataFrame(
            columns=[
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
            ]
        )

    return pd.DataFrame(list(records_by_id.values()))


def _clean_int(val: Any) -> int | None:
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("np", "na", "n/a", "null", "none"):
        return None
    try:
        return int(round(float(s)))
    except (ValueError, TypeError):
        return None


def _clean_float(val: Any) -> float | None:
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("np", "na", "n/a", "null", "none"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _clean_str(val: Any) -> str | None:
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("np", "na", "n/a", "null", "none", "nan"):
        return None
    return s


def load_snapshots_dataframe(
    external_dir: Path | str | None = None,
) -> pd.DataFrame:
    """Compile canonical school_snapshots DataFrame from ACARA Profile files.

    Args:
        external_dir: Directory containing ACARA datasets.

    Returns:
        Pandas DataFrame ready for insertion into canonical `school_snapshots` table.
    """
    ext_dir = Path(external_dir or EXTERNAL_DIR).resolve()
    candidates = [
        ext_dir / "school-profile-2008-2025.csv",
        ext_dir / "school-profile-2025.csv",
        ext_dir / "school-profile-2022.csv",
    ]

    snapshots: dict[tuple[str, int], dict[str, Any]] = {}

    for pfile in candidates:
        if not pfile.exists():
            continue
        try:
            df = pd.read_csv(pfile, dtype=str)
            id_col = (
                "ACARA SML ID"
                if "ACARA SML ID" in df.columns
                else ("ACARAId" if "ACARAId" in df.columns else None)
            )
            year_col = "Calendar Year" if "Calendar Year" in df.columns else "Year"

            if not id_col:
                continue

            for _, row in df.iterrows():
                aid = str(row[id_col]).strip()
                if not aid:
                    continue
                year_val = 2025
                if pd.notna(row.get(year_col)):
                    try:
                        year_val = int(row[year_col])
                    except ValueError:
                        year_val = 2025

                total_enrolments = _clean_int(row.get("Total Enrolments"))
                girls_enrolments = _clean_int(
                    row.get("Girls Enrolments") or row.get("Girls")
                )
                boys_enrolments = _clean_int(
                    row.get("Boys Enrolments") or row.get("Boys")
                )
                fte_enrolments = _clean_float(
                    row.get("Full Time Equivalent Enrolments")
                    or row.get("FTE Enrolments")
                )
                icsea = _clean_int(row.get("ICSEA"))
                icsea_percentile = _clean_int(
                    row.get("ICSEA Percentile") or row.get("ICSEA Percentile (%)")
                )
                sea_bottom_quarter_pct = _clean_float(
                    row.get("Bottom SEA Quarter (%)") or row.get("Bottom SEA Quarter")
                )
                sea_lower_middle_quarter_pct = _clean_float(
                    row.get("Lower Middle SEA Quarter (%)")
                    or row.get("Lower Middle SEA Quarter")
                )
                sea_upper_middle_quarter_pct = _clean_float(
                    row.get("Upper Middle SEA Quarter (%)")
                    or row.get("Upper Middle SEA Quarter")
                )
                sea_top_quarter_pct = _clean_float(
                    row.get("Top SEA Quarter (%)") or row.get("Top SEA Quarter")
                )
                indigenous_enrolments_pct = _clean_float(
                    row.get("Indigenous Enrolments (%)")
                    or row.get("Indigenous Enrolments")
                )
                lbote_pct = _clean_float(
                    row.get("Language Background Other Than English - Yes (%)")
                    or row.get("LBOTE (%)")
                    or row.get("LBOTE")
                )
                year_range = _clean_str(row.get("Year Range"))
                remoteness_category = _clean_str(
                    row.get("Geolocation")
                    or row.get("Remoteness Category")
                    or row.get("Remoteness")
                )

                inst_id = f"acara-{aid}"
                key = (inst_id, year_val)
                if key not in snapshots:
                    snapshots[key] = {
                        "institution_id": inst_id,
                        "snapshot_year": year_val,
                        "total_enrolments": total_enrolments,
                        "girls_enrolments": girls_enrolments,
                        "boys_enrolments": boys_enrolments,
                        "fte_enrolments": fte_enrolments,
                        "icsea": icsea,
                        "icsea_percentile": icsea_percentile,
                        "sea_bottom_quarter_pct": sea_bottom_quarter_pct,
                        "sea_lower_middle_quarter_pct": sea_lower_middle_quarter_pct,
                        "sea_upper_middle_quarter_pct": sea_upper_middle_quarter_pct,
                        "sea_top_quarter_pct": sea_top_quarter_pct,
                        "indigenous_enrolments_pct": indigenous_enrolments_pct,
                        "lbote_pct": lbote_pct,
                        "year_range": year_range,
                        "remoteness_category": remoteness_category,
                        "financial_profile_2021": None,
                    }
        except Exception as e:
            logger.warning("Error reading snapshot file %s: %s", pfile, e)

    if not snapshots:
        return pd.DataFrame(
            columns=[
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
            ]
        )

    return pd.DataFrame(list(snapshots.values()))


def ingest_school_finances(
    conn: DuckDBPyConnection,
    source_path: Path | str,
    reporting_year: int = 2021,
    source_dataset: str = "ACARA My School Finance",
    source_url: str = "https://myschool.edu.au",
    licence: str = "ACARA My School Terms of Use (July 2020)",
    notes: str | None = None,
) -> int:
    """Ingest authorised school finance records into canonical school_finances table.

    Requires local, authorised source input (CSV or Parquet) keyed by ACARA SML ID.
    Flags rolled multi-campus reporting where specified in input data.
    Never initiates network requests to myschool.edu.au.

    Args:
        conn: Active DuckDB connection.
        source_path: Path to local authorised CSV or Parquet file.
        reporting_year: Reporting calendar year for the finance data.
        source_dataset: Provenance dataset identifier.
        source_url: Upstream reference URL.
        licence: Terms of use or license declaration.
        notes: Audit reviewer notes.

    Returns:
        Number of ingested school finance records.
    """
    path = Path(source_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Authorised finance source file not found at: {path}")

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    elif path.suffix.lower() in (".parquet", ".pq"):
        df = pd.read_parquet(path)
    else:
        raise ValueError(f"Unsupported file format for school finances: {path.suffix}")

    if df.empty:
        return 0

    if "acara_id" not in df.columns:
        raise ValueError("School finances source data must contain 'acara_id' column")

    inserted = 0
    for _, row in df.iterrows():
        acara_val = str(row["acara_id"]).strip()
        if acara_val.endswith(".0"):
            acara_val = acara_val[:-2]
        acara_id = acara_val
        inst_id = f"acara-{acara_id}"
        rec_year = (
            int(row["reporting_year"])
            if "reporting_year" in row and pd.notna(row["reporting_year"])
            else reporting_year
        )

        is_rolled = (
            bool(row["is_rolled_reporting"])
            if "is_rolled_reporting" in row and pd.notna(row["is_rolled_reporting"])
            else False
        )
        parent_id = None
        if "parent_acara_id" in row and pd.notna(row["parent_acara_id"]):
            p_val = str(row["parent_acara_id"]).strip()
            if p_val and p_val.lower() != "nan":
                if p_val.endswith(".0"):
                    p_val = p_val[:-2]
                parent_id = p_val

        # Ensure institution exists in institutions table to preserve FK integrity
        conn.execute(
            """
            INSERT INTO institutions (
                institution_id, acara_id, school_name, sector
            ) VALUES (?, ?, ?, 'Other')
            ON CONFLICT (institution_id) DO NOTHING
            """,
            [inst_id, acara_id, f"ACARA School {acara_id}"],
        )

        def _val(col_name: str) -> int | None:
            if col_name in row and pd.notna(row[col_name]):
                try:
                    return int(float(row[col_name]))
                except (ValueError, TypeError):
                    return None
            return None

        conn.execute(
            """
            INSERT OR REPLACE INTO school_finances (
                institution_id,
                acara_id,
                reporting_year,
                recurrent_funding_gov_total,
                recurrent_funding_state_total,
                fees_charges_parent_total,
                other_private_sources_total,
                total_gross_income_total,
                total_net_recurrent_income_total,
                recurrent_funding_gov_per_student,
                recurrent_funding_state_per_student,
                fees_charges_parent_per_student,
                other_private_sources_per_student,
                total_gross_income_per_student,
                total_net_recurrent_income_per_student,
                is_rolled_reporting,
                parent_acara_id,
                source_dataset,
                source_url,
                licence,
                retrieved_at,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
            """,
            [
                inst_id,
                acara_id,
                rec_year,
                _val("recurrent_funding_gov_total"),
                _val("recurrent_funding_state_total"),
                _val("fees_charges_parent_total"),
                _val("other_private_sources_total"),
                _val("total_gross_income_total"),
                _val("total_net_recurrent_income_total"),
                _val("recurrent_funding_gov_per_student"),
                _val("recurrent_funding_state_per_student"),
                _val("fees_charges_parent_per_student"),
                _val("other_private_sources_per_student"),
                _val("total_gross_income_per_student"),
                _val("total_net_recurrent_income_per_student"),
                is_rolled,
                parent_id,
                str(row.get("source_dataset") or source_dataset),
                str(row.get("source_url") or source_url),
                str(row.get("licence") or licence),
                notes
                or (
                    str(row.get("notes"))
                    if "notes" in row and pd.notna(row["notes"])
                    else None
                ),
            ],
        )
        inserted += 1

    return inserted


def run_acara_ingestion(
    download_latest: bool = True,
    use_longitudinal: bool = True,
    db_path: Path | str | None = None,
    conn: DuckDBPyConnection | None = None,
    gpkg_path: Path | str | None = None,
    finance_path: Path | str | None = None,
    finance_year: int = 2021,
    export_parquet_files: bool = True,
    output_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
) -> AcaraIngestResult:
    """Execute complete ACARA ingestion pipeline and DuckDB table synchronization.

    1. Optionally downloads official 2025 School Location and Profile XLSX files.
    2. Converts XLSX sheets to canonical CSV files in external_dir.
    3. Builds and populates `institutions` table.
    4. Builds and populates `school_snapshots` table.
    5. Ingests authorised finance records or migrates historical finances into `school_finances`.
    6. Optionally exports updated canonical tables to Parquet.

    Args:
        download_latest: Whether to download fresh files from ACARA blob storage.
        use_longitudinal: Whether to download and process the 2008-2025 longitudinal profile.
        db_path: Path to DuckDB file or None for in-memory (ignored if conn is provided).
        conn: Optional caller-managed DuckDB connection. If provided, caller retains ownership.
        gpkg_path: Optional path to legacy aped.gpkg for financial migration.
        finance_path: Optional path to local authorised school finances file.
        finance_year: School finances reporting year (defaults to 2021).
        export_parquet_files: Whether to export canonical tables to Parquet.
        output_dir: Destination directory for Parquet exports.
        external_dir: Directory containing or receiving ACARA external data.

    Returns:
        Structured dictionary of ingestion metrics and counts.
    """
    ext_dir = Path(external_dir or EXTERNAL_DIR).resolve()
    ext_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Download official ACARA datasets if requested
    if download_latest:
        try:
            # Download School Location 2025
            loc_xlsx = ext_dir / "School Location 2025.xlsx"
            download_acara_dataset(ACARA_LOCATION_2025_URL, loc_xlsx)
            convert_xlsx_to_csv(loc_xlsx, ext_dir / "school-location-2025.csv")

            if use_longitudinal:
                prof_long_xlsx = ext_dir / "School Profile 2008-2025.xlsx"
                download_acara_dataset(ACARA_PROFILE_LONGITUDINAL_URL, prof_long_xlsx)
                convert_xlsx_to_csv(
                    prof_long_xlsx, ext_dir / "school-profile-2008-2025.csv"
                )
            else:
                prof_2025_xlsx = ext_dir / "School Profile 2025.xlsx"
                download_acara_dataset(ACARA_PROFILE_2025_URL, prof_2025_xlsx)
                convert_xlsx_to_csv(prof_2025_xlsx, ext_dir / "school-profile-2025.csv")
        except Exception as e:
            logger.error("Error downloading or converting ACARA datasets: %s", e)

    # 2. Compile DataFrames
    inst_df = load_institutions_dataframe(external_dir=ext_dir)
    snap_df = load_snapshots_dataframe(external_dir=ext_dir)

    # 3. Synchronize with DuckDB
    should_close_conn = conn is None
    active_conn = conn or get_connection(db_path)
    conn = active_conn
    try:
        init_schema(conn)

        # Insert institutions
        if not inst_df.empty:
            with temporary_dataframe_view(conn, "df_institutions_staging", inst_df):
                conn.execute(
                    """
                    INSERT INTO institutions (
                        institution_id, acara_id, school_name, school_type,
                        sector, campus_type, state, suburb, postcode, longitude, latitude
                    )
                    SELECT
                        institution_id, acara_id, school_name, school_type,
                        sector, campus_type, state, suburb, postcode, longitude, latitude
                    FROM df_institutions_staging
                    ON CONFLICT (institution_id) DO NOTHING
                    """
                )

        # Insert school snapshots
        if not snap_df.empty:
            # Filter snapshots to only valid institutions in institutions table to preserve FK integrity
            with temporary_dataframe_view(conn, "df_snapshots_staging", snap_df):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO school_snapshots (
                        institution_id, snapshot_year, total_enrolments, girls_enrolments, boys_enrolments,
                        fte_enrolments, icsea, icsea_percentile, sea_bottom_quarter_pct, sea_lower_middle_quarter_pct,
                        sea_upper_middle_quarter_pct, sea_top_quarter_pct, indigenous_enrolments_pct, lbote_pct,
                        year_range, remoteness_category, financial_profile_2021
                    )
                    SELECT
                        s.institution_id, s.snapshot_year, s.total_enrolments, s.girls_enrolments, s.boys_enrolments,
                        s.fte_enrolments, s.icsea, s.icsea_percentile, s.sea_bottom_quarter_pct, s.sea_lower_middle_quarter_pct,
                        s.sea_upper_middle_quarter_pct, s.sea_top_quarter_pct, s.indigenous_enrolments_pct, s.lbote_pct,
                        s.year_range, s.remoteness_category, s.financial_profile_2021
                    FROM df_snapshots_staging s
                    WHERE s.institution_id IN (SELECT institution_id FROM institutions)
                    """
                )

        # 4. Ingest or migrate school finances
        if finance_path:
            ingest_school_finances(conn, finance_path, reporting_year=finance_year)
        else:
            migrate_historical_finances(conn, gpkg_path=gpkg_path)

        # Count final tables
        res_inst = conn.execute("SELECT count(*) FROM institutions").fetchone()
        inst_count = res_inst[0] if res_inst is not None else 0
        res_snap = conn.execute("SELECT count(*) FROM school_snapshots").fetchone()
        snap_count = res_snap[0] if res_snap is not None else 0
        res_fin = conn.execute("SELECT count(*) FROM school_finances").fetchone()
        fin_count = res_fin[0] if res_fin is not None else 0
        res_fin_2021 = conn.execute(
            "SELECT count(*) FROM school_finances_2021"
        ).fetchone()
        fin_2021_count = res_fin_2021[0] if res_fin_2021 is not None else 0

        # 5. Export Parquet if requested
        exported_files: dict[str, Path] = {}
        if export_parquet_files:
            exported_files = export_to_parquet(conn, out_dir)

        summary: AcaraIngestResult = {
            "institutions_loaded": inst_count,
            "school_snapshots_loaded": snap_count,
            "school_finances_loaded": fin_count,
            "school_finances_2021_migrated": fin_2021_count,
            "parquet_exported": export_parquet_files,
            "exported_files": {k: str(v) for k, v in exported_files.items()},
        }
        logger.info("ACARA ingestion finished successfully: %s", summary)
        return summary
    finally:
        if should_close_conn:
            active_conn.close()
