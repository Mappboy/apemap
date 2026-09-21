"""DuckDB canonical database manager and builder for APEMAP.

Provides deterministic schema initialization, Parquet build/export workflows,
and parameterized queries for parliament and education analytics.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import pandas as pd

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

CANONICAL_TABLES = (
    "members",
    "parliament_service",
    "institutions",
    "member_education",
    "school_snapshots",
)


def get_connection(db_path: Path | str | None = None) -> DuckDBPyConnection:
    """Obtain a DuckDB connection.

    Args:
        db_path: Path to DuckDB file or None for in-memory database.

    Returns:
        DuckDBPyConnection instance.
    """
    if db_path is None:
        return duckdb.connect(":memory:")

    resolved_path = Path(db_path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(resolved_path))


def init_schema(conn: DuckDBPyConnection) -> None:
    """Initialize canonical tables and views from schema DDL scripts.

    Args:
        conn: Active DuckDB connection.
    """
    schema_sql_path = SCHEMA_DIR / "01_schema.sql"
    views_sql_path = SCHEMA_DIR / "02_views.sql"

    if not schema_sql_path.exists():
        raise FileNotFoundError(f"Schema DDL script not found at {schema_sql_path}")
    if not views_sql_path.exists():
        raise FileNotFoundError(f"Views DDL script not found at {views_sql_path}")

    conn.execute(schema_sql_path.read_text(encoding="utf-8"))
    conn.execute(views_sql_path.read_text(encoding="utf-8"))


def load_parquet_sources(
    conn: DuckDBPyConnection, parquet_dir: Path | str
) -> dict[str, int]:
    """Populate canonical tables from corresponding Parquet files.

    Expects files named <table_name>.parquet inside `parquet_dir`.

    Args:
        conn: Active DuckDB connection.
        parquet_dir: Directory containing Parquet source files.

    Returns:
        Mapping of table name to inserted row count.
    """
    source_dir = Path(parquet_dir).resolve()
    if not source_dir.is_dir():
        raise NotADirectoryError(
            f"Parquet source directory does not exist: {source_dir}"
        )

    counts: dict[str, int] = {}
    for table in CANONICAL_TABLES:
        parquet_file = source_dir / f"{table}.parquet"
        if parquet_file.exists():
            # Parameterize file path safely
            conn.execute(
                f"INSERT INTO {table} SELECT * FROM read_parquet(?)",
                [str(parquet_file)],
            )
            result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(result[0]) if result else 0
        else:
            counts[table] = 0

    return counts


def build_database(
    db_path: Path | str | None = None,
    parquet_dir: Path | str | None = None,
) -> DuckDBPyConnection:
    """Build and initialize the canonical DuckDB database.

    Args:
        db_path: Target DuckDB database path or None for in-memory.
        parquet_dir: Optional directory containing Parquet source files to load.

    Returns:
        Active DuckDBPyConnection.
    """
    conn = get_connection(db_path)
    init_schema(conn)

    if parquet_dir is not None:
        load_parquet_sources(conn, parquet_dir)

    return conn


def export_to_parquet(
    conn: DuckDBPyConnection, output_dir: Path | str
) -> dict[str, Path]:
    """Export canonical tables to individual Parquet files.

    Args:
        conn: Active DuckDB connection.
        output_dir: Destination directory for Parquet files.

    Returns:
        Mapping of table names to output Parquet file paths.
    """
    target_dir = Path(output_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    exported: dict[str, Path] = {}
    for table in CANONICAL_TABLES:
        target_path = target_dir / f"{table}.parquet"
        conn.execute(
            f"COPY {table} TO ? (FORMAT PARQUET)",
            [str(target_path)],
        )
        exported[table] = target_path

    return exported


def get_members_by_parliament(
    conn: DuckDBPyConnection, parliament_number: int
) -> pd.DataFrame:
    """Retrieve members for a specific parliament term using parameterized query.

    Args:
        conn: Active DuckDB connection.
        parliament_number: Parliament term number (e.g. 46, 47, 48).

    Returns:
        Pandas DataFrame containing member records.
    """
    return conn.execute(
        "SELECT * FROM get_parliament_members(?)", [parliament_number]
    ).df()


def get_secondary_education_by_parliament(
    conn: DuckDBPyConnection, parliament_number: int
) -> pd.DataFrame:
    """Retrieve secondary education records for a specific parliament term.

    Args:
        conn: Active DuckDB connection.
        parliament_number: Parliament term number (e.g. 46, 47, 48).

    Returns:
        Pandas DataFrame containing education records.
    """
    return conn.execute(
        "SELECT * FROM get_parliament_education(?)", [parliament_number]
    ).df()
