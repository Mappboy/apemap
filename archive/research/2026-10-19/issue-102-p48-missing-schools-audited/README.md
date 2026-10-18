# Parliament 48 missing secondary-school research — issue 102

**Unreviewed proposals, not authoritative data.** Open this batch in the normal
Research batches reviewer at `/research-batches/issue-102-p48-missing-schools-audited`.
No individual reviews, evidence retention, decisions, database rebuild or dataset
publication have been performed for these findings.

## Scope and findings

The published opening-day dataset has 226 Parliament 48 members: 177 with a named
institutional secondary school, 48 without a secondary assertion, and Jordon
Steele-John with reviewed home education. The frozen audit therefore contains 49
members, with one resolved skip and 48 research tasks. Named but unresolved schools
are deliberately excluded. `audit.json` pins the exact published **data 0.7.0**
manifest and member, service and education payload checksums, rather than a local
recipe reconstruction whose timestamp serialization differs.

All 48 tasks have saved findings: **17 proposed attendance assertions and 31
unresolved results** (12 not-found, 19 uncertain). Across the 48 cases, 213 actual
queries were made; every final broad pass stays within six queries. If the
reviewer approves all 17 attendance findings, named-school coverage could rise
from 177 to 194 of 226, leaving 32 without an institutional school, including the
home-education skip.
This is a conditional count, not an applied coverage result.

Each case records Wikipedia/article identity, the Wikipedia-and-citations outcome,
the final broad-search outcome, access failures, leads, limitations and actual
queries. QIDs unavailable for live confirmation remain cached hints, not confirmed
identities. Wikipedia identity evidence never substitutes for attendance evidence.
The 17 proposals name schools but select **no institutional targets**: accepting
attendance must not infer ACARA identity, sector, geography or a successor.
Unresolved placeholders preserve the existing data contract until a separate,
sourced institution mapping is reviewed.

## Reproduction and integrity

`research-*.json` are retained session notes. They preserve the reported research
phases and bounded retrieval windows; exact per-request chronology, model identity
and token usage are unavailable and are not invented. `assemble.py` deterministically
normalizes them into `results.json`; `proposals-0001.json` is the actual ordinary
batch import. `tasks.json`, the audit and the imported proposal snapshot are
content-pinned by their native SHA-256 digests.

The earlier 22-finding import was never reviewed or applied. Its six original
preparation/import files are preserved under
`archive/research/2026-10-18/issue-102-p48-missing-schools/`. Five alumni-only
findings were withdrawn to uncertain leads; Varun Ghosh now has explicit Year 11
evidence in an original school PDF. This fresh batch reuses the identical frozen
task/audit hashes rather than rewriting an immutable import. Only the audited
batch appears in the current Research batches list.

Run from the repository root (no network and no authority writes):

```powershell
uv run python data/reference/review/research/issue-102-p48-missing-schools-audited/verify.py
uv run python data/reference/review/research/issue-102-p48-missing-schools-audited/verify.py --release-dir data/processed/candidates/0.7.0-published-verify/extracted --db-path data/aped-historical-v0.7.0.duckdb --external-dir data/raw/historical/acara
```

The first command verifies raw-to-result reproduction, native hashes, strict result
validation, scope/counts/budgets, zero reviews/applications and unchanged authoritative
ledgers. The second additionally verifies the extracted published payloads and
current read-only review projection. Its inputs are local provisioned artifacts,
not checked-in copies of the large dataset. Ledger digests are frozen in
`manifest.json`; this delivery verifier intentionally fails after real reviews or
authority changes and must not be repurposed as a permanent live-batch health check.

## Human review and eventual data release

```powershell
uv run apemap review --db-path data/aped-historical-v0.7.0.duckdb --external-dir data/raw/historical/acara serve --port 8782
```

Inspect each source, identify yourself explicitly, record each individual review,
retain only approved evidence, and approve the exact fresh decision preview before
applying it. Negative findings cannot create attendance. Rebuild derived data only
after that approval. Do not rewrite the published 0.7.0 bundle. If corrections are
approved, prepare an unused dataset PATCH version (currently 0.7.1), verify its
before/after coverage and release diffs, and publish only with separate authorization.

The compatible new workflow changes package **0.15.0 → 0.16.0**. Dataset contents
and dataset version remain unchanged in this delivery. See
[`docs/guided-research-batches.md`](../../../../../docs/guided-research-batches.md)
for the staged workflow and [`issue 102`](https://github.com/Mappboy/apemap/issues/102)
for the research request.
