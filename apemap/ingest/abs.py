"""ABS Schools statistical reference benchmark ingestion.

Ingests official ABS Schools annual statistical benchmarks (e.g. 2025 student enrolments
and proportions by school affiliation: Government, Catholic, Independent) into the
canonical `education_sector_benchmarks` table and exports canonical Parquet artifacts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from apemap.constants import (
    ABS_BENCHMARK_2025,
    ABS_SCHOOLS_2025_RELEASED_AT,
    ABS_SCHOOLS_2025_TITLE,
    ABS_SCHOOLS_2025_URL,
    DATA_DIR,
    PROCESSED_DIR,
)
from apemap.db import export_to_parquet, get_connection, init_schema

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


def ingest_abs_benchmarks(
    conn: duckdb.DuckDBPyConnection,
    benchmark_year: int = 2025,
) -> int:
    """Ingest versioned statistical reference benchmarks into DuckDB.

    Args:
        conn: Active DuckDB connection.
        benchmark_year: Target benchmark year (default: 2025).

    Returns:
        Number of inserted benchmark rows.
    """
    if benchmark_year != 2025:
        raise ValueError(
            f"Unsupported benchmark year: {benchmark_year}. Supported: [2025]"
        )

    total_enrolments = ABS_BENCHMARK_2025["total_student_enrolments"]
    sectors_data = ABS_BENCHMARK_2025["sectors"]

    inserted = 0
    for sector, data in sectors_data.items():
        enrolments = data["student_enrolments"]
        share = data["student_enrolment_share"]

        conn.execute(
            """
            INSERT OR REPLACE INTO education_sector_benchmarks (
                benchmark_year,
                sector,
                student_enrolment_share,
                student_enrolments,
                total_student_enrolments,
                source_title,
                source_url,
                released_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                benchmark_year,
                sector,
                share,
                enrolments,
                total_enrolments,
                ABS_SCHOOLS_2025_TITLE,
                ABS_SCHOOLS_2025_URL,
                ABS_SCHOOLS_2025_RELEASED_AT,
            ],
        )
        inserted += 1

    logger.info(
        "Ingested %d ABS sector benchmarks for year %d (total enrolments: %d)",
        inserted,
        benchmark_year,
        total_enrolments,
    )
    return inserted


def run_abs_ingestion(
    db_path: Path | str | None = None,
    export_parquet_files: bool = True,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute end-to-end ingestion of ABS reference benchmarks.

    Args:
        db_path: Target DuckDB database path (defaults to data/aped.duckdb).
        export_parquet_files: Export updated canonical tables to Parquet.
        output_dir: Destination directory for Parquet exports.

    Returns:
        Summary metrics dictionary.
    """
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        benchmarks_loaded = ingest_abs_benchmarks(conn, 2025)

        parquet_paths: dict[str, Path] = {}
        if export_parquet_files:
            parquet_paths = export_to_parquet(conn, effective_out_dir)
    finally:
        conn.close()

    return {
        "benchmark_year": 2025,
        "benchmarks_loaded": benchmarks_loaded,
        "source_title": ABS_SCHOOLS_2025_TITLE,
        "source_url": ABS_SCHOOLS_2025_URL,
        "released_at": ABS_SCHOOLS_2025_RELEASED_AT,
        "parquet_exported": bool(parquet_paths),
        "parquet_path": str(parquet_paths.get("education_sector_benchmarks", "")),
    }
