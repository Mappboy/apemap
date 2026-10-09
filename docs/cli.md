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
│   ├── finances  # Ingest authorised school finance records
│   ├── funding   # Ingest ACARA benchmarks & jurisdictional public funding
│   ├── benchmarks# Ingest ABS education sector benchmarks
│   ├── aec       # Ingest electoral boundaries from the pinned AEC cache
│   └── wikimedia # Enrich members & review unmatched schools via Wikimedia
├── inputs
│   ├── restore   # Restore and verify pinned APH/AEC inputs
│   └── verify    # Audit cached source files against inputs manifest
├── review        # Authoritative queues, decisions, history, preview and replay
├── transform     # Initialize DuckDB schema, canonical views, and table macros
├── validate      # Check DB relational integrity, constraints, & coverage gates
├── backtest-benchmarks # Empirical backtesting of finance benchmarks
├── analyze       # Compute demographic, sector, and school funding summaries
├── export        # Export Parquet tables, GeoJSON layers, & analysis JSON
├── release
│   ├── build     # Build immutable dataset release bundle
│   ├── verify    # Read-back verify manifest, checksums, bounds, privacy
│   ├── diff      # Compare changes between two dataset releases
│   └── recipe    # Pin, provision, build and compare historical releases
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
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Comma- or space-separated parliament numbers (e.g. `'46,47,48'`). |
| `--refresh` | `BOOL` | `False` | Force re-fetching from live APH Handbook API instead of local disk cache. |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--export-parquet` / `--no-export-parquet` | `BOOL` | `True` | Export updated canonical tables to Parquet files. |
| `--output-dir` | `PATH` | `data/processed` | Directory for exported Parquet, CSV, and JSON files. |

### Pipeline Behavior
- **Network Access**: Only when `--refresh` is supplied or `data/raw/aph/individuals.json` does not exist locally.
- **Database Mutation**: Yes (modifies `members`, `parliament_service`, `institutions`, `member_education`).
- **Inputs**: `data/raw/aph/individuals.json` (or live APH API), `data/reference/review/decisions.jsonl`, ACARA reference registers.
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
| `--finance-file` | `PATH` | `None` | Path to local authorised school finances CSV or Parquet file. |
| `--finance-year` | `INT` | `2021` | Reporting calendar year for school finances. |
| `--export-parquet` / `--no-export-parquet` | `BOOL` | `True` | Export updated canonical tables to Parquet files. |
| `--output-dir` | `PATH` | `data/processed` | Directory for Parquet exports. |

### Pipeline Behavior
- **Network Access**: Yes if `--download` is specified; No if `--no-download` is specified (zero scraping of myschool).
- **Database Mutation**: Yes (populates `institutions`, `school_snapshots`, `school_finances`).
- **Inputs**: Excel or CSV files in `data/external/` (or downloaded from ACARA portal, or authorised finance file).
- **Outputs**:
  - `data/aped.duckdb`
  - `data/processed/institutions.parquet`
  - `data/processed/school_snapshots.parquet`
  - `data/processed/school_finances.parquet`
  - `data/processed/school_finances_2021.parquet`

### Example Usage
```bash
# Offline ingestion from existing downloaded files
uv run apemap ingest acara --no-download

# Download fresh official ACARA workbooks and parse longitudinal profiles
uv run apemap ingest acara --download --longitudinal

# Ingest authorised school finances from local CSV file
uv run apemap ingest finances -f data/restricted/acara_finances_2024.csv -y 2024
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
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Comma- or space-separated parliament numbers. |
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

### Review workflow

Generated CSVs are disposable queue views. Use `uv run apemap review --help` to
export queues, record supported decisions, inspect history, validate and replay.
All mutations append events to `data/reference/review/decisions.jsonl`; ordinary
ingestion replays accepted corrections. Existing CSV annotations require an
explicit dry-run import with evidence validation. See [Review decisions](review-decisions.md).

### Example Usage
```bash
# Enrich 48th Parliament using local cache
uv run apemap ingest wikimedia -p "48"

# Force live refresh for all parliaments and output review artifacts
uv run apemap ingest wikimedia --refresh
```

---

## 4. `apemap ingest funding`

Ingests ACARA National Report on Schooling public finance benchmarks and jurisdictional public school funding allocations (NSW RAM, Tasmania SRP, NT Needs-Based Resourcing, Queensland Non-State Recurrent Grants, and reviewed manual disclosures).

### Invocation
```bash
uv run apemap ingest funding [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--benchmarks-file` | `PATH` | `data/reference/acara_school_finance_benchmarks.csv` | Path to ACARA finance benchmarks CSV. |
| `--nsw-file` | `PATH` | `data/reference/nsw_ram_allocations.csv` | Path to NSW RAM allocations CSV. |
| `--tas-file` | `PATH` | `data/reference/tasmania_srp_allocations.csv` | Path to Tasmania SRP allocations CSV. |
| `--nt-file` | `PATH` | `data/reference/nt_school_funding.csv` | Path to Northern Territory school funding CSV. |
| `--qld-file` | `PATH` | `data/reference/qld_non_state_grants.csv` | Path to Queensland non-state recurrent grants CSV. |
| `--manual-file` | `PATH` | `data/reference/manual_school_funding.csv` | Path to manual school funding CSV. |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `--export-parquet` / `--no-export-parquet` | `BOOL` | `True` | Export updated canonical tables to Parquet files. |
| `--output-dir` | `PATH` | `data/processed` | Directory for exported Parquet files. |

### Pipeline Behavior
- **Network Access**: None (operates deterministically from local reference datasets or user-specified CSV files).
- **Database Mutation**: Yes (populates `school_finance_benchmarks` and `school_public_funding`).
- **Inputs**: Reference CSV files under `data/reference/`.
- **Outputs**:
  - `data/aped.duckdb` (canonical tables populated)
  - `data/processed/school_finance_benchmarks.parquet`
  - `data/processed/school_public_funding.parquet`

### Example Usage
```bash
# Ingest all funding streams using canonical reference datasets
uv run apemap ingest funding

# Ingest custom NSW RAM allocation file into test database
uv run apemap ingest funding --nsw-file path/to/nsw_ram_custom.csv --db-path data/test.duckdb
```

---

## 5. `apemap transform`

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
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Comma- or space-separated parliament numbers to validate. |
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

## 7. `apemap backtest-benchmarks`

Empirically evaluates the ACARA benchmark estimation methodology against historical observed school finances, reporting median actual/benchmark ratio, Median Absolute Deviation (MAD), Median Absolute Percentage Error (MAPE), and identifying high-dispersion peer groups that fall back to peer-group averages.

### Invocation
```bash
uv run apemap backtest-benchmarks [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `-y`, `--year` | `INT` | `2021` | Historical calendar year for backtesting. |
| `-m`, `--metric` | `TEXT` | `total_net_recurrent_income_per_student` | Finance metric to backtest. |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |

### Pipeline Behavior
- **Network Access**: None.
- **Database Mutation**: None (read-only queries).
- **Inputs**: `data/aped.duckdb`.
- **Outputs**: Rich console tables of overall, sector, and peer-group backtesting statistics and high-dispersion flags.

### Example Usage
```bash
# Backtest net recurrent income across 2021 historical actuals
uv run apemap backtest-benchmarks

# Backtest total gross income
uv run apemap backtest-benchmarks --metric total_gross_income_per_student
```

---

## 8. `apemap analyze`

Computes deterministic demographic summaries (average/median age at opening day), secondary school sector distributions (unique MPs vs. attendance instances), and historical 2021 funding averages.

### Invocation
```bash
uv run apemap analyze [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Comma- or space-separated parliament numbers to analyze. |
| `--output-dir` | `PATH` | `data/processed` | Directory to save `analysis_report.json`. |
| `--finance-year` | `INT` | `2021` | Calendar reporting year for school finances. |

### Pipeline Behavior
- **Network Access**: None.
- **Database Mutation**: None (read-only queries).
- **Inputs**: `data/aped.duckdb`.
- **Outputs**:
  - `data/processed/analysis_metrics.json`
  - Formatted Rich console tables for demographics, sectors, and funding.

### Example Usage
```bash
uv run apemap analyze -p "46,47,48"
uv run apemap analyze --finance-year 2024
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
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Comma- or space-separated parliament numbers. |
| `--output-dir` | `PATH` | `data/processed` | Output directory for exported files. |
| `--finance-year` | `INT` | `2021` | Calendar reporting year for school finances in analytical reports. |
| `--web-release` / `--no-web-release` | `BOOL` | `False` | Also generate the website bundle and manifest. |
| `--data-release-version` | `TEXT` | `0.2.0` | Data version recorded in the web manifest. |
| `--source-commit` | `TEXT` | APEMAP checkout HEAD or `unknown` | Explicit source SHA for packaged/archive builds. |
| `--source-snapshot-dates` | `PATH` | `None` | JSON object mapping source names to recorded `YYYY-MM-DD` dates or null. |

### Pipeline Behavior
- **Network Access**: None.
- **Database Mutation**: None (read-only queries).
- **Inputs**: `data/aped.duckdb`.
- **Outputs**:
  - `data/processed/{members,parliament_service,institutions,member_education,school_snapshots,school_finances,school_finances_2021}.parquet`
  - `data/processed/parliament_{46,47,48}_combined.geojson`
  - `data/processed/analysis_metrics.json`

### Example Usage
```bash
uv run apemap export --parliament "46,47"
uv run apemap export --finance-year 2024
uv run apemap export --web-release --data-release-version 2026.09.30 \
  --source-snapshot-dates source-dates.json \
  --output-dir data/processed/releases/2026.09.30
```

With `--web-release`, the output also includes `results-summary.json`,
`schools.geojson`, `downloads/parliament-education.csv`,
`downloads/school-profiles.parquet`, and `manifest.json`. See the
[website release contract](web-release.md) for cohort, row grain, service context,
source years and provenance. Source dates are null when not recorded; the
generation timestamp does not substitute for a source date.

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
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Comma- or space-separated parliament numbers. |
| `--download` / `--no-download` | `BOOL` | `False` | Download fresh ACARA datasets from portal; default uses local ACARA files. |
| `--refresh` / `--no-refresh` | `BOOL` | `False` | Force refresh from live APH API (default is cached). |
| `--longitudinal` / `--single-year` | `BOOL` | `True` | Use ACARA longitudinal profiles or single-year. |
| `--strict` / `--no-strict` | `BOOL` | `True` | Exit immediately if validation check fails. |
| `--enrich-wikimedia` / `--no-enrich-wikimedia` | `BOOL` | `False` | Enrich canonical members and unmatched schools with Wikimedia data. |
| `--finance-year` | `INT` | `2021` | Calendar reporting year for school finances in analytical reports. |
| `--offline` / `--no-offline` | `BOOL` | `False` | Run pipeline in strict offline mode using verified local inputs with zero network requests. |
| `--inputs-manifest` | `PATH` | `data/inputs-manifest.json` | Path to inputs manifest file for offline verification. |

### Example Usage
```bash
# Development run using local cache when present
uv run apemap run-all

# Strict offline run validating inputs against manifest
uv run apemap run-all --offline --inputs-manifest data/inputs-manifest.json

# Complete upstream refresh with live downloads and strict validation
uv run apemap run-all --download --refresh --strict

# Run end-to-end with Wikimedia identity enrichment enabled
uv run apemap run-all --enrich-wikimedia
```

---

## 9. `apemap inputs verify`

Verifies local cached source input files against the canonical cryptographic manifest (`data/inputs-manifest.json`).

### Invocation
```bash
uv run apemap inputs verify [OPTIONS]
```

### Options
| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--manifest`, `-m` | `PATH` | `data/inputs-manifest.json` | Path to input files manifest JSON. |

### Example Usage
```bash
# Verify integrity of all cached source files
uv run apemap inputs verify
```

---

## 10. `apemap release`

Expanded dataset release commands for building, verifying, and diffing immutable, versioned dataset releases.

### 10.1. `apemap release build`
Builds complete, validated dataset release bundle containing web layers, analytical metrics, canonical data tables, `manifest.json`, and `SHA256SUMS`.

```bash
uv run apemap release build [OPTIONS]
```

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--db-path` | `PATH` | `data/aped.duckdb` | Path to DuckDB database file. |
| `-o`, `--output-dir` | `PATH` | `data/processed/release-v<version>` | Destination directory for release bundle; use an explicit fresh path. |
| `-v`, `--version` | `TEXT` | `1.0.0` | Semantic release version string. |
| `-p`, `--parliament` | `TEXT` | `"42,43,44,45,46,47,48"` | Parliaments to include. |
| `--finance-year` | `INT` | `2021` | Calendar reporting year for school finances. |
| `--strict` / `--no-strict` | `BOOL` | `True` | Halt with non-zero exit code if validation fails. |

### 10.2. `apemap release verify`
Verifies integrity, bidirectional inventory, coordinate bounds, and privacy compliance of a release bundle.

```bash
uv run apemap release verify <release-dir> [OPTIONS]
```

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--strict-assertions` / `--no-strict-assertions` | `BOOL` | `False` | Enforce that database assertions report passed in `web/assertions.json`. |

### 10.3. `apemap release diff`
Generates a comparative diff report between two release dataset bundles.

```bash
uv run apemap release diff <old-release-dir> <new-release-dir> [OPTIONS]
```

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--json` | `BOOL` | `False` | Output diff report as formatted JSON. |

---

## 11. `apemap review`

Review commands share the append-only decision log with the optional local GUI.
Generated queues are disposable views. See [Review Decisions](review-decisions.md)
for evidence requirements, explicit CSV imports and migration lineage.

```powershell
uv run apemap review build --db-path data/aped-review.duckdb
uv run apemap review --db-path data/aped-review.duckdb list
uv run apemap review show <review-id>
uv run apemap review history <review-id>
uv run apemap review check
uv run apemap review readiness --parliament 47 --parliament 48
uv run apemap review import --source <annotated-csv> --reviewer <name>
uv run apemap review --db-path data/aped.duckdb --external-dir data/raw/historical/acara serve
```

The group accepts `--log-path`, `--db-path` and `--external-dir` before the
subcommand. CSV import defaults to a dry run. School proposals, including imports
and supersessions, require `--preview-out <fresh.json>` followed by
`apply-preview <fresh.json> --approve <printed-sha256>`; inspect the affected
assertions before approving. `--apply` alone cannot save school imports. Use
`accept`, `reject`, `research` or `supersede` to append a decision and
`add-education` or `add-institution` to create an assertion or registry entry.
Each command's `--help` describes its payload and evidence arguments.

`review build` ingests cached APH records and regenerates candidate queues with
the selected register. It does not load the complete historical ACARA profile,
funding and release pipeline. For full historical ingestion, use `release recipe
build`; review its populated database directly with `serve`, `show` or `list`.

Optional `serve --research-config docs/research-providers.example.json` enables
the provider selector. `readiness` reports advisory analytical impact; it does not
approve decisions or publication. See [assisted research and readiness](review-assisted-search.md)
for provider setup, inspection and retention limits.

Use `map-education <education-review-id> --institution-ref acara:ID
--relationship-type direct --source <url> --dry-run` to preview an
assertion-specific resolution without replacing attendance facts. Remove
`--dry-run` to append it, or use `supersede --replacement-action map` when
replacing an existing decision. Relationships also support `alias`, `rename`
and `successor`. `accept --relationship-type` is supported for education
corrections with an explicit target; omitting that field preserves legacy
matching-default semantics. `check` reports relationship disagreements as
diagnostics, allowing reviewed assertions to override school defaults.
See [Assertion-level resolution](assertion-resolution.md) for JSON examples,
research precedence and migration compatibility. See
[retained evidence and scoring](review-evidence.md) for `retain-evidence`,
`evidence`, `rank-education`, and mapping payload `evidence_refs`.

`research --payload` can record `resolution_reason: ambiguous_name` or
`no_suitable_candidate`. School payloads use
`requires_individual_resolution: true`; named education payloads use
`resolution_only: true`. These preserve attendance while withholding unreviewed
school identities. School queues, status summaries and generated candidate exports
derive `needs_individual_review` or `resolved_individually` from the active
assertions; event history retains its action-based status. See
[Resolve a shared name individually](assertion-resolution.md#resolve-a-shared-name-individually).

Successor school payloads also support a separately sourced original institution,
historical campus and broad/detailed sector evidence. Use `accept --payload`
with a JSON file or the guided school form; preview the school-wide scope before
saving. See [Original schools and reviewed successors](successor-context.md)
for fields, verification rules and the effect on exports and analysis.


## Pinned historical release recipes

`uv run apemap release recipe --help` lists the shared local/Actions release path.
Every build requires an explicit recipe, unused dataset version, fresh database
and output directory.

The retained template is pinned to package 0.5.0. Pin a fresh recipe for the
installed package (0.7.0 on this branch) and pass its path to `--recipe`; see
[Assertion-level resolution](assertion-resolution.md#legacy-defaults-and-migration)
for the pinning command. Preserve existing recipe and dataset version pins.

```bash
uv run apemap release recipe pin --template data/release-recipes/historical.json --output data/release-recipes/new-candidate.json
uv run apemap release recipe build --recipe data/release-recipes/new-candidate.json --version 0.6.2-rc.1 --db-path data/aped-new-candidate.duckdb --output-dir data/processed/releases/new-candidate
```

Dataset versions follow SemVer 2.0, including optional prerelease and build
metadata (for example `0.4.0-rc.1+review.2`). Numeric prerelease identifiers cannot
have leading zeroes, and identifiers cannot be empty.

- `recipe pin --template <recipe> --output <fresh-recipe>` freezes current source
  manifest and review hashes and package version, preserving analytical choices.
  Validation finishes before the new recipe is written; failed validation leaves
  no output file.
- `recipe inputs-bundle --recipe <recipe> --archive <fresh.tar.gz>` packages verified
  raw inputs deterministically without publishing them.
- `recipe inputs-restore --recipe <recipe> --archive <local-bundle>` provisions offline;
  use `--archive-url <https-url>` instead for online setup. Every archived file must
  match the pinned manifest inventory, size and SHA-256. Remote restoration fails
  when `APEMAP_OFFLINE=1`; a local archive remains available. Recipe paths and
  restoration targets must stay within the checkout and contain no symlinks.
- `recipe compare <first-release> <second-release>` strictly verifies both bundles
  and requires identical effective metadata and artifact hashes at the same source
  commit and dataset version.

The historical-v1 recipe supports ACARA location 2025, profiles 2008–2025, latest
available profiles, finance analysis 2024, pinned legacy finance 2021 and ABS/AEC 2025. All fields
are required; unsupported years fail instead of falling back. The build manifest
and web metadata record its effective configuration and copied review revision.
See [Decisions to a new release](decisions-to-release.md) for review and publication.

Ingestion commands `ingest aph`, `ingest acara`, `ingest wikimedia` and `run-all`
accept `--decision-log <path>` for explicit review authority. Omitting it retains
production's working-ledger default; fixture tests pass a private empty or pinned
ledger explicitly. An explicit path must be an existing, readable file; missing
paths and directories fail before ingestion. Use an existing empty file when an
empty review authority is intended.
