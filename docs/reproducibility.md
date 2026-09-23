# Reproducibility Guide

APEMAP is designed so that any researcher or auditor can clone the repository and reproduce canonical database tables, analytical reports, and spatial exports deterministically.

---

## 1. Principles of Reproducibility in APEMAP

APEMAP guarantees reproducibility through four structural architectural practices:

1. **Pinned Dependency Lockfile**: Dependencies, sub-dependencies, and wheel hashes are strictly pinned in `uv.lock`.
2. **Network-Isolated Downstream Stages**: Ingestion of data (`ingest`) is decoupled from transformation (`transform`), validation (`validate`), analysis (`analyze`), export (`export`), and notebooks. Once raw inputs are present, the rest of the pipeline executes 100% offline.
3. **Deterministic SQL Aggregation**: Analytical metrics are computed through standardized SQL queries against canonical DuckDB views, avoiding divergent calculations across different notebooks or scripts.
4. **Automated Validation Gates**: A strict validation suite enforces data integrity, key uniqueness, and benchmark coverage counts before artifacts can be considered valid.

---

## 2. Complete End-to-End Reproduction Workflow

To reproduce all artifacts from a fresh environment:

### Step 1: Clone Repository & Sync Locked Environment
```bash
git clone https://github.com/Mappboy/apemap.git
cd apemap

# Sync pinned runtime dependencies
uv sync

# Sync notebook and analysis optional dependencies
uv sync --extra analysis
```

### Step 2: Run Coordinated Pipeline
Execute the complete pipeline across supported Parliaments (46, 47, 48):
```bash
uv run apemap run-all --parliament "46,47,48" --strict
```

This single command will:
1. Load ACARA school registers and isolate historical 2021 finances.
2. Ingest APH member biographies and match institutions using local caches.
3. Apply canonical schema DDL, views, and macros.
4. Run 11 relational validation checks with `--strict` enforcement.
5. Export Parquet files, GeoJSON layers, and `analysis_report.json`.

### Step 3: Run Validation Integrity Gate
Confirm that the database passes all structural constraints:
```bash
uv run apemap validate --parliament "46,47,48" --strict
```

### Step 4: Verify Notebook Execution
Execute all five canonical analysis notebooks sequentially in a clean kernel environment:
```bash
uv run pytest tests/test_notebook_execution.py
```

---

## 3. Local Caching vs. Upstream Refreshes

To understand reproducibility differences:

| Stage | Default Mode | Offline? | Network Access |
| :--- | :--- | :--- | :--- |
| `apemap ingest acara` | `--no-download` | Yes | Only when `--download` is passed |
| `apemap ingest aph` | Reads local cache `data/raw/aph/individuals.json` | Yes | Only when `--refresh` is passed |
| `apemap ingest wikimedia` | Reads local cache `data/raw/wikimedia/` | Yes | Only when `--refresh` is passed |
| `apemap transform` | Applies SQL files locally | Yes | Never |
| `apemap validate` | Queries DuckDB locally | Yes | Never |
| `apemap analyze` | Queries DuckDB locally | Yes | Never |
| `apemap export` | Writes local Parquet & GeoJSON | Yes | Never |
| Notebooks (`notebooks/*.ipynb`) | Read-only queries to `aped.duckdb` | Yes | Never |

### Why We Default to Local Cache
Official government endpoints (such as `handbookapi.aph.gov.au` or the ACARA portal) can update their records, alter biographical wording, or become temporarily unavailable. Upstream Wikimedia queries can also reflect crowdsourced edits over time.
By checking in cached raw payloads (`data/raw/aph/individuals.json`), curated aliases (`data/reference/school_aliases.json`), and disk caching Wikimedia responses under `data/raw/wikimedia/` (`members/` and `institutions/`), APEMAP guarantees that running `apemap run-all` produces identical outputs today, tomorrow, and years from now. All automated tests run strictly against fixtures and mocks without live network calls.

When an intentional dataset update is desired, supply `--refresh` and `--download` to incorporate live upstream changes.

---

## 4. Supported Parliaments

APEMAP currently defines fixed opening dates and benchmark seat parameters for three parliaments:

| Parliament | Period | Opening Date | Benchmark Seats |
| :--- | :--- | :--- | :--- |
| **46th Parliament** | 2019–2022 | `2019-07-02` | 227 (151 Reps, 76 Senate) |
| **47th Parliament** | 2022–2025 | `2022-07-26` | 227 (151 Reps, 76 Senate) |
| **48th Parliament** | 2025–present | `2025-07-22` | 227 (151 Reps, 76 Senate) |

Any request to process an unsupported parliament number will be rejected at the CLI boundary with an informative message.
