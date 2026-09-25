# CLI Reference

The `apemap` command-line interface provides the primary user-facing workflow for APEMAP. Built on [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/), it offers structured, deterministic execution across all ingestion, transformation, validation, analytical, and export stages.

---

## Global Options

```text
Usage: apemap [OPTIONS] COMMAND [ARGS]...
```

| Option | Description |
| :--- | :--- |
| `--help` | Show top-level help message and exit. |
| `--install-completion` | Install shell tab completion for bash, zsh, fish, or powershell. |
| `--show-completion` | Print shell tab completion code. |

---

## Command Hierarchy

```text
apemap
├── ingest
│   ├── aph       # Ingest parliamentarian demographics & schools from APH
│   ├── acara     # Ingest ACARA school profiles & migrate 2021 finances
│   └── wikimedia # Enrich members & review unmatched schools via Wikimedia
├── transform     # Initialize DuckDB schema, canonical views, and table macros
├── validate      # Check DB relational integrity, constraints, & coverage gates
├── analyze       # Compute demographic, sector, and school funding summaries
├── export        # Export Parquet tables, GeoJSON layers, & analysis JSON
└── run-all       # Coordinated end-to-end pipeline execution
```

---

## 1. `apemap ingest aph`

Ingests parliamentarian biographies, terms of service, and secondary education records from the Australian Parliament House (APH) Parliamentary Handbook API. Extracts school names and matches them against ACARA reference schools.

### Invocation
```bash
uv run apemap ingest aph [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `-p`, `--parliament` | `TEXT` | `"46,47,48"` | Comma- or space-separated parliament numbers (e.g. `'46,47,48'`). |
| `--refresh` | `BOOL` | `False` | Force re-fetching from live APH Handbook API instead of local disk cache. |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--export-parquet` / `--no-export-parquet` | `BOOL` | `True` | Export updated canonical tables to Parquet files. |
| `--output-dir` | `PATH` | `data/processed` | Directory for exported Parquet, CSV, and JSON files. |

### Pipeline Behavior
- **Network Access**: Only when `--refresh` is supplied or `data/raw/aph/individuals.json` does not exist locally.
- **Database Mutation**: Yes (modifies `members`, `parliament_service`, `institutions`, `member_education`).
- **Inputs**: `data/raw/aph/individuals.json` (or live APH API), `data/reference/school_aliases.json`, ACARA reference registers.
- **Outputs**:
  - `data/aped.duckdb`
  - `data/processed/unmatched_schools.csv`
  - `data/processed/coverage_metrics.json`
  - `data/processed/*.parquet` (if enabled)

### Example Usage
```bash
# Ingest 47th and 48th Parliaments using local cache
uv run apemap ingest aph -p "47,48"

# Force live refresh from APH API for 48th Parliament
uv run apemap ingest aph -p "48" --refresh
```

---

## 2. `apemap ingest acara`

Ingests official ACARA school locations and longitudinal profiles. Isolates historical 2021 school finances into a dedicated table.

### Invocation
```bash
uv run apemap ingest acara [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--download` / `--no-download` | `BOOL` | `True` | Download latest official 2025 ACARA datasets from Data Access portal. |
| `--longitudinal` / `--single-year` | `BOOL` | `True` | Download 2008–2025 longitudinal profile workbook or 2025 single-year profile. |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--export-parquet` / `--no-export-parquet` | `BOOL` | `True` | Export updated canonical tables to Parquet files. |
| `--output-dir` | `PATH` | `data/processed` | Directory for Parquet exports. |

### Pipeline Behavior
- **Network Access**: Yes if `--download` is specified; No if `--no-download` is specified.
- **Database Mutation**: Yes (populates `institutions`, `school_snapshots`, `school_finances_2021`).
- **Inputs**: Excel or CSV files in `data/external/` (or downloaded from ACARA portal).
- **Outputs**:
  - `data/aped.duckdb`
  - `data/processed/institutions.parquet`
  - `data/processed/school_snapshots.parquet`
  - `data/processed/school_finances_2021.parquet`

### Example Usage
```bash
# Offline ingestion from existing downloaded files
uv run apemap ingest acara --no-download

# Download fresh official ACARA workbooks and parse longitudinal profiles
uv run apemap ingest acara --download --longitudinal
```

---

## 3. `apemap ingest wikimedia`

Enriches canonical members with Wikidata identifiers (`members.wikidata_id`) using identifier-first matching ([P10020](https://www.wikidata.org/wiki/Property:P10020)), cross-checks demographic attributes (birth dates, gender), and suggests institution names, coordinates, and types for unmatched or international secondary schools.

### Invocation
```bash
uv run apemap ingest wikimedia [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `-p`, `--parliament` | `TEXT` | `"46,47,48"` | Comma- or space-separated parliament numbers. |
| `--refresh` / `--no-refresh` | `BOOL` | `False` | Force re-fetching live from Wikimedia API instead of local disk cache. |
| `--members` / `--no-members` | `BOOL` | `True` | Enrich members with Wikidata identifiers and cross-check demographics. |
| `--schools` / `--no-schools` | `BOOL` | `True` | Generate suggestions for unconfirmed and international schools via Wikimedia. |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--output-dir` | `PATH` | `data/processed` | Directory for exported CSV review files. |
| `--cache-dir` | `PATH` | `data/raw/wikimedia` | Directory for raw Wikimedia disk cache. |
| `--timeout` | `INT` | `45` | Timeout in seconds for Wikimedia HTTP requests. |

### Pipeline Behavior
- **Network Access**: Only when `--refresh` is supplied or cached queries are not present on disk in `data/raw/wikimedia/`.
- **Database Mutation**: Updates `members.wikidata_id` if previously unset. Never mutates birth dates, gender, or verified ACARA records.
- **Inputs**: `data/aped.duckdb`, local cache files in `data/raw/wikimedia/{members,institutions}/`.
- **Outputs**:
  - `data/aped.duckdb` (canonical `members.wikidata_id` populated)
  - `data/processed/wikimedia_member_review.csv` (discrepancies, missing supplemental values, conflicts, manual decisions)
  - `data/processed/wikimedia_school_review.csv` (filtered unmatched school suggestions and manual review decisions)

### Review & Promotion Workflow
1. **Inspect Review CSVs**: Reviewers inspect the generated candidate rows in `data/processed/wikimedia_member_review.csv` and `data/processed/wikimedia_school_review.csv`.
2. **Record Decisions Directly**: Set `review_status` to `accepted`, `rejected`, or `needs_research`, and populate resolved attributes (`resolved_school_name`, `resolved_acara_id`, `resolved_wikidata_id`, etc.) and `review_notes`.
3. **Commit Review Decisions**: Commit modified review CSVs to version control. Reviewer decisions and rationale are tracked transparently through Git history.
4. **Promote Accepted School Mappings**: For accepted domestic school resolutions, add the mapping to `data/reference/school_aliases.json`. When the pipeline next runs (`apemap ingest aph`), the alias is matched deterministically via Tier 0.
5. **Idempotent Reruns**: Rerunning `apemap ingest wikimedia` updates generated metadata while strictly preserving existing manual review statuses and notes.

### Example Usage
```bash
# Enrich 48th Parliament using local cache
uv run apemap ingest wikimedia -p "48"

# Force live refresh for all parliaments and output review artifacts
uv run apemap ingest wikimedia --refresh
```

---

## 4. `apemap transform`

Applies canonical relational DDL, analytical views, and parameterized macros to DuckDB. Idempotently migrates historical 2021 school finances from `data/aped.gpkg` if not yet populated.

### Invocation
```bash
uv run apemap transform [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--gpkg-path` | `PATH` | `data/aped.gpkg` | Optional path to legacy GeoPackage for 2021 finance migration. |

### Pipeline Behavior
- **Network Access**: None (strictly offline).
- **Database Mutation**: Yes (creates/replaces views `v_parliament_members`, `v_member_secondary_education`, macros, tables).
- **Inputs**: `apemap/schema/01_schema.sql`, `apemap/schema/02_views.sql`, `data/aped.gpkg` (if migrating finances).
- **Outputs**: Updated views and macros in `data/aped.duckdb`.

### Example Usage
```bash
uv run apemap transform
```

---

## 5. `apemap validate`

Executes automated integrity gates against the canonical database. Verifies primary keys, foreign key referential integrity, non-null requirements, valid sector/chamber enumerations, and benchmark parliament member counts.

### Invocation
```bash
uv run apemap validate [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `-p`, `--parliament` | `TEXT` | `"46,47,48"` | Comma- or space-separated parliament numbers to validate. |
| `--strict` / `--no-strict` | `BOOL` | `True` | Exit with non-zero exit code (1) if any check fails. |

### Pipeline Behavior
- **Network Access**: None.
- **Database Mutation**: None (read-only queries).
- **Inputs**: `data/aped.duckdb`.
- **Outputs**: Rich console summary of table counts, parliament benchmarks, and pass/fail panel.

### Example Usage
```bash
# Standard strict validation (ideal for CI)
uv run apemap validate --strict

# Non-strict run for exploratory inspection
uv run apemap validate --no-strict -p "47"
```

---

## 6. `apemap analyze`

Computes deterministic demographic summaries (average/median age at opening day), secondary school sector distributions (unique MPs vs. attendance instances), and historical 2021 funding averages.

### Invocation
```bash
uv run apemap analyze [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `-p`, `--parliament` | `TEXT` | `"46,47,48"` | Comma- or space-separated parliament numbers to analyze. |
| `--output-dir` | `PATH` | `data/processed` | Directory to save `analysis_report.json`. |

### Pipeline Behavior
- **Network Access**: None.
- **Database Mutation**: None (read-only queries).
- **Inputs**: `data/aped.duckdb`.
- **Outputs**:
  - `data/processed/analysis_report.json`
  - Formatted Rich console tables for demographics, sectors, and funding.

### Example Usage
```bash
uv run apemap analyze -p "46,47,48"
```

---

## 7. `apemap export`

Exports canonical tables to Parquet, creates GeoJSON layers for each parliament joining member details to school coordinates, and writes analytical summary JSON.

### Invocation
```bash
uv run apemap export [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `-p`, `--parliament` | `TEXT` | `"46,47,48"` | Comma- or space-separated parliament numbers. |
| `--output-dir` | `PATH` | `data/processed` | Output directory for exported files. |

### Pipeline Behavior
- **Network Access**: None.
- **Database Mutation**: None (read-only queries).
- **Inputs**: `data/aped.duckdb`.
- **Outputs**:
  - `data/processed/{members,parliament_service,institutions,member_education,school_snapshots,school_finances_2021}.parquet`
  - `data/processed/parliament_{46,47,48}_combined.geojson`
  - `data/processed/analysis_report.json`

### Example Usage
```bash
uv run apemap export --parliament "46,47"
```

---

## 8. `apemap run-all`

Coordinates the complete end-to-end pipeline deterministically:
1. Ingests ACARA school datasets.
2. Ingests APH parliamentarians and matches institutions.
   - Optional step 2b: Enriches members and suggests unmatched schools via Wikimedia (opt-in).
3. Applies schema transformations, views, and macros.
4. Validates database integrity against coverage gates.
5. Exports Parquet tables, GeoJSON layers, and analytical reports.

### Invocation
```bash
uv run apemap run-all [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--output-dir` | `PATH` | `data/processed` | Output directory for all generated artifacts. |
| `-p`, `--parliament` | `TEXT` | `"46,47,48"` | Comma- or space-separated parliament numbers. |
| `--download` / `--no-download` | `BOOL` | `False` | Download fresh ACARA datasets from portal (default is offline). |
| `--refresh` / `--no-refresh` | `BOOL` | `False` | Force refresh from live APH API (default is cached). |
| `--longitudinal` / `--single-year` | `BOOL` | `True` | Use ACARA longitudinal profiles or single-year. |
| `--strict` / `--no-strict` | `BOOL` | `True` | Exit immediately if validation check fails. |
| `--enrich-wikimedia` / `--no-enrich-wikimedia` | `BOOL` | `False` | Enrich canonical members and unmatched schools with Wikimedia data. |

### Example Usage
```bash
# Deterministic offline run using local cache
uv run apemap run-all

# Complete upstream refresh with live downloads and strict validation
uv run apemap run-all --download --refresh --strict

# Run end-to-end with Wikimedia identity enrichment enabled
uv run apemap run-all --enrich-wikimedia
```
