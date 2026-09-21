"""ACARA 2025 official datasets ingestion pipeline and DuckDB synchronization.

Ingests official ACARA datasets (School Locations, School Profiles),
integrates with acara_school_results.json, isolates historical 2021 finances,
and populates canonical DuckDB tables and Parquet artifacts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

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
)

logger = logging.getLogger(__name__)


def download_acara_dataset(
    url: str, target_path: Path | str, force: bool = False, timeout: int = 120
) -> Path:
    """Download an official ACARA dataset file via HTTP streaming.

    Args:
        url: Remote URL on ACARA Data Access blob storage.
        target_path: Local filesystem destination.
        force: If True, re-download even if target file already exists.
        timeout: HTTP request timeout in seconds.

    Returns:
        Resolved Path to the downloaded file.
    """
    path = Path(target_path).resolve()
    if path.exists() and not force:
        logger.info("Using cached ACARA file at %s", path)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading ACARA dataset from %s to %s", url, path)

    response = requests.get(url, stream=True, timeout=timeout)
    response.raise_for_status()

    with open(path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

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
    header = next(rows)
    if not header:
        raise ValueError(f"Sheet {target_sheet} is empty in {in_path}")

    header_clean = [
        str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(header)
    ]

    data_rows = []
    for r in rows:
        if any(cell is not None for cell in r):
            data_rows.append(r[: len(header_clean)])

    df = pd.DataFrame(data_rows, columns=header_clean)
    df.to_csv(out_path, index=False, encoding="utf-8")
    logger.info("Wrote %d rows to %s", len(df), out_path)
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

                icsea_val = None
                if (
                    pd.notna(row.get("ICSEA"))
                    and str(row.get("ICSEA")).strip().isdigit()
                ):
                    icsea_val = int(row["ICSEA"])

                enrol_val = None
                if (
                    pd.notna(row.get("Total Enrolments"))
                    and str(row.get("Total Enrolments")).strip().isdigit()
                ):
                    enrol_val = int(row["Total Enrolments"])

                inst_id = f"acara-{aid}"
                key = (inst_id, year_val)
                if key not in snapshots:
                    snapshots[key] = {
                        "institution_id": inst_id,
                        "snapshot_year": year_val,
                        "total_enrolments": enrol_val,
                        "icsea": icsea_val,
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
                "icsea",
                "financial_profile_2021",
            ]
        )

    return pd.DataFrame(list(snapshots.values()))


def run_acara_ingestion(
    download_latest: bool = True,
    use_longitudinal: bool = True,
    db_path: Path | str | None = None,
    gpkg_path: Path | str | None = None,
    export_parquet_files: bool = True,
    output_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute complete ACARA ingestion pipeline and DuckDB table synchronization.

    1. Optionally downloads official 2025 School Location and Profile XLSX files.
    2. Converts XLSX sheets to canonical CSV files in external_dir.
    3. Builds and populates `institutions` table.
    4. Builds and populates `school_snapshots` table.
    5. Migrates historical 2021 financial data from GeoPackage into `school_finances_2021`.
    6. Optionally exports updated canonical tables to Parquet.

    Args:
        download_latest: Whether to download fresh files from ACARA blob storage.
        use_longitudinal: Whether to download and process the 2008-2025 longitudinal profile.
        db_path: Path to DuckDB file or None for in-memory.
        gpkg_path: Optional path to legacy aped.gpkg for financial migration.
        export_parquet_files: Whether to export canonical tables to Parquet.
        output_dir: Destination directory for Parquet exports.
        external_dir: Directory containing or receiving ACARA external data.

    Returns:
        Dictionary of ingestion metrics and counts.
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
    conn = get_connection(db_path)
    init_schema(conn)

    # Insert institutions
    if not inst_df.empty:
        conn.register("df_institutions_staging", inst_df)
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
        conn.unregister("df_institutions_staging")

    # Insert school snapshots
    if not snap_df.empty:
        # Filter snapshots to only valid institutions in institutions table to preserve FK integrity
        conn.register("df_snapshots_staging", snap_df)
        conn.execute(
            """
            INSERT OR REPLACE INTO school_snapshots (
                institution_id, snapshot_year, total_enrolments, icsea, financial_profile_2021
            )
            SELECT s.institution_id, s.snapshot_year, s.total_enrolments, s.icsea, s.financial_profile_2021
            FROM df_snapshots_staging s
            WHERE s.institution_id IN (SELECT institution_id FROM institutions)
            """
        )
        conn.unregister("df_snapshots_staging")

    # 4. Migrate historical 2021 finances
    migrate_historical_finances(conn, gpkg_path=gpkg_path)

    # Count final tables
    res_inst = conn.execute("SELECT count(*) FROM institutions").fetchone()
    inst_count = res_inst[0] if res_inst is not None else 0
    res_snap = conn.execute("SELECT count(*) FROM school_snapshots").fetchone()
    snap_count = res_snap[0] if res_snap is not None else 0
    res_fin = conn.execute("SELECT count(*) FROM school_finances_2021").fetchone()
    fin_count = res_fin[0] if res_fin is not None else 0

    # 5. Export Parquet if requested
    exported_files: dict[str, Path] = {}
    if export_parquet_files:
        exported_files = export_to_parquet(conn, out_dir)

    summary = {
        "institutions_loaded": inst_count,
        "school_snapshots_loaded": snap_count,
        "school_finances_2021_migrated": fin_count,
        "parquet_exported": export_parquet_files,
        "exported_files": {k: str(v) for k, v in exported_files.items()},
    }
    logger.info("ACARA ingestion finished successfully: %s", summary)
    return summary
