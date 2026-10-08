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
uv sync --all-groups

# Install prek Git hook shims
uv run prek install
```

---

## 2. Standard Quality Gates

Before opening a pull request or committing changes, run the full verification suite:

```bash
# 1. Run the complete unit, integration, and notebook execution suite
uv run pytest

# 2. Lint Python source code with Ruff
uv run ruff check .

# 3. Verify code formatting with Ruff
uv run ruff format --check .

# 4. Run static type checking with ty
uv run ty check apemap/

# 5. Audit dependency hygiene with deptry
uv run deptry apemap

# 6. Run all Git hooks with prek
uv run prek run --all-files
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
uv add --group analysis <package-name>

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

`uv run pytest` remains the authoritative complete suite. Use marker selection
for a shorter development loop, then run the full suite before a PR:

```bash
# Fast development loop, including static notebook mutation checks
uv run pytest -m "not integration and not notebook and not slow"

# Component, CLI, database migration, spatial, and export integration checks
uv run pytest -m "integration and not notebook"

# All five notebook execution checks (requires uv sync --all-groups)
uv run pytest -m notebook

# Complete suite and timing profile
uv run pytest --durations=25
```

Each test carries an explicit execution category: `unit` for isolated logic with
small local fixtures, or `integration` for multiple components and orchestration.
`notebook` additionally identifies tests that launch Jupyter kernels; these also
carry `slow`. Reserve `slow` for tests whose remaining cost cannot reasonably be
reduced. Markers are registered in `pyproject.toml` and checked strictly. No
category is excluded by default, and parallel execution is not enabled.
The [issue #39 measurements](testing-performance.md) record the original and
updated full-suite and priority-module timings.

Tests stay in domain-oriented files under `tests/`. Keep new fixtures small and
offline; do not ingest the entire research dataset to test orchestration. The
funding orchestration fixture supplies synthetic, source-native CSVs for all six
input types, with exact row counts, validation, and rerun assertions.

`tests/conftest.py` supplies an empty canonical `db_conn` and a
`database_factory`. The CLI, funding, web-release, and Wikimedia tests build
closed seeded templates once per session using `tests/db_fixtures.py`. Each test
receives a private file copy and a connection closed by fixture teardown, even
on failure. Domain seeds stay beside their tests. Never copy an open database
or share a mutable connection between tests. Schema migration tests still build
their original legacy layouts explicitly.

The autouse HTTP guard fails immediately on an unmocked `requests.Session`
request, including clients created by the retry-session helper. Mock clients or
use deterministic files in `tests/fixtures/`; Jupyter's local kernel sockets are
allowed. Spatial checks require an installed DuckDB spatial extension. Provision
that extension before running tests in an environment without network access.

Fixture ingestion must pass `decision_log_path=empty_review_log` (the shared
private empty-ledger fixture), or an explicit temporary/pinned review log when
testing decisions. CLI fixtures use `--decision-log`. An autouse fallback also
redirects ingestion defaults to the private ledger; it is a guard for orchestration
paths, not a replacement for declaring review state in fixture setup. Tests that
exercise review validation deliberately select their own nonempty ledger.

Notebook execution tests read a small temporary on-disk DuckDB fixture and verify
its bytes are unchanged. Static notebook mutation checks remain in the fast
suite. Missing optional notebook dependencies retain pytest's visible skip
report; install the analysis group to exercise the complete notebook coverage.

DuckDB's Python timestamp conversion requires the explicit `pytz` runtime
dependency. The ABS provenance regression fetches a `TIMESTAMPTZ` value to cover
that requirement, which pandas 3 no longer supplies transitively.

---

## 8. Generated Data Policy

- Do not commit large binary artifacts or temporary database files.
- Inspect `git status` and `git diff` before committing to ensure no unintended output files or secret credentials are staged.
- The canonical database `data/aped.duckdb` can be rebuilt deterministically from source at any time with `uv run apemap run-all`.
