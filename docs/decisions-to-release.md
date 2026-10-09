# From saved decisions to a new dataset release

Saving a school/member decision appends to `data/reference/review/decisions.jsonl`.
It does not rebuild DuckDB, replace a release, or update cpoole.dev. The sequence is
**save -> validate -> commit -> rebuild -> verify -> compare -> package -> review
and publish -> update the website pin**.

These PowerShell commands run from the repository root. Local and Actions builds
use `apemap release recipe build` with the same committed recipe: parliaments
42–48, ACARA 2008–2025 profiles, latest available profiles and 2024 finance analysis.
The recipe pins the source manifest and reviewed ledger separately. Public funding inputs and the tracked 2021 finance GeoPackage are pinned
explicitly; restricted finance fields remain excluded from public exports.
See the [0.3.3 delivery record](releases/0.3.3/README.md) for the previous delivery
and the [local 0.6.1 record](releases/0.6.1/README.md) for the earlier frozen local build.
`0.6.2` below is only an example next correction version: check local bundles,
tags and GitHub releases before choosing an unused version. Package and dataset
versions are separate; a data-only correction needs no package version bump.
The retained template is pinned to package 0.5.0; `recipe pin` creates a new recipe
for the installed package and current ledger/evidence instead of modifying it.
Web/analysis contracts are `2.0.0`. Migrating a dataset from contract `1.0.0`
requires a dataset MINOR bump while at 0.x and coordinated frontend joins.

## 1. Finish and validate the decisions

Avoid saving more decisions during this build. Keep old events unchanged; append
superseding events for corrections. Run:

```powershell
git status --short --branch
uv sync --all-groups --extra review-ui --frozen
uv run apemap review --external-dir data/raw/historical/acara check --base origin/main
```

Resolve validation errors or conflicting decisions before proceeding. The
historical register must be selected explicitly; the review CLI otherwise uses
`data/external/`. A valid ledger proves event/reference integrity, not that every
school claim has been researched. See [Review decisions](review-decisions.md) for
evidence and the [full review checklist](review-update-publish.md) for research.

## 2. Commit the reviewed state and choose fresh output paths

Create a focused branch if you have not already done so. Stage only reviewed
changes, then commit so the release's `source_commit` identifies the actual ledger.

```powershell
git add data/reference/review/decisions.jsonl
git commit -m "Record reviewed school and member decisions"
$Version = '0.6.2'
$Baseline = 'data/processed/releases/0.3.3'
$CandidateDb = "data/aped-historical-v$Version.duckdb"
$ReleaseDir = "data/processed/releases/$Version"
$PackageDir = "data/processed/candidates/$Version"
$Recipe = "data/release-recipes/reviewed-$Version.json"
if (Test-Path $CandidateDb) { throw 'Choose a fresh database path.' }
if (Test-Path $ReleaseDir) { throw 'Choose a fresh release directory.' }
if (Test-Path $PackageDir) { throw 'Choose a fresh package directory.' }
git rev-parse HEAD
uv run apemap release recipe pin --template data/release-recipes/historical.json --output $Recipe
if ($LASTEXITCODE -ne 0) { throw 'Recipe pinning failed.' }
git add $Recipe
git commit -m "Pin reviewed historical release recipe"
```

If the decisions are already committed, skip the add/commit. Do not include
unrelated working changes. Use a real, available baseline bundle for comparison;
old releases and databases remain unchanged.

## 3. Verify pinned inputs; rebuild and replay offline

```powershell
uv run apemap inputs verify --manifest data/historical-inputs-manifest.json
if ($LASTEXITCODE -ne 0) { throw 'Historical input verification failed.' }
$env:APEMAP_OFFLINE = '1'
uv run apemap release recipe build --recipe $Recipe --db-path $CandidateDb --output-dir $ReleaseDir --version $Version
if ($LASTEXITCODE -ne 0) { throw 'Historical rebuild failed; do not package it.' }
```

This verifies every recipe pin, loads ACARA before APH, replays the decisions, captures their exact bytes in
the database, loads funding references, builds all seven parliament layers, and
runs strict database and release validation. It also regenerates review queues
under the new release's `review/` directory. Never use `--download` merely to apply
decisions: that refreshes upstream inputs and rewrites their manifest. Missing
inputs need deliberate provisioning; see [Historical coverage](historical-coverage.md).
Install the DuckDB spatial extension during online setup if it is absent.

Re-exporting the old database with `apemap release build` cannot include new
decisions. The historical command above already builds the release; do not run a
second build into its populated directory. Stop after any command returns a
nonzero exit code; PowerShell does not automatically stop on native command errors.

## 4. Verify the release and its consumed review revision

An existing database is current only when its consumed decision/evidence hashes
match the intended review revision, its input-manifest hash matches the selected
recipe, and it passes current schema/cohort validation. A recent modification
timestamp or `transform` alone cannot establish freshness. Keep the old database
and build into a fresh path before promoting the verified result to
`data/aped.duckdb`; close review servers and all database connections first.
For builds from uncommitted code, retain a frozen source copy and its hashes and
label the result a local candidate: `source_commit` alone does not describe those bytes.

```powershell
uv run apemap release verify $ReleaseDir --strict-assertions
if ($LASTEXITCODE -ne 0) { throw 'Release verification failed.' }
$Manifest = Get-Content "$ReleaseDir/manifest.json" -Raw | ConvertFrom-Json
$LedgerHash = (Get-FileHash data/reference/review/decisions.jsonl -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Manifest.review_snapshot.decision_log_sha256 -ne $LedgerHash) {
    throw 'The ledger changed or was not consumed; inspect and rebuild to a fresh path.'
}
$Manifest.review_snapshot | Select-Object event_count,effective_decisions,decision_log_sha256
$Manifest | Select-Object data_release_version,source_commit,parliaments,source_snapshot_dates
$Manifest.release_recipe
uv run apemap release diff $Baseline $ReleaseDir --json
```

Inspect corrected school identities, source URLs, recorded attendance names,
opening-day member counts, unresolved schools and coordinates. Compare
`analysis/parliament_coverage.json`, `web/results-summary.json`, public tables and
map layers. Distinct people, assertions and institutions have different counts.
Strict verification checks inventory, hashes, assertions and restricted fields;
it cannot establish the truth of a research claim.

The historical manifest's `generated_at` deliberately retains the pinned input
timestamp for replay. It is not the decision date or publication date. The new
source commit and `review_snapshot` identify the updated decisions; record the
actual build/review date separately in delivery notes. Unknown APH retrieval dates
remain null. See [Release system](release.md) for privacy and provenance details.

## 5. Package the public bundle and submit it for review

```powershell
uv run python -m apemap.release.package $ReleaseDir --output-dir $PackageDir
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
Get-Content "$PackageDir/SHA256SUMS.dist"
Get-FileHash "$PackageDir/apemap-release-v$Version.tar.gz" -Algorithm SHA256
```

The packager strictly verifies the bundle, normalizes archive timestamps and
ownership, and refuses to overwrite an existing archive or inventory. Distribute
the archive, `manifest.json`, `SHA256SUMS` and `SHA256SUMS.dist`. The latter checks
the archive; the bundled `SHA256SUMS` checks payload files. Keep the working
DuckDB privately for reproducibility; it contains the full consumed ledger.

Run `uv run pytest`, Ruff lint/format checks and the documented type checks before
opening the draft PR. Include baseline comparisons, decision revision, source
commit, archive hash, source/reporting years, validation and unresolved questions.
Commit delivery documentation, push the branch and have the corrections reviewed.
Full databases and release payloads are ignored and belong in release assets.

## 6. Publish the reviewed dataset, then update the website

An uploaded draft is an unpublished preview. Public publication requires the
reviewed source revision and recipe on `main`. `release.yml` calls the same recipe
builder, repeats the build, requires identical manifests/payload hashes, and uses
the deterministic packager after strict integrity/privacy verification.

Provision the historical input bundle separately, without refreshing upstream
files. The published `inputs-20261002` bundle contains only standard APH/AEC inputs;
it cannot provision historical ACARA. Build the exact raw-input bundle locally:

```powershell
uv run apemap release recipe inputs-bundle --recipe $Recipe --archive "data/processed/candidates/historical-inputs-$Version.tar.gz"
```

Have a maintainer upload that bundle to an approved immutable input asset location,
then pass its HTTPS URL as `inputs_archive_url` to workflow dispatch. For tag runs,
set repository variables `APEMAP_HISTORICAL_INPUTS_URL` and `APEMAP_RELEASE_RECIPE`
to the approved asset URL and committed recipe path. No historical input asset is
published by this code change. CI fails if the URL is missing or any archived file
differs from the recipe's per-file SHA-256/size pins. Local offline provisioning
uses `release recipe inputs-restore --recipe $Recipe --archive <local-bundle>`.

To compare an equivalent local/CI replay at the same source commit and version:
`uv run apemap release recipe compare <local-release> <ci-release>`. This verifies
both bundles strictly and requires identical effective metadata and artifact
hashes, including LF JSON bytes. Commit changes intentionally change provenance;
review those differences rather than claiming byte equality across revisions.

Follow [Publish the approved dataset](review-update-publish.md#10-publish-the-approved-dataset)
once the source and recipe are approved. Download the resulting public archive,
verify its hash and run strict verification after extraction. A merge changes the
source commit, so compare the final release against the reviewed candidate and
record its own immutable hashes; do not replace a published version in place.

Finally update the pinned dataset version and asset URL in the cpoole.dev
repository, preview the changed records/filters/downloads, then deploy through
that project's workflow. Publishing a GitHub release alone does not change the
website. Retain the previous version pin for rollback.
