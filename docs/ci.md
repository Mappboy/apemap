# Continuous Integration (CI) Architecture

This document describes the offline continuous integration (CI) architecture for APEMAP. Setup downloads pinned dependencies, input assets and the DuckDB spatial extension; data processing, tests and release verification then run offline.

---

## 1. CI Principles

1. **Deterministic Offline Execution**: CI runs in strict offline mode with `APEMAP_OFFLINE=1`. Network calls to external services (APH Handbook, ACARA, AEC, Wikimedia, etc.) are strictly disallowed and fail immediately if attempted.
2. **Pinned Input Verification**: All upstream source datasets required to build the database are tracked in `data/inputs-manifest.json` with SHA-256 hashes and file sizes. Preflight verification ensures zero tampering or input corruption before pipeline execution.
3. **Automated Quality Gates**: Static code analysis, formatting, type checking, dependency auditing, and pre-commit checks run on every push and pull request.
4. **Immutable Release Contract Verification**: Release artifacts are built and audited for schema compliance, coordinate bounds, and privacy enforcement before any publication.

---

## 2. CI Jobs Overview

Both CI data jobs and the release workflow provision inputs before preflight:

```bash
APEMAP_OFFLINE=0 uv run apemap inputs restore
uv run python -c "import duckdb; duckdb.connect().execute('INSTALL spatial; LOAD spatial')"
uv run apemap inputs verify
```

Restoration downloads the release URL pinned in `data/inputs-manifest.json`,
checks the archive SHA-256 before extraction, rejects traversal, links and
special members, and checks the exact six-file inventory and individual bytes
before copying into ignored `data/raw/`. It never overwrites tracked inputs.
Scoped `.gitattributes` rules force manifested CSV/JSON inputs to LF on Windows
and Unix; hashes and sizes describe those checkout bytes. Spatial installation
belongs to setup because offline preflight only loads an existing extension.

The CI workflow is configured in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) and comprises four specialized jobs:

```text
GitHub Actions CI Pipeline
├── 1. quality           # Ruff lint/format, ty typecheck, deptry, prek, uv lock check
├── 2. tests             # Pytest unit & integration suites (fully offline)
├── 3. pipeline          # apemap run-all --offline against pinned inputs manifest
└── 4. release-contract  # apemap release build & verify with strict validation assertions
```

### Job 1: `quality` (Code Quality & Dependency Audits)
Executes static analysis and formatting checks:
- **Lockfile Integrity**: `uv lock --check` confirms `uv.lock` is synchronised with `pyproject.toml`.
- **Linting & Formatting**: `uv run ruff check .` and `uv run ruff format --check .` enforce standard code styles.
- **Type Checking**: `uv run ty check apemap/` validates type annotations.
- **Dependency Hygiene**: `uv run deptry apemap` detects unused, missing, or misclassified dependencies.
- **Pre-commit Hooks**: `uv run prek run --all-files` audits end-of-file formatting, trailing whitespace, and file safety.

### Job 2: `tests` (Offline Test Suite)
Runs the test suite across unit and integration categories:
- Pre-verifies cached inputs: `uv run apemap inputs verify`.
- Executes `uv run pytest` with mocked external boundaries and deterministic local fixtures.

### Job 3: `pipeline` (Offline Pipeline Execution)
Exercises the full database build pipeline end-to-end:
```bash
uv run apemap run-all \
  --offline \
  --inputs-manifest data/inputs-manifest.json \
  --db-path /tmp/ci_aped.duckdb \
  --output-dir /tmp/ci_pipeline_out \
  --strict
```
- Validates that reference boundaries, benchmarks, ACARA profiles, and APH cohorts load without internet access.
- Verifies that all relational integrity and coverage assertions pass.

### Job 4: `release-contract` (Release Build & Verify)
Simulates release bundle generation from the newly built database:
```bash
uv run apemap release build \
  --db-path /tmp/ci_release.duckdb \
  --output-dir /tmp/ci_release_bundle \
  --version 0.0.0-ci \
  --strict

uv run apemap release verify /tmp/ci_release_bundle --strict-assertions
```
- Ensures all canonical data tables, web summaries, GeoJSON layers, and analytical outputs build cleanly.
- Verifies manifest hashes, file sizes, coordinate bounding boxes, and absence of private financial data.

---

## 3. Running CI Checks Locally

To replicate CI checks locally before committing or opening a pull request:

```bash
# 1. Synchronize environment
uv sync --all-groups --frozen
uv lock --check

# 2. Quality and lint checks
uv run ruff check .
uv run ruff format --check .
uv run ty check apemap/
uv run deptry apemap
uv run prek run --all-files

# 3. Test suite
APEMAP_OFFLINE=0 uv run apemap inputs restore
uv run python -c "import duckdb; duckdb.connect().execute('INSTALL spatial; LOAD spatial')"
uv run apemap inputs verify
uv run pytest

# 4. Pipeline and release verification
uv run apemap run-all --offline --inputs-manifest data/inputs-manifest.json --strict
uv run apemap release build --version 0.0.0-dev --strict
uv run apemap release verify data/processed/releases/v0.0.0-dev --strict-assertions
```
