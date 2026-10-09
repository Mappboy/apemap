# Issue #66 acceptance audit

This tracks [issue #66](https://github.com/Mappboy/apemap/issues/66) against the
unreleased package 0.6.0 work in [PR #75](https://github.com/Mappboy/apemap/pull/75).
The initial PR implemented assertion resolution. The follow-up adds structured
retained evidence and deterministic scoring. The complete issue remains open.

| # | Acceptance criterion | Status | Implementation or remaining work |
| --- | --- | --- | --- |
| 1 | Identical school text can resolve to different institutions | Implemented | Assertion-scoped mapping overrides; John Paul College split verified read-only on the two real member IDs and deterministic fixtures. |
| 2 | Reuse school mappings without forcing all assertions to one target | Implemented | School defaults apply only where no overriding assertion resolution exists. Research on one assertion leaves the other resolutions intact. |
| 3 | Accepted mappings reference multiple retained evidence items | Implemented | Immutable evidence records and validated decision `evidence_refs`; retained snapshot and release provenance. |
| 4 | Represent supporting and contradicting evidence | Implemented | `supports`, `contradicts`, `contextual` stances; source cards and explained positive/negative score components. |
| 5 | Deterministic, explainable candidate ranking | Implemented | Versioned advisory scoring, visible dimensions, source deduplication, explicit unknowns, stable reference tie order. |
| 6 | Scores never automatically change canonical state | Implemented | Ranking is a pure copied presentation; evidence retention does not append decisions or alter attendance confidence. Reviewed mapping remains a separate signed preview/save. |
| 7 | Compare all assertions sharing recorded text | Implemented | Per-member comparison groups retain source rows and link directly to individual resolution; resolved references and localities distinguish identical institution names. |
| 8 | Bulk/default mapping requires an explicit affected-record preview | Partial | GUI lists affected/protected assertions and disallows saving when that list is unavailable. CLI produces a semantic preview internally, but still supports immediate save; an enforced separate CLI preview approval remains outstanding. |
| 9 | Assisted search suggests evidence without authoritative writes | Missing | Manual evidence retention and local institution lookup exist. External assisted evidence search, disposable suggestions and explicit suggestion-retention workflow are not implemented. |
| 10 | Releases do not require live web/model access | Implemented | Evidence and ledger bytes are frozen in a source snapshot; pinned recipes copy exact inputs and releases export archived evidence. Scoring and replay are offline. |
| 11 | Preserve append-only and supersession semantics | Implemented | Existing decision bytes remain untouched. Evidence has its own immutable append log; legacy URL provenance is represented deterministically without rewriting events. |
| 12 | Readiness identifies unresolved cases with analytical impact | Missing | Existing counts and advisory candidate scores do not calculate headline-sector/shared-school impact or provide a release-readiness report. |
| 13 | Migration covers existing mappings and detects ambiguous defaults | Implemented | Retained historical parity, scoped split diagnostics, same-name mappings and independent unresolved states are tested. No reviewed dataset is published by this work. |
| 14 | Successors use the common evidence/resolution framework | Partial | Successor mappings can reference the same structured records and retain scoped historical claims. Full timing, campus/profile/finance evidence adaptation and release sensitivity preparation remain later integration work. |

## Verification boundaries

The John Paul College probe uses `school:0477b661fc755dd3`, education assertions
for APH 297964 and 298800, and distinct Frankston/Kalgoorlie references. It proves
that separate reviewed targets and a member-specific research state can coexist
without changing attendance evidence. It does not establish the correct real
school for either person or save a decision.

The user's existing ledger modification is preserved and excluded from code
commits. New retained evidence is created only by explicit reviewer actions;
development tests use temporary fixtures. Existing release directories, recipe
pins, manifests, tags, databases and research outputs are preserved.

Relevant tests include `test_assertion_resolution.py`,
`test_assertion_migration.py`, `test_assertion_service.py`,
`test_review_evidence.py`, `test_review_scoring.py`,
`test_review_evidence_service.py`, `test_review_evidence_gui.py` and
`test_review_evidence_replay.py`. The PR records actual validation results; a
criterion being implemented does not claim the entire issue is complete.

Package metadata remains at the shared, unpublished **0.6.0** target, previously
bumped from 0.5.0 for this coherent review-v2 change. No further bump is needed
within the same PR, and no dataset release version is selected or published.
