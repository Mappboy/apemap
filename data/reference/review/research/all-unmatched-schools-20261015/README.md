# Unmatched school research across all parliaments

Prepared on 15 October 2026 from the current decision projection of
`data/aped.duckdb`, using `data/raw/historical/acara`. No parliament filter was
applied. The frozen scope contains **20 unresolved attendance assertions and
19 school descriptions**, including both members recorded against Aquinas College.

Open the ordinary [Research batches page](http://localhost:8765/research-batches)
and select `all-unmatched-schools-20261015`, or open
[the normal batch workspace](http://localhost:8765/research-batches/all-unmatched-schools-20261015).
This is a native batch with individual source inspection, review, evidence
retention, exact decision previews and application. No packet URL is required.

At delivery: **20 proposals, 74 evidence records, zero reviews and zero applied
decisions**. Eleven proposals map exact attendance assertions; five include
separate manual institution definitions. Nine remain unresolved. Proposed
mappings are advisory and require inspection of every attached source.

| Member | Recorded school | Finding |
| --- | --- | --- |
| Peter Dutton | St Paul's School | Bald Hills, `acara:47999`; secondary attendance linkage needs inspection |
| Brendan O'Connor | Aquinas College | Ringwood, `acara:45947`; secondary attendance linkage needs inspection |
| John McVeigh | St Joseph's College | Rangeville, `acara:47861`; secondary attendance linkage and government identity evidence |
| David Coleman | St Thomas | Unresolved; primary-versus-secondary classification conflict |
| Michael Sukkar | Aquinas College | Ringwood, `acara:45947`; school-owned alumni evidence |
| Matt Canavan | Chisholm Catholic College | Cornubia, `acara:47888`; reused original interview evidence |
| Luke Howarth | St Patrick's College | Unresolved; campus-specific attendance evidence missing |
| Jordon Steele-John | Home educated | Non-institutional education; no invented school |
| Lucy Gichuhi | Lwak Girls | Proposed Kenyan manual institution; first-person parliamentary speech |
| Lidia Thorpe | Fitzroy and Collingwood Secondary Schools | Unresolved combined description; no unsupported split or current-school substitution |
| Gladys Liu | Belilios Public Girls School | Proposed Hong Kong manual institution; APH attendance and government identity evidence |
| Jessica Collins | John Paul College | Unresolved; maiden-name and New Zealand lead remains unverified |
| Damian Drum | Assumption College | Unresolved; exact attendance linkage remains insufficient |
| Stephen Jones | Edmund Rice College | West Wollongong, `acara:43663`; firsthand ABC interview |
| Jason Wood | Ferntree Gully Technical School | Proposed historical manual institution; own speech and government directory |
| Nola Marino | Bunbury Techincal School | Unresolved; historical identity and institution level need clarification |
| Steve Irons | Box Hill Technical College | Unresolved; College-versus-School identity ambiguity |
| Linda White | Greythorn High School | Proposed historical manual institution; government school history |
| Jennie George | Secondary school teacher at Bankstown Girls' School and Queanbeyan High School | Employment parsed as attendance; no mapping or split proposed |
| Kristina Keneally | Notre Dame Academy (Toledo, Ohio) | Proposed United States manual institution; official alumni publication |

The unmatched review queue also contains cases with existing derived institution
matches that are awaiting review. Those are recorded in `baseline.json`; they
are outside the unresolved-attendance research scope. This batch does not
overwrite accepted mappings or broaden member findings to shared school defaults.

## Provenance and preservation

`scope.json` freezes all 20 assertions, member identities, parliament memberships
and current decision heads. `baseline.json` binds the source and authority ledger
revisions. `tasks.json`, `manifest.json` and `proposals-0001.json` are native batch
snapshots. `results.json` preserves the imported findings.

`research-log.json` records **38 new query objects and 51 source-open attempts**
across the 15 additional cases, including failures and shared-source reuse. Each
case uses at most three query objects and six source opens. The five earlier
findings are reused with original retrieval dates and original research counts;
their earlier work is not represented as unused budget or newly performed work.
Prior guided attempts, conflicting evidence and historical budget exceptions
remain in their original artifacts and in the per-case references preserved by
`prior-research.json`.

The research was performed by the current Codex session and its research workers.
Exact model identifiers and token usage are unavailable and explicitly null.
`origin=agent-session` does not invent Luna/Sol attempts or change the rules for
existing guided model batches. Source URLs, retrieval dates and short paraphrases
remain attached to each evidence record; third-party ownership and attribution
remain with their cited publishers. Register identifiers come from the pinned
local ACARA files. Unsupported coordinates, dates and institution facts are omitted.

The earlier Parliament 48 packet and all applied research history are preserved.
Some reused candidate-specific attendance records are represented as explicit
identity claims in this new batch to satisfy the current evidence contract;
their original records remain unchanged. New evidence hashes and selected
references are validated independently.

## Verification

Run from the repository root:

```text
uv run python data/reference/review/research/all-unmatched-schools-20261015/verify.py
uv run python data/reference/review/research/all-unmatched-schools-20261015/verify.py --preview-check
```

The second command makes isolated copies of the evidence/decision ledgers and
batch, records synthetic test approvals only in that temporary copy, and checks
retention, preview and application. It never records human approvals or applies
decisions in the real reviewer. The first command checks the complete live scope,
evidence hashes, imports, per-case budgets and frozen prior-artifact references.
If the user subsequently changes the authority ledgers or scope, the baseline
check intentionally requires a new research snapshot.

Delivery verification passed for all 20 cases and all 74 evidence records. The
isolated workflow successfully applied 25 synthetic events (20 assertion outcomes
and five definitions), with canonical unresolved assertions changing from 20 to
nine in that copy. Shared school review statuses are separate from these
assertion-level mappings; no reduction in the pending shared-school queue is
claimed. The complete offline suite passed: 1,322 tests, three optional browser
tests skipped. Ruff, maintained-code typing and lock checks passed; repository-wide
typing still reports 158 pre-existing diagnostics.

Package version moves from **0.13.0 to 0.14.0** for native assertion-scoped batch
support. There is no dataset release or publication: these are unapplied research
proposals, and the authoritative data is unchanged.
