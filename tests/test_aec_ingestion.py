"""Tests for official AEC 2025 federal electoral boundary ingestion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from apemap.constants import RAW_AEC_2025_DIR
from apemap.db import get_connection, init_schema
from apemap.ingest.aec import (
    ingest_aec_boundaries,
    run_aec_ingestion,
    slugify,
)


def test_slugify_deterministic() -> None:
    """slugify produces clean, URL/identifier-safe lowercase slugs."""
    assert slugify("Clark") == "clark"
    assert slugify("Tas") == "tas"
    assert slugify("Kingsford Smith") == "kingsford-smith"
    assert slugify("Eden-Monaro") == "eden-monaro"
    assert slugify("O'Connor") == "o-connor"
    assert slugify("Wide Bay") == "wide-bay"


def test_aec_ingestion_divisions_and_geometries(tmp_path: Path) -> None:
    """Ingest 2025 AEC shapefile and verify exactly 150 divisions, deterministic IDs, and valid geometries."""
    shp_path = RAW_AEC_2025_DIR / "AUS_ELB_region.shp"
    if not shp_path.exists():
        pytest.skip("Cached raw AEC 2025 shapefile not available for test")

    db_path = tmp_path / "test_aec.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)

    count = ingest_aec_boundaries(conn, shp_path, election_year=2025)
    assert count == 150

    # Total division count
    row = conn.execute(
        "SELECT count(*) FROM electoral_boundaries WHERE election_year = 2025"
    ).fetchone()
    assert row is not None
    assert row[0] == 150

    # Unique boundary_ids
    row = conn.execute(
        "SELECT count(DISTINCT boundary_id) FROM electoral_boundaries WHERE election_year = 2025"
    ).fetchone()
    assert row is not None
    assert row[0] == 150

    # Unique electorates per year
    row = conn.execute(
        "SELECT count(DISTINCT electorate) FROM electoral_boundaries WHERE election_year = 2025"
    ).fetchone()
    assert row is not None
    assert row[0] == 150

    # Geometries must NOT be NULL
    row = conn.execute(
        "SELECT count(*) FROM electoral_boundaries WHERE geometry IS NULL"
    ).fetchone()
    assert row is not None
    assert row[0] == 0

    # Check a deterministic ID sample (e.g. 2025-clark-tas)
    clark = conn.execute(
        "SELECT boundary_id, electorate, state_or_territory FROM electoral_boundaries WHERE electorate = 'Clark'"
    ).fetchone()
    assert clark == ("2025-clark-tas", "Clark", "Tas")

    conn.close()


def test_aec_ingestion_idempotence(tmp_path: Path) -> None:
    """Ingesting AEC boundaries multiple times preserves 150 unique records without duplicating."""
    shp_path = RAW_AEC_2025_DIR / "AUS_ELB_region.shp"
    if not shp_path.exists():
        pytest.skip("Cached raw AEC 2025 shapefile not available for test")

    db_path = tmp_path / "test_aec_idempotent.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)

    ingest_aec_boundaries(conn, shp_path, 2025)
    ingest_aec_boundaries(conn, shp_path, 2025)

    row = conn.execute("SELECT count(*) FROM electoral_boundaries").fetchone()
    assert row is not None
    assert row[0] == 150
    conn.close()


def test_run_aec_ingestion_offline_from_cache(tmp_path: Path) -> None:
    """run_aec_ingestion operates completely offline when cache is populated."""
    shp_path = RAW_AEC_2025_DIR / "AUS_ELB_region.shp"
    if not shp_path.exists():
        pytest.skip("Cached raw AEC 2025 shapefile not available for test")

    db_path = tmp_path / "test_pipeline.duckdb"
    out_dir = tmp_path / "processed"

    # Patch requests.get so if any network request is attempted, it fails
    with patch(
        "requests.get",
        side_effect=RuntimeError("Network access forbidden in offline test"),
    ):
        results = run_aec_ingestion(
            election_year=2025,
            refresh=False,
            db_path=db_path,
            raw_dir=RAW_AEC_2025_DIR,
            export_parquet_files=True,
            output_dir=out_dir,
        )

    assert results["divisions_loaded"] == 150
    assert results["parquet_exported"] is True

    parquet_file = out_dir / "electoral_boundaries.parquet"
    assert parquet_file.exists()
