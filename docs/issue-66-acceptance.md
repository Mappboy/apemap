# Issue #66 acceptance audit

This tracks [issue #66](https://github.com/Mappboy/apemap/issues/66) against the
unreleased review work in [PR #75](https://github.com/Mappboy/apemap/pull/75)
and its package 0.7.0 follow-up.

The [local 0.6.1 build record](releases/0.6.1/README.md) separately preserves an
earlier frozen working checkout and its validation limits; it does not establish
the current branch's acceptance status or package version.

The initial PR implemented assertion resolution. The follow-up adds structured
retained evidence and deterministic scoring. A further follow-up distinguishes
ambiguous names and unsuitable candidates from rejected attendance, requires
individual review for those shared names, and derives case completion from the
active assertions. The complete issue remains open.

| # | Acceptance criterion | Status | Implementation or remaining work |
| --- | --- | --- | --- |
| 1 | Identical school text can resolve to different institutions | Implemented | Assertion-scoped mapping overrides; John Paul College split verified read-only on the two real member IDs and deterministic fixtures. |
| 2 | Reuse school mappings without forcing all assertions to one target | Implemented | School defaults apply only where no overriding assertion resolution exists. Typed ambiguous/no-candidate school research requires individual reviewed targets, including for exact register matches, while preserving attendance and explicit member resolutions. |
| 3 | Accepted mappings reference multiple retained evidence items | Implemented | Immutable evidence records and validated decision `evidence_refs`; retained snapshot and release provenance. |
| 4 | Represent supporting and contradicting evidence | Implemented | `supports`, `contradicts`, `contextual` stances; source cards and explained positive/negative score components. |
| 5 | Deterministic, explainable candidate ranking | Implemented | Versioned advisory scoring, visible dimensions, source deduplication, explicit unknowns, stable reference tie order. |
| 6 | Scores never automatically change canonical state | Implemented | Ranking is a pure copied presentation; evidence retention does not append decisions or alter attendance confidence. Reviewed mapping remains a separate signed preview/save. |
| 7 | Compare all assertions sharing recorded text | Implemented | Per-member comparison groups retain source rows and link directly to individual resolution; resolved references and localities distinguish identical institution names. Shared-name completion counts distinct active assertions, excludes withdrawn attendance, and reopens on newly unresolved claims; GUI, CLI and exports use the same progress. |
| 8 | Bulk/default mapping requires an explicit affected-record preview | Implemented | GUI affected/protected assertions and separate CLI JSON artifact approval. Exact event bytes, source revisions, inventory and effects bind atomic imports and supersessions; stale or altered previews append nothing. |
| 9 | Assisted search suggests evidence without authoritative writes | Implemented | Configurable OpenRouter, Gemini and OpenAI provider choices; cited disposable jobs, explicit inspection/edit/evidence preview/retention, then separate mapping review. Mocked HTTP validation; live account/model availability is not established. |
| 10 | Releases do not require live web/model access | Implemented | Evidence and ledger bytes are frozen in a source snapshot; pinned recipes copy exact inputs and releases export archived evidence. Scoring and replay are offline. |
| 11 | Preserve append-only and supersession semantics | Implemented | Existing decision bytes remain untouched. Evidence has its own immutable append log; legacy URL provenance is represented deterministically without rewriting events. |
| 12 | Readiness identifies unresolved cases with analytical impact | Implemented | Advisory sector/shared-school top-ten scenarios, ties, joint bounds, evidence scores and unknown candidate coverage. CLI/UI working projections and frozen offline release reports preserve person/attendance grains. |
| 13 | Migration covers existing mappings and detects ambiguous defaults | Implemented | Retained historical parity, scoped split diagnostics, same-name mappings and independent unresolved states are tested. No reviewed dataset is published by this work. |
| 14 | Successors use the common evidence/resolution framework | Implemented | Independently selected identity/location/campus/sector/timing/profile/finance evidence roles; copied historical replay adaptation and successor report with separate proxy suitability. Research attendance estimates remain non-authoritative; sector sensitivity retains denominators. |

## Implemented follow-up milestones

The follow-up implements criteria 8, 14, 12 and 9 in that order. School-wide CLI
changes require a separately approved affected-record preview, including imports
and supersessions. Successor evidence uses the common retained-source framework
with independent timing, campus, sector, profile and finance claims. Attendance
estimates (birth year +12 through +18) are labelled research context only.

Readiness is an advisory, deterministic report of sector and shared-school impact,
including incomplete candidate coverage and jointly influential unresolved cases.
Releases compute it from consumed snapshots without network or model access.

Assisted research keeps disposable suggestions separate from authority. The
reviewer selects OpenRouter, Gemini or OpenAI, inspects sources, previews retained
evidence and separately previews a decision. Provider requests use environment
credentials; tests use mocked HTTP and temporary evidence/decision fixtures.
No real research ledger, historical release or pinned source is refreshed.

Implementation requires focused regression tests, the full available pytest
suite, Ruff lint/format checks, ty, lock/version checks and a complete branch
review before a draft PR. Focused fixtures verify these milestones. Provider calls
use mocked HTTP; implementation does not establish live account availability or
source correctness. See [assisted research and readiness](review-assisted-search.md)
for usage and limitations.

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
`test_review_evidence_replay.py`. Follow-up fixtures include
`test_review_cli_approval.py`, `test_review_context_evidence.py`,
`test_review_research.py` and `test_review_readiness.py`.
The PR records actual validation results; a
criterion being implemented does not claim the entire issue is complete.

The follow-up targets package **0.6.1 → 0.7.0** for new functionality and the
incompatible separate school-wide CLI approval requirement. Package and root
lockfile versions must agree before handoff. No dataset release version is selected
or published; historical dataset contracts remain unchanged.
