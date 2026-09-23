# Development & Contributing Guide

This document describes how to contribute to APEMAP, set up a development environment, run automated quality gates, and manage data and schema changes.

---

## 1. Development Environment Setup

APEMAP uses [uv](https://docs.astral.sh/uv/) for Python environment management, dependency resolution, and tool execution.

```bash
# Clone the repository
git clone https://github.com/Mappboy/apemap.git
cd apemap

# Install all development and runtime dependencies
uv sync

# Install analysis dependencies for notebooks
uv sync --extra analysis
```

---

## 2. Standard Quality Gates

Before opening a pull request or committing changes, run the full verification suite:

```bash
# 1. Run unit, integration, and notebook execution tests
uv run pytest

# 2. Lint Python source code with Ruff
uv run ruff check .

# 3. Verify code formatting with Ruff
uv run ruff format --check .

# 4. Run static type checking with ty
uv run ty check
```

To automatically format code with Ruff:
```bash
uv run ruff format .
```

---

## 3. Repository & Source Code Layout

```text
apemap/
├── apemap/                   # Core Python package
│   ├── __init__.py           # Public exports (get_connection, init_schema, build_database)
│   ├── analysis.py           # SQL analytics (demographics, sectors, funding)
│   ├── cli.py                # Typer CLI application
│   ├── constants.py          # Fixed paths, URLs, parliament dates, enums
│   ├── db.py                 # DuckDB connection handling, DDL execution
│   ├── export.py             # Parquet and GeoJSON exporters
│   ├── validate.py           # Integrity checks and validation report
│   ├── ingest/               # Source ingestion subpackage
│   │   ├── aph.py            # APH Handbook API client and parsing
│   │   ├── acara.py          # ACARA portal download & finance migration
│   │   ├── matching.py       # SchoolMatcher (Rapidfuzz, alias overrides)
│   │   └── pipeline.py       # Ingestion orchestration
│   └── schema/               # Canonical SQL files
│       ├── 01_schema.sql     # DDL table creation and constraints
│       └── 02_views.sql      # Canonical views and parameterized macros
├── app/                      # Plotly Dash web application (legacy prototype)
├── data/                     # Data directory (external, raw, reference, processed)
├── docs/                     # Modular Markdown documentation hierarchy
├── notebooks/                # Numbered Jupyter & Marimo analysis notebooks
├── tests/                    # Pytest test suite & deterministic fixtures
└── pyproject.toml            # Hatchling build configuration & dependencies
```

---

## 4. Managing Dependencies

All dependencies are defined in `pyproject.toml` and locked in `uv.lock`. Do not use `pip install` or create ad-hoc virtual environments.

```bash
# Add a runtime dependency
uv add <package-name>

# Add a development-only dependency
uv add --dev <package-name>

# Add an optional analysis dependency
uv add --optional analysis <package-name>

# Remove a dependency
uv remove <package-name>
```

---

## 5. Adding or Modifying CLI Commands

The CLI is structured in `apemap/cli.py` using `typer`.
- Subcommands for data ingestion are grouped under `ingest_app` (`apemap ingest aph`, `apemap ingest acara`).
- Pipeline and transformation commands are registered directly on `app` (`transform`, `validate`, `analyze`, `export`, `run-all`).
- When adding a new option or command:
  1. Add type annotations with `typing.Annotated` and `typer.Option`.
  2. Provide clear, descriptive `help` strings.
  3. Update `docs/cli.md` and verify with `tests/test_docs.py`.

---

## 6. Schema Migrations & DDL Changes

The canonical schema is maintained in `apemap/schema/`:
- `01_schema.sql`: Contains `CREATE TABLE IF NOT EXISTS` statements with explicit primary keys, foreign keys, and column constraints.
- `02_views.sql`: Contains `CREATE OR REPLACE VIEW` and `CREATE OR REPLACE MACRO` statements.

Guidelines for schema evolution:
1. Ensure all new tables or columns support idempotent creation (`IF NOT EXISTS`).
2. Include mandatory audit provenance columns (`source_url`, `retrieved_at`, `confidence`, `reviewer_notes`) on any assertions.
3. Update `apemap.validate.validate_database()` with corresponding integrity checks.
4. Update `docs/data-model.md` and Mermaid diagrams.

---

## 7. Writing Tests & Working with Fixtures

Tests live in `tests/` and use `pytest`:
- **Deterministic Fixtures**: Small, offline sample fixtures are kept in `tests/fixtures/` (`aph_sample.json`, `acara_sample.json`, `acara_profile_sample.csv`).
- **No Live Network Calls**: Unit and regression tests must **never** make live network requests to APH, ACARA, or other external services. Mock HTTP clients or use local file fixtures.
- **Notebook Tests**: `tests/test_notebook_execution.py` tests all notebooks top-to-bottom against an in-memory DuckDB database to verify they run cleanly without network access or disk mutation.

---

## 8. Generated Data Policy

- Do not commit large binary artifacts or temporary database files.
- Inspect `git status` and `git diff` before committing to ensure no unintended output files or secret credentials are staged.
- The canonical database `data/aped.duckdb` can be rebuilt deterministically from source at any time with `uv run apemap run-all`.
