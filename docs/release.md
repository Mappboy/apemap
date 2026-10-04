# Dataset Release System & Immutability

This document defines the release architecture, artifact layout, privacy auditing, and publishing lifecycle for immutable APEMAP dataset releases.

---

## 1. Principles of Immutable Releases

1. **Deterministic & Self-Contained**: Every release bundle contains all web layers, analytical metrics, canonical data tables, a signed inventory manifest (`manifest.json`), and cryptographic checksums (`SHA256SUMS`).
2. **Read-Back Verification**: Before publication, releases must pass automated read-back verification: file sizes and SHA-256 hashes must match, all files must be accounted for, and validation assertions must report clean passes.
3. **Strict Privacy Enforcement**: Sensitive school financial profile fields (`financial_profile_2021`, `total_gross_income_per_student`, etc.) are strictly excluded from public distribution layers. Verification fails if any restricted column is detected in public CSV or Parquet files.
4. **Publication Discipline**: Releases are only published on push to the `main` branch (either via `data-v*` / `data-*` tags or manual workflow dispatch on `main`). Arbitrary feature branch pushes cannot trigger a release.

---

## 2. Release Directory Layout

Each release bundle is structured as follows:

```text
release-v<version>/
├── manifest.json                  # Top-level release manifest with file hashes and metadata
├── SHA256SUMS                     # Standard UNIX-compatible SHA-256 checksums
├── analysis/                      # Analytical and statistical summary outputs
│   ├── demographics.json          # Chamber and parliament demographic breakdowns
│   ├── education_sectors.json     # Primary & secondary schooling sector distributions
│   ├── party_sectors.json         # Cross-tabulation of political party by school sector
│   ├── shared_schools.json        # Schools attended by multiple parliamentarians
│   ├── cross_parliament.json      # Inter-parliament cohort continuity and trends
│   ├── school_finance.json        # Macro financial distributions (summary only)
│   └── parliament_comparison.json # Multi-parliament cohort comparisons
├── web/                           # Optimized web layers for frontend applications
│   ├── metadata.json              # Release metadata, provenance, and commit hash
│   ├── assertions.json            # Database validation check assertions and status
│   ├── members.json               # Member education profiles grouped by parliament
│   ├── schools.geojson            # GeoJSON Point features for represented schools
│   └── results-summary.json       # Headline metrics and cohort attendance rates
└── data/                          # Canonical public research datasets (CSV & Parquet)
    ├── members.csv / .parquet
    ├── parliament_service.csv / .parquet
    ├── institutions.csv / .parquet
    ├── member_education.csv / .parquet
    ├── school_snapshots.csv / .parquet
    ├── education_sector_benchmarks.csv / .parquet
    ├── school_finance_benchmarks.csv / .parquet
    └── school_public_funding.csv / .parquet
```

---

## 3. CLI Release Commands

The `apemap release` command group provides tools for building, verifying, and comparing release bundles.

### 3.1. Build a Release (`apemap release build`)

Builds the entire release bundle from a canonical DuckDB database:

```bash
uv run apemap release build [OPTIONS]
```

#### Options:
- `--db-path`: Path to DuckDB database (default: `data/aped.duckdb`).
- `-o`, `--output-dir`: Output directory for release (default: `data/processed/releases/v<version>`).
- `-v`, `--version`: Semantic release version string (e.g. `1.0.0`).
- `-p`, `--parliament`: Parliaments to include (default: all supported terms, currently `42,43,44,45,46,47,48`).
- `--finance-year`: Calendar year for school finance analysis (default: `2021`).
- `--strict` / `--no-strict`: Enforce validation gates before building (default: `--strict`).

#### Example:
```bash
uv run apemap release build --version 1.0.0 --parliament "46,47,48" --strict
```

---

### 3.2. Verify a Release (`apemap release verify`)

Executes rigorous read-back verification against an existing release directory:

```bash
uv run apemap release verify <release-directory> [OPTIONS]
```

#### Verification Steps:
1. **Manifest & Checksums**: Ensures `manifest.json` and `SHA256SUMS` exist and are valid.
2. **Bidirectional File Inventory**:
   - Every file declared in `manifest.json` must exist on disk.
   - Every file on disk must be declared in `manifest.json` (no unmanifested or stray files).
   - Every file must be listed in `SHA256SUMS`.
3. **Cryptographic Integrity**: Recomputes SHA-256 hash and verifies exact byte size for every file.
4. **GeoJSON Geometry & Bounds**: Verifies `schools.geojson` contains valid `Point` coordinates within global coordinate ranges `[-180, 180]` longitude and `[-90, 90]` latitude.
5. **Privacy Audit**: Inspects CSV headers and Parquet schemas in `data/` and `web/` to guarantee no restricted financial columns have leaked.
6. **Assertions Gate**: When `--strict-assertions` is enabled, checks that `web/assertions.json` records a passing validation status.

#### Example:
```bash
uv run apemap release verify data/processed/releases/v1.0.0 --strict-assertions
```

---

### 3.3. Compare Releases (`apemap release diff`)

Compares two release bundles to identify file additions, removals, modifications, and file size changes:

```bash
uv run apemap release diff <old-release-dir> <new-release-dir> [--json]
```

#### Example:
```bash
uv run apemap release diff data/processed/releases/v1.0.0 data/processed/releases/v1.1.0
```

---

## 4. Release Publishing Workflow

### Unpublished frontend candidates

The [v0.3.1 candidate handoff](releases/0.3.1-candidate/README.md) describes the
historical replay and consumption order. Package a strictly verified local bundle
without publishing or tagging:

```text
uv run python -m apemap.release.package data/processed/releases/0.3.1-candidate --output-dir data/processed/candidates/0.3.1
```

The archive is deterministic across filesystem timestamps and checkout locations.
Existing archives, manifests and checksum files cannot be overwritten; use a
separate output directory for each candidate. `SHA256SUMS.dist` checks the archive;
the bundled `SHA256SUMS` checks individual payloads.

Dataset releases are published automatically via GitHub Actions ([`.github/workflows/release.yml`](../.github/workflows/release.yml)).

### Guardrails:
- **Main Branch Only**: Release jobs verify that the target commit is contained within `main`. If a release tag is pushed on a feature branch, the workflow immediately fails.
- **Automated Verification**: Before publication, the workflow builds the release bundle in an isolated offline environment and runs `apemap release verify --strict-assertions`.
- **Packaging**: The bundle is archived into `apemap-release-v<version>.tar.gz` alongside `manifest.json` and `SHA256SUMS`.
- **GitHub Release**: The artifacts are uploaded to a draft-free GitHub Release tagged as `data-v<version>`.

### Original v0.3.0 publication checkpoint

The historical v0.3.0 archive remains a draft preview, separate from the
[v0.3.1 candidate](releases/0.3.1-candidate/README.md). Its recorded verification
passed **213/213 checks across 49 files**. Preserve these original bytes:

| Field | Recorded value |
| --- | --- |
| Archive | `apemap-historical-v0.3.0.tar.gz` |
| SHA-256 | `48efea9e49556d766bf496d857d037b90dc96063b9e0d86a50a38f534fbd97fc` |
| Data source commit | `19dcd581081d87cbe32eaa4ed3c0a9906cb5d5c6` |

Before a maintainer publishes this preview, confirm the release tag and recorded
source commit satisfy the main-branch publication policy, download the existing
draft asset, check its archive hash, and run strict read-back verification on the
extracted bundle. Confirm the same 49-file inventory and all 213 checks pass.
After publication, verify that the public asset downloads with the same archive
hash and that its manifest and checksum inventory still match. Record that
verification in the release notes.

Do not rebuild, replace, retag, or publish v0.3.0 as part of a frontend or
correction-form change. If data or release contents need changing, use a new
version and the normal verified release workflow.

## 5. Public Data Corrections

Use the [data correction form](https://github.com/Mappboy/apemap/issues/new?template=data-correction.yml)
to report a school, parliamentarian, or attendance correction, including a
missing school. It requests the entity identity, dataset release, parliament,
claimed correction, supporting source URL, and optional notes. The form applies
the existing `transparency` label; it does not create labels or change data.

The cpoole.dev results page can prefill the release and parliament through the
stable text-field IDs `release_version` and `parliament`, for example:

```text
https://github.com/Mappboy/apemap/issues/new?template=data-correction.yml&release_version=0.3.1&parliament=48
```

Merge the form into the default branch before deploying links that depend on
it. GitHub requires a nonempty source field but does not validate its URL or
verify the claim. Reviewers must check the cited source, identify the canonical
record, and distinguish attendance from graduation before accepting a change.
Use the existing source/review and alias-promotion workflow described in the
[methodology](methodology.md); a submitted report is not verified evidence by
itself.

Record accepted changes in the canonical inputs or reference mappings with
their provenance, then validate and publish a new immutable release. Existing
archives, manifests, and hashes stay unchanged. The public site continues to
show the pinned release until its data version is deliberately updated.

Form syntax and URL prefills follow the
[GitHub form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema).
