"""Canonical export pipelines for APEMAP.

Exports deterministic Parquet tables, GeoJSON spatial layers per parliament term,
and analytical summaries to data/processed/.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb

from apemap.analysis import export_analysis_report
from apemap.constants import PROCESSED_DIR
from apemap.db import export_to_parquet

logger = logging.getLogger(__name__)


def export_canonical_parquet(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Export all canonical tables to Parquet files.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.

    Returns:
        Mapping of table names to output file paths.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    return export_to_parquet(conn, out_dir)


def export_spatial_geojson(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
) -> dict[int, Path]:
    """Export secondary school attendance spatial layers to GeoJSON.

    Produces RFC 7946 GeoJSON FeatureCollections for each parliament term
    containing school geographic coordinates and parliamentarian metadata.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: List of parliaments to export (default: [46, 47, 48]).

    Returns:
        Mapping of parliament number to GeoJSON file path.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parls = parliaments or [46, 47, 48]

    exported: dict[int, Path] = {}

    query = """
    SELECT
        education_id,
        member_id,
        display_name,
        family_name,
        given_name,
        gender,
        date_of_birth,
        chamber,
        party,
        party_abbrev,
        electorate,
        state_or_territory,
        is_opening_day_member,
        is_current_member,
        institution_id,
        acara_id,
        school_name,
        school_type,
        school_sector,
        campus_type,
        school_state,
        school_suburb,
        school_postcode,
        longitude,
        latitude,
        years_attended,
        graduation_year,
        attended_status,
        confidence,
        historical_2021_net_recurrent_income_per_student
    FROM v_member_secondary_education
    WHERE parliament_number = ?
      AND longitude IS NOT NULL
      AND latitude IS NOT NULL
    ORDER BY display_name, school_name, education_id, member_id, institution_id
    """

    for p in target_parls:
        df = conn.execute(query, [p]).df()

        features: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            lon = float(row["longitude"])
            lat = float(row["latitude"])
            props: dict[str, Any] = {
                "education_id": row["education_id"],
                "member_id": row["member_id"],
                "member": row["display_name"],
                "family_name": row["family_name"],
                "given_name": row["given_name"],
                "gender": row["gender"],
                "dob": str(row["date_of_birth"])
                if row["date_of_birth"] is not None
                else None,
                "chamber": row["chamber"],
                "party": row["party"],
                "party_abbrev": row["party_abbrev"],
                "electorate": row["electorate"],
                "state_or_territory": row["state_or_territory"],
                "is_opening_day_member": bool(row["is_opening_day_member"]),
                "is_current_member": bool(row["is_current_member"]),
                "institution_id": row["institution_id"],
                "acara_id": row["acara_id"],
                "school_name": row["school_name"],
                "school_type": row["school_type"],
                "school_sector": row["school_sector"],
                "campus_type": row["campus_type"],
                "school_state": row["school_state"],
                "school_suburb": row["school_suburb"],
                "school_postcode": row["school_postcode"],
                "years_attended": row["years_attended"],
                "graduation_year": int(row["graduation_year"])
                if row["graduation_year"] is not None
                and str(row["graduation_year"]).isdigit()
                else None,
                "attended_status": row["attended_status"],
                "confidence": row["confidence"],
                "historical_2021_net_recurrent_income_per_student": int(
                    row["historical_2021_net_recurrent_income_per_student"]
                )
                if row["historical_2021_net_recurrent_income_per_student"] is not None
                and str(
                    row["historical_2021_net_recurrent_income_per_student"]
                ).isdigit()
                else None,
            }
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": props,
            }
            features.append(feature)

        geojson_data = {
            "type": "FeatureCollection",
            "name": f"parliament_{p}_combined",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
            },
            "features": features,
        }

        geojson_path = out_dir / f"parliament_{p}_combined.geojson"
        geojson_path.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")
        exported[p] = geojson_path
        logger.info(
            "Exported Parliament %d GeoJSON layer to %s (%d features)",
            p,
            geojson_path,
            len(features),
        )

    return exported


def export_all_artifacts(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
) -> dict[str, Any]:
    """Export all canonical artifacts: Parquet, GeoJSON, and analytical metrics.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: List of parliaments to process.

    Returns:
        Summary dictionary with paths to all generated artifacts.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    target_parls = parliaments or [46, 47, 48]

    parquet_paths = export_canonical_parquet(conn, out_dir)
    geojson_paths = export_spatial_geojson(conn, out_dir, target_parls)
    analysis_path = export_analysis_report(conn, out_dir, target_parls)

    return {
        "output_directory": str(out_dir),
        "parquet_files": {k: str(v) for k, v in parquet_paths.items()},
        "geojson_layers": {str(k): str(v) for k, v in geojson_paths.items()},
        "analysis_report": str(analysis_path),
    }
