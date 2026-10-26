# Missing secondary schools — round 2, 19 October 2026

This is a fresh, ordinary **Research batches** follow-up to issue #102 and PR
#106. It contains exactly the **30** members individually marked Needs further
research who still lack named institutional secondary attendance. The 18 applied
findings are excluded; Jordon Steele-John remains the separate home-education skip.
The scope is the original Parliament 48 opening-day cohort, not membership as of
October 2026 and not the separate backlog of 18 known-but-unmapped schools.

Open `/research-batches/issue-102-p48-round-2-20261019` in the local reviewer.
Use `data/aped-historical-v0.7.0.duckdb` and `data/raw/historical/acara`, the same
source snapshot as the parent. The original published cohort and parent artifacts
are preserved. Package **0.18.0** adds reusable follow-up preparation; published
dataset **0.7.0** is unchanged, with no dataset rebuild or publication.

## Results and approval boundary

- **30/30 researched**, with **136 new queries** and **155 conservatively counted
  source-open/read operations**, within every case's initial 3-query/6-open and
  targeted-retry 2-query/4-open limits.
- **One member / two school assertions supported:** Ali France's retained survey
  response explicitly identifies Benowa State High for grades 8–10 and St Michael’s
  College on the Gold Coast for grades 11–12. The original retained attendance
  evidence objects and retrieval dates are reused unchanged; grades are not
  converted into calendar years, completion or graduation.
- **Two independent institution-link proposals:** Benowa State High School
  (`acara:47529`) and St Michael's College, Merrimac (`acara:47887`). Queensland's
  directory and a school-hosted review independently support institution identity;
  register candidates are checked locally. The Caboolture primary-school namesake
  (`48049`) and Ali's explicitly primary St Kevin’s are excluded. Current campus
  descriptions do not establish historical attendance coordinates or dates.
- **29 members unresolved:** three bounded not-found outcomes and 26 uncertain
  outcomes. See [all case conclusions](comparison.md) and `research.json` for
  specific source failures, leads, gaps, wrong-person and non-attendance exclusions.
  Unresolved is not evidence that a person attended no secondary school.
- **Eight unavailable QIDs retried:** all remain unavailable, not absent. Reused
  identity evidence keeps its original retrieval dates; new checks are separately
  logged. The sailor Lisa Darmanin's Manly claim remains excluded from the senator.

There are **zero new reviews, zero evidence appends, zero applied proposals and
zero authority changes**. The 29 survey/history records previously retained for
issue #104 do not approve attendance or mapping. Every proposal remains available
for individual source inspection, with explicit reviewer attribution. Only after
review: retain approved evidence, generate and inspect a fresh exact preview,
then explicitly approve its returned SHA-256. No preview approval is inferred from
this research request. No member or school was contacted.

## Provenance and reproduction

`manifest.json`, `audit.json` and `tasks.json` were produced by the reusable CLI:

```powershell
uv run apemap review --db-path data/aped-historical-v0.7.0.duckdb --external-dir data/raw/historical/acara research-batch follow-up-missing-education issue-102-p48-round-2-20261019 --from-batch issue-102-p48-missing-schools-followup-20261019 --expected-count 30
```

Do not rerun preparation over this frozen destination. The parent manifest,
content, reviews and applications are hash-linked. Each task preserves its prior
findings, review history and retained member evidence **as context, not approval**.
Original research/search accounting is not reset or folded into new operations.

`research.json` preserves all 30 session observations, every actual query/access
attempt, source/reuse references, phase accounting and observed time bounds.
Cached finds, failed citation clicks and failed PDF screenshots conservatively
consume open budget without pretending each was a new page fetch. Unknown click
targets stay null in raw observations; native logs explicitly label the known
referrer rather than invent a target URL. Batched request timestamps are observed
group bounds, not invented individual completion times. Model/token usage is null
where unavailable; no guided-model attempts are invented.

`assemble.py` deterministically converts observations to `results.json`, reusing
retained evidence IDs and preserving exact school names. Late Wikipedia citation
repairs are labelled final-pass work, with original reported stage and chronology
preserved. The initial import attempt was rejected for stage ordering and wrote
no proposal snapshot; `results-before-stage-normalization.json` preserves that
unreviewed draft. `proposals-0001.json` is the sole successful native import.

The first preparation's four exact files are preserved under
`archive/research/2026-10-19/issue-102-round2-preparation-without-retained-context/`.
It had no results or approvals. Integration found and regression-tested a frozen
claim Mapping-versus-dict issue before preparing the active snapshot afresh.

`baseline.json` pins database, authority, parent files and canonical projection,
excluding runtime locks/bytecode. `verify.py` checks those unchanged inputs,
30-case membership, deterministic assembly/import equality, per-phase budgets,
eight QID retries, reused attendance IDs, candidate existence and zero decisions.
It deliberately becomes stale after later legitimate reviews or authority changes;
do not rewrite the delivery baseline to conceal them.

```powershell
uv run python data/reference/review/research/issue-102-p48-round-2-20261019/assemble.py data/reference/review/research/issue-102-p48-round-2-20261019/research.json --check
uv run python data/reference/review/research/issue-102-p48-round-2-20261019/verify.py
```

The baseline's `--record-baseline` option was used once before native import; it
refuses to overwrite an existing baseline. No full publisher pages, credentials,
source downloads, runtime caches or test reports are part of this delivery.
