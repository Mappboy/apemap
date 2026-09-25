# Python Package Documentation

The `apemap` Python package provides programmatic access to APEMAP's data ingestion, institutional matching, database initialization, analytical aggregations, and export pipelines.

---

## 1. Supported Public API

The canonical public interface exposed at the root package level is:

```python
from apemap import build_database, get_connection, init_schema
```

### Core Functions

#### `get_connection(db_path: Path | str | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection`
Establishes a connection to the canonical DuckDB database (`data/aped.duckdb` by default). Handles path resolution and ensures parent directories exist.

```python
import duckdb
from apemap import get_connection

conn = get_connection(read_only=True)
df = conn.execute("SELECT * FROM v_parliament_members_current").df()
print(df.head())
conn.close()
```

#### `init_schema(conn: duckdb.DuckDBPyConnection) -> None`
Applies canonical DDL (`01_schema.sql`) and analytical views and parameterized macros (`02_views.sql`) idempotently to the target database.

#### `build_database(db_path: Path | str | None = None) -> duckdb.DuckDBPyConnection`
Initializes a new database at `db_path`, applies schema and views, and returns an open connection.

---

## 2. Package Architecture & Subsystems

To maintain architectural boundaries, internal functions are separated from stable public entry points.

```text
apemap/
├── __init__.py           # Canonical public API (get_connection, init_schema, build_database)
├── analysis.py           # Deterministic SQL analytical aggregation functions
├── constants.py          # Paths, URLs, parliament metadata, and canonical enums
├── db.py                 # DuckDB connection handling, schema DDL, and data migration
├── export.py             # Parquet table generation, GeoJSON feature collections
├── validate.py           # Automated relational validation and integrity gates
└── ingest/
    ├── __init__.py
    ├── aph.py            # APH Handbook API client, individual parsing, caching
    ├── acara.py          # ACARA workbook downloading, parsing, finance isolation
    ├── matching.py       # SchoolMatcher: normalisation, alias lookup, RapidFuzz
    └── pipeline.py       # High-level APH ingestion & matching orchestration
```

---

## 3. High-Level Subsystem Overview

### `apemap.analysis`
Provides deterministic, aggregation-only functions that query canonical views. All functions accept an active DuckDB connection and return structured Python dictionaries and Pandas DataFrames.
- **`compute_parliament_demographics(conn, parliament_number)`**: Computes opening-day baseline age averages, medians, and chamber/gender breakdowns.
- **`compute_sector_summary(conn, parliament_number)`**: Calculates unique parliamentarians by sector versus raw attendance instances.
- **`compute_funding_summary(conn, parliament_number)`**: Aggregates 2021 gross and net recurrent income averages and reporting sample sizes per sector.
- **`export_analysis_report(conn, output_dir, parliaments)`**: Combines all analytical summaries into `data/processed/analysis_report.json`.

```python
from apemap import get_connection
from apemap.analysis import compute_sector_summary

conn = get_connection(read_only=True)
sector_data = compute_sector_summary(conn, parliament_number=47)
print(
    "47th Parliament Sector Distribution:",
    sector_data["percentage_of_known_parliamentarians"],
)
conn.close()
```

### `apemap.validate`
Provides relational constraints and benchmark coverage verification.
- **`validate_database(conn, parliaments)`**: Runs 11 distinct checks covering primary keys, foreign keys, non-null values, valid sector/chamber enumerations, and expected parliament seat counts. Returns a `ValidationReport` dataclass with `.passed`, `.failures`, and `.parliament_metrics`.

```python
from apemap import get_connection
from apemap.validate import validate_database

conn = get_connection(read_only=True)
report = validate_database(conn, parliaments=[46, 47, 48])
if not report.passed:
    print(f"Validation failed with {len(report.failures)} issues:")
    for issue in report.failures:
        print(f"  - {issue}")
else:
    print(f"All {report.checks_run} checks passed!")
conn.close()
```

### `apemap.export`
Generates portable Parquet tables and spatial GeoJSON layers.
- **`export_canonical_parquet(conn, output_dir)`**: Exports all six canonical tables to `.parquet`.
- **`export_parliament_geojson(conn, parliament_number, output_dir)`**: Constructs GeoJSON `FeatureCollection`s where each point represents an educational institution attended by parliamentarians of that parliament, enriched with coordinates and member attributes.
- **`export_all_artifacts(conn, output_dir, parliaments)`**: Orchestrates Parquet, GeoJSON, and JSON metric exports.

### `apemap.ingest.matching`
Implements the school-matching pipeline via `SchoolMatcher`.
- Maps raw, noisy school names from parliamentary records against the ACARA school register.
- Uses exact alias overrides (`data/reference/school_aliases.json`), normalisation keys, and RapidFuzz token-set scoring.
- Retains unmatched institutions as provisional records (`inst-unmatched-...`) with `confidence='unconfirmed'`.

```python
from apemap.ingest.matching import SchoolMatcher

matcher = SchoolMatcher()
match = matcher.match("Sydney Grammar School")
print(
    f"Matched: {match.school_name} (ACARA ID: {match.acara_id}, Sector: {match.sector}, Confidence: {match.confidence})"
)

unmatched = matcher.match("Eton College, Berkshire")
print(
    f"Unmatched: {unmatched.school_name} (ID: {unmatched.institution_id}, International: {unmatched.is_international})"
)
```

### `apemap.ingest.aph` & `apemap.ingest.acara`
- `apemap.ingest.aph`: Handles retrieval, local disk caching (`data/raw/aph/individuals.json`), and structured parsing of biographical records from the APH Handbook API.
- `apemap.ingest.acara`: Handles downloading and reading official ACARA School Location and Longitudinal School Profile workbooks, isolating 2021 financial data into `school_finances_2021`.

---

## 4. API Stability and Extension Guidelines

- **Stable Surface**: `apemap.__init__` exports, DuckDB canonical table schemas, and views (`v_parliament_members`, `v_member_secondary_education`). Downstream scripts, Dash applications, and external tools should rely on these.
- **Internal / Implementation Details**: Helper functions in `apemap.ingest.*` (e.g. `_parse_terms`, `_extract_candidates`) are internal implementation details subject to refactoring as upstream government APIs evolve.
