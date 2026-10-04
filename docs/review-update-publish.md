# Reviewing schools and members, updating data, and publishing

Use this checklist for a research review or dataset correction. Work through the
steps in order: prepare a working copy, review evidence, apply supported
corrections, rebuild, verify, submit a pull request, then publish the approved
dataset and update the website.

The maintained data pipeline uses DuckDB and `data/reference/`. The Dash app in
`app/` and older GeoPackages are historical artifacts. Website changes for
cpoole.dev belong in its frontend repository.

Commands below use **PowerShell from the repository root**. The example release
version `0.3.2` and review date `2026-10-04` are placeholders: choose an unused
version and the actual review date. This guide was checked against the repository
on 4 October 2026; consult command help and the linked implementation when the
workflow changes.

## 1. Prepare the review

1. Define the scope: selected parliaments, members or schools, reason for review,
   baseline release, profile years and finance reporting year. Supported terms
   are 42–48; use the opening-day cohort for published comparisons.
   For a reported correction, record its issue and verify the submitted source;
   the report itself is not evidence. The public intake process is described in
   [Dataset Release System](release.md).
2. Inspect existing changes and start a focused branch. Preserve unrelated work.

   ```powershell
   git status --short
   git switch -c data/review-education-example
   uv sync --all-groups --frozen
   uv run apemap --help
   ```

   Replace the example branch with `issue-<number>-<slug>` for issue work or a
   descriptive `data/` branch for a data correction.
3. Set working paths and select the database that produced the baseline release.

   ```powershell
   $Parliaments = '42,43,44,45,46,47,48'
   $Version = '0.3.2'
   $ReviewDir = 'data/processed/reviews/2026-10-04'
   $WorkingDb = 'data/aped-review-2026-10-04.duckdb'
   $BaselineDb = 'data/aped.duckdb'
   $ReleaseDir = "data/processed/releases/v$Version"
   New-Item -ItemType Directory -Path $ReviewDir -Force
   ```

   For the historical 0.3.0 baseline, select
   `data/aped-historical-v0.3.0.duckdb` if that is the database used locally.
   Close database connections before copying it. Use fresh paths for each job.

   ```powershell
   if (Test-Path $WorkingDb) { throw 'Choose a fresh working database path.' }
   Copy-Item -LiteralPath $BaselineDb -Destination $WorkingDb
   ```

4. Record baseline counts and save the existing coverage report and review CSVs.
   Keep an untouched baseline database/release for comparison. A new checkout
   may have no database: provision inputs and build the appropriate baseline
   using [Reproducibility](reproducibility.md) or
   [Historical Coverage](historical-coverage.md) first.
5. Restore missing pinned APH/AEC caches and install DuckDB spatial during online
   setup if necessary, then verify inputs. These setup steps can use the network.

   ```powershell
   uv run apemap inputs restore
   uv run python -c "import duckdb; duckdb.connect().execute('INSTALL spatial; LOAD spatial')"
   uv run apemap inputs verify
   ```

Do not refresh sources merely to review existing records. Keep raw snapshots,
`data/external/`, old release bundles, notebooks and figures intact.

## 2. Open the review queues and identify priorities

Start with existing queues in `data/processed/` or the baseline release's
`review/` directory. Copy any prior manually reviewed CSVs into `$ReviewDir`
before regenerating them there; merge preservation only reads the same output
path. Keep CSV headers, row keys and UTF-8 encoding, and treat identifiers and
postcodes as text when opening them in a spreadsheet editor.

| File | What to review | Editable decision fields |
| --- | --- | --- |
| `historical_education_review.csv` | Missing education, unresolved school names and overseas institutions | `review_status`, `resolved_value`, `manual_source_url`, `review_notes` |
| `historical_service_review.csv` | Uncertain service dates and membership histories | `review_status`, `resolved_value`, `manual_source_url`, `review_notes` |
| `wikimedia_member_review.csv` | Identifier conflicts and demographic differences | `review_status`, `resolved_value`, `manual_source_url`, `review_notes`, historical evidence fields |
| `wikimedia_school_review.csv` | Candidate school identities and locations | `review_status`, `resolved_*`, `manual_source_url`, `address_source_url`, `review_notes`, historical evidence fields |
| `unmatched_schools.csv` | Generated list of education gaps | Use a persistent review queue for decisions; this file is rewritten |
| `upstream_profile_review.csv` | Invalid upstream profile percentages | Record evidence separately; this is a generated diagnostic, not an override input |

For the standard workflow, regenerate APH queues against the working copy with
the existing source cache:

```powershell
uv run apemap ingest aph --db-path $WorkingDb --parliament $Parliaments --output-dir $ReviewDir --no-export-parquet
```

This command updates the working database using the current references. It does
not operate read-only. If the APH cache is absent, the client may fetch it; restore
the pinned cache first for a snapshot-based review.

For a historical baseline, start with its existing release queues. The CLI above
uses `data/external/` for matching, whereas historical replay uses
`data/raw/historical/acara/`. Regenerate historical queues with the fresh
historical build in step 6 so historical-only institutions use the correct
register. That build writes its queues under the new release's `review/` directory;
retain your reviewed baseline CSVs separately because it starts with fresh paths.

Optional Wikimedia enrichment generates extra candidates and can populate
member identifiers. It can contact Wikimedia on cache misses even without
`--refresh`, so run it only when that acquisition is intended:

```powershell
uv run apemap ingest wikimedia --db-path $WorkingDb --parliament $Parliaments --output-dir $ReviewDir --no-refresh
```

Prioritize identity conflicts, wrong school matches and cohort errors, then
missing education and incomplete locations. Separate unresolved evidence from a
confirmed absence of schooling information.

## 3. Review each member

1. Locate the member by `aph_id` and `member_id`, rather than name alone. Compare
   their APH biography and Parliamentary Handbook record with the queued claim.
2. Verify each relevant service interval: parliament, chamber, party, electorate,
   state, start and end dates. Party and chamber belong to service records; they
   can change during a person's career.
3. Check whether the person served on the parliament's opening date. Later
   entrants remain valid service records but are outside the published
   opening-day cohort. Use the occupied-seat benchmarks in
   [Historical Coverage](historical-coverage.md), including the 44th Senate's
   two opening-day vacancies.
4. For each school, record the source's actual school name and what it says about
   attendance. Prefer APH, National Archives, official school histories and
   education authorities; Wikimedia is a candidate lead requiring corroboration.
5. Keep attendance and graduation separate. Use `attended_unspecified` unless
   evidence explicitly supports `graduated` or `attended_did_not_graduate`.
   Record multiple schools separately; do not choose one just to simplify counts.
6. Record the source URL, actual retrieval date, evidence summary, reviewer and
   decision. Choose `pending`, `accepted`, `rejected` or `needs_research`.
   Explain rejection or unresolved conflicts in `review_notes`.

**Checkpoint:** another reviewer can identify the person, reproduce the evidence,
and understand why each claim was accepted or left unresolved.

## 4. Review each school

1. Compare the recorded name with the ACARA register and annual profiles. Check
   ACARA ID, state, suburb, campus and school type as well as the name. A similar
   name or fuzzy-match score alone is insufficient evidence.
2. Establish whether the match is the same institution, a rename, a merger or a
   successor. Find an official history or education authority source describing
   that relationship. Keep the original attendance name separate from the
   canonical institution name.
3. Check sector (`Government`, `Catholic`, `Independent` or unresolved `Other`)
   against the relevant source. Do not infer sector from the school's name.
4. Check coordinates against the school/campus and location source. GeoJSON uses
   `[longitude, latitude]`. Record a location source and leave unknown coordinates
   null. Historical-only schools may legitimately have no map point.
5. Treat overseas schools separately: do not assign an Australian ACARA ID to
   force a match. Reject candidates that are people, towns or disambiguation pages.
6. Check profile and finance years. Recent data describes that reporting year or
   a reviewed successor, not the resources available when the member attended.
   Preserve missing values and recorded source anomalies.
7. Complete the queue's manual fields, evidence URLs and notes. Generated
   candidate columns can change on rerun, so put decisions in the manual fields.

**Checkpoint:** every accepted match has identity/location evidence and any
historical relationship is explicit. Unresolved schools remain visible as gaps.

## 5. Apply accepted corrections to reproducible inputs

**An `accepted` queue row is a review record, not an automatic database update.**
Promote supported decisions into the inputs consumed by ingestion:

| Correction | Where to apply it | Required evidence and limits |
| --- | --- | --- |
| Add or supplement a member's secondary education | `data/reference/manual_member_education.csv` | `aph_id`, `school_name`, `source_url`, `retrieved_at`, `confidence`, `reviewer_notes`, `attended_status` |
| Resolve a spelling, rename or successor to ACARA | `data/reference/school_aliases.json` | Canonical ID/name, state, sector, school type, notes, `source_url`, `relationship_type` and `reviewed_at` |
| Correct a member's service history | `data/reference/historical_service_overrides.csv` | Full replacement intervals for the member/term, with URL, retrieval date and review notes |
| Correct demographics, remove a false APH education claim, or apply an overseas school's reviewed coordinates | No general review-CSV application command exists | Keep the decision queued and make a focused ingestion/reference feature with regression tests before claiming it is applied |

For manual education, copy the existing CSV header exactly. Confidence values
are `verified`, `provisional` or `unconfirmed`. Strong attendance evidence does
not guarantee a verified institution match: final assertion confidence is capped
by the matcher. Do not invent attendance years or graduation dates.

For aliases, use the recorded school name as the key and verify the canonical
ACARA ID exists in the source register. Use `rename` or `successor` when supported
by evidence; update `total_aliases` if adding/removing entries. Historical runs
including terms before 46 require sourced aliases. See the reviewed Ogilvie and
Nambour entries for examples; their sources establish different relationships.

Service overrides replace **all** generated intervals for that member/parliament.
Supply every interval that must remain, within the term dates and without
overlap. Use `representatives` or `senate`, ISO dates, and an empty end date only
where the open term and evidence allow it. Never add a single replacement row
while assuming the member's other intervals will survive.

Save a review log in `$ReviewDir` containing IDs, old/new values, sources, actual
retrieval dates, reviewer, disposition and the affected input file. Retain queue
decisions alongside the source correction. Rebuild from inputs instead of
manually editing DuckDB or exported JSON/GeoJSON.

## 6. Update input pins and rebuild

1. Inspect the reference diff and archive exact prior files when replacing or
   removing an artifact requires preservation under [AGENTS.md](../AGENTS.md).
   A focused record edit remains reviewable in Git history.
2. Update the hash and byte size of **only the intentionally changed inputs** in
   every applicable manifest: `data/inputs-manifest.json` for the standard
   workflow and `data/historical-inputs-manifest.json` for historical replay.
   Add new consumed references to the inventory with source/provenance metadata.
   Preserve raw-source acquisition dates; a review date is not a new retrieval
   date for an unchanged APH/ACARA snapshot.
3. Obtain the new hash and size for each edited file, then update its manifest
   entry. For example:

   ```powershell
   Get-FileHash -Algorithm SHA256 data/reference/school_aliases.json
   (Get-Item data/reference/school_aliases.json).Length
   uv run apemap inputs verify
   uv run apemap inputs verify --manifest data/historical-inputs-manifest.json
   ```

   Store the SHA-256 in lowercase. A changed raw archive also needs a new pinned
   archive URL/hash and distribution asset; keep the existing archive pin when
   only tracked references changed. Never repin an unexplained mismatch.
4. Choose the build recipe that matches the intended release. Use a fresh
   database and output directory, outside the baseline paths.

   **Standard pinned pipeline** (the recipe used by GitHub release Actions):

   ```powershell
   $CandidateDb = 'data/aped-candidate-0.3.2.duckdb'
   $CandidateDir = 'data/processed/reviews/2026-10-04/candidate'
   $FinanceYear = 2021
   uv run apemap run-all --offline --inputs-manifest data/inputs-manifest.json --db-path $CandidateDb --output-dir $CandidateDir --parliament $Parliaments --finance-year $FinanceYear --strict
   ```

   **Historical longitudinal recipe** (2008–2025 profiles and 2024 finance
   analysis), after historical inputs have been provisioned and verified:

   ```powershell
   $CandidateDb = 'data/aped-historical-candidate-0.3.2.duckdb'
   $FinanceYear = 2024
   uv run python -m apemap.historical --db-path $CandidateDb --output-dir $ReleaseDir --manifest-path data/historical-inputs-manifest.json --version $Version
   ```

   This historical command already builds and verifies the release. Use
   [Historical Coverage](historical-coverage.md) for first-time acquisition.
   Running with `--download` is an intentional refresh and rewrites its input
   manifest; do not use it as a shortcut for applying review decisions.

## 7. Validate the candidate and compare it with the baseline

```powershell
uv run apemap transform --db-path $CandidateDb
uv run apemap validate --db-path $CandidateDb --parliament $Parliaments --strict
uv run apemap analyze --db-path $CandidateDb --parliament $Parliaments --finance-year $FinanceYear --output-dir $ReviewDir
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Run focused regression tests first for any ingestion/code change. If a full gate
has a pre-existing failure or cannot run, record its exact command and error and
run unaffected checks. A documented tooling failure does not waive data/release
validation.

Compare baseline and candidate records using read-only DuckDB connections:

- Distinct members and opening-day counts per parliament/chamber; service changes
  should match the scope and occupied-seat benchmarks.
- Education assertions and distinct institutions separately; check the intended
  member/school, source URL, recorded name, confidence and resolution evidence.
- Duplicate keys, orphan joins, nulls and missing education/profile coverage.
- School coordinates, campus/state, sector and annual profile uniqueness.
- Coverage and sector denominator changes, including unmapped and historical-only
  schools. A missing map point does not imply a missing attendance assertion.
- Representative members with multiple schools or terms; annual profiles must
  not multiply website attendance rows.

Use `parliament_coverage.json` for distinct-person/assertion/institution coverage
and `coverage_metrics.json` for ingestion diagnostics. The release builder writes
the former under `analysis/`. See [Data Model](data-model.md) for queryable tables
and [Website Release Contract](web-release.md) for row grain and denominators.

**Checkpoint:** explain every material count/value change, and confirm unresolved
items retain their status and provenance. Strict validation is necessary but
does not prove that every education claim has been researched.

## 8. Build and verify the publication bundle

For the standard pipeline, build the candidate release into a new directory:

```powershell
uv run apemap release build --db-path $CandidateDb --output-dir $ReleaseDir --version $Version --parliament $Parliaments --finance-year $FinanceYear --strict
```

The historical recipe already performed this step. Verify either bundle:

```powershell
uv run apemap release verify $ReleaseDir --strict-assertions
uv run apemap release diff 'data/processed/releases/0.3.0' $ReleaseDir
```

Replace the diff's first path with the available baseline bundle. Inspect
`manifest.json`, `SHA256SUMS`, `web/assertions.json`, `web/metadata.json`,
`web/results-summary.json`, `web/members.json`, `web/schools.geojson`, per-term
`web/parliament_<number>_combined.geojson` files and public tables under `data/`.
Check version, source commit, selected terms, source dates, reporting years,
temporal warnings and attribution. The manifest records hashes, not a digital
signature. Unknown acquisition dates must remain unknown.

The immutable release builder excludes restricted private finance fields.
Passing its verifier checks file inventory, hashes, coordinates, restricted
columns and assertions. Do not publish the working DuckDB, raw sources or generic
Parquet exports as substitutes for this public bundle. The separate
`export --web-release` format has a different layout and is not interchangeable
with `release build`; use the frontend's expected contract.

Keep review notes outside the finished bundle. Any intentional bundle change
requires rebuilding its manifest/checksums and reverifying; never overwrite an
already published version.

## 9. Submit the change for review

1. Review `git diff`, `git diff --stat` and `git status --short`. Include only
   relevant references, manifests, documentation, code/tests and deliberately
   selected review artifacts. Full releases under `data/processed/releases/`
   and local databases are ignored; distribute them as release assets.
2. Commit verified checkpoints, push the branch and open a draft pull request.
   Stage named files; do not stage the entire data directory indiscriminately.

   ```powershell
   git add <reviewed-file-paths>
   git commit -m "Correct reviewed member education and school mappings"
   git push -u origin <review-branch>
   gh pr create --draft --base main --title "Update reviewed education records" --body-file <saved-pr-description-file>
   ```

   Replace angle-bracket placeholders with real paths/names. Confirm the target
   branch before using `--base main`. Include the issue, scope, evidence, input
   pins, count/schema effects, validation commands/results and unresolved items.
3. Have the evidence and candidate differences reviewed and merge through the
   repository's normal process. A draft PR is a review checkpoint; publication
   follows approval and merge.

## 10. Publish the approved dataset

The current [release workflow](../.github/workflows/release.yml) accepts tags
`data-v*` / `data-*` or manual dispatch on `main` (also accepts `master`). Tag
commits must be contained in the accepted release branch. The workflow restores
the pinned inputs, rebuilds offline, verifies the public bundle, packages it and
creates a **public, non-draft GitHub Release**.

Before triggering it, confirm the merged commit contains all reviewed inputs and
pins. **Actions uses `data/inputs-manifest.json`, the standard `run-all` recipe
and the default 2021 finance year. It does not upload your local bundle or run
`apemap.historical`.** If publishing the historical recipe or a different finance
year, first implement and review the workflow changes needed to reproduce that
candidate. Manual dispatch currently exposes only version and parliaments.

Once the correct recipe is approved, choose one trigger. For manual dispatch:

```powershell
gh workflow run release.yml --ref main -f "version=$Version" -f "parliaments=$Parliaments"
gh run list --workflow release.yml --limit 5
```

Alternatively, tag the approved merged commit and push that tag:

```powershell
git tag -a "data-v$Version" <approved-merged-commit> -m "APEMAP dataset v$Version"
git push origin "data-v$Version"
```

Watch the identified run with `gh run watch <run-id>`. After success, inspect
`gh release view "data-v$Version"`. Download and unpack the published
`apemap-release-v<version>.tar.gz` into a fresh directory and run
`apemap release verify <unpacked-directory> --strict-assertions` again. Check the
published source commit, cohort and source/reporting years against the approved
candidate; publication success alone does not establish data equivalence.

If publication fails, inspect the workflow logs and fix the specific input,
validation or permission failure. Keep the previous public version available.

## 11. Update the website and check the published result

1. In the cpoole.dev frontend repository, pin the new dataset version and asset
   URLs using that project's documented hosting/deployment procedure. This
   repository contains no cpoole.dev deployment command.
2. Match the chosen bundle layout. Populate the parliament selector from release
   metadata and load its `parliament_<number>_combined.geojson` layer.
3. Preview the website and inspect corrected members and schools, multi-school
   members, overseas/unmapped schools, missing profiles and historical successors.
4. Test parliament, party and chamber filters. Party/chamber must match the same
   service entry as the selected parliament. Compare displayed counts with the
   opening-day summary and coverage reports.
5. Check downloads, source links, attribution, profile/finance years, confidence,
   mapping evidence and temporal warnings. Missing coordinates should not create
   invented points. Check that restricted fields are absent from downloadable data.
6. Deploy through the frontend's approved process. Repeat the checks on the public
   site, including a fresh browser load to catch stale cached asset versions.
7. Record the release URL/tag, data source commit, frontend deployment commit/date,
   validation outcome and remaining research questions in the publication log.
   Keep the previous version pin so the frontend can be rolled back if needed.

**Done:** evidence and corrections are reviewable, the inputs reproduce the
validated public release, the merged change and GitHub assets are recorded, and
the website serves the intended version with its sources and limitations visible.
