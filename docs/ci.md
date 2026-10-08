# Continuous Integration (CI) Architecture

This document describes the offline continuous integration (CI) architecture for APEMAP. Setup downloads pinned dependencies, input assets and the DuckDB spatial extension; data processing, tests and release verification then run offline.

---

## 1. CI Principles

1. **Deterministic Offline Execution**: CI runs in strict offline mode with `APEMAP_OFFLINE=1`. Network calls to external services (APH Handbook, ACARA, AEC, Wikimedia, etc.) are strictly disallowed and fail immediately if attempted.
2. **Pinned Input Verification**: All upstream source datasets required to build the database are tracked in `data/inputs-manifest.json` with SHA-256 hashes and file sizes. Preflight verification ensures zero tampering or input corruption before pipeline execution.
3. **Automated Quality Gates**: Static code analysis, formatting, type checking, dependency auditing, and pre-commit checks run on every push and pull request.
4. **Immutable Release Contract Verification**: Release artifacts are built and audited for schema compliance, coordinate bounds, and privacy enforcement before any publication.
5. **Review Authority Validation**: The tracked decision ledger is validated separately from upstream source pins. Base-branch events retain their exact serialized bytes; ingestion and releases capture the consumed ledger revision.

---

## 2. CI Jobs Overview

CI data jobs provision standard inputs before preflight:

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

Publication in `release.yml` uses `apemap release recipe inputs-restore` and
`apemap release recipe build` with the same committed historical recipe used
locally. It requires an approved historical input-bundle URL: the standard asset
above does not include longitudinal ACARA. Every restored raw file is checked
against the recipe's pinned manifest before copying. The workflow repeats the
build, compares metadata and artifact hashes, strictly verifies privacy/integrity,
and uses the deterministic Python packager. See
[the release path](decisions-to-release.md) for pinning/provisioning and trigger inputs.

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
- **Review Ledger**: `uv run apemap review check` validates decisions; `review check --base <base-sha>` rejects changes to prior events while permitting independent appends.
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
uv sync --all-groups --extra review-ui --frozen
uv lock --check

# 2. Quality and lint checks
uv run apemap review check
uv run apemap review check --base main
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
uv run apemap run-all --offline --inputs-manifest data/inputs-manifest.json \
  --db-path data/raw/local-ci/aped.duckdb --output-dir data/raw/local-ci/processed --strict
uv run apemap release build --db-path data/raw/local-ci/aped.duckdb \
  --output-dir data/raw/local-ci/release --version 0.0.0-dev --strict
uv run apemap release verify data/raw/local-ci/release --strict-assertions
```

## 4. Fresh-checkout verification (2026-10-02)

The [CI run at `0a19257`](https://github.com/Mappboy/apemap/actions/runs/36950069975)
passes all four jobs on fresh Ubuntu runners with Python 3.12. Setup restores
the [published six-file input bundle](https://github.com/Mappboy/apemap/releases/tag/inputs-20261002)
and installs spatial before any offline preflight. Its 46,242,386-byte archive has
SHA-256 `aba83a54dfcabc2cd889c2756e3ffe50cce240adc5704f62a15955216378db56`;
the manifest pins this bundle digest separately from the AEC ZIP digest.

A separate Windows clone with `core.autocrlf=true` verifies that all manifested
tracked text uses LF. It starts with exactly six missing raw inputs and an empty
spatial cache. After downloading the pinned bundle, manifest verification passes;
offline preflight correctly fails until spatial is installed. Setting
`APEMAP_DUCKDB_EXTENSION_DIR` to an absolute, isolated directory makes that cache
test independent of the workstation's existing DuckDB installation. The offline
full suite, strict pipeline and release build/verification then pass. The pipeline
passes 65 database checks; the CI baseline bundle passes 185 release checks over
42 files. The optional test requiring an existing `data/aped.duckdb` is skipped in
clean checkouts; pipeline validation exercises the newly built database.

Ruff, scoped `ty check apemap/`, dependency checks, lockfile checks and repository
hooks pass. Repository-wide `uv run ty check` still reports 157 existing diagnostics
in legacy code, preserved archives and existing tests; CI uses the package scope.
The larger, separately pinned historical report bundle retains its original
213-check verification and is not regenerated by this setup fix.
