"""DuckDB canonical database manager and builder for APEMAP.

Provides deterministic schema initialization, Parquet build/export workflows,
and parameterized queries for parliament and education analytics.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Generator

import duckdb
import pandas as pd

from apemap.constants import PARLIAMENT_METADATA
from apemap.education_context import CONTEXT_COLUMNS

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
    "school_finances",
    "school_finances_2021",
    "electoral_boundaries",
    "education_sector_benchmarks",
    "school_finance_benchmarks",
    "school_public_funding",
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
    "school_finances": "institution_id, reporting_year",
    "school_finances_2021": "institution_id",
    "electoral_boundaries": "boundary_id",
    "education_sector_benchmarks": "benchmark_year, sector",
    "school_finance_benchmarks": "reporting_year, state_or_territory, sector, geolocation, metric",
    "school_public_funding": "institution_id, reporting_year, metric, source_dataset",
}


def ensure_spatial(
    conn: DuckDBPyConnection, *, allow_install: bool | None = None
) -> None:
    """Load spatial, optionally using an isolated APEMAP_DUCKDB_EXTENSION_DIR."""
    extension_dir = os.environ.get("APEMAP_DUCKDB_EXTENSION_DIR")
    if extension_dir:
        conn.execute(
            "SET extension_directory = ?", [str(Path(extension_dir).resolve())]
        )
    if allow_install is None:
        allow_install = os.environ.get("APEMAP_OFFLINE") != "1"
    try:
        conn.execute("LOAD spatial;")
    except Exception as e:
        if not allow_install:
            raise RuntimeError(
                "DuckDB spatial extension is not installed and offline mode prevents network installation. "
                "Ensure the spatial extension is pre-installed in the DuckDB extension directory."
            ) from e
        conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET geometry_always_xy = true;")


def get_connection(
    db_path: Path | str | None = None, *, read_only: bool = False
) -> DuckDBPyConnection:
    """Obtain a DuckDB connection.

    Args:
        db_path: Path to DuckDB file or None for in-memory database.
        read_only: Open an existing file without permitting database mutations.

    Returns:
        DuckDBPyConnection instance.
    """
    if db_path is None or str(db_path) == ":memory:":
        return duckdb.connect(":memory:")

    resolved_path = Path(db_path).resolve()
    if read_only:
        if not resolved_path.exists():
            raise FileNotFoundError(f"Read-only DuckDB file not found: {resolved_path}")
        return duckdb.connect(str(resolved_path), read_only=True)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(resolved_path))


def backfill_recorded_school_ids(conn: DuckDBPyConnection) -> None:
    """Persist frozen Python identities for imported and pre-upgrade assertions."""
    from apemap.review.model import school_review_id

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    for table in ("member_education", "review_source_member_education"):
        if table not in tables:
            continue
        rows = conn.execute(
            f"SELECT education_id, school_name_as_recorded FROM {table} "
            "WHERE recorded_school_id IS NULL AND NULLIF(TRIM(school_name_as_recorded), '') IS NOT NULL"
        ).fetchall()
        if rows:
            conn.executemany(
                f"UPDATE {table} SET recorded_school_id = ? WHERE education_id = ?",
                [(school_review_id(name), education_id) for education_id, name in rows],
            )


@contextmanager
def temporary_dataframe_view(
    conn: DuckDBPyConnection, view_name: str, df: pd.DataFrame
) -> Generator[str, None, None]:
    """Register a pandas DataFrame as a temporary DuckDB view and guarantee its unregistration.

    Args:
        conn: Active DuckDB connection.
        view_name: Temporary view name to register.
        df: Pandas DataFrame to stage.

    Yields:
        Registered view name.
    """
    conn.register(view_name, df)
    try:
        yield view_name
    finally:
        try:
            conn.unregister(view_name)
        except Exception:
            pass


def init_schema(conn: DuckDBPyConnection) -> None:
    """Initialize canonical tables and views from schema DDL scripts.

    Migrates legacy physical school_finances_2021 tables to the generalised
    school_finances table and compatibility view when upgrading existing databases.

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

    # If school_finances_2021 exists as a physical TABLE (from previous schema),
    # migrate records to school_finances and drop the table so the compatibility VIEW can be created.
    try:
        res = conn.execute(
            "SELECT table_type FROM information_schema.tables WHERE table_name = 'school_finances_2021'"
        ).fetchone()
        if res and res[0] in ("BASE TABLE", "Table"):
            conn.execute(
                """
                INSERT INTO school_finances (
                    institution_id, acara_id, reporting_year,
                    recurrent_funding_gov_total, recurrent_funding_state_total,
                    fees_charges_parent_total, other_private_sources_total,
                    total_gross_income_total, total_net_recurrent_income_total,
                    recurrent_funding_gov_per_student, recurrent_funding_state_per_student,
                    fees_charges_parent_per_student, other_private_sources_per_student,
                    total_gross_income_per_student, total_net_recurrent_income_per_student
                )
                SELECT
                    institution_id, acara_id, reporting_year,
                    recurrent_funding_gov_total, recurrent_funding_state_total,
                    fees_charges_parent_total, other_private_sources_total,
                    total_gross_income_total, total_net_recurrent_income_total,
                    recurrent_funding_gov_per_student, recurrent_funding_state_per_student,
                    fees_charges_parent_per_student, other_private_sources_per_student,
                    total_gross_income_per_student, total_net_recurrent_income_per_student
                FROM school_finances_2021
                ON CONFLICT (institution_id, reporting_year) DO NOTHING;
                DROP TABLE school_finances_2021;
                """
            )
    except Exception:
        pass
    # DuckDB appends migrated columns, so physical order differs from fresh DDL.
    # Propagate migration errors and import releases by column name below.
    snapshot_columns = (
        ("girls_enrolments", "INTEGER"),
        ("boys_enrolments", "INTEGER"),
        ("fte_enrolments", "DOUBLE"),
        ("icsea_percentile", "INTEGER"),
        ("sea_bottom_quarter_pct", "DOUBLE"),
        ("sea_lower_middle_quarter_pct", "DOUBLE"),
        ("sea_upper_middle_quarter_pct", "DOUBLE"),
        ("sea_top_quarter_pct", "DOUBLE"),
        ("indigenous_enrolments_pct", "DOUBLE"),
        ("lbote_pct", "DOUBLE"),
        ("year_range", "VARCHAR"),
        ("remoteness_category", "VARCHAR"),
    )
    for column, sql_type in snapshot_columns:
        conn.execute(
            f"ALTER TABLE school_snapshots ADD COLUMN IF NOT EXISTS {column} {sql_type}"
        )

    for table, columns in {
        "parliament_service": [
            ("source_url", "VARCHAR"),
            ("retrieved_at", "TIMESTAMPTZ"),
            ("source_service_start", "DATE"),
            ("source_service_end", "DATE"),
        ],
        "institutions": [
            ("country", "VARCHAR"),
            ("institution_status", "VARCHAR DEFAULT 'unknown'"),
        ],
        "member_education": [
            ("school_name_as_recorded", "VARCHAR"),
            ("institution_resolution", "VARCHAR"),
            ("resolution_source_url", "VARCHAR"),
            ("evidence_origin", "VARCHAR"),
            *CONTEXT_COLUMNS,
        ],
    }.items():
        for column, sql_type in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}"
            )
    source_exists = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'review_source_member_education'"
    ).fetchone()
    if source_exists and source_exists[0]:
        for column, sql_type in CONTEXT_COLUMNS:
            conn.execute(
                f"ALTER TABLE review_source_member_education ADD COLUMN IF NOT EXISTS {column} {sql_type}"
            )
    backfill_recorded_school_ids(conn)
    for info in PARLIAMENT_METADATA.values():
        conn.execute(
            """INSERT OR REPLACE INTO parliament_metadata
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                info[key]
                for key in (
                    "parliament_number",
                    "general_election_date",
                    "opening_date",
                    "end_date",
                    "description",
                    "expected_representatives",
                    "expected_senators",
                    "source_url",
                )
            ],
        )
    conn.execute(views_sql_path.read_text(encoding="utf-8"))


def load_parquet_sources(
    conn: DuckDBPyConnection, parquet_dir: Path | str
) -> dict[str, int]:
    """Populate canonical tables from corresponding Parquet files.

    Expects files named <table_name>.parquet inside `parquet_dir`. Columns are
    matched by name; missing nullable columns retain their schema defaults.

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
    ensure_spatial(conn)
    for table in CANONICAL_TABLES:
        # school_finances_2021 is a compatibility view over school_finances
        if table == "school_finances_2021":
            res = conn.execute("SELECT COUNT(*) FROM school_finances_2021").fetchone()
            counts[table] = int(res[0]) if res else 0
            continue

        parquet_file = source_dir / f"{table}.parquet"
        if parquet_file.exists():
            conn.execute(
                f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet(?)",
                [str(parquet_file)],
            )
            result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            counts[table] = int(result[0]) if result else 0
        elif table == "school_finances":
            legacy_p = source_dir / "school_finances_2021.parquet"
            if legacy_p.exists():
                conn.execute(
                    """
                    INSERT INTO school_finances (
                        institution_id, acara_id, reporting_year,
                        recurrent_funding_gov_total, recurrent_funding_state_total,
                        fees_charges_parent_total, other_private_sources_total,
                        total_gross_income_total, total_net_recurrent_income_total,
                        recurrent_funding_gov_per_student, recurrent_funding_state_per_student,
                        fees_charges_parent_per_student, other_private_sources_per_student,
                        total_gross_income_per_student, total_net_recurrent_income_per_student
                    )
                    SELECT
                        institution_id, acara_id, reporting_year,
                        recurrent_funding_gov_total, recurrent_funding_state_total,
                        fees_charges_parent_total, other_private_sources_total,
                        total_gross_income_total, total_net_recurrent_income_total,
                        recurrent_funding_gov_per_student, recurrent_funding_state_per_student,
                        fees_charges_parent_per_student, other_private_sources_per_student,
                        total_gross_income_per_student, total_net_recurrent_income_per_student
                    FROM read_parquet(?)
                    ON CONFLICT (institution_id, reporting_year) DO NOTHING
                    """,
                    [str(legacy_p)],
                )
                result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                counts[table] = int(result[0]) if result else 0
            else:
                counts[table] = 0
        else:
            counts[table] = 0

    backfill_recorded_school_ids(conn)
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

    ensure_spatial(conn)
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

    Isolates historical financial metrics from the legacy aped.gpkg database
    into the dedicated canonical table `school_finances`.

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
        reporting_year = int(row["year"]) if pd.notna(row.get("year")) else 2021
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, NULL,
                      'ACARA My School Finance (Historical Archive)',
                      'https://myschool.edu.au',
                      'ACARA My School Terms of Use (July 2020)',
                      '2022-01-01 00:00:00+00',
                      'Migrated from historical aped.gpkg baseline')
            """,
            [
                inst_id,
                acara_id,
                reporting_year,
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
            ],
        )
        inserted += 1

    return inserted
