# Dataset Release System & Immutability

This document defines the release architecture, artifact layout, privacy auditing, and publishing lifecycle for immutable APEMAP dataset releases.

---

## 1. Principles of Immutable Releases

1. **Deterministic & Self-Contained**: Every release bundle contains all web layers, analytical metrics, canonical data tables, a hash inventory manifest (`manifest.json`), and cryptographic checksums (`SHA256SUMS`). The manifest is not digitally signed.
2. **Read-Back Verification**: Before publication, releases must pass automated read-back verification: file sizes and SHA-256 hashes must match, all files must be accounted for, and validation assertions must report clean passes.
3. **Strict Privacy Enforcement**: Sensitive school financial profile fields (`financial_profile_2021`, `total_gross_income_per_student`, etc.) are strictly excluded from public distribution layers. Verification fails if any restricted column is detected in public CSV or Parquet files.
4. **Publication Discipline**: Publication is triggered by `data-v*` / `data-*` tags whose commits are contained in `main`, or manual workflow dispatch on `main` (with legacy `master` support). An ordinary branch push does not publish a release.

---

## 2. Release Directory Layout

The main directories are shown below. The manifest defines the exact inventory;
the historical recipe also includes review queues and `analysis_metrics.json`.

```text
release-v<version>/
├── manifest.json                  # Top-level release manifest with file hashes and metadata
├── SHA256SUMS                     # Standard UNIX-compatible SHA-256 checksums
├── analysis/                      # Analytical and statistical summary outputs
│   ├── demographics.json          # Chamber and parliament demographic breakdowns
│   ├── education_sectors.json     # Primary & secondary schooling sector distributions
│   ├── party_sectors.json         # Cross-tabulation of political party by school sector
│   ├── chamber_sectors.json       # Chamber sector comparisons
│   ├── parliament_coverage.json   # Distinct people, assertions and institution coverage
│   ├── shared_schools.json        # Schools attended by multiple parliamentarians
│   ├── cross_parliament.json      # Inter-parliament cohort continuity and trends
│   ├── school_finance.json        # Macro financial distributions (summary only)
│   └── parliament_comparison.json # Multi-parliament cohort comparisons
├── web/                           # Optimized web layers for frontend applications
│   ├── metadata.json              # Release metadata, provenance, and commit hash
│   ├── assertions.json            # Database validation check assertions and status
│   ├── members.json               # Member education profiles grouped by parliament
│   ├── schools.geojson            # GeoJSON Point features for represented schools
│   ├── results-summary.json       # Headline metrics and cohort attendance rates
│   └── parliament_<number>_combined.geojson # One school-point layer per selected term
└── data/                          # Canonical public research datasets (CSV & Parquet)
    ├── members.csv / .parquet
    ├── parliament_service.csv / .parquet
    ├── institutions.csv / .parquet
    ├── member_education.csv / .parquet
    ├── school_snapshots.csv / .parquet
    ├── electoral_boundaries.csv / .parquet
    ├── education_sector_benchmarks.csv / .parquet
    ├── school_finance_benchmarks.csv / .parquet
    └── school_public_funding.csv / .parquet
```

---

The release manifest and web metadata include `review_snapshot` provenance from
the database's consumed decision revision and source fingerprints. Building a
release never substitutes a newer working ledger. The build database retains the
exact consumed ledger and input-manifest bytes; keep it or the matching Git/source
revision when reproducing a release.

## 3. CLI Release Commands

The `apemap release` command group provides tools for building, verifying, and comparing release bundles.

Build Python distributions separately with `uv build`; this does not ingest data
or build a dataset. Package and dataset versions are independent. For the local
0.6.1 source/data build and database freshness audit, see the
[build record](releases/0.6.1/README.md). An uncommitted source build needs a retained
frozen copy and source hashes in addition to the manifest's `source_commit`.

For saved decision updates, first follow [Decisions to a new release](decisions-to-release.md).
`release build` exports an existing database; it does not ingest sources or apply
new ledger entries. Use `release recipe build` for the longitudinal dataset, then
verify and package the resulting bundle.

### 3.1. Build a Release (`apemap release build`)

Builds the entire release bundle from a canonical DuckDB database:

```bash
uv run apemap release build [OPTIONS]
```

#### Options:
- `--db-path`: Path to DuckDB database (default: `data/aped.duckdb`).
- `-o`, `--output-dir`: Output directory for release (default: `data/processed/release-v<version>`). Prefer an explicit fresh path under `data/processed/releases/`.
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

The [v0.3.3 reviewed release](releases/0.3.3/README.md) records the current decision
snapshot and delivery checks. The [v0.3.1 candidate handoff](releases/0.3.1-candidate/README.md)
retains its original historical replay and consumption record. Package a strictly verified local bundle
without publishing or tagging:

```text
uv run python -m apemap.release.package data/processed/releases/0.3.1-candidate --output-dir data/processed/candidates/0.3.1
```

The archive is deterministic across filesystem timestamps and checkout locations.
Existing archives, manifests and checksum files cannot be overwritten; use a
separate output directory for each candidate. `SHA256SUMS.dist` checks the archive;
the bundled `SHA256SUMS` checks individual payloads.

Dataset releases are published automatically via GitHub Actions ([`.github/workflows/release.yml`](../.github/workflows/release.yml)).

Local and Actions releases use the same committed historical recipe through
`apemap release recipe build --recipe <recipe.json> --version <unused-version>
--db-path <fresh-db> --output-dir <fresh-release>`. The effective recipe records
parliaments/cohort, source and ledger SHA-256 pins, ACARA/profile years, latest-profile
selection, finance/ABS/AEC years, package/dataset versions and web/analysis contracts
in both the release manifest and web metadata. The build copies the pinned ledger
for both ingestion stages, verifies sources offline and explicitly pins the tracked 2021 finance
GeoPackage; public exports exclude restricted finance fields. The source manifest timestamp fixes generated timestamps.

Workflow dispatch requires the committed recipe path and an approved historical
input archive URL. Tag runs use `APEMAP_RELEASE_RECIPE` (defaulting to the checked-in
historical recipe) and `APEMAP_HISTORICAL_INPUTS_URL` repository variables. Historical
ACARA is absent from the standard input asset; provision a bundle with `release
recipe inputs-bundle` and have it uploaded separately before publication. Restore
verifies exact inventory, sizes and each file's pinned hash before copying raw
files. Missing inputs, changed review pins and unsupported source years fail.

Actions builds the recipe twice and runs `release recipe compare` before strict
privacy verification and deterministic packaging. The comparison requires identical
metadata and payload hashes at the same commit/version. Existing releases retain
their original metadata and bytes. See the
[publication checklist](review-update-publish.md#10-publish-the-approved-dataset).

### Guardrails:

- **Main Branch Only**: Release jobs verify that the target commit is contained within `main`. If a release tag is pushed on a feature branch, the workflow immediately fails.
- **Version and Tag Identity**: Before building and again before publication, the workflow checks remote tags and releases. Manual dispatch requires an unused version with neither a `data-v<version>` nor `data-<version>` tag. Tag runs preserve the incoming tag and require it to resolve to the exact build commit. An alternate tag or an existing draft/public release for that version stops publication.
- **Legacy Dataset Releases**: A release under `v<version>` also reserves the dataset version when it contains an APEMAP dataset archive, including the published `v0.3.3` release. Bare package tags and releases containing only Python packages remain independent of dataset versions.
- **Publication Failure Handling**: Tag pushes must succeed, and GitHub Release creation requires the verified remote tag. Permission, network and tag-identity errors stop the run; GitHub cannot silently select another commit.
- **Prerelease Status**: SemVer prereleases such as `0.4.0-rc.1` are explicitly marked as prereleases and never promoted to the latest release. Build metadata alone does not make a version a prerelease.
- **Automated Verification**: Before publication, the workflow builds the release bundle in an isolated offline environment and runs `apemap release verify --strict-assertions`.
- **Packaging**: The bundle is archived into `apemap-release-v<version>.tar.gz` alongside `manifest.json` and `SHA256SUMS`.
- **GitHub Release**: The artifacts are uploaded to a public GitHub Release using the verified incoming tag, or `data-v<version>` for manual dispatch.

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
Use the authoritative decision-log review and replay workflow described in the
[methodology](methodology.md); a submitted report is not verified evidence by
itself.

Record accepted changes in the canonical inputs or reference mappings with
their provenance, then validate and publish a new immutable release. Existing
archives, manifests, and hashes stay unchanged. The public site continues to
show the pinned release until its data version is deliberately updated.

Form syntax and URL prefills follow the
[GitHub form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema).
