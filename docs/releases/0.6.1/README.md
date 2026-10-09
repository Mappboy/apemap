# Local 0.6.1 build — 10 October 2026

This records an earlier local candidate built from the frozen 0.6.1 working
checkout, including uncommitted review work. It does not publish a GitHub Release
or deploy cpoole.dev.
Package and dataset both use the explicitly requested version `0.6.1`; the web
and analysis contracts remain `2.0.0`. Documentation and database rebuilding do
not require an additional package bump. Historical releases, recipe pins and raw
sources are preserved.

The current review branch targets package `0.7.0`. This record, recipe and
distribution hashes describe the frozen `0.6.1` candidate only. Reproducing it
requires its retained source and review inputs; do not run its recipe against the
current package or update its pins to match newer code.

## Database audit

The old `data/aped.duckdb` was last written on 26 September 2026 and had no consumed
review snapshot. It contained 335 members, 705 service records, 469 education
assertions, 11,247 institutions and 9,709 school snapshots. Its opening-day cohorts
covered only terms 46–48: 227, 227 and 226 people. It could not represent the
current longitudinal recipe or prove consumption of the current ledger.

The selected historical inputs passed their manifest checks. The ledger passed
reference validation with 302 events and 209 effective decisions. One diagnostic
identifies an assertion mapping that overrides a school default for Australian
Islamic College; it is permitted assertion-specific precedence, not a ledger error.
“Current” here means the selected pinned sources and reviewed revision, not a new
live upstream acquisition. ACARA profiles cover 2008–2025; finance analysis uses
2024, with separately pinned legacy 2021 finances and ABS/AEC 2025 context.

## Frozen build and provenance

The recipe is [reviewed-0.6.1.json](../../../data/release-recipes/reviewed-0.6.1.json).
It pins package `0.6.1`, parliaments 42–48, the historical input manifest and ledger.
The retained `historical.json` template remains pinned to package `0.5.0`.

Because source edits continued during this job, code and tests were copied to
`data/processed/candidates/0.6.1/source/` before the final build. The private local
`source-snapshot.json` records every file hash, the base commit and working status.
The build loads only frozen code while reading the verified input paths in the
original checkout. The initial live-checkout runs were superseded by frozen runs;
their partial outputs are not the delivered candidate.

The source inventory SHA-256 is
`ad09570e2aef3f2d52f1aaff29f87c99c8c59c6d300d72554812971ab07a3bea`.
The manifest's source commit identifies the base commit; it cannot reproduce
uncommitted code on its own. Retain the local source copy and Python source archive
with the database until the review code has been committed and rebuilt.

## Local artifacts and checks

Python distributions were built with:

```powershell
uv build data/processed/candidates/0.6.1/source --out-dir data/processed/candidates/0.6.1/python --offline
```

Both `apemap-0.6.1.tar.gz` and `apemap-0.6.1-py3-none-any.whl` were built.
Frozen package Ruff lint, Ruff formatting and a type check using the frozen
project root passed. Full-checkout `ty check` failed on legacy/archived files and
evolving review code; it is not a passing repository-wide gate.

The [delivery record](delivery-record.json) records the database promotion,
distribution hashes, 69 passing database checks and 233 passing release checks.
The [manifest](manifest.json), [payload checksums](SHA256SUMS) and
[archive checksum](SHA256SUMS.dist) preserve the delivered inventory. These are
copies of the original records; the payload, database and Python distributions
remain local artifacts. The delivery record does not record a full pytest result;
its build/verification checks are not a claim that the full test suite passed.

## Rebuild and review

For a reproducible build from a committed revision, follow
[Decisions to a new release](../../decisions-to-release.md). Pin a fresh recipe
matching that revision, choose unused paths/version, build offline, strictly
verify and package. Do not rerun into the populated local 0.6.1 bundle or modify
historical manifests to match the current package.

Review the working database with the historical register:

```powershell
uv sync --extra review-ui --frozen
uv run apemap review --db-path data/aped.duckdb --external-dir data/raw/historical/acara serve
```

Saving a decision changes the ledger, not the consumed database or this bundle.
Rebuild before packaging newer decisions. Close database connections and preserve
the prior working database before promoting another verified build.
