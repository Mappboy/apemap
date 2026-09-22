"""DuckDB canonical database manager and builder for APEMAP.

Provides deterministic schema initialization, Parquet build/export workflows,
and parameterized queries for parliament and education analytics.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
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
    "school_finances_2021",
)

# Keep physical exports stable even when DuckDB's table scan order changes.
# These columns are immutable primary keys (or the natural composite key) for
# the canonical tables and are therefore safe ordering keys for release files.
CANONICAL_ORDER_BY = {
    "members": "member_id",
    "parliament_service": "service_id",
    "institutions": "institution_id",
    "member_education": "education_id",
    "school_snapshots": "institution_id, snapshot_year",
    "school_finances_2021": "institution_id",
}


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
        order_by = CANONICAL_ORDER_BY[table]
        conn.execute(
            f"COPY (SELECT * FROM {table} ORDER BY {order_by}) TO ? (FORMAT PARQUET)",
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


def migrate_historical_finances(
    conn: DuckDBPyConnection, gpkg_path: Path | str | None = None
) -> int:
    """Migrate historical 2021 school finances from GeoPackage into DuckDB.

    Isolates historical 2021 financial metrics from the legacy aped.gpkg database
    into the dedicated canonical table `school_finances_2021`.

    Args:
        conn: Active DuckDB connection.
        gpkg_path: Optional path to aped.gpkg (defaults to data/aped.gpkg).

    Returns:
        Number of migrated rows.
    """
    path = Path(gpkg_path or (PROJECT_ROOT / "data" / "aped.gpkg")).resolve()
    if not path.exists():
        return 0

    with sqlite3.connect(path) as sq_conn:
        cursor = sq_conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='acara_education_finances'"
        )
        if not cursor.fetchone():
            return 0

        df = pd.read_sql("SELECT * FROM acara_education_finances", sq_conn)

    if df.empty:
        return 0

    inserted = 0
    for _, row in df.iterrows():
        acara_id = str(row["acara_id"]).strip()
        inst_id = f"acara-{acara_id}"
        # Ensure parent institution exists to satisfy foreign key constraint
        conn.execute(
            """
            INSERT INTO institutions (
                institution_id, acara_id, school_name, sector
            ) VALUES (?, ?, ?, 'Other')
            ON CONFLICT (institution_id) DO NOTHING
            """,
            [inst_id, acara_id, f"ACARA School {acara_id}"],
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO school_finances_2021 (
                institution_id,
                acara_id,
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
                reporting_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                inst_id,
                acara_id,
                int(row["australian_government_recurrent_funding_total"])
                if pd.notna(row.get("australian_government_recurrent_funding_total"))
                else None,
                int(row["state__territory_government_recurring_funding_total"])
                if pd.notna(
                    row.get("state__territory_government_recurring_funding_total")
                )
                else None,
                int(row["fees_charges_and_parent_contributions_total"])
                if pd.notna(row.get("fees_charges_and_parent_contributions_total"))
                else None,
                int(row["other_private_sources_total"])
                if pd.notna(row.get("other_private_sources_total"))
                else None,
                int(row["total_gross_income_total"])
                if pd.notna(row.get("total_gross_income_total"))
                else None,
                int(row["total_net_recurrent_income_total"])
                if pd.notna(row.get("total_net_recurrent_income_total"))
                else None,
                int(row["australian_government_recurrent_funding_per_student"])
                if pd.notna(
                    row.get("australian_government_recurrent_funding_per_student")
                )
                else None,
                int(row["state__territory_government_recurring_funding_per_student"])
                if pd.notna(
                    row.get("state__territory_government_recurring_funding_per_student")
                )
                else None,
                int(row["fees_charges_and_parent_contributions_per_student"])
                if pd.notna(
                    row.get("fees_charges_and_parent_contributions_per_student")
                )
                else None,
                int(row["other_private_sources_per_student"])
                if pd.notna(row.get("other_private_sources_per_student"))
                else None,
                int(row["total_gross_income_per_student"])
                if pd.notna(row.get("total_gross_income_per_student"))
                else None,
                int(row["total_net_recurrent_income_per_student"])
                if pd.notna(row.get("total_net_recurrent_income_per_student"))
                else None,
                int(row["year"]) if pd.notna(row.get("year")) else 2021,
            ],
        )
        inserted += 1

    return inserted
