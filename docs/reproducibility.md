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
uv sync --group analysis
```

### Step 2: Choose the historical release or standard development pipeline

For a longitudinal release, use [Decisions to a new release](decisions-to-release.md):
verify `data/historical-inputs-manifest.json`, pin a fresh recipe matching the
installed package and current review inputs, then run `release recipe build`
into fresh paths. It consumes historical ACARA 2008–2025 and 2024 funding context.
The standard input archive and `run-all` below are a different development
workflow; they do not provision or reproduce the historical register.

The six APH/AEC cache files under `data/raw/` are ignored by Git. Download the
pinned [input release](https://github.com/Mappboy/apemap/releases/tag/inputs-20261002)
and install spatial during online setup:

```bash
uv run apemap inputs restore
uv run python -c "import duckdb; duckdb.connect().execute('INSTALL spatial; LOAD spatial')"
uv run apemap inputs verify
```

The archive SHA-256 is checked before safe extraction; its exact file inventory,
hashes and sizes are checked before copying. An offline workstation can use
`uv run apemap inputs restore --archive /path/to/apemap-inputs-20261002.tar.gz`.
Tracked input CSV/JSON files use LF bytes on every platform. Source snapshots
are preserved without a refresh; original raw retrieval times were not recorded.

Execute the complete pipeline across supported Parliaments (42–48):
```bash
uv run apemap run-all --offline --inputs-manifest data/inputs-manifest.json --strict
```

This single command will:
1. Load ACARA school registers and isolate historical 2021 finances.
2. Ingest APH member biographies and match institutions using local caches.
3. Apply canonical schema DDL, views, and macros.
4. Run relational and coverage validation checks with `--strict` enforcement.
5. Export Parquet files, GeoJSON layers, and `analysis_report.json`.

### Step 3: Run Validation Integrity Gate
Confirm that the database passes all structural constraints:
```bash
uv run apemap validate --strict
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
| `apemap ingest acara` | `--download` | No by default | Use `--no-download` for local files |
| `apemap ingest aph` | Reads local cache `data/raw/aph/individuals.json` | With populated cache | On cache misses or `--refresh` |
| `apemap ingest wikimedia` | Reads local cache `data/raw/wikimedia/` | With populated cache | On cache misses or `--refresh` |
| `apemap transform` | Applies SQL files locally | Yes | Never |
| `apemap validate` | Queries DuckDB locally | Yes | Never |
| `apemap analyze` | Queries DuckDB locally | Yes | Never |
| `apemap export` | Writes local Parquet & GeoJSON | Yes | Never |
| Notebooks (`notebooks/*.ipynb`) | Read-only queries to `aped.duckdb` | Yes | Never |

### Why pinned offline builds use local cache
Official government endpoints (such as `handbookapi.aph.gov.au` or the ACARA portal) can update their records, alter biographical wording, or become temporarily unavailable. Upstream Wikimedia queries can also reflect crowdsourced edits over time.
APH/AEC raw payloads are distributed in the pinned input release; curated aliases
are tracked in `data/reference/review/decisions.jsonl`. Optional Wikimedia responses
under `data/raw/wikimedia/` are local caches and are not part of this release.
The strict offline pipeline uses the manifested sources without refreshing them.
Automated tests block live HTTP requests and use local inputs or mocked fixtures.

When an intentional dataset update is desired, supply `--refresh` and `--download` to incorporate live upstream changes.

### Preservation of review decisions across reruns

The authoritative [review log](review-decisions.md) preserves immutable decisions
independently of generated CSV queues. Ordinary ingestion applies its effective
projection; replaying the same sources and log is idempotent. Existing annotated
CSVs are preserved for an explicit validated import rather than silently adopted.
Static upstream manifests exclude the mutable ledger and registry. Ingestion and
release metadata separately identify the exact consumed review revision and source
fingerprint, so appending a decision does not invalidate unchanged source pins.

---

For a new release after decisions are saved, follow
[Decisions to a new release](decisions-to-release.md). Reproducing an older
release requires its recorded source commit and consumed ledger, not the latest
working log. The [0.3.3 record](releases/0.3.3/README.md) records the previous
historical delivery. The [0.6.1 local record](releases/0.6.1/README.md) records the
earlier frozen local build. Historical releases use the committed recipe through `apemap release
recipe build` in both local/manual workflows and Actions. The recipe records source
years, parliament selection, finance mode/year, package/contracts and exact source
and review hashes. Use `release recipe compare` on two strictly verified builds at
the same source commit and dataset version to require matching metadata and all
payload hashes. See [Decisions to a new release](decisions-to-release.md) for recipe
pinning and historical input-bundle provisioning.

## 4. Supported Parliaments

APEMAP supports the 42nd through 48th parliaments. Fixed opening dates,
historical cohort denominators and documented upstream anomalies are described
in the [historical coverage guide](historical-coverage.md). Canonical dates and
seat expectations live in `apemap/constants.py`.

Any request to process an unsupported parliament number will be rejected at the CLI boundary with an informative message.
