"""Build closed database templates and give each test an independent copy."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil

import duckdb

from apemap.db import get_connection, init_schema

SeedDatabase = Callable[[duckdb.DuckDBPyConnection], None]
DatabaseFactory = Callable[[Path | None], tuple[Path, duckdb.DuckDBPyConnection]]


def build_template(path: Path, seed: SeedDatabase | None = None) -> Path:
    """Commit setup once and close the connection before anyone copies the file."""
    conn = get_connection(path)
    try:
        conn.execute("BEGIN TRANSACTION")
        init_schema(conn)
        if seed is not None:
            seed(conn)
        conn.execute("COMMIT")
    finally:
        conn.close()
    return path


def copy_database(template: Path, destination: Path) -> Path:
    """Copy a closed template without modifying it or sharing database state."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, destination)
    return destination
