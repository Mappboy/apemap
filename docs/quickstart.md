# Quickstart Guide

This guide walks you through setting up APEMAP, exploring the command-line interface (CLI), working with existing canonical datasets, and running the complete reproducible pipeline.

---

## Prerequisites

- **Python**: Version `>= 3.10` (tested with Python 3.10, 3.11, 3.12).
- **uv**: Modern, fast Python package and environment manager ([Astral uv](https://docs.astral.sh/uv/)).
  Install `uv` if not already present:
  - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

---

## 1. Installation and Environment Setup

Clone the repository and synchronize dependencies into a project virtual environment using `uv`:

```bash
git clone https://github.com/Mappboy/apemap.git
cd apemap

# Sync canonical runtime and development dependencies
uv sync

# (Optional) Sync interactive notebook and analysis dependencies
uv sync --extra analysis
```

`uv` reads `pyproject.toml` and locks exact dependency versions via `uv.lock`.

---

## 2. CLI Discovery

APEMAP provides a unified Typer CLI installed as the console script `apemap`. Verify the installation and inspect available commands:

```bash
uv run apemap --help
```

You will see the top-level commands:
- `ingest aph`: Ingest parliamentarian demographics and secondary education records from APH.
- `ingest acara`: Ingest official ACARA school locations, profiles, and historical finances.
- `transform`: Initialize canonical DuckDB relational schema, views, and macros.
- `validate`: Execute database integrity validation, FK constraints, and parliament coverage gates.
- `analyze`: Compute deterministic demographic, sector distribution, and school funding statistics.
- `export`: Export canonical Parquet files, GeoJSON layers, and JSON analytical metrics.
- `run-all`: Execute end-to-end pipeline deterministically from raw inputs to exported artifacts.

For options specific to any command, append `--help`:

```bash
uv run apemap ingest aph --help
uv run apemap validate --help
```

---

## 3. Using Existing Data vs. Refreshing Upstream Sources

An important distinction exists between working with existing data and refreshing upstream sources:

### A. Working with Local / Existing Data (Default)
The repository includes checked-in reference data (`data/reference/school_aliases.json`), cached APH individual records (`data/raw/aph/individuals.json`), and sample ACARA files (`data/external/`).
By default, commands do **not** trigger live external network downloads:
- `apemap ingest aph` uses `data/raw/aph/individuals.json` if present.
- `apemap ingest acara --no-download` parses local CSV/Excel files in `data/external/`.
- `apemap transform`, `validate`, `analyze`, and `export` operate locally against DuckDB (`data/aped.duckdb`) and are completely offline and deterministic.

### B. Live Upstream Refreshes
When you want to refresh upstream data directly from official government endpoints:
- `--refresh` (APH): Contacts `https://handbookapi.aph.gov.au/api/individuals` to download fresh individual biographies.
- `--download` (ACARA): Downloads the latest official 2025 school profile and location workbooks from ACARA's Data Access Portal.

---

## 4. Running the Pipeline Step-by-Step

You can execute the pipeline incrementally to observe each stage:

### Step 1: Ingest ACARA School Data
Load school registers and isolate historical 2021 school finances into canonical DuckDB:
```bash
uv run apemap ingest acara --no-download
```

### Step 2: Ingest APH Parliamentarians
Extract members and secondary education records for the 46th, 47th, and 48th Parliaments, matching schools against ACARA:
```bash
uv run apemap ingest aph --parliament "46,47,48"
```

### Step 3: Initialize Views and Schema Transforms
Ensure all canonical views (`v_parliament_members`, `v_member_secondary_education`) and macros are current:
```bash
uv run apemap transform
```

### Step 4: Validate Database Integrity
Execute relational foreign-key integrity checks, non-null assertions, and coverage gates:
```bash
uv run apemap validate --parliament "46,47,48" --strict
```

### Step 5: Run Deterministic Analysis
Calculate demographics, attendance instances, sector percentages, and school funding averages:
```bash
uv run apemap analyze --parliament "46,47,48"
```

### Step 6: Export Output Artifacts
Generate Parquet tables, GeoJSON map layers, and metrics JSON:
```bash
uv run apemap export --parliament "46,47,48"
```

---

## 5. Running the Complete Coordinated Pipeline

To run all pipeline stages sequentially in a single command:

```bash
uv run apemap run-all --parliament "46,47,48"
```

To include live network downloads during a full pipeline run:
```bash
uv run apemap run-all --download --refresh --parliament "46,47,48"
```

---

## 6. Locating Generated Outputs

All canonical pipeline outputs are written to predictable locations in `data/`:

- **DuckDB Database**: `data/aped.duckdb`
- **Processed Parquet Tables**:
  - `data/processed/members.parquet`
  - `data/processed/parliament_service.parquet`
  - `data/processed/institutions.parquet`
  - `data/processed/member_education.parquet`
  - `data/processed/school_snapshots.parquet`
  - `data/processed/school_finances_2021.parquet`
- **Spatial GeoJSON Layers**:
  - `data/processed/parliament_46_combined.geojson`
  - `data/processed/parliament_47_combined.geojson`
  - `data/processed/parliament_48_combined.geojson`
- **Analytical Metrics Reports**:
  - `data/processed/analysis_report.json`
  - `data/processed/coverage_metrics.json`
  - `data/processed/unmatched_schools.csv`

---

## 7. Troubleshooting

### Missing Local Source Files
- If `apemap ingest acara --no-download` fails because local CSV or Excel files are missing in `data/external/`, run `uv run apemap ingest acara --download` once with internet access to fetch the official datasets.
- If `apemap ingest aph` fails because `data/raw/aph/individuals.json` is missing, run with `--refresh` to fetch the records from the APH API.

### Custom Database or Output Locations
You can isolate experimental pipeline runs by specifying custom database paths and output directories:
```bash
uv run apemap run-all --db-path /tmp/test.duckdb --output-dir /tmp/test-output/
```

### Validation Failure
If `uv run apemap validate --strict` exits with non-zero status, review the numbered failure reasons in the console output. To inspect the database interactively using Python:
```python
import duckdb

conn = duckdb.connect("data/aped.duckdb", read_only=True)
print(conn.execute("SELECT count(*) FROM members").fetchone())
```

---

## Next Steps

- Explore the [CLI Reference](cli.md) for full argument definitions.
- Read [Methodology](methodology.md) for details on the school-matching algorithm and cohort benchmarks.
- Consult [Python Package](package.md) to integrate APEMAP into your Python applications.
