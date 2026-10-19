# Parliament 48 missing-school research — user-led follow-up

## Applied checkpoint — 19 October 2026

All 48 proposals now have the user's individual source reviews: **18 accepted
and applied**, 30 still needing research. Exactly 18 secondary attendance
decisions were appended through the normal exact-preview workflow. No mapping
or institution-definition decisions were made. The user's reviews and retained
evidence were preserved unchanged; no reviews were invented or rewritten.

The reviewer on port 8765 was incorrectly using `data/aped.duckdb`. Restarting it
with the pinned historical database restored the original source fingerprint
without weakening any stale-source guard. Start it with:

```powershell
uv run apemap review --db-path data/aped-historical-v0.7.0.duckdb --external-dir data/raw/historical/acara serve --port 8765
```

Open the batch workspace, not its POST-only `/preview` action as a bookmark.
The applied exact preview is
`previews/b96a0698-63ab-4099-a746-88ae39055ceb.json`, SHA-256
`9f14b606fea0118e899f0a957078d0cf35a6df24b5d1def01998c895ee27b989`.
Its native `application-<SHA>.json` receipt records measured effects.
`applied-attendance-audit.json` separately pins inputs, user authorization and
version intent; audit metadata must not use the native receipt filename prefix.

The current projection has **370 attendance rows** (352 + 18), **343 mapped**
unchanged and **27 unresolved** (9 + 18). All new attendance is verified but uses
unmapped placeholders with no ACARA identity or eligible sector/geography.
Named secondary coverage in the 226-member opening cohort is **195** (177 + 18).
The missing-attendance research scope falls **48 → 30**, plus the unchanged home
education skip. These are not the receipt's separate school-identity queue
counts, which remain 0 pending before/after.

```powershell
uv run python data/reference/review/research/issue-102-p48-missing-schools-followup-20261019/verify-application.py
```

This checks the exact ledger append, unchanged user reviews/evidence/database,
native receipt, canonical projection, old-row preservation and cohort changes.
Package stays **0.17.0**: no runtime behavior changed. The unused dataset PATCH
target for a separate corrected build/release is **0.7.1**; no database rebuild
or publication occurred, and published dataset 0.7.0 remains immutable.

## Original research delivery, before source review and application

**Unreviewed research, not authoritative data.** Open the ordinary Research
batches reviewer at
`/research-batches/issue-102-p48-missing-schools-followup-20261019`.
No reviews, authority evidence retention, attendance decisions, database rebuild
or dataset publication are performed by this update.

## Four updated cases

- **Michelle Ananda-Rajah:** the Saturday Paper survey names Santa Sabina College,
  Strathfield, NSW. This corrects the earlier not-found school-name result, but
  secondary stage, attendance dates and graduation remain unverified.
- **Wendy Askew:** preserve the user-supplied Scotch Oakburn gallery excerpt and
  its `('80)` label as an alumni/cohort lead. The specific Facebook post, date,
  secondary attendance and graduation year were not independently verified.
- **Lisa Darmanin:** retain the wrong-person exclusion. The Manly Selective
  Campus/2007 profile belongs to the Olympic sailor, not the Victorian senator.
- **Jacinta Nampijinpa Price:** propose attendance at the exact source name
  **Centralian Senior College**, supported by Tim Rowse's published Inside Story
  book review and its person-specific senior-schooling context. Do not infer
  graduation, dates, certification or equivalence to Centralian College.
  St Philip's College remains an unverified indexed lead; the combined LinkedIn
  date span must not be divided between schools.

The frozen population remains 226 opening-day members, 177 with named secondary
schools, 49 audit members and 48 tasks plus one reviewed home-education skip.
This fresh import has **18 attendance proposals and 30 unresolved cases**
(9 not-found, 21 uncertain). The other 44 results and all original identity
evidence are unchanged. No attendance selects an institution target.
If all 18 findings are individually approved and subsequently rebuilt, named
coverage could rise from 177 to 195 of 226. This is not an applied result.

## Provenance and reproduction

`follow-up.json` saves the four updates, source checks, attribution limits and
available earlier query strings. The update itself made **zero new searches**;
it directly reopened the three supplied Michelle/Lisa URLs and Inside Story
(also independently reopened by the audit agent). Older conversation lead checks
are reused: their complete query log and exact aggregate count are unavailable.
The original **213 queries** remain unchanged and are not represented as the
combined session total. Follow-up accounting appears separately under
`research_log.followup`, not as an invented second broad pass. Model identity and
token usage remain unavailable.

The prior imported batch's 14 tracked files are archived byte-for-byte under
`archive/research/2026-10-19/issue-102-p48-missing-schools-audited/`.
`archive-hashes.json` pins every file. `assemble.py` reuses its hash-pinned results
and overlays only these four cases; `results.json` is the deterministic output,
and `proposals-0001.json` is the ordinary immutable import. The original raw notes
remain in that archive. Its historical verifier is preserved, not rewritten to
run from the relocated directory.

From the repository root:

```powershell
uv run python data/reference/review/research/issue-102-p48-missing-schools-followup-20261019/verify.py
uv run python data/reference/review/research/issue-102-p48-missing-schools-followup-20261019/verify.py --release-dir data/processed/candidates/0.7.0-published-verify/extracted --db-path data/aped-historical-v0.7.0.duckdb --external-dir data/raw/historical/acara
```

Verification checks all archived hashes, the unchanged scope/published baseline,
exact four-case delta, 44 unchanged results, unchanged original query logs,
strict validation, native import hashes, no reviews/applications/targets, unchanged
authority ledgers and optionally the unchanged database/current projection.
It intentionally fails after actual reviews or authority changes.

Use the same database/register options to run `apemap review ... serve`.
Inspect every source, identify the reviewer explicitly, and review each proposal
individually before retention and exact-preview approval/application. A saved
research proposal does not establish an authoritative attendance record.

This follow-up changes research artifacts only. Package remains **0.16.0** and
dataset remains **0.7.0**; no additional version bump or publication is required.
Any later approved data corrections need a fresh dataset PATCH release and
separate rebuild/publication authorization.
