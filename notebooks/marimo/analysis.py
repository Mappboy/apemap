"""Optional interactive companion for the canonical APEMAP analysis API."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import marimo

root = Path(os.environ.get("APEMAP_PROJECT_ROOT", Path.cwd())).resolve()
while not (root / "pyproject.toml").exists() and root != root.parent:
    root = root.parent
sys.path.insert(0, str(root))

from apemap.analysis import (  # noqa: E402
    compute_funding_summary,
    compute_parliament_demographics,
    compute_sector_summary,
)
from apemap.db import get_connection  # noqa: E402

app = marimo.App()


@app.cell
def _():
    parliament = 47
    db_path = Path(
        os.environ.get("APEMAP_DB_PATH", root / "data" / "aped.duckdb")
    ).resolve()
    conn = get_connection(db_path, read_only=True)
    return conn, parliament


@app.cell
def _(conn, parliament):
    demographics = compute_parliament_demographics(conn, parliament)
    sectors = compute_sector_summary(conn, parliament)
    finance = compute_funding_summary(conn, parliament)
    return demographics, finance, sectors


@app.cell
def _(demographics, finance, sectors):
    summary = {
        "opening_date": demographics["reference_opening_date"],
        "parliamentarians": demographics["total_parliamentarians"],
        "known_schools": sectors["parliamentarians_with_known_schools"],
        "finance_reporting_year": finance["reporting_year"],
        "finance_gross_n": finance["overall_gross_income"]["n"],
        "finance_gross_missing": finance["overall_gross_income"]["missing"],
    }
    summary


@app.cell
def _(conn):
    conn.close()


if __name__ == "__main__":
    app.run()
