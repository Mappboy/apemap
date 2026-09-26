"""Tests for spatial boundaries, GeoParquet export, view linkage, and label audit."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from apemap.analysis import export_analysis_report
from apemap.constants import RAW_AEC_2025_DIR
from apemap.db import ensure_spatial, export_to_parquet, get_connection, init_schema
from apemap.ingest.abs import ingest_abs_benchmarks
from apemap.ingest.aec import ingest_aec_boundaries


def test_geometry_types_and_validity(tmp_path: Path) -> None:
    """All 150 division geometries are valid POLYGON or MULTIPOLYGON instances."""
    shp_path = RAW_AEC_2025_DIR / "AUS_ELB_region.shp"
    if not shp_path.exists():
        pytest.skip("Cached raw AEC 2025 shapefile not available for test")

    db_path = tmp_path / "test_validity.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)
    ensure_spatial(conn)

    ingest_aec_boundaries(conn, shp_path, 2025)

    res = conn.execute(
        """
        SELECT
            count(*),
            sum(CASE WHEN ST_IsValid(geometry) THEN 1 ELSE 0 END),
            sum(CASE WHEN ST_GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON') THEN 1 ELSE 0 END)
        FROM electoral_boundaries
        WHERE election_year = 2025
        """
    ).fetchone()
    assert res is not None
    total, valid_count, poly_count = res
    assert total == 150
    assert valid_count == 150
    assert poly_count == 150

    conn.close()


def test_every_48th_parliament_house_member_matches_one_boundary(
    tmp_path: Path,
) -> None:
    """Every 48th-Parliament House of Representatives service links to exactly one 2025 boundary via v_house_electorates."""
    shp_path = RAW_AEC_2025_DIR / "AUS_ELB_region.shp"
    if not shp_path.exists():
        pytest.skip("Cached raw AEC 2025 shapefile not available for test")

    # Connect to the canonical database where 48th Parliament members are loaded
    canonical_db = Path("data/aped.duckdb")
    if not canonical_db.exists():
        pytest.skip("data/aped.duckdb not available for test")

    conn = get_connection(canonical_db)
    init_schema(conn)
    ensure_spatial(conn)
    ingest_aec_boundaries(conn, shp_path, 2025)

    # 1. Check House members in Parliament 48 (150 opening-day divisions, total stints include by-elections)
    opening_row = conn.execute(
        """
        SELECT count(*)
        FROM parliament_service
        WHERE parliament_number = 48 AND chamber = 'representatives' AND is_opening_day_member = TRUE
        """
    ).fetchone()
    assert opening_row is not None
    assert opening_row[0] == 150

    total_row = conn.execute(
        """
        SELECT count(*)
        FROM parliament_service
        WHERE parliament_number = 48 AND chamber = 'representatives'
        """
    ).fetchone()
    assert total_row is not None
    total_services = total_row[0]

    # 2. Check matches in v_house_electorates: every service links to exactly one boundary
    view_matches = conn.execute(
        """
        SELECT count(*), count(DISTINCT service_id), count(DISTINCT boundary_id)
        FROM v_house_electorates
        WHERE parliament_number = 48
        """
    ).fetchone()
    assert view_matches is not None

    matched_rows, unique_services, unique_boundaries = view_matches
    assert matched_rows == total_services
    assert unique_services == total_services
    assert unique_boundaries == 150

    # 3. Check for any unmatched House members
    unmatched = conn.execute(
        """
        SELECT ps.electorate
        FROM parliament_service ps
        LEFT JOIN electoral_boundaries eb
          ON LOWER(ps.electorate) = LOWER(eb.electorate)
         AND eb.election_year = 2025
        WHERE ps.parliament_number = 48
          AND ps.chamber = 'representatives'
          AND eb.boundary_id IS NULL
        """
    ).fetchall()
    assert unmatched == []

    conn.close()


def test_geoparquet_roundtrip_preserves_geometry(tmp_path: Path) -> None:
    """Exporting to Parquet produces valid GeoParquet and preserves geometry upon reload."""
    shp_path = RAW_AEC_2025_DIR / "AUS_ELB_region.shp"
    if not shp_path.exists():
        pytest.skip("Cached raw AEC 2025 shapefile not available for test")

    db_path = tmp_path / "test_geoparquet.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)
    ensure_spatial(conn)

    ingest_aec_boundaries(conn, shp_path, 2025)
    export_dir = tmp_path / "processed"
    export_to_parquet(conn, export_dir)

    parquet_file = export_dir / "electoral_boundaries.parquet"
    assert parquet_file.exists()

    # Verify GeoParquet metadata exists
    meta = pq.read_metadata(parquet_file)
    assert meta.metadata is not None
    assert b"geo" in meta.metadata

    geo_meta = json.loads(meta.metadata[b"geo"].decode("utf-8"))
    assert geo_meta["version"] == "1.0.0"
    assert "geometry_types" in geo_meta["columns"]["geometry"]

    # Reload into fresh connection and verify geometries
    conn2 = get_connection(tmp_path / "reload.duckdb")
    init_schema(conn2)
    ensure_spatial(conn2)

    conn2.execute(
        "INSERT INTO electoral_boundaries SELECT * FROM read_parquet(?)",
        [str(parquet_file)],
    )

    row = conn2.execute(
        "SELECT count(*) FROM electoral_boundaries WHERE ST_IsValid(geometry)"
    ).fetchone()
    assert row is not None
    assert row[0] == 150

    conn.close()
    conn2.close()


def test_no_population_share_label_leakage(tmp_path: Path) -> None:
    """Audit analysis reports and exported schemas to ensure no misleading population_share field is present."""
    db_path = tmp_path / "test_audit.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)
    ingest_abs_benchmarks(conn, 2025)

    out_dir = tmp_path / "processed"
    export_analysis_report(conn, out_dir, [47, 48])

    analysis_file = out_dir / "analysis_metrics.json"
    sectors_file = out_dir / "analysis" / "education_sectors.json"

    assert analysis_file.exists()
    assert sectors_file.exists()

    analysis_text = analysis_file.read_text(encoding="utf-8")
    sectors_text = sectors_file.read_text(encoding="utf-8")

    assert "population_share" not in analysis_text
    assert "population_share" not in sectors_text

    # Verify student_enrolment_share is present when benchmarks are populated
    sectors_data = json.loads(sectors_text)
    p48_sectors = sectors_data["parliaments"]["48"]
    assert "benchmark_comparison" in p48_sectors
    for sname, sdata in p48_sectors["benchmark_comparison"].items():
        assert "student_enrolment_share" in sdata
        assert "population_share" not in sdata

    conn.close()
